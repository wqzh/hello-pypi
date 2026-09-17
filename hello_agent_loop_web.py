import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from pi.pi_ai import Model, TextContent, UserMessage, AssistantMessage, ToolResultMessage
from pi.pi_agent_core import Agent, AgentOptions
from pi.pi_agent_core.harness.session import Session, JsonlSessionStorage, SessionEntryType
from pi.pi_agent_core.harness.skills import LoadSkillsOptions, load_skills, format_skills_for_prompt
from pi.pi_agent_core.harness.compaction import (
    estimate_context_tokens,
    should_compact,
    find_cut_point,
    generate_summary,
    CompactionSettings,
)
from load_env import load_env_config
from pi.pi_tools import GetCityWeatherTool, WebSearchTavilyTool
from pi.pi_coding_agent.tools import (
    BashTool, EditTool, FindTool, GrepTool, LsTool, ReadTool, WriteTool
)

PI_BUILTIN_TOOLS = [BashTool(), EditTool(), FindTool(), GrepTool(), LsTool(), ReadTool(), WriteTool()]

API_KEY, MODEL_CONFIG = load_env_config()

model = Model(
    id=MODEL_CONFIG['id'],
    name=MODEL_CONFIG['name'],
    base_url=MODEL_CONFIG['base_url'],
    api=MODEL_CONFIG['api'],
    provider=MODEL_CONFIG['provider'],
    input=["text"],
    context_window=MODEL_CONFIG['context_window'],
    max_tokens=MODEL_CONFIG['max_tokens'],
    reasoning=MODEL_CONFIG['reasoning'],
)
print(f"[INFO] 模型配置: {model.id} @ {model.base_url}")

skills = load_skills(LoadSkillsOptions())
if skills.diagnostics:
    print(f"[WARN] Skill diagnostics: {skills.diagnostics}")
skills_block = format_skills_for_prompt(skills.skills)
print(f"[INFO] Loaded {len(skills.skills)} skill(s)")

compaction_settings = CompactionSettings(
    enabled=True,
    reserve_tokens=30000,
    keep_recent_tokens=8000,
)

BASE_PROMPT = "你是一个智能助手，你必须用用户提问对应的语种进行思考和回答！你可以调用你掌握的工具来辅助自己。"
SYSTEM_PROMPT = BASE_PROMPT + "\n\n" + skills_block if skills_block else BASE_PROMPT


# ==================== 通用取值工具 ====================
def _get(obj, *names, default=None):
    """同时支持属性访问与 dict 访问，兼容反序列化后的不同形态。"""
    if obj is None:
        return default
    for n in names:
        if isinstance(obj, dict):
            if n in obj and obj[n] is not None:
                return obj[n]
        else:
            if hasattr(obj, n):
                v = getattr(obj, n)
                if v is not None:
                    return v
    return default


def _extract_blocks(content) -> tuple[str, str]:
    """从 message.content 提取 (thinking_text, answer_text)。

    兼容 list[TextContent] / list[dict] / str / None。
    reasoning 块的判定：
      - textSignature / text_signature / signature == "reasoning_content"
      - type in ("reasoning", "reasoning_content", "thinking")
      - 有独立 .thinking 字段
    非文本块（tool_call / tool_result 等）会被忽略，由调用方单独处理。
    """
    thinking = ""
    answer = ""
    if content is None:
        return thinking, answer

    if isinstance(content, str):
        return thinking, content

    if isinstance(content, list):
        for item in content:
            item_type = _get(item, "type", default="")
            # 跳过非文本块
            if item_type in ("tool_call", "tool_use", "tool_result", "image", "file"):
                continue

            sig = _get(item, "textSignature", "text_signature", "signature")
            is_reasoning = (
                sig == "reasoning_content"
                or item_type in ("reasoning", "reasoning_content", "thinking")
            )

            text = _get(item, "text", default="")
            if not text and isinstance(item, str):
                text = item
            if not isinstance(text, str):
                text = str(text)

            thinking_field = _get(item, "thinking", default="")

            if is_reasoning or thinking_field:
                thinking += text or ""
                if thinking_field:
                    thinking += thinking_field if isinstance(thinking_field, str) else str(thinking_field)
            else:
                answer += text
        return thinking, answer

    return thinking, str(content)


