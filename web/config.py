"""Web 层公共配置：路径、模型注册表、工具、系统提示词。

所有 Web 相关代码统一放在 ``web/`` 下：
    web/config.py    路径与运行时配置、模型注册表
    web/events.py    Agent 事件 → WebSocket JSON 协议
    web/sessions.py  会话状态、展示消息、持久化
    web/agent.py     Agent 工厂与上下文压缩
    web/app.py       FastAPI 路由与 WebSocket
    web/static/      前端资源（index.html / app.css / app.js）
"""

from __future__ import annotations

import os
from pathlib import Path

from pi.pi_ai import Model
from pi.pi_agent_core.harness.skills import (
    LoadSkillsOptions,
    format_skills_for_prompt,
    load_skills,
)
from pi.pi_agent_core.harness.compaction import CompactionSettings
from pi.pi_tools import GetCityWeatherTool, WebSearchTavilyTool
from pi.pi_coding_agent.tools import (
    BashTool,
    EditTool,
    FindTool,
    GrepTool,
    LsTool,
    ReadTool,
    WriteTool,
)

from load_env import load_models

# ---------------------------------------------------------------- 路径

BASE_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
SESSIONS_DIR = BASE_DIR / ".pi" / "sessions"

# ---------------------------------------------------------------- 模型

#: 每个模型：key / label（供应商/模型名）/ Model 实例 / api_key
MODELS: list[dict] = []
MODEL_INDEX: dict[str, dict] = {}

for _entry in load_models():
    _entry["model"] = Model(
        id=_entry["id"],
        name=_entry["name"],
        base_url=_entry["base_url"],
        api=_entry["api"],
        provider=_entry["provider"],
        input=["text"],
        context_window=_entry["context_window"],
        max_tokens=_entry["max_tokens"],
        reasoning=_entry["reasoning"],
    )
    MODELS.append(_entry)
    MODEL_INDEX[_entry["key"]] = _entry

if not MODELS:  # load_models 已 raise，这里只是防御
    raise RuntimeError("没有可用模型，请检查 .env")

DEFAULT_MODEL_KEY = MODELS[0]["key"]
DEFAULT_MODEL = MODELS[0]

for _entry in MODELS:
    print(f"[INFO] 模型: {_entry['label']}  ({_entry['id']} @ {_entry['base_url']})")


def get_model(key: str | None) -> dict:
    """按 key 取模型；未知 key 回落到默认模型。"""
    if key and key in MODEL_INDEX:
        return MODEL_INDEX[key]
    return DEFAULT_MODEL


def model_public_list() -> list[dict]:
    """给前端的模型列表（不含密钥）。"""
    return [
        {
            "key": m["key"],
            "label": m["label"],
            "provider": m["provider"],
            "model": m["name"],
            "base_url": m["base_url"],
            "context_window": m["context_window"],
            "max_tokens": m["max_tokens"],
            "has_key": bool(m.get("api_key")),
        }
        for m in MODELS
    ]


# ---------------------------------------------------------------- 工具

PI_BUILTIN_TOOLS = [
    BashTool(),
    EditTool(),
    FindTool(),
    GrepTool(),
    LsTool(),
    ReadTool(),
    WriteTool(),
]

TOOLS = [GetCityWeatherTool(), WebSearchTavilyTool()] + PI_BUILTIN_TOOLS
TOOL_NAMES = [tool.name for tool in TOOLS]


def tool_public_list() -> list[dict]:
    """给前端的工具清单：名称 + 中文说明。"""
    items = []
    for tool in TOOLS:
        description = (getattr(tool, "description", "") or "").strip()
        items.append(
            {
                "name": tool.name,
                "label": (getattr(tool, "label", "") or tool.name),
                "description": description.split("\n")[0][:160],
            }
        )
    return items


# ---------------------------------------------------------------- 提示词

skills = load_skills(LoadSkillsOptions())
if skills.diagnostics:
    print(f"[WARN] Skill diagnostics: {skills.diagnostics}")
skills_block = format_skills_for_prompt(skills.skills)
print(f"[INFO] Loaded {len(skills.skills)} skill(s)")

BASE_PROMPT = (
    "你是一个智能助手，你必须用用户提问对应的语种进行思考和回答！"
    "你可以调用你掌握的工具来辅助自己。"
)
SYSTEM_PROMPT = f"{BASE_PROMPT}\n\n{skills_block}" if skills_block else BASE_PROMPT

# ---------------------------------------------------------------- 压缩

compaction_settings = CompactionSettings(
    enabled=True,
    reserve_tokens=30000,
    keep_recent_tokens=8000,
)

#: 是否启用 thinking（默认关闭；开启后前端会显示可折叠的思考过程）。
THINKING_LEVEL = None