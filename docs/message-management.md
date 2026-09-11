# Agent 消息管理机制

> 基于 `pypi/pi` 源码分析（对应上游 @earendil-works/pi@v0.84.1）

本文档解释 agent 的**消息管理系统**，涵盖消息类型、内容块结构、消息队列（steering / follow-up）、消息快照与上下文转换、流式消息处理。

---

## 目录

1. [消息类型体系](#1-消息类型体系)
2. [内容块结构（Content Blocks）](#2-内容块结构content-blocks)
3. [消息队列系统](#3-消息队列系统)
4. [消息快照与上下文](#4-消息快照与上下文)
5. [流式消息处理](#5-流式消息处理)
6. [消息在 Loop 中的流转](#6-消息在-loop-中的流转)
7. [transform_context 与 convert_to_llm 钩子](#7-transform_context-与-convert_to_llm-钩子)
8. [关键类与关系](#8-关键类与关系)
9. [流程图](#9-流程图)

---

## 1. 消息类型体系

### 1.1 三种核心消息

Agent 使用 `pi_ai.types.Message` 联合类型，按 `role` 区分：

| 类型 | role | 来源 | 用途 |
|------|------|------|------|
| `UserMessage` | `"user"` | 用户输入 | 携带文本/图片 |
| `AssistantMessage` | `"assistant"` | LLM 响应 | 携带文本/思考/工具调用 |
| `ToolResultMessage` | `"toolResult"` | 工具执行结果 | 携带文本/图片/错误标记 |

### 1.2 UserMessage

```python
class UserMessage(BaseModel):
    role: Literal["user"] = "user"
    content: str | list[UserContentBlock]   # 纯文本或内容块数组
    timestamp: int = 0                      # Unix 毫秒时间戳
```

- `content` 可以是：
  - 字符串：纯文本消息（最简单形式）。
  - `list[TextContent | ImageContent]`：内容块数组（支持多模态）。

### 1.3 AssistantMessage

```python
class AssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: list[AssistantContentBlock]    # 内容块数组
    api: Api = ""
    provider: ProviderId = ""
    model: str = ""
    response_model: str | None = None
    response_id: str | None = None
    usage: Usage = Usage()                  # token 用量
    stop_reason: StopReason = "pending"     # 停止原因
    error_message: str | None = None        # 错误信息
    timestamp: int = 0
```

- `content` 包含 `TextContent | ThinkingContent | ToolCall`。
- `stop_reason`：`"stop"` / `"length"` / `"toolUse"` / `"error"` / `"aborted"` / `"pending"`。
- `error_message`：错误编码为消息，不抛异常。

### 1.4 ToolResultMessage

```python
class ToolResultMessage(BaseModel):
    role: Literal["toolResult"] = "toolResult"
    tool_call_id: str                       # 对应的工具调用 ID
    tool_name: str                          # 工具名
    content: list[ToolResultContentBlock]   # 结果内容
    details: Any = None                     # 结构化详情（不发给模型）
    usage: Usage | None = None
    added_tool_names: list[str] | None = None
    is_error: bool = False                  # 是否执行失败
    timestamp: int = 0
```

### 1.5 AgentMessage 别名

```python
#: agent 循环中的消息（复用 pi-ai 的 Message 联合）。
AgentMessage = Message
```

在 agent 层，`AgentMessage` 是 `UserMessage | AssistantMessage | ToolResultMessage` 的联合。

---

## 2. 内容块结构（Content Blocks）

### 2.1 内容块类型

所有内容块用 `type` 字段做 discriminator（discriminated union）：

| 内容块 | type | 出现位置 |
|--------|------|----------|
| `TextContent` | `"text"` | user / assistant / toolResult |
| `ThinkingContent` | `"thinking"` | assistant（推理过程） |
| `ImageContent` | `"image"` | user / toolResult |
| `ToolCall` | `"toolCall"` | assistant（工具调用） |

### 2.2 TextContent

```python
class TextContent(BaseModel):
    type: Literal["text"] = "text"
    text: str
    text_signature: str | None = None   # 签名（可选）
```

### 2.3 ThinkingContent

```python
class ThinkingContent(BaseModel):
    type: Literal["thinking"] = "thinking"
    thinking: str
    thinking_signature: str | None = None
    redacted: bool = False              # True 时真实载荷在 signature 中
```

### 2.4 ImageContent

```python
class ImageContent(BaseModel):
    type: Literal["image"] = "image"
    data: str                           # base64 编码
    mime_type: str                      # 如 "image/png"
```

### 2.5 ToolCall（工具调用块）

```python
class ToolCall(BaseModel):
    type: Literal["toolCall"] = "toolCall"
    id: str                             # 调用唯一 ID
    name: str                           # 工具名
    arguments: dict[str, Any] = {}      # 参数（JSON Schema 校验后）
    thought_signature: str | None = None
```

### 2.6 内容块联合类型

```python
AssistantContentBlock = Annotated[
    TextContent | ThinkingContent | ToolCall,
    Field(discriminator="type"),
]

UserContentBlock = Annotated[
    TextContent | ImageContent,
    Field(discriminator="type"),
]

ToolResultContentBlock = Annotated[
    TextContent | ImageContent,
    Field(discriminator="type"),
]
```

### 2.7 AgentToolCall 别名

```python
AgentToolCall = ToolCall
```

在 agent 层，工具调用块用 `AgentToolCall` 别名。

---

## 3. 消息队列系统

### 3.1 两个队列

Agent 维护两个独立的消息队列：

| 队列 | 用途 | 注入时机 |
|------|------|----------|
| `steering_queue` | 工作中注入，引导/纠正 | agent 运行时（prompt 进行中） |
| `follow_up_queue` | 完成后追加，延续会话 | agent 本应停止时 |

### 3.2 _PendingMessageQueue 实现

```python
class _PendingMessageQueue:
    """消息队列（all 或 one-at-a-time 模式）。"""

    def __init__(self, mode: QueueMode = "one-at-a-time") -> None:
        self.mode = mode
        self._messages: list[AgentMessage] = []

    def enqueue(self, message: AgentMessage) -> None:
        self._messages.append(message)

    def has_items(self) -> bool:
        return len(self._messages) > 0

    def drain(self) -> list[AgentMessage]:
        if self.mode == "all":
            drained = list(self._messages)
            self._messages.clear()
            return drained
        if not self._messages:
            return []
        first = self._messages[0]
        self._messages = self._messages[1:]
        return [first]

    def clear(self) -> None:
        self._messages.clear()
```

### 3.3 QueueMode 模式

```python
QueueMode = Literal["all", "one-at-a-time"]
```

| 模式 | 行为 |
|------|------|
| `"all"` | drain() 一次性返回所有排队消息 |
| `"one-at-a-time"` | drain() 每次只返回第一条 |

### 3.4 Agent 队列 API

```python
class Agent:
    def steer(self, message: AgentMessage) -> None:
        """入 steering 队列（工作中注入）。"""
        self.steering_queue.enqueue(message)

    def follow_up(self, message: AgentMessage) -> None:
        """入 follow-up 队列（完成后追加）。"""
        self.follow_up_queue.enqueue(message)

    def clear_steering_queue(self) -> None: ...
    def clear_follow_up_queue(self) -> None: ...
    def clear_all_queues(self) -> None: ...
    def has_queued_messages(self) -> bool: ...
```

### 3.5 队列与 Loop 的集成

队列通过 `AgentLoopConfig` 的钩子函数暴露给 loop：

```python
def _create_loop_config(self, skip_initial_steering: bool = False):
    cfg = AgentLoopConfig(...)

    _skip = [skip_initial_steering]

    async def _get_steering() -> list[AgentMessage]:
        if _skip[0]:
            _skip[0] = False
            return []
        return self.steering_queue.drain()

    async def _get_follow_up() -> list[AgentMessage]:
        return self.follow_up_queue.drain()

    cfg.get_steering_messages = _get_steering
    cfg.get_follow_up_messages = _get_follow_up
    return cfg
```

Loop 在适当时机调用：
- `_get_steering()`：内层循环末尾，轮询 steering 消息。
- `_get_follow_up()`：外层循环，检查 follow-up 消息。

### 3.6 队列使用场景

```python
# 场景 1：prompt 进行中，用户中途纠正
await agent.prompt("分析这段代码")
# agent 正在运行...
agent.steer(UserMessage(content="不对，请重新分析第10行"))

# 场景 2：prompt 完成后自动追加
await agent.prompt("写一个函数")
# agent 完成后...
agent.follow_up(UserMessage(content="再加一个测试用例"))
await agent.continue_()
```

---

## 4. 消息快照与上下文

### 4.1 AgentContext（上下文快照）

```python
@dataclass
class AgentContext:
    system_prompt: str
    messages: list[AgentMessage]
    tools: list[AgentTool] | None = None
```

每次 `prompt()` 或 `continue_()` 创建快照：

```python
def _create_context_snapshot(self) -> AgentContext:
    return AgentContext(
        system_prompt=self._state.system_prompt,
        messages=list(self._state.messages),   # 浅拷贝
        tools=list(self._state.tools) if self._state.tools else None,
    )
```

关键点：
- **浅拷贝**：列表是新对象，元素（消息）共享引用。
- 全程使用 `AgentMessage`，仅在 LLM 调用边界转换格式。

### 4.2 Context（pi-ai LLM 上下文）

```python
class Context(BaseModel):
    system_prompt: str | None = None
    messages: list[Message] = []
    tools: list[Tool] | None = None
```

在 `_stream_assistant_response` 中，`AgentContext` 被转换为 `Context`：

```python
async def _stream_assistant_response(context, config, ...):
    messages = context.messages
    if config.transform_context:
        messages = config.transform_context(messages, cancel_event)

    if config.convert_to_llm:
        llm_messages = config.convert_to_llm(messages)
    else:
        llm_messages = [
            m for m in messages
            if isinstance(m, (UserMessage, AssistantMessage, ToolResultMessage))
        ]

    llm_context = Context(
        system_prompt=context.system_prompt or None,
        messages=llm_messages,
        tools=_convert_tools(context.tools) if context.tools else None,
    )
```

### 4.3 消息过滤规则

默认过滤逻辑（无 `convert_to_llm` 钩子时）：

```python
llm_messages = [
    m for m in messages
    if isinstance(m, (UserMessage, AssistantMessage, ToolResultMessage))
]
```

- 仅保留三种核心消息类型。
- 如果 `transform_context` 或 `convert_to_llm` 被设置，由钩子函数决定。

---

## 5. 流式消息处理

### 5.1 占位-替换模式

Stream 过程中，partial assistant message 被附加到 context.messages，完成时替换：

```python
async def _stream_assistant_response(...):
    partial_message: AssistantMessage | None = None
    added_partial = False

    async for event in response:
        if event.type == "start":
            partial_message = event.partial
            context.messages.append(partial_message)   # 占位
            added_partial = True
            emit(MessageStartEvent(message=partial_message.model_copy()))

        elif event.type in ("text_delta", "toolcall_delta", ...):
            partial_message = event.partial
            context.messages[-1] = partial_message     # 替换（原地更新）
            emit(MessageUpdateEvent(message=partial_message.model_copy()))

        elif event.type in ("done", "error"):
            final_message = await response.result()
            if added_partial:
                context.messages[-1] = final_message    # 替换为最终版本
            else:
                context.messages.append(final_message)
            emit(MessageEndEvent(message=final_message))
            return final_message
```

关键设计：
- **`context.messages[-1]` 始终指向最新的 partial/final message**。
- **`model_copy()`**：emit 时创建副本，避免外部修改影响内部状态。

### 5.2 流式事件映射

pi-ai 流式事件 → agent 事件映射：

| pi-ai 事件 | agent 事件 |
|------------|------------|
| `start` | `MessageStartEvent(partial assistant)` |
| `text_start` / `text_delta` / `text_end` | `MessageUpdateEvent(updated partial)` |
| `thinking_start` / `thinking_delta` / `thinking_end` | `MessageUpdateEvent(updated partial)` |
| `toolcall_start` / `toolcall_delta` / `toolcall_end` | `MessageUpdateEvent(updated partial)` |
| `done` / `error` | `MessageEndEvent(final assistant)` |

### 5.3 AgentState.streaming_message

```python
async def _process_events(self, event: AgentEvent):
    if isinstance(event, (MessageStartEvent, MessageUpdateEvent)):
        self._state.streaming_message = event.message   # 指向当前 partial
    elif isinstance(event, MessageEndEvent):
        self._state.streaming_message = None            # 完成，清空
        self._state.messages.append(event.message)      # 追加到消息历史
```

- `streaming_message`：当前正在流式的 partial message（用于 UI 展示）。
- `messages`：已完成的消息历史（用于后续 LLM 调用）。

---

## 6. 消息在 Loop 中的流转

### 6.1 prompt() 消息流

```
agent.prompt("你好")
    │
    ├─ _normalize_input → [UserMessage(content="你好")]
    │
    ├─ _run_agent_loop(prompts=[UserMessage], context_snapshot, ...)
    │   │
    │   ├─ emit(AgentStartEvent)
    │   ├─ emit(TurnStartEvent)
    │   ├─ for prompt in prompts:
    │   │     emit(MessageStartEvent(prompt))
    │   │     emit(MessageEndEvent(prompt))
    │   │     current_context.messages.append(prompt)
    │   │     new_messages.append(prompt)
    │   │
    │   └─ _run_loop(current_context, new_messages, ...)
    │       │
    │       ├─ inner loop:
    │       │   ├─ 注入 pending_messages (steering)
    │       │   ├─ stream_assistant_response()
    │       │   │   ├─ emit(MessageStartEvent(assistant partial))
    │       │   │   ├─ emit(MessageUpdateEvent × N)
    │       │   │   ├─ emit(MessageEndEvent(assistant final))
    │       │   │   └─ new_messages.append(assistant_message)
    │       │   │
    │       │   ├─ 有 tool calls?
    │       │   │   ├─ execute_tool_calls()
    │       │   │   │   ├─ emit(ToolExecutionStartEvent)
    │       │   │   │   ├─ emit(ToolExecutionEndEvent)
    │       │   │   │   └─ for result in tool_results:
    │       │   │   │         emit(MessageStartEvent(result))
    │       │   │   │         emit(MessageEndEvent(result))
    │       │   │   │         current_context.messages.append(result)
    │       │   │   │         new_messages.append(result)
    │       │   │
    │       │   └─ emit(TurnEndEvent)
    │       │
    │       └─ outer loop:
    │           └─ 检查 follow-up → 继续或结束
    │
    └─ emit(AgentEndEvent(new_messages))
```

### 6.2 消息追加时机汇总

| 时机 | 追加的消息类型 | 位置 |
|------|---------------|------|
| prompt() 开始 | UserMessage | `new_messages` + `current_context.messages` |
| steering 注入 | UserMessage / ToolResultMessage | `new_messages` + `current_context.messages` |
| LLM 响应完成 | AssistantMessage | `new_messages` + `current_context.messages[-1]`（替换 partial） |
| 工具执行完成 | ToolResultMessage | `new_messages` + `current_context.messages` |
| MessageEndEvent | AssistantMessage / ToolResultMessage | `AgentState.messages`（通过 _process_events） |

---

## 7. transform_context 与 convert_to_llm 钩子

### 7.1 transform_context

在 LLM 调用前对消息序列进行变换：

```python
# AgentLoopConfig
transform_context: Callable[[list[AgentMessage], asyncio.Event], list[AgentMessage]] | None
```

示例用法：
- 移除敏感信息。
- 插入上下文相关的系统提示。
- 合并连续的用户消息。

```python
async def _stream_assistant_response(context, config, ...):
    messages = context.messages
    if config.transform_context:
        messages = config.transform_context(messages, cancel_event)
    # 继续...
```

### 7.2 convert_to_llm

将 agent 消息转换为 LLM 需要的格式：

```python
# AgentLoopConfig
convert_to_llm: Callable[[list[AgentMessage]], list[Message]] | None
```

示例用法：
- 转换为特定 provider 要求的格式。
- 添加/移除某些消息类型。
- 应用自定义的消息压缩或重排序。

默认行为（无钩子时）：
```python
llm_messages = [
    m for m in messages
    if isinstance(m, (UserMessage, AssistantMessage, ToolResultMessage))
]
```

### 7.3 钩子执行顺序

```
AgentContext.messages
    │
    ├─ transform_context(messages, cancel_event)  → 变换消息序列
    │
    ├─ convert_to_llm(messages)                   → 转换为 LLM Message[]
    │
    └─ Context(system_prompt, messages, tools)    → 构造 LLM Context
```

---

## 8. 关键类与关系

### 8.1 涉及的类

| 类 | 文件 | 职责 |
|----|------|------|
| `UserMessage` | `pi_ai/types.py` | 用户消息 |
| `AssistantMessage` | `pi_ai/types.py` | 助手消息 |
| `ToolResultMessage` | `pi_ai/types.py` | 工具结果消息 |
| `TextContent` | `pi_ai/types.py` | 文本内容块 |
| `ThinkingContent` | `pi_ai/types.py` | 思考内容块 |
| `ImageContent` | `pi_ai/types.py` | 图像内容块 |
| `ToolCall` | `pi_ai/types.py` | 工具调用块 |
| `Context` | `pi_ai/types.py` | LLM 调用上下文 |
| `AgentContext` | `types.py` | agent 上下文快照 |
| `AgentState` | `types.py` | agent 运行时状态（messages/streaming_message） |
| `_PendingMessageQueue` | `agent.py` | steering/follow-up 队列 |
| `AgentLoopConfig` | `types.py` | loop 配置（含 transform/convert 钩子） |

### 8.2 类型关系

```
AgentMessage (= Message)
├── UserMessage
│   └── content: str | list[TextContent | ImageContent]
├── AssistantMessage
│   └── content: list[TextContent | ThinkingContent | ToolCall]
└── ToolResultMessage
    └── content: list[TextContent | ImageContent]

AgentContext
├── system_prompt: str
├── messages: list[AgentMessage]
└── tools: list[AgentTool] | None

AgentState
├── messages: list[AgentMessage]         ← 已完成消息
├── streaming_message: AgentMessage | None ← 当前 partial
├── pending_tool_calls: set[str]
├── error_message: str | None
└── ...

_PendingMessageQueue
├── _messages: list[AgentMessage]
└── mode: QueueMode ("all" | "one-at-a-time")
```

---

## 9. 流程图

### 9.1 消息生命周期

```
创建消息
    │
    ├─ UserMessage: agent.prompt() / steer() / follow_up()
    ├─ AssistantMessage: _stream_assistant_response()
    └─ ToolResultMessage: _execute_single()
    │
    ▼
注入到 current_context.messages
    │
    ▼
emit(MessageStartEvent) → AgentState.streaming_message 更新
    │
    ▼ [AssistantMessage 流式]
emit(MessageUpdateEvent × N) → streaming_message 持续更新
    │
    ▼
emit(MessageEndEvent) → AgentState.messages.append() → streaming_message = None
    │
    ▼
包含在后续的 AgentContext snapshot → 下一次 LLM 调用
```

### 9.2 steering / follow-up 队列与 Loop

```
┌─────────────┐         ┌─────────────┐
│  steering   │         │  follow_up  │
│   queue     │         │   queue     │
└──────┬──────┘         └──────┬──────┘
       │ drain()               │ drain()
       ▼                       ▼
┌─────────────────────────────────────┐
│           _run_loop                 │
│  ┌───────────────────────────────┐  │
│  │  outer while:                 │  │
│  │    ┌───────────────────────┐ │ │
│  │    │ inner while:          │ │ │
│  │    │   pending += steering │ │ │ ← steering 注入内层
│  │    │   LLM → tools         │ │ │
│  │    │   pending += steering │ │ │
│  │    └───────────┬───────────┘ │ │
│  │                │             │ │
│  │                ▼             │ │
│  │    pending += follow_up      │ │ ← follow-up 检查在外层
│  │    if pending: continue      │ │
│  │    else: break               │ │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘
```

---

## 总结

Agent 消息管理的关键设计：

1. **三种消息类型**：UserMessage / AssistantMessage / ToolResultMessage，按 role 区分。
2. **内容块 discriminated union**：用 `type` 字段区分 text/thinking/image/toolCall。
3. **双队列系统**：steering（工作中注入）vs follow-up（完成后追加）。
4. **QueueMode**：`"all"`（一次性）或 `"one-at-a-time"`（逐条）。
5. **快照机制**：`AgentContext` 浅拷贝，全程 `AgentMessage`，LLM 边界转换。
6. **流式占位-替换**：`context.messages[-1]` 始终指向最新 partial/final。
7. **灵活钩子**：`transform_context` / `convert_to_llm` 允许自定义消息处理。

---

> 本文档覆盖消息管理。后续文档：工具管理。
