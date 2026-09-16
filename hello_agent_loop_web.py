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


# ==================== 会话管理 ====================
class WebSession:
    def __init__(self, session_id: str, title: str = "新对话"):
        self.id = session_id
        self.title = title
        self.created_at = time.time()
        self.updated_at = time.time()
        # 前端展示用的消息历史（user / assistant / tool）
        self.display_messages: list[dict] = []
        self.agent: Optional[Agent] = None
        self.busy = False   # 是否正在处理中
        self.pi_session: Optional[Session] = None  # pi 会话对象
        self.storage_path = Path(".pi/sessions") / f"{session_id}.jsonl"

    async def ensure_agent(self):
        if self.agent is None:
            self.agent = await create_agent()
            # 加载历史会话
            await self.load_session()
        return self.agent

    async def load_session(self):
        """加载历史会话数据"""
        if self.storage_path.exists():
            storage = JsonlSessionStorage(self.storage_path)
            self.pi_session = Session(storage)
            # 构建显示消息
            self.display_messages = []
            for entry in self.pi_session.get_entries():
                if entry.type == "message":
                    msg = entry.data
                    if isinstance(msg, (UserMessage, AssistantMessage, ToolResultMessage)):
                        role = msg.role
                        content = ""
                        if hasattr(msg, 'content') and msg.content:
                            if isinstance(msg.content, list):
                                for item in msg.content:
                                    if hasattr(item, 'text'):
                                        content += item.text
                            else:
                                content = str(msg.content)
                        self.display_messages.append({
                            "role": role,
                            "content": content,
                            "ts": time.time()
                        })
                elif entry.type == "thinking_level_change":
                    # 可以在这里处理思考级别变化
                    pass
                elif entry.type == "compaction":
                    # 可以在这里处理压缩点
                    pass
        else:
            # 创建新的会话存储
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            storage = JsonlSessionStorage(self.storage_path)
            self.pi_session = Session(storage)

    async def save_message(self, role: str, content: str):
        """保存消息到会话存储"""
        if self.pi_session:
            if role == "user":
                msg = UserMessage(content=[TextContent(text=content)])
            elif role == "assistant":
                msg = AssistantMessage(content=[TextContent(text=content)])
            else:
                msg = ToolResultMessage(content=[TextContent(text=content)])
            self.pi_session.append_message(msg)

    async def save_thinking(self, thinking: str):
        """保存思考过程"""
        if self.pi_session:
            # 这里简化处理，将思考作为助手消息保存
            self.pi_session.append_message(AssistantMessage(content=[TextContent(text=f"[思考] {thinking}")]))

    async def save_tool_call(self, tool_name: str, args: dict, result: str):
        """保存工具调用结果"""
        if self.pi_session:
            self.pi_session.append_message(ToolResultMessage(
                content=[TextContent(text=result)],
                tool_name=tool_name,
                tool_call_id="tool-call-1"
            ))

    async def compact_session(self, summary: str, retained_tail: list):
        """保存压缩点"""
        if self.pi_session:
            self.pi_session.append_compaction(summary, retained_tail)


# ==================== FastAPI ====================
app = FastAPI()

# 启动时加载所有会话
@app.on_event("startup")
async def startup_event():
    await load_all_sessions()


SESSIONS: Dict[str, WebSession] = {}

async def load_all_sessions():
    """加载所有历史会话"""
    sessions_dir = Path(".pi/sessions")
    if sessions_dir.exists():
        for file_path in sessions_dir.glob("*.jsonl"):
            session_id = file_path.stem
            if session_id not in SESSIONS:
                s = WebSession(session_id, f"历史会话 {session_id[:8]}...")
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


# ==================== FastAPI ====================
app = FastAPI()

# 启动时加载所有会话
@app.on_event("startup")
async def startup_event():
    await load_all_sessions()


class CreateSessionReq(BaseModel):
    title: Optional[str] = "新对话"


@app.get("/")
async def index():
    return HTMLResponse(open("index.html", "r", encoding="utf-8").read())