def _extract_tool_calls(content) -> list[dict]:
    """从 assistant 消息 content 中提取工具调用块。

    返回 [{"tool_call_id":..., "tool_name":..., "args": {...}}, ...]
    """
    calls: list[dict] = []
    if not isinstance(content, list):
        return calls
    for item in content:
        item_type = _get(item, "type", default="")
        if item_type not in ("tool_call", "tool_use", "function_call"):
            continue
        # tool_call 常见字段：name / tool_name / function.name
        fn = _get(item, "function")
        tool_name = (
            _get(item, "tool_name", "name")
            or _get(fn, "name")
            or ""
        )
        args = _get(item, "args", "arguments", "input")
        if args is None and fn is not None:
            args = _get(fn, "arguments", "args")
        call_id = _get(item, "tool_call_id", "id", "call_id", default="")
        calls.append({
            "tool_call_id": call_id,
            "tool_name": tool_name,
            "args": args if args is not None else {},
        })
    return calls


def _extract_tool_results(content) -> list[dict]:
    """从 tool / user 消息 content 中提取工具结果块。

    返回 [{"tool_call_id":..., "tool_name":..., "result": str}, ...]
    """
    results: list[dict] = []
    if not isinstance(content, list):
        return results
    for item in content:
        item_type = _get(item, "type", default="")
        if item_type not in ("tool_result", "tool_result_block", "function_result"):
            continue
        call_id = _get(item, "tool_call_id", "id", "call_id", default="")
        tool_name = _get(item, "tool_name", "name", default="")

        # result 内容可能是 str，也可能是 list[TextContent]，还可能是 dict
        inner = _get(item, "content", "result", "output", default="")
        result_text = ""
        if isinstance(inner, str):
            result_text = inner
        elif isinstance(inner, list):
            for sub in inner:
                t = _get(sub, "text", default="")
                if t:
                    result_text += t if isinstance(t, str) else str(t)
                elif isinstance(sub, str):
                    result_text += sub
        elif inner is not None:
            result_text = str(inner)

        results.append({
            "tool_call_id": call_id,
            "tool_name": tool_name,
            "result": result_text,
        })
    return results


