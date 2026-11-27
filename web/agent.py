"""Agent 工厂与上下文压缩。

模型可切换：每个会话持有一个 ``ModelHolder``，切换时只改
``agent.state.model`` 与 holder 里的 api_key，下一次 ``prompt`` 即生效
（``Agent._create_loop_config`` 在每轮开始时读取 ``state.model``）。
"""

from __future__ import annotations

import time
from typing import Any, Optional

from pi.pi_agent_core import Agent, AgentOptions
from pi.pi_agent_core.harness.compaction import (
    estimate_context_tokens,
    find_cut_point,
    generate_summary,
    should_compact,
)
from pi.pi_ai import TextContent, UserMessage

from .config import (
    DEFAULT_MODEL,
    SYSTEM_PROMPT,
    THINKING_LEVEL,
    TOOLS,
    compaction_settings,
    get_model,
)


class ModelHolder:
    """可变的「当前模型」引用，供 agent 的 get_api_key 回调读取。"""

    def __init__(self, entry: Optional[dict] = None) -> None:
        self.entry = entry or DEFAULT_MODEL

    @property
    def api_key(self) -> str:
        return self.entry.get("api_key", "")

    @property
    def label(self) -> str:
        return self.entry.get("label", "")

    def switch(self, key: str) -> dict:
        self.entry = get_model(key)
        return self.entry


def create_agent(holder: Optional[ModelHolder] = None) -> Agent:
    holder = holder or ModelHolder()

    def _get_api_key(_provider: str) -> str:
        # 按会话当前模型取 key：不同供应商 / 不同 key 都能正确切换
        return holder.api_key

    return Agent(
        AgentOptions(
            initial_state={
                "system_prompt": SYSTEM_PROMPT,
                "model": holder.entry["model"],
                "tools": TOOLS,
                "thinking_level": THINKING_LEVEL,
            },
            get_api_key=_get_api_key,
        )
    )


async def maybe_compact(session: Any) -> None:
    """一轮结束后检查并压缩上下文；结果同时落盘为 compaction 条目。"""
    agent = session.agent
    if agent is None:
        return

    current_model = getattr(agent.state, "model", None)
    context_window = getattr(current_model, "context_window", 0) or 54000
    api_key = session.model_holder.api_key

    messages = agent.state.messages or []
    before = estimate_context_tokens(messages)
    if not should_compact(before, context_window, compaction_settings):
        return

    cut = find_cut_point(messages, compaction_settings.keep_recent_tokens)
    if cut <= 1:
        return

    summary = await generate_summary(
        current_model, messages[:cut], api_key=api_key
    )
    if not summary.strip():
        return

    summary_message = UserMessage(
        content=[TextContent(text=f"[Previous conversation summary]\n\n{summary}")],
        timestamp=int(time.time() * 1e9),
    )
    retained = messages[cut:]
    new_messages = [summary_message] + retained

    agent.state.messages = new_messages
    after = estimate_context_tokens(new_messages)
    session.apply_compaction(summary, retained, before, after)
    session.broadcast({"type": "compact", "before": before, "after": after})