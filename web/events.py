"""Agent 事件 → WebSocket JSON 协议。

前端只依赖这里的字段；修改协议时请同步 ``web/static/app.js`` 的
``handleEvent``。协议概览：

    {"type": "user",         "text", "ts", "title"}
    {"type": "busy",         "value": true|false}
    {"type": "message_start","role"}
    {"type": "message_end",  "role", "stop_reason", "usage", "error"}
    {"type": "text_delta",   "delta"}
    {"type": "thinking_start" | "thinking_delta" | "thinking_end"}
    {"type": "tool_start",   "tool_call_id", "tool_name", "args"}
    {"type": "tool_update",  "tool_call_id", "partial"}
    {"type": "tool_end",     "tool_call_id", "tool_name", "result",
                             "details", "is_error", "duration_ms"}
    {"type": "compact",      "before", "after"}
    {"type": "warn" | "error", "message"}
    {"type": "done"}
    {"type": "deleted",      "id"}
"""

from __future__ import annotations

import json
from typing import Any, Optional


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    """同时支持属性访问与 dict 访问。"""
    if obj is None:
        return default
    for name in names:
        if isinstance(obj, dict):
            value = obj.get(name)
        else:
            value = getattr(obj, name, None)
        if value is not None:
            return value
    return default


def safe_json(value: Any) -> Any:
    """尽量保证值可 JSON 序列化。"""
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def result_text(result: Any) -> str:
    """AgentToolResult.content → 纯文本。"""
    if result is None:
        return ""
    content = _get(result, "content", default=None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            text = _get(block, "text", default=None)
            if isinstance(text, str):
                parts.append(text)
        return "\n".join(parts)
    return str(content)


def _usage(msg: Any) -> dict:
    usage = _get(msg, "usage", default=None)
    if usage is None:
        return {}
    return {
        "input": _get(usage, "input", default=0) or 0,
        "output": _get(usage, "output", default=0) or 0,
        "total": _get(usage, "total_tokens", "totalTokens", default=0) or 0,
    }


def serialize_event(ev: Any) -> Optional[dict]:
    """把 pi 的 AgentEvent 转成前端可消费的 payload；无关事件返回 None。"""
    etype = _get(ev, "type", default=None)

    if etype == "message_start":
        msg = _get(ev, "message", default=None)
        return {"type": "message_start", "role": _get(msg, "role", default="")}

    if etype == "message_update":
        aev = _get(ev, "assistant_message_event", default=None)
        if aev is None:
            return None
        atype = _get(aev, "type", default=None)
        if atype == "text_delta":
            return {"type": "text_delta", "delta": _get(aev, "delta", default="")}
        if atype == "thinking_start":
            return {"type": "thinking_start"}
        if atype == "thinking_delta":
            return {"type": "thinking_delta", "delta": _get(aev, "delta", default="")}
        if atype == "thinking_end":
            return {"type": "thinking_end"}
        return None

    if etype == "message_end":
        msg = _get(ev, "message", default=None)
        if msg is None:
            return {"type": "message_end", "role": ""}
        return {
            "type": "message_end",
            "role": _get(msg, "role", default=""),
            "stop_reason": _get(msg, "stop_reason", "stopReason", default=""),
            "usage": _usage(msg),
            "error": _get(msg, "error_message", "errorMessage", default=None),
        }

    if etype == "tool_execution_start":
        return {
            "type": "tool_start",
            "tool_call_id": _get(ev, "tool_call_id", default=""),
            "tool_name": _get(ev, "tool_name", default=""),
            "args": safe_json(_get(ev, "args", default={})),
        }

    if etype == "tool_execution_update":
        return {
            "type": "tool_update",
            "tool_call_id": _get(ev, "tool_call_id", default=""),
            "partial": result_text(_get(ev, "partial_result", default=None)),
        }

    if etype == "tool_execution_end":
        result = _get(ev, "result", default=None)
        return {
            "type": "tool_end",
            "tool_call_id": _get(ev, "tool_call_id", default=""),
            "tool_name": _get(ev, "tool_name", default=""),
            "result": result_text(result),
            "details": safe_json(_get(result, "details", default=None)),
            "is_error": bool(_get(ev, "is_error", default=False)),
        }

    if etype == "agent_end":
        return {"type": "agent_end"}

    return None
