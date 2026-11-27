"""会话状态：展示消息、Agent 生命周期、事件分发与持久化。

设计要点
--------
* **一份事实来源**：``pi_session``（JSONL）保存完整 transcript（user /
  assistant / toolResult），重启后据此重建 Agent 上下文与展示消息。
* **展示与上下文分离**：``display_messages`` 只服务 UI；Agent 上下文走
  ``build_context()``，遇到 compaction 条目会重置为「摘要 + 保留尾部」。
* **广播**：一个会话可被多个 WebSocket 同时订阅，事件广播给所有连接。
* **工具配对**：统一用 ``tool_call_id``，不再按工具名匹配。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Optional

from pi.pi_agent_core.harness.session import JsonlSessionStorage, Session
from pi.pi_ai import (
    AssistantMessage,
    TextContent,
    ToolResultMessage,
    UserMessage,
)

from .config import DEFAULT_MODEL_KEY, SESSIONS_DIR
from .events import serialize_event, usage_dict

log = logging.getLogger("pi.web.sessions")


# ---------------------------------------------------------------- 工具函数


def _f(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _ts_ms(value: Any) -> float:
    """会话条目时间戳（ISO 字符串 / 数字）→ 毫秒。"""
    if isinstance(value, (int, float)) and value > 0:
        return float(value) * 1000 if value < 1e12 else float(value)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value).timestamp() * 1000
        except Exception:
            pass
    return time.time() * 1000


def _message_ts(msg: Any, fallback: float) -> float:
    """消息自身的时间戳（Unix 毫秒）优先，退回条目时间。

    消息是写完整个回合才批量落盘的，条目的 ``timestamp`` 因此全部挤在
    同一个时刻；真正能区分「提问」与「回答」的是消息自带的 ``timestamp``。
    """
    raw = _f(msg, "timestamp", 0)
    try:
        value = float(raw or 0)
    except (TypeError, ValueError):
        return fallback
    # 只接受合理的 Unix 毫秒（2001 年以后、2100 年以前）
    if raw and 1e12 <= value < 4.1e12:
        return value
    return fallback


def _parse_assistant(content: Any) -> tuple[str, str, list[dict]]:
    """assistant content → (thinking, text, tool_calls)。"""
    if isinstance(content, str):
        return "", content, []

    thinking: list[str] = []
    text: list[str] = []
    calls: list[dict] = []

    for block in content or []:
        btype = _f(block, "type", "")
        if btype == "thinking":
            thinking.append(_f(block, "thinking", "") or "")
        elif btype == "text":
            text.append(_f(block, "text", "") or "")
        elif btype in ("toolCall", "tool_call", "tool_use", "function_call"):
            calls.append(
                {
                    "id": _f(block, "id", "") or _f(block, "tool_call_id", ""),
                    "name": _f(block, "name", "") or _f(block, "tool_name", ""),
                    "arguments": _f(block, "arguments", None)
                    or _f(block, "args", None)
                    or {},
                }
            )
    return "".join(thinking), "".join(text), calls


def _parse_text_blocks(content: Any) -> str:
    """user / toolResult content → 纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            if _f(block, "type", "") == "text":
                parts.append(_f(block, "text", "") or "")
        return "".join(parts)
    return "" if content is None else str(content)


def _deserialize(raw: Any):
    """compaction.retained_tail 里存的是 dict，还原成消息对象。"""
    if isinstance(raw, (UserMessage, AssistantMessage, ToolResultMessage)):
        return raw
    if not isinstance(raw, dict):
        return None
    role = raw.get("role")
    try:
        if role == "user":
            return UserMessage.model_validate(raw)
        if role == "assistant":
            return AssistantMessage.model_validate(raw)
        if role == "toolResult":
            return ToolResultMessage.model_validate(raw)
    except Exception:
        return None
    return None


# ---------------------------------------------------------------- WebSession


