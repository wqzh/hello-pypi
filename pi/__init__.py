"""pi: Python port of @earendil-works/pi.

Unified package containing:
- pi_ai: Unified LLM API
- pi_agent_core: Generic agent runtime
- pi_coding_agent: Coding agent
- pi_server: Server
- pi_storage_sqlite: SQLite storage

Usage:
    from pi.pi_ai import Model, TextContent, UserMessage
    from pi.pi_agent_core import Agent, AgentOptions, AgentToolResult
"""

__version__ = "0.84.1"
__upstream_ref__ = "earendil-works/pi@v0.84.1"

__all__ = ["__version__", "__upstream_ref__"]
