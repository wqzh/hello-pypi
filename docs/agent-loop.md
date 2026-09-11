# Agent Loop 循环机制详解

> 基于 `pypi/pi` 源码分析（对应上游 @earendil-works/pi@v0.84.1）

本文档聚焦 agent 的 **双层循环（double-loop）引擎**，解释它如何驱动一次完整的 agent 会话，包括消息流转、工具调用、状态管理和事件广播。

---

## 目录

1. [架构总览](#1-架构总览)
2. [核心入口与生命周期](#2-核心入口与生命周期)
3. [双层循环结构](#3-双层循环结构)
4. [Loop 中的状态流转](#4-loop-中的状态流转)
5. [消息管理机制](#5-消息管理机制)
6. [工具调用机制](#6-工具调用机制)
7. [事件系统（AgentEvent）](#7-事件系统agentevent)
8. [关键类与数据流](#8-关键类与数据流)
9. [流程图](#9-流程图)

---

## 1. 架构总览

Agent 框架分为两层：

```
┌─────────────────────────────────────────────────┐
│               Agent（有状态）                     │
│  - 维护 AgentState（messages/tools/工具调用队列） │
│  - 提供 prompt() / continue_() / steer() /       │
│    follow_up() / subscribe()                     │
│  - 维护 _active_run 生命周期                     │
│  - 事件归约（process_events → _state）           │
└────────────────────┬────────────────────────────┘
                     │ 委托
                     ▼
┌─────────────────────────────────────────────────┐
│            agent_loop（无状态引擎）               │
│  - 双层循环：外层 follow-up / 内层 tool+steering │
│  - _stream_assistant_response（LLM 调用边界）    │
│  - _execute_tool_calls（工具执行）               │
│  - emit AgentEvent → 回调                       │
└────────────────────┬────────────────────────────┘
                     │ 调用
                     ▼
┌─────────────────────────────────────────────────┐
│                pi-ai（LLM 抽象层）                │
│  - stream_simple(model, context, options)        │
│  - EventStream[AssistantMessageEvent]            │
└─────────────────────────────────────────────────┘
```

- **`Agent`**（`agent.py`）：有状态封装，维护会话状态、队列、事件监听。
- **`_run_loop`**（`agent_loop.py`）：无状态核心引擎，执行双层循环。
- **`pi-ai`**：统一 LLM 流式调用抽象。

---

## 2. 核心入口与生命周期

### 2.1 Agent.prompt() —— 新建会话

调用链路：

```
agent.prompt(message)
  → _run_prompt_messages(messages)
    → _run_with_lifetime(executor)
      → _run_agent_loop(prompts, context_snapshot, loop_config, ...)
        → _run_loop(initial_context, new_messages, ...)  # 核心双层循环
```

关键行为：
- 检查 `_active_run` 是否为 None，防止并发调用。
- 创建 `context_snapshot`（messages/tools/system_prompt 的浅拷贝）。
- 创建 `loop_config`，闭包绑定 steering/follow-up 队列的 drain 方法。
- 进入 `_run_with_lifetime`，创建 `cancel_event` 和 `future`。
- 循环结束后清理状态，释放 `_active_run`。

### 2.2 Agent.continue_() —— 续会话

从已有上下文继续：

```
agent.continue_()
  → _run_continuation()
    → _run_agent_loop_continue(context_snapshot, loop_config, ...)
      → _run_loop(initial_context, new_messages=[], ...)
```

约束：
- 末尾消息不能是 `AssistantMessage`（否则要求先消费排队消息或报错）。
- 末尾必须是 `UserMessage` 或 `ToolResultMessage`。

### 2.3 Agent.steer() / Agent.follow_up() —— 注入消息

在 agent 运行时注入消息的两个队列：

| 队列 | 时机 | 队列模式 |
|------|------|----------|
| `steering_queue` | agent **运行时**注入，在下一个 assistant 响应前插入 | `QueueMode`: `"all"` 或 `"one-at-a-time"` |
| `follow_up_queue` | agent **本应停止时**追加，触发新一轮循环 | `QueueMode`: `"all"` 或 `"one-at-a-time"` |

- **steer()**：工作中打断/引导（例如：用户中途纠正）。
- **follow_up()**：完成后追加（例如：UI 自动追加后续问题）。

---

## 3. 双层循环结构

核心函数：`_run_loop()`，位于 `agent_loop.py`。

### 3.1 伪代码结构

```python
async def _run_loop(initial_context, new_messages, config, cancel_event, emit, stream_fn):
    current_context = initial_context
    first_turn = True
    pending_messages = drain(config.get_steering_messages)

    # ========== 外层循环：follow-up ==========
    while True:
        has_more_tool_calls = True

        # ========== 内层循环：tool calls + steering ==========
        while has_more_tool_calls or pending_messages:
            if not first_turn:
                emit(TurnStartEvent())
            else:
                first_turn = False

            # 1. 注入 pending_messages（steering）
            if pending_messages:
                for msg in pending_messages:
                    emit(MessageStartEvent(msg))
                    emit(MessageEndEvent(msg))
                    current_context.messages.append(msg)
                    new_messages.append(msg)
                pending_messages = []

            # 2. 流式拉取 assistant 响应（LLM 调用）
            message = await _stream_assistant_response(...)
            new_messages.append(message)

            # 3. 错误/中止 → 直接结束整个 loop
            if message.stop_reason in ("error", "aborted"):
                emit(TurnEndEvent(message, []))
                emit(AgentEndEvent(new_messages))
                return

            # 4. 抽取工具调用
            tool_calls = [c for c in message.content if isinstance(c, ToolCall)]
            tool_results = []
            has_more_tool_calls = False

            if tool_calls:
                if message.stop_reason == "length":
                    # 输出截断 → 工具参数可能不完整 → 全部失败
                    tool_results = _fail_tool_calls_from_truncated(tool_calls)
                else:
                    batch = await _execute_tool_calls(...)
                    tool_results = batch["messages"]
                    has_more_tool_calls = not batch["terminate"]

                for result in tool_results:
                    current_context.messages.append(result)
                    new_messages.append(result)

            emit(TurnEndEvent(message, tool_results))

            # 5. 检查取消
            if cancel_event.is_set():
                emit(AgentEndEvent(new_messages))
                return

            # 6. should_stop_after_turn 钩子
            if config.should_stop_after_turn and hook_should_stop(...):
                emit(AgentEndEvent(new_messages))
                return

            # 7. 再次拉取 steering
            pending_messages = drain(config.get_steering_messages)

        # ========== 外层：agent 本应停止，检查 follow-up ==========
        follow_ups = drain(config.get_follow_up_messages)
        if follow_ups:
            pending_messages = follow_ups   # 带回内层，继续
            continue
        break  # 无 follow-up → 结束

    emit(AgentEndEvent(new_messages))
```

### 3.2 内外层职责

| 层级 | 职责 | 终止条件 |
|------|------|----------|
| **内层** | 处理一轮或多轮 assistant 响应 + 工具调用 + steering 注入 | `stop_reason in (error, aborted)` 或 `cancel_event` 或 `should_stop_after_turn` |
| **外层** | agent 本应停止时，检查 follow-up 队列，有则重新进入内层 | follow-up 队列为空 |

### 3.3 关键设计要点

1. **错误编码为消息**：不抛异常，用 `stop_reason="error"` 或 `"aborted"` 编码。
2. **流式 partial 占位替换**：streaming 时 context.messages[-1] 始终指向 partial assistant message，完成时替换。
3. **length 截断保护**：`stop_reason="length"` 时跳过真实工具执行，避免截断参数引发错误。
4. **steering vs follow-up**：steering 在内层末尾轮询；follow-up 在外层轮询，语义不同。

---

## 4. Loop 中的状态流转

一次完整的 agent loop 经历以下状态阶段：

```
START
  │
  ├─ AgentStartEvent                    ← agent 运行开始
  │
  ├─ TurnStartEvent                     ← 新一轮 turn 开始
  │   │
  │   ├─ MessageStartEvent (user/steering)    ← 注入消息
  │   ├─ MessageEndEvent (user/steering)
  │   │
  │   ├─ MessageStartEvent (assistant, partial)  ← LLM 开始流式响应
  │   ├─ MessageUpdateEvent (assistant, delta×N) ← 流式更新（文本/工具调用片段）
  │   ├─ MessageEndEvent (assistant, final)      ← LLM 响应完成
  │   │
  │   ├─ [有 tool calls?]
  │   │   ├─ ToolExecutionStartEvent (每个 tool call)
  │   │   ├─ ToolExecutionUpdateEvent (可选，流式工具进度)
  │   │   ├─ ToolExecutionEndEvent (每个 tool call)
  │   │   ├─ MessageStartEvent (toolResult)
  │   │   ├─ MessageEndEvent (toolResult)
  │   │
  │   └─ TurnEndEvent (assistant message + tool_results)
  │
  ├─ [内层继续或外层 follow-up]
  │
  └─ AgentEndEvent (new_messages)       ← agent 运行结束
```

### 4.1 Turn 的语义

一个 **Turn** 包含：
- 可选的 steering 消息注入
- 一次 LLM 调用（assistant 响应）
- 可选的批量工具执行（tool calls → tool results）

一个 agent 运行可以包含多个 turns。

### 4.2 AssistantMessage.stop_reason 含义

| stop_reason | 含义 | 后续行为 |
|-------------|------|----------|
| `"stop"` | 模型自然停止 | 检查 follow-up，无则结束 |
| `"length"` | 输出被截断 | 工具调用全部失败，继续外层 |
| `"toolUse"` | 模型调用了工具 | 执行工具，内层继续 |
| `"error"` | 调用出错 | 结束整个 loop |
| `"aborted"` | 被取消 | 结束整个 loop |
| `"pending"` | 中间状态（streaming） | 流式过程中出现 |

---

## 5. 消息管理机制

### 5.1 消息类型（来自 pi-ai）

| 类型 | role | 内容 |
|------|------|------|
| `UserMessage` | `"user"` | `TextContent` / `ImageContent` |
| `AssistantMessage` | `"assistant"` | `TextContent` / `ThinkingContent` / `ToolCall` |
| `ToolResultMessage` | `"toolResult"` | `TextContent` / `ImageContent` + 错误标记 |

### 5.2 消息队列模式（QueueMode）

`_PendingMessageQueue` 支持两种模式：

```python
class _PendingMessageQueue:
    def __init__(self, mode: QueueMode = "one-at-a-time"):
        self.mode = mode  # "all" | "one-at-a-time"

    def drain(self) -> list[AgentMessage]:
        if self.mode == "all":
            # 一次性返回所有排队消息
            return list(self._messages)
        else:
            # 每次只返回第一条
            return [self._messages.pop(0)]
```

- **"one-at-a-time"**：每次 drain 只取一条，适合逐步交互。
- **"all"**：一次性取所有，适合批量处理。

### 5.3 消息快照（Context Snapshot）

每次 `prompt()` 或 `continue_()` 创建快照：

```python
def _create_context_snapshot(self) -> AgentContext:
    return AgentContext(
        system_prompt=self._state.system_prompt,
        messages=list(self._state.messages),  # 浅拷贝
        tools=list(self._state.tools) if self._state.tools else None,
    )
```

- 全程使用 `AgentMessage`（pi-ai 的 Message 联合）。
- 仅在 LLM 调用边界通过 `convert_to_llm` 钩子转换为 LLM 所需的格式。

---

## 6. 工具调用机制

### 6.1 工具定义（AgentTool）

`AgentTool` 是一个 Protocol，要求实现：

```python
class AgentTool(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]       # JSON Schema
    label: str
    execution_mode: ToolExecutionMode | None  # "sequential" | "parallel" | None

    async def execute(
        self,
        tool_call_id: str,
        params: dict[str, Any],              # 已校验的参数
        cancel_event: asyncio.Event | None,   # 取消信号
        on_update: AgentToolUpdateCallback | None,  # 流式进度回调
    ) -> AgentToolResult: ...
```

### 6.2 执行流程

```
LLM 返回 AssistantMessage（content 包含 ToolCall）
  │
  ├─ 检查 stop_reason == "length"？
  │   └─ 是 → 全部工具调用失败，返回错误 ToolResultMessage
  │
  ├─ 检查 execution_mode：
  │   ├─ config.tool_execution == "sequential"
  │   └─ 或有工具声明 execution_mode == "sequential"
  │       → _execute_sequential()
  │   └─ 否则 → _execute_parallel()（默认）
  │
  ├─ 对每个 tool call：
  │   ├─ emit(ToolExecutionStartEvent)
  │   ├─ 查找 tool（按 name）
  │   ├─ before_tool_call 钩子（可 block）
  │   ├─ 参数校验（tool.prepare_arguments）
  │   ├─ 执行 tool.execute(...)
  │   ├─ after_tool_call 钩子（可修改 result）
  │   └─ emit(ToolExecutionEndEvent)
  │
  └─ 收集 tool_results，判断是否终止：
      └─ has_more_tool_calls = not all(terminate=True)
```

### 6.3 before_tool_call / after_tool_call 钩子

```python
# before_tool_call：执行前拦截
async def before_tool_call(ctx, cancel_event):
    return {
        "block": True,           # 阻止执行
        "reason": "权限不足",
        "terminate": False,      # 是否终止后续轮次
    }

# after_tool_call：执行后修改
async def after_tool_call(ctx, cancel_event):
    return {
        "content": [...],        # 覆盖返回内容
        "is_error": False,
        "terminate": True,       # 提示终止
    }
```

### 6.4 AgentToolResult.terminate

```python
@dataclass
class AgentToolResult:
    content: list[TextContent | ImageContent]
    details: Any = None
    terminate: bool = False  # True 表示此工具建议终止后续轮次
```

- 仅当 **同一批次所有** tool result 都 `terminate=True`，loop 才会停止。

---

## 7. 事件系统（AgentEvent）

Agent loop 通过 emit 事件流通知外部，覆盖四层生命周期：

### 7.1 事件层次

| 层级 | 事件类型 | 含义 |
|------|----------|------|
| **Agent** | `AgentStartEvent` | agent 运行开始 |
| | `AgentEndEvent` | agent 运行结束（携带最终消息列表） |
| **Turn** | `TurnStartEvent` | 新一轮 turn 开始 |
| | `TurnEndEvent` | turn 结束（携带 assistant message + tool_results） |
| **Message** | `MessageStartEvent` | 消息开始（partial assistant 或完整 user/toolResult） |
| | `MessageUpdateEvent` | assistant 流式更新（携带底层 pi-ai 事件） |
| | `MessageEndEvent` | 消息完成（最终形态） |
| **Tool** | `ToolExecutionStartEvent` | 工具执行开始 |
| | `ToolExecutionUpdateEvent` | 工具流式更新（可选） |
| | `ToolExecutionEndEvent` | 工具执行结束（携带 result + is_error） |

### 7.2 事件订阅与状态归约

`Agent` 订阅事件并归约到 `_state`：

```python
async def _process_events(self, event: AgentEvent):
    if isinstance(event, (MessageStartEvent, MessageUpdateEvent)):
        self._state.streaming_message = event.message
    elif isinstance(event, MessageEndEvent):
        self._state.streaming_message = None
        self._state.messages.append(event.message)
    elif isinstance(event, ToolExecutionStartEvent):
        self._state.pending_tool_calls |= {event.tool_call_id}
    elif isinstance(event, ToolExecutionEndEvent):
        self._state.pending_tool_calls -= {event.tool_call_id}
    elif isinstance(event, TurnEndEvent):
        if event.message.error_message:
            self._state.error_message = event.message.error_message
    elif isinstance(event, AgentEndEvent):
        self._state.streaming_message = None

    # 串行广播给所有监听器
    for listener in self._listeners:
        result = listener(event, self.cancel_event)
        if inspect.isawaitable(result):
            await result
```

---

## 8. 关键类与数据流

### 8.1 类关系图

```
┌──────────────────┐
│     Agent        │ 有状态，生命周期管理
│ - _state         │
│ - queues         │
│ - listeners      │
└───────┬──────────┘
        │ 创建快照 + 配置
        ▼
┌──────────────────┐     ┌──────────────────────┐
│   AgentState     │     │   AgentLoopConfig    │
│ - system_prompt  │     │ - model              │
│ - model          │     │ - tool_execution     │
│ - tools          │     │ - 钩子集合           │
│ - messages       │     │ - get_steering/follow_up│
│ - ...            │     └──────────┬───────────┘
└──────────────────┘                │
        │                           ▼
        │                    ┌──────────────────┐
        │                    │   _run_loop      │ 无状态引擎
        │                    │ (双层循环)        │
        │                    └────────┬─────────┘
        │                             │ emit
        ▼                             ▼
┌──────────────────┐     ┌──────────────────────┐
│ AgentContext     │     │   AgentEvent         │
│ - system_prompt  │     │ (联合类型，12种事件)  │
│ - messages       │     └──────────────────────┘
│ - tools          │
└──────────────────┘
```

### 8.2 涉及的核心类（汇总）

| 类 | 文件 | 职责 |
|----|------|------|
| `Agent` | `agent.py` | 有状态封装，prompt/continue/steer/follow_up/subscribe |
| `AgentOptions` | `agent.py` | Agent 配置 |
| `_PendingMessageQueue` | `agent.py` | steering/follow-up 队列 |
| `_run_loop` | `agent_loop.py` | 双层循环引擎 |
| `_stream_assistant_response` | `agent_loop.py` | LLM 调用 + 流式桥接 |
| `_execute_tool_calls` | `agent_loop.py` | 工具执行调度（并行/串行） |
| `AgentState` | `types.py` | Agent 可变状态 |
| `AgentContext` | `types.py` | 调用上下文快照 |
| `AgentLoopConfig` | `types.py` | Loop 配置 + 钩子 |
| `AgentTool` | `types.py` | 工具协议（Protocol） |
| `AgentToolResult` | `types.py` | 工具执行结果 |
| `AgentEvent` | `types.py` | 事件联合类型（12种） |
| `UserMessage/AssistantMessage/ToolResultMessage` | `pi_ai/types.py` | 消息类型 |
| `Context/Model/Tool` | `pi_ai/types.py` | LLM 调用类型 |
| `EventStream` | `pi_ai/event_stream.py` | 事件流抽象 |

---

## 9. 流程图

### 9.1 Agent.prompt() 到结束的完整流程

```
用户调用 agent.prompt("你好")
        │
        ▼
┌─────────────────┐
│ 检查 _active_run │── 非空 ──→ 抛 RuntimeError
│ 为 None？       │
└────────┬────────┘
         │ 空
         ▼
┌─────────────────┐
│ 创建 cancel_event│
│ 创建 future      │
│ _active_run = {...}│
│ is_streaming = True│
└────────┬────────┘
         ▼
┌─────────────────┐
│ _run_agent_loop │
│ (prompts,       │
│  context,       │
│  config, ...)   │
└────────┬────────┘
         ▼
┌─────────────────┐
│ emit(AgentStart)│
│ emit(TurnStart) │
│ emit user消息   │
└────────┬────────┘
         ▼
┌─────────────────────────────────────┐
│            _run_loop                │
│  ┌───────────────────────────────┐  │
│  │ 外层 while True:              │  │
│  │   ┌─────────────────────────┐ │  │
│  │   │ 内层 while (tools/      │ │  │
│  │   │           pending):     │ │  │
│  │   │                         │ │  │
│  │   │ 1. drain steering       │ │  │
│  │   │ 2. stream assistant     │ │  │
│  │   │ 3. handle tools         │ │  │
│  │   │ 4. check stop/cancel    │ │  │
│  │   └────────────┬────────────┘ │  │
│  │                │              │  │
│  │                ▼              │  │
│  │   drain follow-up → continue │  │
│  └───────────────────────────────┘  │
└───────────────┬─────────────────────┘
                │
                ▼
┌─────────────────┐
│ emit(AgentEnd)  │
│ is_streaming = False│
│ _active_run = None │
└─────────────────┘
```

### 9.2 Turn 内部流程

```
TurnStartEvent
    │
    ├─ [inject steering messages]
    │
    ▼
stream_assistant_response()
    │
    ├─ MessageStartEvent (assistant partial)
    ├─ MessageUpdateEvent × N
    ├─ MessageEndEvent (assistant final)
    │
    ├─ stop_reason in (error, aborted)?
    │   └─ YES → TurnEnd + AgentEnd → return
    │
    ├─ tool calls found?
    │   │
    │   ├─ stop_reason == "length"?
    │   │   └─ YES → all tools fail
    │   │
    │   └─ NO → execute_tool_calls()
    │       │
    │       ├─ ToolExecutionStartEvent × N
    │       ├─ ToolExecutionEndEvent × N
    │       ├─ MessageStart/End (toolResult) × N
    │       │
    │       └─ has_more_tool_calls = not all terminate
    │
    ▼
TurnEndEvent(assistant, tool_results)
    │
    ├─ cancel_event set? → AgentEnd → return
    ├─ should_stop_after_turn? → AgentEnd → return
    └─ drain steering → back to inner loop
```

---

## 总结

Agent loop 的核心设计哲学：

1. **双层循环**：外层负责 follow-up（会话延续），内层负责 turn（工具+steering）。
2. **事件驱动**：所有状态变化通过 emit AgentEvent，外部通过订阅观察。
3. **错误编码为消息**：不抛异常，用 `stop_reason` 区分终止原因。
4. **流式友好**：partial assistant message 占位替换，tool execution 支持流式更新。
5. **灵活钩子**：`before_tool_call`/`after_tool_call`/`should_stop_after_turn`/`transform_context` 提供扩展点。
6. **无状态引擎**：`_run_loop` 不维护状态，所有上下文通过参数传入，便于测试和复用。

---

> 本文档仅覆盖 loop 循环机制。后续文档将涵盖：记忆管理与压缩、消息管理、工具管理等主题。
