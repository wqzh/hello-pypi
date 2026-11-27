"""Web 层公共配置：路径、模型、工具、系统提示词。

所有 Web 相关代码统一放在 ``web/`` 下：
    web/config.py    路径与运行时配置
    web/events.py    Agent 事件 → WebSocket JSON 协议
    web/sessions.py  会话状态、展示消息、持久化
    web/agent.py     Agent 工厂与上下文压缩
    web/app.py       FastAPI 路由与 WebSocket
    web/static/      前端资源（index.html / app.css / app.js）
"""

from __future__ import annotations

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

from load_env import load_env_config

# ---------------------------------------------------------------- 路径

BASE_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
SESSIONS_DIR = BASE_DIR / ".pi" / "sessions"

# ---------------------------------------------------------------- 模型

API_KEY, MODEL_CONFIG = load_env_config()

model = Model(
    id=MODEL_CONFIG["id"],
    name=MODEL_CONFIG["name"],
    base_url=MODEL_CONFIG["base_url"],
    api=MODEL_CONFIG["api"],
    provider=MODEL_CONFIG["provider"],
    input=["text"],
    context_window=MODEL_CONFIG["context_window"],
    max_tokens=MODEL_CONFIG["max_tokens"],
    reasoning=MODEL_CONFIG["reasoning"],
)

print(f"[INFO] 模型配置: {model.id} @ {model.base_url}")

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