class WebSession:
    def __init__(self, session_id: str, title: str = "新对话") -> None:
        self.id = session_id
        self.title = title
        self.created_at = time.time()
        self.updated_at = time.time()
        self.first_user_ts: float = 0.0

        # 当前模型（按会话保存，切换后下一轮生效）
        from .agent import ModelHolder

        self.model_holder = ModelHolder()
        self.model_key: str = DEFAULT_MODEL_KEY

        self.display_messages: list[dict] = []
        self.agent: Optional[Any] = None
        self.pi_session: Optional[Session] = None

        self.storage_path = SESSIONS_DIR / f"{session_id}.jsonl"
        self.listeners: set[asyncio.Queue] = set()
        self.run_lock = asyncio.Lock()
        self.run_task: Optional[asyncio.Task] = None
        self.deleted = False

        self._persisted_count = 0
        self._subscribed = False

        # 流式缓冲（用于在 message_end 时生成一条展示消息）
        self._pending_text = ""
        self._pending_thinking = ""
        self._thinking_active = False

        # 工具卡：tool_call_id → display_messages 下标 / 开始时间
        self._tool_index: dict[str, int] = {}
        self._tool_started: dict[str, float] = {}

    # ------------------------------------------------------------ 生命周期

    async def ensure_agent(self):
        if self.agent is None:
            from .agent import create_agent

            self.agent = create_agent(self.model_holder)
            if self.pi_session is None:
                self.load_session()
            # 新会话此时才建好存储：把已选的模型落盘
            self._persist_model_key()
            self.agent.state.messages = self.build_context()
            self._persisted_count = len(self.agent.state.messages)
            if not self._subscribed:
                self.agent.subscribe(self.on_event)
                self._subscribed = True
        return self.agent

    # ------------------------------------------------------------ 模型切换

    def switch_model(self, key: str) -> dict:
        """切换本会话使用的模型；运行中不允许切换。"""
        entry = self.model_holder.switch(key)
        self.model_key = entry["key"]
        if self.agent is not None:
            self.agent.state.model = entry["model"]
        self._persist_model_key()
        return entry

    def _persist_model_key(self) -> None:
        """把模型键写进会话元数据（JSONL 首行），重启后可恢复。"""
        storage = getattr(self.pi_session, "storage", None)
        if storage is None:
            return
        try:
            metadata = getattr(storage, "_metadata", None)
            if not isinstance(metadata, dict):
                return
            metadata["model_key"] = self.model_key
            save = getattr(storage, "_save", None)
            if callable(save):
                save()
        except Exception as exc:  # noqa: BLE001
            log.debug("写入模型选择失败: %s", exc)

    def dispose(self) -> None:
        """删除会话前调用：停止生成、断开订阅。"""
        self.deleted = True
        self.listeners.clear()
        if self.agent is not None:
            try:
                self.agent.abort()
            except Exception:
                pass
        self.agent = None
        self.pi_session = None

    # ------------------------------------------------------------ 广播

    def broadcast(self, payload: dict) -> None:
        for queue in list(self.listeners):
            try:
                queue.put_nowait(payload)
            except Exception:
                self.listeners.discard(queue)

    # ------------------------------------------------------------ 事件

    def on_event(self, ev: Any, _signal: Any = None) -> None:
        payload = serialize_event(ev)
        if payload is None:
            return

        ptype = payload["type"]
        if ptype in ("tool_start", "tool_end"):
            call_id = payload.get("tool_call_id") or payload.get("tool_name") or ""
            payload["tool_call_id"] = call_id
            if ptype == "tool_start":
                self._tool_started[call_id] = time.time()
            else:
                started = self._tool_started.pop(call_id, None)
                if started:
                    payload["duration_ms"] = int((time.time() - started) * 1000)

        self.broadcast(payload)
        self._apply_display(payload)

    def _apply_display(self, payload: dict) -> None:
        """维护 display_messages（刷新/重连时回显）。"""
        ptype = payload["type"]
        now = time.time() * 1000

        if ptype == "message_start":
            if payload.get("role") == "assistant":
                self._pending_text = ""
                self._pending_thinking = ""
            return

        if ptype == "thinking_start":
            self._thinking_active = True
            self._pending_thinking = ""
            return

        if ptype == "thinking_delta":
            if self._thinking_active:
                self._pending_thinking += payload.get("delta", "")
            return

        if ptype == "thinking_end":
            self._thinking_active = False
            return

        if ptype == "text_delta":
            self._pending_text += payload.get("delta", "")
            return

        if ptype == "tool_start":
            call_id = payload["tool_call_id"]
            self.display_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "tool_name": payload.get("tool_name", ""),
                    "args": payload.get("args", {}),
                    "result": "",
                    "status": "running",
                    "duration_ms": 0,
                    "ts": now,
                }
            )
            self._tool_index[call_id] = len(self.display_messages) - 1
            return

        if ptype == "tool_update":
            idx = self._tool_index.get(payload.get("tool_call_id", ""))
            if idx is not None:
                self.display_messages[idx]["result"] = payload.get("partial", "")
            return

        if ptype == "tool_end":
            idx = self._tool_index.pop(payload.get("tool_call_id", ""), None)
            if idx is not None:
                entry = self.display_messages[idx]
                entry["result"] = payload.get("result", "")
                entry["status"] = "error" if payload.get("is_error") else "done"
                entry["duration_ms"] = payload.get("duration_ms", 0)
            return

        if ptype == "message_end":
            if payload.get("error"):
                self._pending_text = ""
                self._pending_thinking = ""
                return
            if payload.get("role") != "assistant":
                return
            if self._pending_text.strip() or self._pending_thinking.strip():
                self.display_messages.append(
                    {
                        "role": "assistant",
                        "text": self._pending_text,
                        "thinking": self._pending_thinking,
                        "usage": payload.get("usage") or {},
                        "model": payload.get("model") or "",
                        "ts": now,
                    }
                )
            self._pending_text = ""
            self._pending_thinking = ""
            return

        if ptype == "error":
            self._pending_text = ""
            self._pending_thinking = ""
            self._thinking_active = False
            return

    # ------------------------------------------------------------ 持久化

    def load_session(self) -> None:
        """从 JSONL 重建展示消息；Agent 上下文由 build_context 单独重建。"""
        self.display_messages = []
        self._tool_index = {}
        self._persisted_count = 0
        self._pending_text = ""
        self._pending_thinking = ""
        self._thinking_active = False

        if not self.storage_path.exists():
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            self.pi_session = Session(JsonlSessionStorage(self.storage_path))
            return

        self.pi_session = Session(JsonlSessionStorage(self.storage_path))
        self.updated_at = self.storage_path.stat().st_mtime
        self.created_at = self.updated_at

        # 恢复上次选择的模型
        try:
            metadata = self.pi_session.storage.get_metadata() or {}
            saved_key = metadata.get("model_key")
            if saved_key:
                self.switch_model(str(saved_key))
        except Exception as exc:  # noqa: BLE001
            log.debug("恢复模型选择失败: %s", exc)

        try:
            entries = list(self.pi_session.get_entries())
        except Exception as exc:  # noqa: BLE001
            log.warning("读取会话失败 %s: %s", self.id, exc)
            entries = []

        if entries:
            first_msg = _f(entries[0], "data", None)
            first_ts = _message_ts(
                first_msg, _ts_ms(_f(entries[0], "timestamp", ""))
            ) / 1000
            if first_ts > 0:
                self.created_at = first_ts

        for entry in entries:
            etype = _f(entry, "type", "")
            ts = _ts_ms(_f(entry, "timestamp", ""))

            if etype == "compaction":
                data = _f(entry, "data", {}) or {}
                summary = data.get("summary", "") if isinstance(data, dict) else ""
                if summary:
                    self.display_messages.append(
                        {
                            "role": "system",
                            "text": "上下文已压缩，早期对话折叠为摘要。",
                            "ts": ts,
                        }
                    )
                continue

            if etype != "message":
                continue

            msg = _f(entry, "data", None)
            if msg is None:
                continue
            role = _f(msg, "role", "")
            content = _f(msg, "content", None)
            # 消息自带的时间比批量落盘时的条目时间可靠得多
            ts = _message_ts(msg, ts)

            if role == "user":
                text = _parse_text_blocks(content)
                if text.strip():
                    self.display_messages.append(
                        {"role": "user", "text": text, "ts": ts}
                    )
                    if not self.first_user_ts:
                        self.first_user_ts = ts / 1000

            elif role == "assistant":
                thinking, text, calls = _parse_assistant(content)
                if text.strip() or thinking.strip():
                    self.display_messages.append(
                        {
                            "role": "assistant",
                            "text": text,
                            "thinking": thinking,
                            "usage": usage_dict(msg),
                            "model": _f(msg, "model", "") or "",
                            "ts": ts,
                        }
                    )
                for call in calls:
                    call_id = call["id"] or call["name"]
                    self.display_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "tool_name": call["name"],
                            "args": call["arguments"],
                            "result": "",
                            "status": "running",
                            "duration_ms": 0,
                            "ts": ts,
                        }
                    )
                    self._tool_index[call_id] = len(self.display_messages) - 1

            elif role == "toolResult":
                call_id = _f(msg, "tool_call_id", "")
                text = _parse_text_blocks(content)
                is_error = bool(_f(msg, "is_error", False))
                idx = self._tool_index.pop(call_id, None)
                if idx is not None:
                    self.display_messages[idx]["result"] = text
                    self.display_messages[idx]["status"] = (
                        "error" if is_error else "done"
                    )
                else:
                    self.display_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "tool_name": _f(msg, "tool_name", ""),
                            "args": {},
                            "result": text,
                            "status": "error" if is_error else "done",
                            "duration_ms": 0,
                            "ts": ts,
                        }
                    )

        # 标题：优先用持久化的 label，退化到首条用户消息
        try:
            label = self.pi_session.get_label()
        except Exception:
            label = None
        if label:
            self.title = label
        else:
            first_user = next(
                (m["text"] for m in self.display_messages if m["role"] == "user"),
                "",
            )
            if first_user:
                self.title = first_user.strip().replace("\n", " ")[:30]

    def build_context(self) -> list:
        """从会话条目重建 Agent 上下文；compaction 条目会重置历史。"""
        if self.pi_session is None:
            return []
        try:
            entries = list(self.pi_session.get_entries())
        except Exception:
            return []

        messages: list = []
        for entry in entries:
            etype = _f(entry, "type", "")
            if etype == "compaction":
                data = _f(entry, "data", {}) or {}
                summary = data.get("summary", "") if isinstance(data, dict) else ""
                messages = []
                if summary:
                    messages.append(
                        UserMessage(
                            content=[
                                TextContent(
                                    text=f"[Previous conversation summary]\n\n{summary}"
                                )
                            ]
                        )
                    )
                for raw in data.get("retained_tail") or []:
                    msg = _deserialize(raw)
                    if msg is not None:
                        messages.append(msg)
            elif etype == "message":
                msg = _f(entry, "data", None)
                if isinstance(msg, (UserMessage, AssistantMessage, ToolResultMessage)):
                    messages.append(msg)
        return messages

    def sync_transcript(self) -> None:
        """把 Agent 新增的消息写回 JSONL（避免重复追加）。"""
        if self.deleted or self.agent is None or self.pi_session is None:
            return
        messages = self.agent.state.messages or []
        if len(messages) < self._persisted_count:
            return
        for message in messages[self._persisted_count :]:
            self.pi_session.append_message(message)
        self._persisted_count = len(messages)

    def apply_compaction(
        self,
        summary: str,
        retained_tail: list,
        before: int,
        after: int,
    ) -> None:
        """写入 compaction 条目并展示分隔线。"""
        if self.pi_session is None:
            return
        self.pi_session.append_compaction(summary, list(retained_tail))
        if self.agent is not None:
            self._persisted_count = len(self.agent.state.messages or [])
        self.display_messages.append(
            {
                "role": "system",
                "text": f"上下文已压缩：{before} → {after} tokens",
                "ts": time.time() * 1000,
            }
        )
