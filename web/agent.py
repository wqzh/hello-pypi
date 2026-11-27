"""Agent 工厂与上下文压缩。"""

from __future__ import annotations

import time
from typing import Any

from pi.pi_agent_core import Agent, AgentOptions
from pi.pi_agent_core.harness.compaction import (
    estimate_context_tokens,
    find_cut_point,
    generate_summary,
    should_compact,
)
from pi.pi_ai import TextContent, UserMessage

from .config import (
    API_KEY,
    SYSTEM_PROMPT,
    THINKING_LEVEL,
    TOOLS,
    compaction_settings,
    model,
)


def create_agent() -> Agent:
    return Agent(
        AgentOptions(
            initial_state={
                "system_prompt": SYSTEM_PROMPT,
                "model": model,
                "tools": TOOLS,
                "thinking_level": THINKING_LEVEL,
            },
            get_api_key=lambda _provider: API_KEY,
        )
    )


async def maybe_compact(session: Any) -> None:
    """一轮结束后检查并压缩上下文；结果同时落盘为 compaction 条目。"""
    agent = session.agent
    if agent is None:
        return

    messages = agent.state.messages or []
    before = estimate_context_tokens(messages)
    if not should_compact(before, model.context_window, compaction_settings):
        return

    cut = find_cut_point(messages, compaction_settings.keep_recent_tokens)
    if cut <= 1:
        return

    summary = await generate_summary(model, messages[:cut], api_key=API_KEY)
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