@app.get("/api/sessions")
async def list_sessions():
    items = sorted(SESSIONS.values(), key=lambda s: s.updated_at, reverse=True)
    return JSONResponse([
        {
            "id": s.id,
            "title": s.title,
            "created_at": s.created_at,
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
    # 删除会话文件
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

    # 用队列把事件从 agent 回调异步推给前端
    out_queue: asyncio.Queue = asyncio.Queue()

    def on_event(ev, sig):
        # 把同步回调转成异步推送到队列
        payload = serialize_event(ev)
        if payload is not None:
            try:
                out_queue.put_nowait(payload)
                # 同时保存事件到会话存储
                session = SESSIONS.get(sid)
                if session and session.pi_session:
                    if payload.get("type") == "thinking_start":
                        session.pi_session.append_thinking_level_change("thinking")
                    elif payload.get("type") == "thinking_delta":
                        # 可以在这里保存思考过程中的内容
                        pass
                    elif payload.get("type") == "thinking_end":
                        session.pi_session.append_thinking_level_change(None)
                    elif payload.get("type") == "tool_start":
                        session.pi_session.append_message(ToolResultMessage(
                            content=[TextContent(text=f"[工具调用开始] {payload.get('tool_name', '')}")],
                            tool_name=payload.get('tool_name', ''),
                            tool_call_id="tool-call-1"
                        ))
                    elif payload.get("type") == "tool_end":
                        session.pi_session.append_message(ToolResultMessage(
                            content=[TextContent(text=f"[工具调用结束] {payload.get('result', '')}")],
                            tool_name=payload.get('tool_name', ''),
                            tool_call_id="tool-call-1"
                        ))
                    elif payload.get("type") == "message_end":
                        # 保存助手消息
                        session.pi_session.append_message(AssistantMessage(
                            content=[TextContent(text=payload.get('content', ''))]
                        ))
                    elif payload.get("type") == "text_delta":
                        # 可以在这里保存文本增量
                        pass
            except Exception:
                pass

    agent.subscribe(on_event)

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

            # 如果会话还没有标题，用首条消息做标题
            if session.title == "新对话":
                session.title = text[:30]

            session.updated_at = time.time()
            session.display_messages.append({"role": "user", "content": text, "ts": time.time()})
            await ws.send_text(json.dumps({
                "type": "user_saved",
                "title": session.title,
            }, ensure_ascii=False))

            # 保存用户消息到会话存储
            await session.save_message("user", text)

            # 重置显示消息列表，从会话存储重新加载
            session.display_messages = []
            if session.pi_session:
                for entry in session.pi_session.get_entries():
                    if entry.type == "message":
                        msg = entry.data
                        if isinstance(msg, (UserMessage, AssistantMessage, ToolResultMessage)):
                            role = msg.role
                            content = ""
                            if hasattr(msg, 'content') and msg.content:
                                if isinstance(msg.content, list):
                                    for item in msg.content:
                                        if hasattr(item, 'text'):
                                            content += item.text
                                else:
                                    content = str(msg.content)
                            session.display_messages.append({
                                "role": role,
                                "content": content,
                                "ts": time.time()
                            })

            session.busy = True
            try:
                # 保存用户消息
                await session.save_message("user", text)
                
                await agent.prompt(text)
            except Exception as e:
                await ws.send_text(json.dumps({
                    "type": "error", "message": str(e)
                }, ensure_ascii=False))
            finally:
                session.busy = False

                # 压缩检查
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
                        # 保存压缩点
                        await session.compact_session(
                            f"上下文压缩，保留最后 {len(new_msgs)} 条消息",
                            new_msgs
                        )
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
    """把 pi 的 event 转成前端可消费的 JSON。"""
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
        err = getattr(msg, "error_message", None) if msg else None
        if err:
            return {"type": "error", "message": err}
        return {"type": "message_end"}

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

    if t == "thinking_start":
        return {"type": "thinking_start"}

    if t == "thinking_delta":
        return {"type": "thinking_delta", "delta": getattr(ev, "delta", "")}

    if t == "thinking_end":
        return {"type": "thinking_end"}

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