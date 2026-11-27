"""FastAPI 应用：静态页面、会话 API、WebSocket 事件流。

启动方式（仓库根目录）::

    python hello_agent_loop_web.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import maybe_compact
from .config import (
    DEFAULT_MODEL_KEY,
    MODEL_INDEX,
    SESSIONS_DIR,
    STATIC_DIR,
    TOOL_NAMES,
    get_model,
    model_public_list,
    tool_public_list,
)
from .sessions import WebSession

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("pi.web")

SESSIONS: dict[str, WebSession] = {}

ALLOWED_ORIGIN_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def load_all_sessions() -> None:
    if not SESSIONS_DIR.exists():
        return
    for path in sorted(SESSIONS_DIR.glob("*.jsonl")):
        session_id = path.stem
        if session_id in SESSIONS:
            continue
        session = WebSession(session_id, "历史会话")
        session.load_session()
        SESSIONS[session_id] = session
    log.info("已加载 %d 个历史会话", len(SESSIONS))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    load_all_sessions()
    yield


app = FastAPI(title="Pi Agent Web", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _origin_allowed(origin: Optional[str]) -> bool:
    """只允许本机页面访问 API / WebSocket（防御跨站脚本驱动 Agent）。"""
    if not origin:
        return True  # 非浏览器客户端（curl / 测试脚本）
    host = urlparse(origin).hostname
    return host in ALLOWED_ORIGIN_HOSTS


@app.middleware("http")
async def origin_guard(request: Request, call_next):
    if request.url.path.startswith("/api/") and not _origin_allowed(
        request.headers.get("origin")
    ):
        return JSONResponse({"detail": "forbidden origin"}, status_code=403)
    return await call_next(request)


# ---------------------------------------------------------------- 页面


@app.get("/")
async def index():
    return FileResponse(
        STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"}
    )


@app.get("/api/meta")
async def meta():
    default_model = get_model(DEFAULT_MODEL_KEY)
    return {
        "model": default_model["key"],
        "model_label": default_model["label"],
        "provider": default_model["provider"],
        "models": model_public_list(),
        "tools": tool_public_list(),
        "tool_names": TOOL_NAMES,
    }


@app.get("/api/models")
async def models():
    return JSONResponse(
        {
            "default": DEFAULT_MODEL_KEY,
            "models": model_public_list(),
        }
    )


# ---------------------------------------------------------------- 模型连通性

#: 鉴权方式：部分服务要 ``Authorization: Bearer <key>``，部分直接给 key。
#: 顺序即尝试顺序；无 key 时只试一次无鉴权请求。
_AUTH_MODES = ("bearer", "raw", "api-key")


def _auth_headers(mode: str, api_key: str) -> dict[str, str]:
    if not api_key:
        return {}
    if mode == "bearer":
        return {"Authorization": f"Bearer {api_key}"}
    if mode == "raw":
        return {"Authorization": api_key}
    return {"api-key": api_key}


def _short(text: str, limit: int = 220) -> str:
    text = " ".join((text or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


async def _probe_models(client, entry) -> dict:
    """GET {base}/models，按鉴权方式依次尝试。"""
    url = entry["base_url"].rstrip("/") + "/models"
    api_key = entry.get("api_key") or ""
    tried: list[dict] = []
    modes = _AUTH_MODES if api_key else (None,)

    last_status = 0
    last_detail = ""
    for mode in modes:
        headers = _auth_headers(mode, api_key) if mode else {}
        started = time.time()
        try:
            resp = await client.get(url, headers=headers)
        except Exception as exc:  # noqa: BLE001
            tried.append(
                {"auth": mode or "none", "status": 0, "error": _short(str(exc), 120)}
            )
            last_detail = str(exc)
            continue
        latency = int((time.time() - started) * 1000)
        body = resp.text or ""
        tried.append({"auth": mode or "none", "status": resp.status_code})

        if resp.status_code == 200:
            # 确认不是网关返回的假 200 页面
            try:
                data = resp.json()
            except Exception:  # noqa: BLE001
                last_status, last_detail = 200, "返回内容不是 JSON"
                continue
            if isinstance(data, dict) and ("data" in data or "models" in data):
                count = len(data.get("data") or data.get("models") or [])
                return {
                    "ok": True,
                    "status": 200,
                    "auth": mode or "none",
                    "endpoint": "/models",
                    "latency_ms": latency,
                    "detail": f"可用，暴露 {count} 个模型",
                    "tried": tried,
                }
            last_status, last_detail = 200, "返回格式不匹配（非 OpenAI 模型列表）"
            continue

        if resp.status_code in (404, 405):
            # 该服务不实现 /models，交给 chat/completions 探测
            return {
                "skipped": True,
                "status": resp.status_code,
                "detail": _short(body),
                "tried": tried,
            }

        last_status = resp.status_code
        last_detail = _short(body) or resp.reason_phrase
        # 401/403：换下一种鉴权方式再试；其余状态直接停
        if resp.status_code not in (401, 403):
            break

    return {
        "ok": False,
        "status": last_status,
        "auth": tried[-1]["auth"] if tried else "none",
        "endpoint": "/models",
        "latency_ms": 0,
        "detail": last_detail,
        "tried": tried,
    }


async def _probe_chat(client, entry) -> dict:
    """POST {base}/chat/completions 发一条最小请求。"""
    url = entry["base_url"].rstrip("/") + "/chat/completions"
    api_key = entry.get("api_key") or ""
    payload = {
        "model": entry["id"],
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }
    tried: list[dict] = []
    modes = _AUTH_MODES if api_key else (None,)

    last_status, last_detail = 0, ""
    for mode in modes:
        headers = _auth_headers(mode, api_key) if mode else {}
        started = time.time()
        try:
            resp = await client.post(url, json=payload, headers=headers)
        except Exception as exc:  # noqa: BLE001
            tried.append(
                {"auth": mode or "none", "status": 0, "error": _short(str(exc), 120)}
            )
            last_detail = str(exc)
            continue
        latency = int((time.time() - started) * 1000)
        tried.append({"auth": mode or "none", "status": resp.status_code})

        if resp.status_code == 200:
            return {
                "ok": True,
                "status": 200,
                "auth": mode or "none",
                "endpoint": "/chat/completions",
                "latency_ms": latency,
                "detail": "可用（chat 探测通过）",
                "tried": tried,
            }

        last_status = resp.status_code
        last_detail = _short(resp.text) or resp.reason_phrase
        if resp.status_code not in (401, 403):
            break

    return {
        "ok": False,
        "status": last_status,
        "auth": tried[-1]["auth"] if tried else "none",
        "endpoint": "/chat/completions",
        "latency_ms": 0,
        "detail": last_detail,
        "tried": tried,
    }


@app.post("/api/models/{key}/test")
async def test_model(key: str):
    entry = MODEL_INDEX.get(key)
    if entry is None:
        return JSONResponse(
            {"ok": False, "status": 404, "detail": f"未知模型键：{key}"},
            status_code=404,
        )

    import httpx

    timeout = float(os.getenv("PI_MODEL_TEST_TIMEOUT", "15"))
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        result = await _probe_models(client, entry)
        if result.get("skipped"):
            chat = await _probe_chat(client, entry)
            chat["tried"] = result.get("tried", []) + chat.get("tried", [])
            result = chat

    result.update(
        {
            "key": entry["key"],
            "label": entry["label"],
            "model": entry["id"],
            "base_url": entry["base_url"],
            "has_key": bool(entry.get("api_key")),
        }
    )
    log.info(
        "模型探测 %s → %s %s",
        entry["key"],
        "OK" if result["ok"] else "FAIL",
        result.get("status"),
    )
    return JSONResponse(result)


async def _fetch_remote_models(client, entry) -> dict:
    """GET {base}/models，按鉴权方式依次尝试，返回上游模型列表。"""
    import httpx  # noqa: F401  (仅作异常类型参考)

    url = entry["base_url"].rstrip("/") + "/models"
    api_key = entry.get("api_key") or ""
    tried: list[dict] = []
    modes = _AUTH_MODES if api_key else (None,)

    last_status = 0
    last_detail = ""
    for mode in modes:
        headers = _auth_headers(mode, api_key) if mode else {}
        started = time.time()
        try:
            resp = await client.get(url, headers=headers)
        except Exception as exc:  # noqa: BLE001
            tried.append({"auth": mode or "none", "status": 0})
            last_detail = _short(str(exc), 200)
            continue

        latency = int((time.time() - started) * 1000)
        tried.append({"auth": mode or "none", "status": resp.status_code})

        if resp.status_code != 200:
            last_status = resp.status_code
            last_detail = _short(resp.text) or resp.reason_phrase
            if resp.status_code not in (401, 403):
                break
            continue

        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            last_status, last_detail = 200, "返回内容不是 JSON"
            continue

        raw_items = None
        if isinstance(data, dict):
            raw_items = data.get("data") or data.get("models")
        elif isinstance(data, list):
            raw_items = data

        if raw_items is None:
            last_status, last_detail = 200, "返回格式不匹配（非 OpenAI 模型列表）"
            continue

        items = []
        for item in raw_items:
            if isinstance(item, str):
                items.append({"id": item})
            elif isinstance(item, dict):
                items.append(
                    {
                        "id": str(item.get("id") or item.get("name") or ""),
                        "owned_by": str(item.get("owned_by") or item.get("ownedBy") or ""),
                        "created": item.get("created"),
                    }
                )
        items = [i for i in items if i["id"]]
        items.sort(key=lambda i: i["id"])

        return {
            "ok": True,
            "status": 200,
            "auth": mode or "none",
            "latency_ms": latency,
            "count": len(items),
            "models": items,
            "detail": "",
            "tried": tried,
        }

    return {
        "ok": False,
        "status": last_status,
        "auth": tried[-1]["auth"] if tried else "none",
        "latency_ms": 0,
        "count": 0,
        "models": [],
        "detail": last_detail,
        "tried": tried,
    }


@app.post("/api/models/{key}/remote")
async def remote_models(key: str):
    """获取该模型地址下可用的模型列表（GET {base_url}/models）。"""
    entry = MODEL_INDEX.get(key)
    if entry is None:
        return JSONResponse(
            {"ok": False, "status": 404, "detail": f"未知模型键：{key}"},
            status_code=404,
        )

    import httpx

    timeout = float(os.getenv("PI_MODEL_TEST_TIMEOUT", "15"))
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        result = await _fetch_remote_models(client, entry)

    result.update({"key": entry["key"], "label": entry["label"], "base_url": entry["base_url"]})
    log.info(
        "获取模型列表 %s → %s (%s 个)",
        entry["key"],
        "OK" if result["ok"] else "FAIL",
        result.get("count"),
    )
    return JSONResponse(result)



# ---------------------------------------------------------------- 会话 API


class CreateSessionReq(BaseModel):
    title: Optional[str] = "新对话"
    model: Optional[str] = None


def _preview(messages: list[dict]) -> str:
    for message in messages:
        if message.get("role") == "user":
            text = (message.get("text") or "").strip().replace("\n", " ")
            return text[:40] + ("…" if len(text) > 40 else "")
    return "新对话"


@app.get("/api/sessions")
async def list_sessions():
    items = sorted(SESSIONS.values(), key=lambda s: s.updated_at, reverse=True)
    return JSONResponse(
        [
            {
                "id": s.id,
                "title": s.title,
                "preview": _preview(s.display_messages),
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "message_count": len(s.display_messages),
                "busy": s.run_lock.locked(),
                "model_key": s.model_key,
                "model_label": MODEL_INDEX.get(s.model_key, {}).get("label", ""),
            }
            for s in items
        ]
    )


@app.post("/api/sessions")
async def create_session(req: CreateSessionReq):
    session_id = uuid.uuid4().hex
    session = WebSession(session_id, req.title or "新对话")
    if req.model and req.model in MODEL_INDEX:
        session.switch_model(req.model)
    SESSIONS[session_id] = session
    return JSONResponse(
        {"id": session_id, "title": session.title, "model_key": session.model_key}
    )


@app.delete("/api/sessions/{sid}")
async def delete_session(sid: str):
    session = SESSIONS.pop(sid, None)
    if session is not None:
        session.dispose()
        try:
            if session.storage_path.exists():
                session.storage_path.unlink()
        except OSError as exc:
            log.warning("删除会话文件失败 %s: %s", sid, exc)
    for other in SESSIONS.values():
        other.broadcast({"type": "deleted", "id": sid})
    return JSONResponse({"ok": True})


@app.get("/api/sessions/{sid}/messages")
async def get_messages(sid: str):
    session = SESSIONS.get(sid)
    if session is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(
        {
            "messages": session.display_messages,
            "title": session.title,
            "busy": session.run_lock.locked(),
            "model_key": session.model_key,
        }
    )


# ---------------------------------------------------------------- 运行一轮


async def handle_user_message(session: WebSession, text: str) -> None:
    async with session.run_lock:
        if session.deleted:
            return
        await session.ensure_agent()

        now = time.time()
        session.display_messages.append(
            {"role": "user", "text": text, "ts": now * 1000}
        )
        if not session.first_user_ts:
            session.first_user_ts = now
        if session.title in ("", "新对话", "历史会话"):
            session.title = text.replace("\n", " ")[:30]
        session.updated_at = now

        # 标题落盘，重启后仍可读回
        try:
            if session.pi_session is not None:
                session.pi_session.storage.set_label(session.title)
        except Exception as exc:  # noqa: BLE001
            log.debug("写入会话标题失败: %s", exc)

        session.broadcast(
            {
                "type": "user",
                "text": text,
                "ts": now * 1000,
                "title": session.title,
                "model_key": session.model_key,
            }
        )
        turn_mark = len(session.display_messages)
        session.broadcast({"type": "busy", "value": True})

        try:
            await session.agent.prompt(text)
        except Exception as exc:  # noqa: BLE001
            log.exception("agent.prompt 失败")
            session.broadcast({"type": "error", "message": str(exc)})
        finally:
            if not session.deleted:
                try:
                    # 先落盘本轮消息，再压缩：否则压缩会把本轮消息只存进
                    # compaction.retained_tail，重启后 UI 历史缺最后一轮。
                    session.sync_transcript()
                except Exception as exc:  # noqa: BLE001
                    log.warning("写入会话失败: %s", exc)
                try:
                    await maybe_compact(session)
                except Exception as exc:  # noqa: BLE001
                    log.warning("上下文压缩失败: %s", exc)
                    session.broadcast(
                        {"type": "warn", "message": f"压缩失败: {exc}"}
                    )

            session.updated_at = time.time()

            # 诊断：本轮若没有产生任何 assistant 展示消息，多半是上游返回了空流
            new_assistant = [
                m
                for m in session.display_messages[turn_mark:]
                if m.get("role") == "assistant"
            ]
            if not new_assistant:
                log.warning(
                    "会话 %s 本轮无 assistant 输出（上游可能返回空流）", session.id
                )

            session.broadcast({"type": "busy", "value": False})
            session.broadcast({"type": "done"})


# ---------------------------------------------------------------- WebSocket


@app.websocket("/ws/{sid}")
async def ws_endpoint(ws: WebSocket, sid: str):
    if not _origin_allowed(ws.headers.get("origin")):
        await ws.close(code=1008)
        return

    await ws.accept()

    session = SESSIONS.get(sid)
    if session is None:
        await ws.send_text(
            json.dumps({"type": "error", "message": "会话不存在"}, ensure_ascii=False)
        )
        await ws.close()
        return

    queue: asyncio.Queue = asyncio.Queue()
    session.listeners.add(queue)

    async def pump() -> None:
        while True:
            item = await queue.get()
            try:
                await ws.send_text(json.dumps(item, ensure_ascii=False))
            except Exception:  # noqa: BLE001
                break

    pump_task = asyncio.create_task(pump())

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except ValueError:
                continue

            mtype = data.get("type")
            if mtype == "abort":
                if session.agent is not None:
                    session.agent.abort()
                continue

            if mtype == "set_model":
                key = str(data.get("model") or "").strip()
                if key not in MODEL_INDEX:
                    await queue.put(
                        {"type": "warn", "message": f"未知模型：{key or '(空)'}"}
                    )
                    continue
                if session.run_lock.locked():
                    await queue.put(
                        {"type": "warn", "message": "生成中，结束或停止后才能切换模型"}
                    )
                    continue
                entry = session.switch_model(key)
                await queue.put(
                    {
                        "type": "model",
                        "key": entry["key"],
                        "label": entry["label"],
                    }
                )
                continue

            if mtype != "user_message":
                continue

            text = (data.get("text") or "").strip()
            if not text:
                continue

            if session.run_task is not None and not session.run_task.done():
                await queue.put(
                    {"type": "warn", "message": "上一条消息仍在处理中，请稍候"}
                )
                continue

            session.run_task = asyncio.create_task(
                handle_user_message(session, text)
            )
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("WebSocket 异常 %s: %s", sid, exc)
    finally:
        session.listeners.discard(queue)
        pump_task.cancel()


def run() -> None:
    import uvicorn

    host = os.getenv("PI_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("PI_WEB_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()