---
name: tool-development
description: Guide for implementing AgentTool in this codebase — schema, return format, error handling, descriptions
---

When implementing a new tool in this codebase, follow this pattern.

## Core structure

Create a class implementing the AgentTool protocol:

```python
from __future__ import annotations

import asyncio
from typing import Any

from pi.pi_agent_core.types import AgentTool, AgentToolResult, ToolExecutionMode
from pi.pi_ai import TextContent


class MyTool:
    name = "my_tool"               # unique snake_case name
    label = "my_tool"               # display name
    execution_mode: ToolExecutionMode | None = None   # "parallel"(default) or "sequential"
    description = (
        "Short description of what this tool does. "
        "Include what inputs it accepts and what it returns. "
        "Write for the LLM — be precise."
    )
    parameters = {
        "type": "object",
        "properties": {
            "arg_name": {
                "type": "string",
                "description": "What this argument means. Be specific.",
            },
        },
        "required": ["arg_name"],   # list of required field names
    }

    def __init__(self, cwd: str = ".") -> None:
        self._cwd = cwd

    async def execute(
        self,
        tool_call_id: str,
        params: dict[str, Any],
        cancel_event: asyncio.Event | None = None,
        on_update: Any = None,
    ) -> AgentToolResult:
        # 1. Extract params
        arg = params["arg_name"]

        # 2. Validate early (return error result, don't raise)
        if not arg:
            return AgentToolResult(
                content=[TextContent(text="Error: arg_name cannot be empty")],
                details={"error": "validation"},
            )

        # 3. Do the work (use async for I/O)
        try:
            result = await some_async_operation(arg)
        except Exception as e:
            return AgentToolResult(
                content=[TextContent(text=f"Error: {e}")],
                details={"error": str(e)},
            )

        # 4. Return result
        return AgentToolResult(
            content=[TextContent(text=str(result))],
            details={"extra_field": result},   # optional, for internal tracking
        )
```

## Key rules

1. **description**: Write for the LLM, not humans. Say what it does, what inputs it takes, what format it returns. Bad: "Search the web." Good: "Search the web for information. Returns a summary of top results with URLs."

2. **parameters**: Use JSON Schema format. Only include fields the tool actually uses. Required fields must be in "required" list.

3. **error handling**: Never raise exceptions from execute(). Always return AgentToolResult with an error message in content. Use details dict for machine-readable error info.

4. **AgentToolResult**: content is a list of TextContent (or ImageContent). Keep text under a few KB — truncate large outputs and save to temp file if needed.

5. **async**: Use async/await for any I/O (file, network, subprocess). Use asyncio.create_subprocess_shell for shell commands.

6. **sequential mode**: Set execution_mode = "sequential" if this tool has side effects that should not run in parallel with other tools (file writes, API mutations).

7. **name**: Must be unique across all registered tools. Use snake_case.

## Registering

In hello_agent_loop.py:

```python
"tools": [MyTool(), ...],
```

That's it. The agent will see the tool and call it when tasks match the description.
