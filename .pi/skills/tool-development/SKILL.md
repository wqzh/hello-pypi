---
name: tool-development
description: 在本项目中实现 AgentTool 的指南 — schema、返回格式、错误处理、描述写法
---

在本项目中实现新工具时，请遵循此模式。

## 核心结构

创建一个实现 AgentTool 协议的类：

```python
from __future__ import annotations

import asyncio
from typing import Any

from pi.pi_agent_core.types import AgentTool, AgentToolResult, ToolExecutionMode
from pi.pi_ai import TextContent


class MyTool:
    name = "my_tool"               # 唯一的蛇形命名
    label = "my_tool"               # 显示名称
    execution_mode: ToolExecutionMode | None = None   # "parallel"(默认) 或 "sequential"
    description = (
        "简要描述这个工具做什么。"
        "包含它接受什么输入，返回什么结果。"
        "写给 LLM 看的 — 要准确。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "arg_name": {
                "type": "string",
                "description": "这个参数的含义。要具体。",
            },
        },
        "required": ["arg_name"],   # 必填字段名列表
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
        # 1. 提取参数
        arg = params["arg_name"]

        # 2. 尽早校验（返回错误结果，不要抛异常）
        if not arg:
            return AgentToolResult(
                content=[TextContent(text="错误：arg_name 不能为空")],
                details={"error": "validation"},
            )

        # 3. 执行工作（I/O 操作使用 async）
        try:
            result = await some_async_operation(arg)
        except Exception as e:
            return AgentToolResult(
                content=[TextContent(text=f"错误：{e}")],
                details={"error": str(e)},
            )

        # 4. 返回结果
        return AgentToolResult(
            content=[TextContent(text=str(result))],
            details={"extra_field": result},   # 可选，用于内部追踪
        )
```

## 关键规则

1. **description**：写给 LLM 看，不是给人看的。说明工具做什么、接受什么输入、返回什么格式。差的描述："搜索网络。" 好的描述："搜索网络获取信息。返回包含 URL 的搜索结果摘要。"

2. **parameters**：使用 JSON Schema 格式。只包含工具实际使用的字段。必填字段必须出现在 "required" 列表中。

3. **错误处理**：不要在 execute() 中抛出异常。总是返回带有错误信息的 AgentToolResult。使用 details 字典存储机器可读的错误信息。

4. **AgentToolResult**：content 是 TextContent（或 ImageContent）的列表。文本保持在几 KB 以内 — 大型输出需要截断并保存到临时文件。

5. **async**：任何 I/O 操作（文件、网络、子进程）都使用 async/await。shell 命令使用 asyncio.create_subprocess_shell。

6. **sequential 模式**：如果工具有副作用且不应与其他工具并行运行（文件写入、API 修改），设置 execution_mode = "sequential"。

7. **name**：在所有已注册工具中必须唯一。使用 snake_case。

## 注册

在 hello_agent_loop.py 中：

```python
"tools": [MyTool(), ...],
```

就这样。agent 会看到这个工具，并在任务匹配描述时调用它。
