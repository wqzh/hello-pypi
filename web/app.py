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
from .config import SESSIONS_DIR, STATIC_DIR, TOOL_NAMES, model
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
    return {
        "model": model.id,
        "provider": model.provider,
        "tools": TOOL_NAMES,
    }


# ---------------------------------------------------------------- 会话 API


class CreateSessionReq(BaseModel):
    title: Optional[str] = "新对话"


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
            }
            for s in items
        ]
    )


@app.post("/api/sessions")
async def create_session(req: CreateSessionReq):
    session_id = uuid.uuid4().hex
    session = WebSession(session_id, req.title or "新对话")
    SESSIONS[session_id] = session
    return JSONResponse({"id": session_id, "title": session.title})


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
            }
        )
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