# Agent 工具管理机制

> 基于 `pypi/pi` 源码分析（对应上游 @earendil-works/pi@v0.84.1）

本文档解释 agent 的**工具管理系统**，涵盖工具定义协议、工具注册、执行模式（并行/串行）、工具调用生命周期、before/after 钩子、技能（Skill）系统。

---

## 目录

1. [工具系统的整体架构](#1-工具系统的整体架构)
2. [AgentTool 协议](#2-agenttool-协议)
3. [工具注册与配置](#3-工具注册与配置)
4. [工具执行模式](#4-工具执行模式)
5. [工具调用生命周期](#5-工具调用生命周期)
6. [before_tool_call / after_tool_call 钩子](#6-before_tool_call--after_tool_call-钩子)
7. [AgentToolResult.terminate 语义](#7-agenttoolresultterminate-语义)
8. [参数校验与 prepare_arguments](#8-参数校验与-prepare_arguments)
9. [技能（Skill）系统](#9-技能skill-系统)
10. [关键类与关系](#10-关键类与关系)
11. [流程图](#11-流程图)

---

## 1. 工具系统的整体架构

```
┌───────────────────────────────────────────────────────────┐
│                   Agent（有状态）                           │
│  - _state.tools: list[AgentTool]     ← 工具注册表           │
│  - _before_tool_call / _after_tool_call  ← 钩子             │
└──────────────────────┬────────────────────────────────────┘
                       │ 传入 AgentContext.tools
                       ▼
┌───────────────────────────────────────────────────────────┐
│               _run_loop（无状态引擎）                       │
│  - 从 AssistantMessage.content 提取 ToolCall               │
│  - _execute_tool_calls（调度并行/串行）                     │
│  - _execute_single（执行单个工具）                          │
│  - emit ToolExecutionStart/Update/EndEvent                 │
└──────────────────────┬────────────────────────────────────┘
                       │ 查找工具 + 调用 execute
                       ▼
┌───────────────────────────────────────────────────────────┐
│            AgentTool.execute()                             │
│  （用户实现的工具逻辑）                                      │
│  - params: dict[str, Any]（已校验）                        │
│  - cancel_event: 取消信号                                  │
│  - on_update: 流式进度回调                                 │
│  - returns: AgentToolResult                               │
└──────────────────────┬────────────────────────────────────┘
                       │ 转换
                       ▼
┌───────────────────────────────────────────────────────────┐
│           ToolResultMessage                                │
│  （返回给 LLM，携带执行结果/错误）                           │
└───────────────────────────────────────────────────────────┘
```

- **Agent**：维护工具注册表 `_state.tools`，提供钩子。
- **_run_loop**：从 assistant 响应中提取 ToolCall，调度执行。
- **AgentTool.execute()**：用户实现的工具核心逻辑。
- **ToolResultMessage**：执行结果，返回给 LLM 作为下一轮输入。

---

## 2. AgentTool 协议

### 2.1 Protocol 定义

```python
class AgentTool(Protocol):
    """工具契约。"""

    name: str                                    # 工具名（LLM 调用时使用）
    description: str                             # 工具描述（LLM 决策参考）
    parameters: dict[str, Any]                   # JSON Schema（参数定义）
    label: str                                   # 人类可读标签
    execution_mode: ToolExecutionMode | None     # "sequential" | "parallel" | None

    def execute(
        self,
        tool_call_id: str,
        params: dict[str, Any],              # 已校验的参数
        cancel_event: asyncio.Event | None,   # 取消信号
        on_update: AgentToolUpdateCallback | None,  # 流式进度回调
    ) -> Awaitable[AgentToolResult]: ...
```

- `AgentTool` 是 **Protocol**（结构性类型），不是抽象基类。
- 用户实现工具时，只需遵守 `execute` 签名即可。

### 2.2 execute 参数说明

| 参数 | 类型 | 含义 |
|------|------|------|
| `tool_call_id` | `str` | 工具调用的唯一 ID（LLM 生成） |
| `params` | `dict[str, Any]` | 已校验的工具参数（LLM 提供） |
| `cancel_event` | `asyncio.Event \| None` | 取消信号（`cancel_event.is_set()` 为 True 时应停止） |
| `on_update` | `Callable \| None` | 流式进度回调（工具执行过程中调用） |

### 2.3 execute 返回与异常

- **成功**：返回 `AgentToolResult`。
- **失败**：抛异常 → 被 loop 捕获 → 转为错误 `ToolResultMessage`。
- **流式更新**：调用 `on_update(partial_result)` 推送中间结果。

### 2.4 示例工具实现

```python
from pi_agent_core import AgentTool, AgentToolResult
from pi_ai import TextContent

class SearchTool:
    name = "search"
    description = "搜索信息"
    label = "Search"
    execution_mode = None
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"}
        },
        "required": ["query"]
    }

    async def execute(self, tool_call_id, params, cancel_event, on_update):
        query = params["query"]
        # 模拟搜索
        result = f"搜索结果: {query}"
        return AgentToolResult(
            content=[TextContent(text=result)],
            details={"query": query}
        )
```

---

## 3. 工具注册与配置

### 3.1 通过 AgentOptions 注册

```python
from pi_agent_core import Agent, AgentOptions

tools = [SearchTool(), ReadFileTool(), WriteFileTool()]

agent = Agent(
    AgentOptions(
        initial_state={
            "system_prompt": "你是一个编程助手",
            "model": model,
            "tools": tools,              # 注册工具列表
        },
    )
)
```

### 3.2 工具存储在 AgentState

```python
@dataclass
class AgentState:
    system_prompt: str = ""
    model: Model = DEFAULT_MODEL
    tools: list[AgentTool] = field(default_factory=list)  # ← 工具列表
    messages: list[AgentMessage] = field(default_factory=list)
    pending_tool_calls: set[str] = field(default_factory=set)
    # ...
```

- `tools`：当前会话可用的工具列表。
- `pending_tool_calls`：正在执行的工具调用 ID 集合。

### 3.3 工具转换为 LLM Tool

在 LLM 调用边界，`AgentTool` 被转换为 `pi_ai.Tool`：

```python
def _convert_tools(tools: list[AgentTool]) -> list[Tool]:
    from pi_ai import Tool
    out = []
    for t in tools:
        params = t.parameters
        out.append(
            Tool(
                name=t.name,
                description=t.description,
                parameters=params,
                constrained_sampling=getattr(t, "constrained_sampling", None),
            )
        )
    return out
```

- `Tool` 只包含 `name` / `description` / `parameters` / `constrained_sampling`。
- `execute` 方法不传给 LLM（只在 agent 端执行）。

### 3.4 动态修改工具集

```python
# 添加工具
agent.state.tools.append(NewTool())

# 移除工具
agent.state.tools = [t for t in agent.state.tools if t.name != "old_tool"]

# 完整替换
agent.state.tools = [ToolA(), ToolB()]
```

- 直接修改 `agent.state.tools`，下一次 LLM 调用时生效。

---

## 4. 工具执行模式

### 4.1 ToolExecutionMode

```python
ToolExecutionMode = Literal["sequential", "parallel"]
```

### 4.2 模式选择逻辑

```python
async def _execute_tool_calls(...):
    tool_calls = [c for c in assistant_message.content if isinstance(c, ToolCall)]

    # 检查是否有 sequential 工具
    has_sequential = False
    for tc in tool_calls:
        tool = _find_tool(context.tools, tc.name)
        if tool and getattr(tool, "execution_mode", None) == "sequential":
            has_sequential = True
            break

    if config.tool_execution == "sequential" or has_sequential:
        return await _execute_sequential(...)
    return await _execute_parallel(...)
```

优先级：
1. **config.tool_execution == "sequential"**：强制串行（最高优先级）。
2. **有工具声明 execution_mode == "sequential"**：该批次降级为串行。
3. **默认**：并行执行。

### 4.3 并行执行（_execute_parallel）

```python
async def _execute_parallel(...):
    tasks = [
        _execute_single(context, assistant_message, tc, config, cancel_event, emit)
        for tc in tool_calls
    ]
    outcomes = await asyncio.gather(*tasks)   # 并发执行
    results = []
    all_terminate = True
    for result_msg, terminate in outcomes:
        results.append(result_msg)
        emit(MessageStartEvent(message=result_msg))
        emit(MessageEndEvent(message=result_msg))
        if not terminate:
            all_terminate = False
    return {"messages": results, "terminate": len(results) > 0 and all_terminate}
```

- `asyncio.gather` 并发执行所有工具调用。
- 按原始顺序收集结果。

### 4.4 串行执行（_execute_sequential）

```python
async def _execute_sequential(...):
    results = []
    all_terminate = True
    for tc in tool_calls:
        if cancel_event is not None and cancel_event.is_set():
            break
        result_msg, terminate = await _execute_single(...)
        results.append(result_msg)
        emit(MessageStartEvent(message=result_msg))
        emit(MessageEndEvent(message=result_msg))
        if not terminate:
            all_terminate = False
    return {"messages": results, "terminate": len(results) > 0 and all_terminate}
```

- 按顺序逐个执行。
- 每次执行前检查 `cancel_event`。

---

## 5. 工具调用生命周期

### 5.1 完整生命周期

```
LLM 返回 AssistantMessage（content 包含 ToolCall）
        │
        ▼
┌───────────────────────────────────────────────────┐
│ 1. 提取 ToolCall                                  │
│    tool_calls = [c for c in message.content       │
│                  if isinstance(c, ToolCall)]      │
└──────────────────┬────────────────────────────────┘
                   │
                   ▼
┌───────────────────────────────────────────────────┐
│ 2. 检查 stop_reason                               │
│    - "length" → 全部失败（参数可能被截断）          │
│    - 其他 → 继续执行                              │
└──────────────────┬────────────────────────────────┘
                   │
                   ▼
┌───────────────────────────────────────────────────┐
│ 3. 选择执行模式（并行/串行）                        │
└──────────────────┬────────────────────────────────┘
                   │
                   ▼
┌───────────────────────────────────────────────────┐
│ 4. 对每个 ToolCall 执行 _execute_single():        │
│                                                    │
│    4a. emit(ToolExecutionStartEvent)               │
│        tool_call_id, tool_name, args               │
│                                                    │
│    4b. _find_tool(context.tools, tool_name)       │
│        - 未找到 → 错误结果                         │
│        - 找到 → 继续                               │
│                                                    │
│    4c. 参数校验（tool.prepare_arguments）           │
│                                                    │
│    4d. before_tool_call 钩子                       │
│        - block=True → 错误结果                     │
│        - 否则 → 继续                               │
│                                                    │
│    4e. tool.execute(tool_call_id, params,          │
│                     cancel_event, on_update)       │
│        - on_update(partial) →                      │
│          emit(ToolExecutionUpdateEvent)            │
│        - 正常返回 → 成功结果                       │
│        - 抛异常 → 错误结果                         │
│                                                    │
│    4f. after_tool_call 钩子                        │
│        - 可修改 content/details/is_error/terminate │
│                                                    │
│    4g. emit(ToolExecutionEndEvent)                 │
│        tool_call_id, result, is_error              │
└──────────────────┬────────────────────────────────┘
                   │
                   ▼
┌───────────────────────────────────────────────────┐
│ 5. 构建 ToolResultMessage                         │
│    role="toolResult", tool_call_id, content,      │
│    is_error, timestamp                            │
└──────────────────┬────────────────────────────────┘
                   │
                   ▼
┌───────────────────────────────────────────────────┐
│ 6. emit(MessageStartEvent + MessageEndEvent)      │
│    for result in tool_results:                    │
└──────────────────┬────────────────────────────────┘
                   │
                   ▼
┌───────────────────────────────────────────────────┐
│ 7. 追加到 current_context.messages                 │
│    作为下一轮 LLM 调用的输入                        │
└───────────────────────────────────────────────────┘
```

### 5.2 事件序列

一个工具调用的完整事件序列：

```
ToolExecutionStartEvent(tool_call_id="tc1", tool_name="search", args={...})
  │
  ├─ ToolExecutionUpdateEvent(tool_call_id="tc1", partial_result={...})  [可选，多次]
  │
  └─ ToolExecutionEndEvent(tool_call_id="tc1", result={...}, is_error=False)

MessageStartEvent(message=ToolResultMessage(tool_call_id="tc1", ...))
MessageEndEvent(message=ToolResultMessage(tool_call_id="tc1", ...))
```

### 5.3 AgentState.pending_tool_calls

```python
async def _process_events(self, event: AgentEvent):
    if isinstance(event, ToolExecutionStartEvent):
        self._state.pending_tool_calls |= {event.tool_call_id}
    elif isinstance(event, ToolExecutionEndEvent):
        self._state.pending_tool_calls -= {event.tool_call_id}
```

- `pending_tool_calls`：跟踪正在执行的工具调用 ID。
- 可用于 UI 展示工具执行状态。

---

## 6. before_tool_call / after_tool_call 钩子

### 6.1 before_tool_call

在工具执行前拦截，可阻止执行：

```python
# AgentLoopConfig
before_tool_call: Callable[
    [{
        "assistant_message": AssistantMessage,
        "tool_call": AgentToolCall,
        "args": dict,
        "context": AgentContext,
    }, asyncio.Event | None],
    Awaitable[dict | None]
] | None
```

返回格式：

```python
{
    "block": True,           # 阻止执行
    "reason": "权限不足",     # 阻止原因
    "terminate": False,      # 是否终止后续轮次
}
```

使用示例：

```python
async def before_tool_call(ctx, cancel_event):
    tool_call = ctx["tool_call"]
    if tool_call.name == "delete_file":
        # 危险操作，要求确认
        if not await user_confirms("确定要删除文件？"):
            return {"block": True, "reason": "用户拒绝", "terminate": False}
    return None  # 不拦截，继续执行
```

执行逻辑：

```python
if config.before_tool_call:
    before = config.before_tool_call(
        {"assistant_message", "tool_call", "args", "context"},
        cancel_event,
    )
    if before and before.get("block"):
        result = _error_result(before.get("reason", "Tool execution was blocked"))
        return _make_result_msg(tool_call, result, is_error=True), before.get("terminate")
```

### 6.2 after_tool_call

在工具执行后修改结果：

```python
# AgentLoopConfig
after_tool_call: Callable[
    [{
        "assistant_message": AssistantMessage,
        "tool_call": AgentToolCall,
        "args": dict,
        "result": AgentToolResult,
        "is_error": bool,
        "context": AgentContext,
    }, asyncio.Event | None],
    Awaitable[dict | None]
] | None
```

返回格式：

```python
{
    "content": [...],        # 覆盖返回内容（发给 LLM）
    "details": {...},        # 覆盖详情（不发给 LLM）
    "is_error": False,       # 覆盖错误状态
    "terminate": True,       # 提示终止后续轮次
}
```

使用示例：

```python
async def after_tool_call(ctx, cancel_event):
    result = ctx["result"]
    tool_call = ctx["tool_call"]
    # 记录审计日志
    log_audit(tool_call.name, ctx["args"], result)
    # 如果结果太长，截断
    if len(result.content[0].text) > 10000:
        result.content[0].text = result.content[0].text[:10000] + "...[truncated]"
    return None  # 不覆盖，使用原始结果
```

执行逻辑：

```python
if config.after_tool_call:
    after = config.after_tool_call(
        {"assistant_message", "tool_call", "args", "result", "is_error", "context"},
        cancel_event,
    )
    if after:
        if "content" in after:
            result.content = after["content"]
        if "details" in after:
            result.details = after["details"]
        if "is_error" in after:
            is_error = after["is_error"]
        if "terminate" in after:
            terminate = after["terminate"]
```

### 6.3 钩子执行顺序

```
before_tool_call(ctx)
    │
    ├─ block=True → 返回错误 ToolResultMessage（不执行工具）
    │
    ▼ block=False 或 None
tool.execute(tool_call_id, params, cancel_event, on_update)
    │
    ├─ 成功 → result
    ├─ 异常 → 错误 result
    │
    ▼
after_tool_call(ctx)
    │
    └─ 可修改 result.content/details/is_error/terminate
```

---

## 7. AgentToolResult.terminate 语义

### 7.1 AgentToolResult 结构

```python
@dataclass
class AgentToolResult:
    content: list[TextContent | ImageContent] = field(default_factory=list)
    details: Any = None
    added_tool_names: list[str] | None = None
    terminate: bool = False   # ← 终止标记
```

### 7.2 terminate 语义

- `terminate=True`：此工具建议终止后续轮次。
- **仅当同一批次所有 tool result 都 `terminate=True`，loop 才会停止**。

```python
def _execute_parallel(...):
    all_terminate = True
    for result_msg, terminate in outcomes:
        if not terminate:
            all_terminate = False
    return {"messages": results, "terminate": len(results) > 0 and all_terminate}
```

### 7.3 terminate 的汇聚逻辑

在 `_run_loop` 中：

```python
if tool_calls:
    batch = await _execute_tool_calls(...)
    tool_results = batch["messages"]
    has_more_tool_calls = not batch["terminate"]  # ← 所有 terminate=True → 停止
```

示例场景：

```
LLM 返回 2 个工具调用：
  - tool_call_1 → result.terminate = True   （用户已登录，无需继续）
  - tool_call_2 → result.terminate = False  （还有任务要做）

has_more_tool_calls = not (True and False) = True
→ 继续下一轮（至少有一个工具建议继续）
```

### 7.4 terminate 的来源

| 来源 | 说明 |
|------|------|
| `AgentToolResult.terminate` | 工具实现直接设置 |
| `after_tool_call["terminate"]` | 钩子函数设置 |
| `before_tool_call["terminate"]` | 钩子函数设置（block 时） |

---

## 8. 参数校验与 prepare_arguments

### 8.1 _validate_args

```python
def _validate_args(tool: AgentTool, args: dict[str, Any]) -> dict[str, Any]:
    """参数校验（简单版）。"""
    prepare = getattr(tool, "prepare_arguments", None)
    if prepare:
        prepared: dict[str, Any] = prepare(args)
        return prepared
    return args
```

- 如果工具定义了 `prepare_arguments(args) → dict`，调用它进行校验/转换。
- 否则直接返回原始参数。

### 8.2 prepare_arguments 示例

```python
class CalculateTool:
    name = "calculate"
    description = "计算表达式"
    parameters = {
        "type": "object",
        "properties": {
            "expression": {"type": "string"}
        },
        "required": ["expression"]
    }
    label = "Calculate"
    execution_mode = None

    def prepare_arguments(self, args):
        """校验和规范化参数"""
        expression = args.get("expression", "").strip()
        if not expression:
            raise ValueError("expression is required")
        # 只允许安全的字符
        if not re.match(r'^[\d+\-*/().\s]+$', expression):
            raise ValueError("Invalid characters in expression")
        return {"expression": expression}

    async def execute(self, tool_call_id, params, cancel_event, on_update):
        result = str(eval(params["expression"]))
        return AgentToolResult(content=[TextContent(text=result)])
```

### 8.3 错误处理

- `prepare_arguments` 抛异常 → 被 `_execute_single` 捕获 → 转为错误 `ToolResultMessage`。
- `execute` 抛异常 → 被 `_execute_single` 捕获 → 转为错误 `ToolResultMessage`。

---

## 9. 技能（Skill）系统

### 9.1 Skill 是什么

Skill 是遵循 [agentskills.io](https://agentskills.io) 标准的 Markdown 文件，提供特定任务的指导。Skill **不是工具**，而是注入 system prompt 的上下文信息。

### 9.2 Skill 结构

```markdown
---
name: git-workflow
description: Git 工作流程指南
---

# Git Workflow

当用户请求 Git 操作时，遵循以下流程：
1. ...
2. ...
```

### 9.3 Skill 加载

```python
@dataclass
class Skill:
    name: str
    description: str
    content: str
    file_path: str
    disable_model_invocation: bool = False
```

加载位置（优先级从高到低）：

| 位置 | 路径 |
|------|------|
| 用户级 | `~/.pi/agent/skills/` |
| 项目级 | `<cwd>/.pi/skills/` |
| 显式路径 | 自定义路径 |

### 9.4 Skill 发现算法（两阶段）

```
遍历目录：
  阶段一：找到 SKILL.md → 加载并立即返回（不递归子目录）
  阶段二：无 SKILL.md → 按字典序扫描
    - 根目录（include_root_files=True）：加载所有 .md 文件
    - 子目录（include_root_files=False）：仅通过 SKILL.md 贡献
```

### 9.5 Skill 注入 system prompt

```python
def format_skills_for_prompt(skills: list[Skill]) -> str:
    """把技能列表渲染成 XML，注入 system prompt。"""
    visible = [s for s in skills if not s.disable_model_invocation]
    if not visible:
        return ""
    lines = [
        "The following skills provide specialized instructions...",
        "<available_skills>",
        "  <skill>",
        "    <name>git-workflow</name>",
        "    <description>Git 工作流程指南</description>",
        "    <location>/path/to/git-workflow.md</location>",
        "  </skill>",
        "...",
        "</available_skills>",
    ]
    return "\n".join(lines)
```

### 9.6 Skill 与工具的区别

| 维度 | Skill | Tool |
|------|-------|------|
| 本质 | Markdown 文档（注入 system prompt） | 代码函数（LLM 调用后执行） |
| 标准 | agentskills.io | AgentTool Protocol |
| LLM 交互 | 读取内容作为指导 | 生成 ToolCall，agent 执行 |
| 位置 | system prompt / user message | AgentContext.tools |

---

## 10. 关键类与关系

### 10.1 涉及的类

| 类 | 文件 | 职责 |
|----|------|------|
| `AgentTool` | `types.py` | 工具协议（Protocol） |
| `AgentToolResult` | `types.py` | 工具执行结果 |
| `AgentToolCall` | `types.py` | 工具调用块（= ToolCall） |
| `AgentToolUpdateCallback` | `types.py` | 流式进度回调类型 |
| `ToolExecutionMode` | `types.py` | 执行模式（"sequential"/"parallel"） |
| `Tool` | `pi_ai/types.py` | LLM Tool（name/description/parameters） |
| `ToolCall` | `pi_ai/types.py` | 工具调用内容块 |
| `ToolResultMessage` | `pi_ai/types.py` | 工具结果消息 |
| `_find_tool` | `agent_loop.py` | 按 name 查找工具 |
| `_execute_tool_calls` | `agent_loop.py` | 调度执行（并行/串行） |
| `_execute_single` | `agent_loop.py` | 执行单个工具调用 |
| `Skill` | `harness/skills.py` | 技能定义 |
| `SkillLoadResult` | `harness/skills.py` | 技能加载结果 |
| `format_skills_for_prompt` | `harness/skills.py` | 技能格式化为 system prompt |

### 10.2 类型关系图

```
AgentTool (Protocol)
├── name: str
├── description: str
├── parameters: dict[str, Any]   ← JSON Schema
├── label: str
├── execution_mode: ToolExecutionMode | None
└── execute(tool_call_id, params, cancel_event, on_update)
        │
        ▼
AgentToolResult
├── content: list[TextContent | ImageContent]
├── details: Any
├── added_tool_names: list[str] | None
└── terminate: bool

ToolCall (= AgentToolCall)
├── type: "toolCall"
├── id: str
├── name: str
└── arguments: dict[str, Any]

ToolResultMessage
├── role: "toolResult"
├── tool_call_id: str
├── tool_name: str
├── content: list[ToolResultContentBlock]
├── details: Any
├── is_error: bool
└── timestamp: int

LLM Tool (pi_ai.Tool)
├── name: str
├── description: str
├── parameters: dict[str, Any]
└── constrained_sampling: dict | False | None
    ← AgentTool 转换而来（不含 execute）
```

---

## 11. 流程图

### 11.1 工具调用完整流程

```
LLM 返回 AssistantMessage(content=[..., ToolCall(...)])
        │
        ▼
┌─────────────────────────────────┐
│ 检查 stop_reason == "length"?   │
│   YES → _fail_tool_calls_from_  │
│        truncated() → 全部失败    │
└────────────┬────────────────────┘
             │ NO
             ▼
┌─────────────────────────────────┐
│ _execute_tool_calls()           │
│ 选择并行/串行模式                │
└────────────┬────────────────────┘
             │
             ▼
┌─────────────────────────────────┐
│ _execute_single() [每个 ToolCall]│
│                                 │
│ 1. emit(ToolExecutionStart)     │
│ 2. _find_tool(context.tools)    │
│    - None → 错误结果             │
│ 3. _validate_args(tool, args)   │
│    - prepare_arguments(...)     │
│ 4. before_tool_call 钩子        │
│    - block → 错误结果            │
│ 5. tool.execute(...)            │
│    - on_update → ToolExecUpdate │
│    - Exception → 错误结果        │
│ 6. after_tool_call 钩子         │
│    - 可修改 result               │
│ 7. emit(ToolExecutionEnd)       │
└────────────┬────────────────────┘
             │
             ▼
┌─────────────────────────────────┐
│ _make_result_msg()              │
│ ToolResultMessage(...)          │
└────────────┬────────────────────┘
             │
             ▼
┌─────────────────────────────────┐
│ emit(MessageStart + End)        │
│ current_context.messages.append │
│ new_messages.append             │
└────────────┬────────────────────┘
             │
             ▼
┌─────────────────────────────────┐
│ has_more_tool_calls =           │
│ not all(result.terminate)       │
│ TRUE → 内层继续（下一轮 LLM）    │
│ FALSE → 外层检查 follow-up       │
└─────────────────────────────────┘
```

### 11.2 Skill 加载与注入

```
load_skills(options)
    │
    ├─ 用户级：~/.pi/agent/skills/
    ├─ 项目级：<cwd>/.pi/skills/
    ├─ 显式路径：自定义
    │
    ▼
SkillLoadResult(skills=[Skill(...), ...])
    │
    ▼
format_skills_for_prompt(skills)
    │
    ▼
<available_skills>
  <skill><name>...</name><description>...</description></skill>
  ...
</available_skills>
    │
    ▼
拼接到 system_prompt
```

---

## 总结

Agent 工具管理的关键设计：

1. **AgentTool Protocol**：结构性类型，用户只需实现 `execute` 方法。
2. **两种执行模式**：并行（默认）/ 串行（config 或工具声明）。
3. **完整生命周期事件**：ToolExecutionStart → Update → End → MessageStart/End。
4. **before/after 钩子**：执行前拦截（block/terminate），执行后修改（content/is_error/terminate）。
5. **terminate 汇聚语义**：仅当同一批次所有结果都 terminate=True 才停止。
6. **错误编码为消息**：工具异常 → 错误 ToolResultMessage，不中断 loop。
7. **Skill vs Tool**：Skill 是注入 prompt 的文档，Tool 是 LLM 调用的函数。
8. **动态工具集**：直接修改 `agent.state.tools`，下次 LLM 调用生效。

---

> 本文档覆盖工具管理。至此，agent-loop、memory-management、message-management、tool-management 四个文档已完成。