# ==================== 会话管理 ====================
class WebSession:
    def __init__(self, session_id: str, title: str = "新对话"):
        self.id = session_id
        self.title = title
        self.created_at = time.time()
        self.updated_at = time.time()
        self.display_messages: list[dict] = []
        self.first_user_ts: float = 0
        self.agent: Optional[Agent] = None
        self.busy = False
        self.pi_session: Optional[Session] = None
        self.storage_path = Path(".pi/sessions") / f"{session_id}.jsonl"
        self._pending_thinking = ""
        self._pending_text = ""
        self._thinking_active = False
        self._subscribed = False
        self._out_queue: Optional[asyncio.Queue] = None

    async def ensure_agent(self):
        if self.agent is None:
            self.agent = await create_agent()
            await self.load_session()
            self.agent.state.messages = self.build_agent_messages()
        if not self._subscribed:
            self._subscribed = True
            self.agent.subscribe(self.on_event)
        return self.agent

    def on_event(self, ev, sig):
        payload = serialize_event(ev)
        if payload is None:
            return

        if self._out_queue is not None:
            try:
                self._out_queue.put_nowait(payload)
            except Exception:
                pass

        ptype = payload.get("type")
        sess = self
        if not sess or not sess.pi_session:
            return

        if ptype == "thinking_start":
            sess._thinking_active = True
            sess._pending_thinking = ""
        elif ptype == "thinking_delta":
            if sess._thinking_active:
                sess._pending_thinking += payload.get("delta", "")
        elif ptype == "thinking_end":
            sess._thinking_active = False

        if ptype == "text_delta":
            sess._pending_text += payload.get("delta", "")

        if ptype == "tool_start":
            sess.display_messages.append({
                "role": "tool",
                "tool_name": payload.get("tool_name", ""),
                "args": payload.get("args", {}),
                "result": "",
                "ts": time.time()
            })
        if ptype == "tool_end":
            tool_name = payload.get("tool_name", "")
            result = payload.get("result", "")
            for m in reversed(sess.display_messages):
                if m.get("role") == "tool" and m.get("tool_name") == tool_name and m.get("result") == "":
                    m["result"] = result
                    break

        if ptype == "message_end":
            role = payload.get("role", "")
            if role == "assistant" and (sess._pending_text or sess._pending_thinking):
                thinking = sess._pending_thinking
                text = sess._pending_text
                asyncio.create_task(sess.save_assistant_message(text, thinking))
                sess.display_messages.append({
                    "role": "assistant",
                    "content": text,
                    "thinking": thinking,
                    "ts": time.time()
                })
                sess._pending_thinking = ""
                sess._pending_text = ""

    def build_agent_messages(self):
        msgs = []
        for m in self.display_messages:
            if m["role"] == "user":
                msgs.append(UserMessage(content=[TextContent(text=m["content"])], timestamp=int(time.time() * 1e9)))
            elif m["role"] == "assistant":
                blocks = [TextContent(text=m["content"])] if m.get("content") else []
                msgs.append(AssistantMessage(content=blocks))
        return msgs

    async def load_session(self):
        """加载历史会话数据（健壮版，兼容属性/dict 及 tool_call/tool_result 块）"""
        if not self.storage_path.exists():
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            storage = JsonlSessionStorage(self.storage_path)
            self.pi_session = Session(storage)
            return

        storage = JsonlSessionStorage(self.storage_path)
        self.pi_session = Session(storage)
        self.display_messages = []
        first_user_text = None
        seen_entry_ids = set()

        # 挂起的工具调用：key = tool_call_id（若无则用 tool_name），value = display_messages 中的下标
        pending_tools: Dict[str, int] = {}

        try:
            entries = list(self.pi_session.get_entries())
        except Exception as e:
            print(f"[WARN] load_session get_entries failed: {e}")
            entries = []

        for entry in entries:
            etype = _get(entry, "type")
            if etype != "message":
                continue

            eid = _get(entry, "id")
            if eid is not None:
                if eid in seen_entry_ids:
                    continue
                seen_entry_ids.add(eid)

            msg = _get(entry, "data")
            if msg is None:
                continue

            role = _get(msg, "role", default="")
            content = _get(msg, "content")

            # ---------- 1) 先看是否携带 tool_result 块（不管 role 是 tool 还是 user） ----------
            tool_results = _extract_tool_results(content)
            if tool_results:
                for tr in tool_results:
                    key = tr.get("tool_call_id") or tr.get("tool_name") or ""
                    idx = pending_tools.get(key)
                    if idx is None:
                        # 没有匹配到挂起调用，兜底：新建一条 tool 记录
                        self.display_messages.append({
                            "role": "tool",
                            "tool_name": tr.get("tool_name", "") or "",
                            "args": {},
                            "result": tr.get("result", ""),
                            "ts": time.time()
                        })
                    else:
                        self.display_messages[idx]["result"] = tr.get("result", "")
                        pending_tools.pop(key, None)
                # 该消息只承载工具结果，不作为普通消息渲染
                continue

            # ---------- 2) role == tool：纯工具结果消息（老格式） ----------
            if role == "tool":
                tool_name = _get(msg, "tool_name", "toolName", "name", default="") or ""
                call_id = _get(msg, "tool_call_id", "id", default="")
                _, result_text = _extract_blocks(content)
                key = call_id or tool_name
                idx = pending_tools.get(key)
                if idx is not None:
                    self.display_messages[idx]["result"] = result_text
                    pending_tools.pop(key, None)
                else:
                    self.display_messages.append({
                        "role": "tool",
                        "tool_name": tool_name,
                        "args": {},
                        "result": result_text,
                        "ts": time.time()
                    })
                continue

            # ---------- 3) assistant：提取 reasoning/text + tool_call ----------
            if role == "assistant":
                thinking, answer = _extract_blocks(content)

                # 若 assistant 只包含 tool_call 而没有正文，也要保留 tool 调用记录
                # 正文（含 thinking）若非空，加入一条 assistant 展示
                if answer or thinking:
                    self.display_messages.append({
                        "role": "assistant",
                        "content": answer,
                        "thinking": thinking,
                        "ts": time.time()
                    })

                # 提取 tool_call 块，挂起等待结果
                calls = _extract_tool_calls(content)
                for c in calls:
                    self.display_messages.append({
                        "role": "tool",
                        "tool_name": c["tool_name"],
                        "args": c["args"],
                        "result": "",
                        "ts": time.time()
                    })
                    key = c.get("tool_call_id") or c.get("tool_name") or ""
                    pending_tools[key] = len(self.display_messages) - 1
                continue

            # ---------- 4) user：真正的用户输入 ----------
            if role == "user":
                thinking, answer = _extract_blocks(content)
                # user 消息一般没有 thinking，但兼容处理
                self.display_messages.append({
                    "role": "user",
                    "content": answer or (thinking and ""),
                    "thinking": "",
                    "ts": time.time()
                })
                if first_user_text is None:
                    first_user_text = answer
                    self.first_user_ts = time.time()
                continue

        if first_user_text and self.title.startswith("历史会话"):
            self.title = first_user_text[:30]

    async def save_assistant_message(self, text: str, thinking: str = ""):
        if self.pi_session:
            blocks = []
            if thinking:
                blocks.append(TextContent(text=thinking, textSignature="reasoning_content"))
            if text:
                blocks.append(TextContent(text=text))
            self.pi_session.append_message(AssistantMessage(content=blocks))


# ==================== FastAPI ====================
app = FastAPI()

@app.on_event("startup")
async def startup_event():
    await load_all_sessions()


SESSIONS: Dict[str, WebSession] = {}

async def load_all_sessions():
    sessions_dir = Path(".pi/sessions")
    if sessions_dir.exists():
        for file_path in sessions_dir.glob("*.jsonl"):
            session_id = file_path.stem
            if session_id not in SESSIONS:
                s = WebSession(session_id, "历史会话")
                await s.load_session()
                SESSIONS[session_id] = s


async def create_agent():
    return Agent(AgentOptions(
        initial_state={
            "system_prompt": SYSTEM_PROMPT,
            "model": model,
            "tools": [GetCityWeatherTool(), WebSearchTavilyTool()] + PI_BUILTIN_TOOLS,
            "thinking_level": None,
        },
        get_api_key=lambda p: API_KEY,
    ))


async def compact_messages(messages):
    before_tokens = estimate_context_tokens(messages)
    if before_tokens == 0:
        return messages, before_tokens, before_tokens
    cut_idx = find_cut_point(messages, compaction_settings.keep_recent_tokens)
    if cut_idx <= 1:
        return messages, before_tokens, before_tokens
    to_summarize = messages[:cut_idx]
    kept = messages[cut_idx:]
    summary_text = await generate_summary(model, to_summarize, api_key=API_KEY)
    summary_msg = UserMessage(
        role="user",
        content=[TextContent(text=f"[Previous conversation summary]\n\n{summary_text}")],
        timestamp=int(time.time() * 1e9),
    )
    new_messages = [summary_msg] + kept
    after_tokens = estimate_context_tokens(new_messages)
    return new_messages, before_tokens, after_tokens


class CreateSessionReq(BaseModel):
    title: Optional[str] = "新对话"


@app.get("/")
async def index():
    return HTMLResponse(open("index.html", "r", encoding="utf-8").read())


def get_first_user_preview(messages: list[dict]) -> str:
    for m in messages:
        if m["role"] == "user":
            text = m.get("content", "")
            return text[:25] + "..." if len(text) > 25 else text
    return "新对话"


@app.get("/api/sessions")
async def list_sessions():
    items = sorted(SESSIONS.values(), key=lambda s: s.updated_at, reverse=True)
    return JSONResponse([
        {
            "id": s.id,
            "title": s.title,
            "preview": get_first_user_preview(s.display_messages),
            "created_at": s.first_user_ts or s.created_at,
            "updated_at": s.updated_at,
            "message_count": len(s.display_messages),
        }
        for s in items
    ])


@app.post("/api/sessions")
async def create_session(req: CreateSessionReq):
    sid = uuid.uuid4().hex
    s = WebSession(sid, req.title or "新对话")
    SESSIONS[sid] = s
    return JSONResponse({"id": sid, "title": s.title})


@app.delete("/api/sessions/{sid}")
async def delete_session(sid: str):
    session = SESSIONS.get(sid)
    if session and session.storage_path.exists():
        session.storage_path.unlink()
    SESSIONS.pop(sid, None)
    return JSONResponse({"ok": True})


@app.get("/api/sessions/{sid}/messages")
async def get_messages(sid: str):
    s = SESSIONS.get(sid)
    if not s:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"messages": s.display_messages, "title": s.title})


@app.websocket("/ws/{sid}")
async def ws_endpoint(ws: WebSocket, sid: str):
    await ws.accept()
    session = SESSIONS.get(sid)
    if not session:
        await ws.send_text(json.dumps({"type": "error", "message": "会话不存在"}))
        await ws.close()
        return

    agent = await session.ensure_agent()

    out_queue: asyncio.Queue = asyncio.Queue()
    session._out_queue = out_queue

    async def sender():
        while True:
            item = await out_queue.get()
            try:
                await ws.send_text(json.dumps(item, ensure_ascii=False))
            except Exception:
                break

    sender_task = asyncio.create_task(sender())

    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            if data.get("type") != "user_message":
                continue
            text = (data.get("text") or "").strip()
            if not text:
                continue

            if session.title in ("新对话", "历史会话"):
                session.title = text[:30]

            session.updated_at = time.time()
            if not session.first_user_ts:
                session.first_user_ts = time.time()
            session.display_messages.append({"role": "user", "content": text, "ts": time.time()})
            if session.pi_session:
                session.pi_session.append_message(UserMessage(content=[TextContent(text=text)]))
            await ws.send_text(json.dumps({
                "type": "user_saved",
                "title": session.title,
            }, ensure_ascii=False))

            session.busy = True
            print(f"[DEBUG] Starting agent.prompt for: {text[:50]!r}")
            try:
                await agent.prompt(text)
                print(f"[DEBUG] agent.prompt completed")
            except Exception as e:
                print(f"[ERROR] agent.prompt raised: {e}")
                await ws.send_text(json.dumps({
                    "type": "error", "message": str(e)
                }, ensure_ascii=False))
            finally:
                session.busy = False

                try:
                    msgs = agent.state.messages
                    tokens = estimate_context_tokens(msgs)
                    if should_compact(tokens, model.context_window, compaction_settings):
                        new_msgs, before_t, after_t = await compact_messages(msgs)
                        agent.state.messages = new_msgs
                        await ws.send_text(json.dumps({
                            "type": "compact",
                            "before": before_t,
                            "after": after_t,
                        }, ensure_ascii=False))
                except Exception as ce:
                    await ws.send_text(json.dumps({
                        "type": "warn", "message": f"压缩失败: {ce}"
                    }, ensure_ascii=False))

                await ws.send_text(json.dumps({"type": "done"}, ensure_ascii=False))
    except WebSocketDisconnect:
        pass
    finally:
        sender_task.cancel()


def serialize_event(ev) -> Optional[dict]:
    t = getattr(ev, "type", None)

    if t == "message_update":
        aev = getattr(ev, "assistant_message_event", None)
        if aev:
            at = getattr(aev, "type", None)
            if at == "text_delta":
                return {"type": "text_delta", "delta": aev.delta}
            if at == "thinking_start":
                return {"type": "thinking_start"}
            if at == "thinking_delta":
                return {"type": "thinking_delta", "delta": aev.delta}
            if at == "thinking_end":
                return {"type": "thinking_end"}
        return None

    if t == "message_end":
        msg = getattr(ev, "message", None)
        if not msg:
            return {"type": "message_end", "role": "", "content": ""}
        err = getattr(msg, "error_message", None)
        if err:
            return {"type": "error", "message": err}
        role = getattr(msg, "role", "")
        return {"type": "message_end", "role": role}

    if t == "tool_execution_start":
        return {
            "type": "tool_start",
            "tool_name": getattr(ev, "tool_name", ""),
            "args": safe_json(getattr(ev, "args", None)),
        }

    if t == "tool_execution_end":
        result_text = ""
        result = getattr(ev, "result", None)
        if result and getattr(result, "content", None):
            for block in result.content:
                if getattr(block, "type", None) == "text":
                    result_text += block.text
        return {
            "type": "tool_end",
            "tool_name": getattr(ev, "tool_name", ""),
            "result": result_text,
        }

    return None


def safe_json(v):
    try:
        json.dumps(v)
        return v
    except Exception:
        return str(v)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)