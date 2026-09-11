# Agent 记忆管理与上下文压缩

> 基于 `pypi/pi` 源码分析（对应上游 @earendil-works/pi@v0.84.1）

本文档解释 agent 的**记忆管理机制**与**上下文压缩（Compaction）**策略，涵盖会话持久化、树结构分支、token 估算、压缩触发与执行。

---

## 目录

1. [记忆管理的三层结构](#1-记忆管理的三层结构)
2. [会话持久化与树结构](#2-会话持久化与树结构)
3. [上下文压缩（Compaction）](#3-上下文压缩compaction)
4. [Token 估算机制](#4-token-估算机制)
5. [压缩触发条件](#5-压缩触发条件)
6. [压缩执行流程](#6-压缩执行流程)
7. [Session 上下文重建](#7-session-上下文重建)
8. [关键类与关系](#8-关键类与关系)
9. [流程图](#9-流程图)

---

## 1. 记忆管理的三层结构

Agent 的记忆管理分为三层：

```
┌─────────────────────────────────────────────────────────┐
│            AgentState（运行时内存）                       │
│  - messages: list[AgentMessage]    ← 当前会话全部消息     │
│  - streaming_message               ← 正在流式的消息      │
│  - pending_tool_calls              ← 未完成的工具调用     │
│  - error_message                   ← 最新错误            │
└──────────────────────┬──────────────────────────────────┘
                       │ append_message / append_compaction
                       ▼
┌─────────────────────────────────────────────────────────┐
│              Session（会话树）                            │
│  - 以树结构存储 SessionEntry（message/compaction/label等）│
│  - get_branch() 回溯当前分支                              │
│  - build_context() 重建消息序列                           │
│  - fork() 创建分支                                       │
└──────────────────────┬──────────────────────────────────┘
                       │ 读写
                       ▼
┌─────────────────────────────────────────────────────────┐
│          SessionStorage（存储后端）                       │
│  - InMemorySessionStorage    ← 内存（测试/临时）         │
│  - JsonlSessionStorage       ← JSONL 文件（持久化）      │
└─────────────────────────────────────────────────────────┘
```

- **AgentState**：运行时内存，维护当前会话的消息列表和流式状态。
- **Session**：会话树管理器，提供消息追加、分支回溯、上下文重建。
- **SessionStorage**：存储后端协议，支持内存和 JSONL 两种实现。

---

## 2. 会话持久化与树结构

### 2.1 SessionEntry 条目类型

会话以**树结构**存储条目，每个 `SessionEntry` 有 `id`、`parent_id`、`timestamp`、`type`、`data`。

支持的条目类型（`SessionEntryType`）：

| 类型 | 含义 | data 内容 |
|------|------|-----------|
| `message` | 一条消息 | `UserMessage` / `AssistantMessage` / `ToolResultMessage` |
| `compaction` | 上下文压缩点 | `{summary, retained_tail}` |
| `branch_summary` | 分支摘要 | 分支切换时的摘要文本 |
| `thinking_level_change` | 思考级别变更 | 新的 thinking level |
| `model_change` | 模型变更 | 新模型的配置 dict |
| `active_tools_change` | 工具集变更 | 当前激活的工具列表 |
| `label` | 会话标签 | 标签字符串 |
| `session_info` | 会话元信息 | 会话级别元数据 |

### 2.2 树结构的意义

- **分支（Branch）**：每个 `parent_id` 构成一条链，从叶节点（`leaf_id`）回溯到根即为当前上下文。
- **Fork**：从任意节点可以 fork 出新会话，共享到该点的历史，形成对话分支。
- **Compaction 作为断点**：从叶节点回溯时，遇到 `compaction` 条目即停止，compaction 之前的历史已被压缩为摘要。

### 2.3 存储后端实现

#### InMemorySessionStorage（纯内存）

```python
class InMemorySessionStorage:
    def __init__(self, metadata: dict = None):
        self._entries: dict[str, SessionEntry] = {}
        self._order: list[str] = []
        self._leaf_id: str | None = None
```

- 使用 `dict` 按 id 索引条目。
- 使用 `list` 保持追加顺序。
- 适合测试和临时会话。

#### JsonlSessionStorage（JSONL 文件）

```python
class JsonlSessionStorage:
    def __init__(self, file_path: str, metadata: dict = None):
        self._path = Path(file_path)
        # ...
        if self._path.exists():
            self._load()
```

文件格式（每行一个 JSON）：

```json
{"_meta": {"_leaf_id": "abc123", "_label": "project-x"}}
{"id": "e1", "parent_id": null, "timestamp": "...", "type": "message", "data": {...}}
{"id": "e2", "parent_id": "e1", "timestamp": "...", "type": "message", "data": {...}}
{"id": "e3", "parent_id": "e2", "timestamp": "...", "type": "compaction", "data": {...}}
```

- 第一行是 metadata（含 leaf_id / label）。
- 每追加一个条目立即 `_save()`。

### 2.4 Session 核心操作

```python
class Session:
    def append_message(self, message: Message) -> SessionEntry:
        """追加一条消息（自动设置 parent_id = leaf_id）"""

    def append_compaction(self, summary: str, retained_tail: list[Message]) -> SessionEntry:
        """追加一个压缩点"""

    def get_branch(self, from_id: str | None = None) -> list[SessionEntry]:
        """从叶节点回溯到根（或到 compaction），返回路径上的条目"""

    def build_context(self) -> list[Message]:
        """从分支重建消息列表（展开 compaction）"""

    def move_to(self, entry_id: str | None) -> None:
        """切换叶节点（分支切换）"""

    def fork(self, from_id: str | None = None) -> Session:
        """从指定节点 fork 出新会话"""
```

---

## 3. 上下文压缩（Compaction）

### 3.1 为什么需要压缩

- LLM 有 context window 限制（如 128K tokens）。
- 长对话的消息列表会持续增长，最终耗尽 context。
- 压缩策略：**保留最近的对话，将早期对话压缩为摘要**，减少 token 消耗。

### 3.2 CompactionSettings

```python
@dataclass
class CompactionSettings:
    enabled: bool = True
    reserve_tokens: int = 30000     # 压缩后保留的目标 token 数
    keep_recent_tokens: int = 8000  # 压缩点之后保留的最近 token 数（不压缩）
```

- **enabled**：是否启用压缩。
- **reserve_tokens**：触发压缩的阈值参考（与 context_window 配合使用）。
- **keep_recent_tokens**：压缩时保留的最近对话 token 数，这些消息不会被压缩。

### 3.3 CompactionResult

```python
@dataclass
class CompactionResult:
    summary: str                      # LLM 生成的对话摘要
    retained_tail: list[Message]      # 保留的最近消息（未压缩）
    removed_count: int                # 被压缩的消息数量
```

### 3.4 压缩后的效果

压缩前：

```
[user] message 1
[assistant] response 1
[user] message 2
[assistant] response 2
... (大量历史消息) ...
[user] message N-1
[assistant] response N-1
[user] message N       ← 最新
```

压缩后：

```
[user] [Previous conversation summary]
       {LLM 生成的摘要文本}
       ← compaction 条目展开
[user] message N-k+1   ← retained_tail 开始
[assistant] response N-k+1
...
[user] message N       ← 最新
```

---

## 4. Token 估算机制

### 4.1 estimate_tokens（单条消息估算）

```python
_CHARS_PER_TOKEN = 4  # 启发式：约 4 字符 ≈ 1 token

def estimate_tokens(message: Message) -> int:
    total_chars = 0
    content = getattr(message, "content", None)
    if isinstance(content, str):
        total_chars += len(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, TextContent):
                total_chars += len(block.text)
            elif isinstance(block, ThinkingContent):
                total_chars += len(block.thinking)
            elif isinstance(block, ToolCall):
                total_chars += len(json.dumps(block.arguments))
                total_chars += len(block.name)
    total_chars += 20  # role 等元数据开销
    return max(1, total_chars // _CHARS_PER_TOKEN)
```

- **启发式估算**：字符数 / 4。
- ToolCall 的参数 JSON 序列化后计入。
- 每条消息额外加 20 字符元数据开销。
- 最小返回 1 token。

### 4.2 estimate_context_tokens（整体会话估算）

```python
def estimate_context_tokens(messages: list[Message]) -> int:
    return sum(estimate_tokens(m) for m in messages)
```

### 4.3 calculate_context_tokens（从 usage 提取）

```python
def calculate_context_tokens(usage: Any) -> int:
    if usage is None:
        return 0
    input_t = getattr(usage, "input", 0) or 0
    cache_read = getattr(usage, "cache_read", 0) or 0
    cache_write = getattr(usage, "cache_write", 0) or 0
    return input_t + cache_read + cache_write
```

- 从 `AssistantMessage.usage` 提取真实 token 数（比估算更精确）。
- 包含 input + cache_read + cache_write。

---

## 5. 压缩触发条件

### 5.1 should_compact

```python
def should_compact(
    context_tokens: int,
    context_window: int,
    settings: CompactionSettings | None = None,
) -> bool:
    if not settings or not settings.enabled:
        return False
    if context_window <= 0:
        return False
    threshold = int(context_window * 0.8)  # 80% 阈值
    return context_tokens >= threshold
```

触发条件：
- `settings.enabled == True`
- `context_tokens >= context_window * 0.8`

**80% 阈值**：在耗尽 context window 之前提前触发，预留空间给后续对话。

---

## 6. 压缩执行流程

### 6.1 compact 主函数

```python
async def compact(
    model: Model,
    messages: list[Message],
    settings: CompactionSettings | None = None,
    **options: Any,
) -> CompactionResult:
    settings = settings or CompactionSettings()
    cut = find_cut_point(messages, settings.keep_recent_tokens)
    if cut == 0:
        # 全部保留，无需压缩
        return CompactionResult(summary="", retained_tail=list(messages), removed_count=0)

    to_summarize = messages[:cut]
    retained = messages[cut:]

    summary = await generate_summary(model, to_summarize, **options)
    return CompactionResult(summary=summary, retained_tail=retained, removed_count=cut)
```

步骤：
1. 调用 `find_cut_point` 找到切割点。
2. 切割点之前的消息送入 LLM 生成摘要。
3. 返回 `CompactionResult(summary, retained_tail, removed_count)`。

### 6.2 find_cut_point（切割点查找）

```python
def find_cut_point(
    messages: list[Message],
    keep_recent_tokens: int,
) -> int:
    if not messages:
        return 0
    acc = 0
    cut = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        msg_tokens = estimate_tokens(messages[i])
        if acc + msg_tokens > keep_recent_tokens and i < len(messages) - 1:
            cut = i + 1
            break
        acc += msg_tokens
        cut = i

    # 避免切割点落在 toolResult（需要配对的前序 assistant）
    while cut < len(messages) and isinstance(messages[cut], ToolResultMessage):
        cut -= 1
    return max(0, cut)
```

算法：
- 从末尾向前累加 token，直到达到 `keep_recent_tokens`。
- 返回切割点索引：`messages[:cut]` 被总结，`messages[cut:]` 保留。
- **保护规则**：切割点不落在 `ToolResultMessage`，确保工具调用的完整性。

### 6.3 generate_summary（LLM 生成摘要）

```python
SUMMARIZATION_SYSTEM_PROMPT = (
    "You are a conversation summarizer. Summarize the following conversation "
    "concisely, preserving key decisions, context, and any pending tasks. "
    "Write in the same language as the conversation."
)

async def generate_summary(
    model: Model,
    messages: list[Message],
    **options: Any,
) -> str:
    serialized = _serialize_conversation(messages)
    ctx_msg = UserMessage(content=f"Summarize this conversation:\n\n{serialized}")
    ctx = Context(system_prompt=SUMMARIZATION_SYSTEM_PROMPT, messages=[ctx_msg])
    opts = SimpleStreamOptions(
        max_tokens=2000,
        session_id=str(uuid.uuid4()),   # 隔离会话
        cache_retention="none",         # 不缓存
    )
    result = await complete_simple(model, ctx, opts)
    # 提取文本
    return result.content[0].text
```

关键点：
- 使用独立的 `session_id`（UUID）隔离摘要生成会话。
- `cache_retention="none"`：不占用缓存。
- `max_tokens=2000`：限制摘要长度。
- `_serialize_conversation` 将消息序列化为 `[role] content` 格式。

---

## 7. Session 上下文重建

### 7.1 build_context

```python
def build_context(self) -> list[Message]:
    """从当前分支构建 AgentMessage 列表。"""
    messages: list[Message] = []
    for entry in self.get_branch():
        if entry.type == "message":
            if isinstance(entry.data, (UserMessage, AssistantMessage, ToolResultMessage)):
                messages.append(entry.data)
        elif entry.type == "compaction" and isinstance(entry.data, dict):
            # 展开 compaction：摘要 + retained_tail
            summary = entry.data.get("summary", "")
            if summary:
                messages.append(
                    UserMessage(content=f"[Previous conversation summary]\n{summary}")
                )
            for raw in entry.data.get("retained_tail", []):
                msg = _deserialize_message(raw)
                if isinstance(msg, (UserMessage, AssistantMessage, ToolResultMessage)):
                    messages.append(msg)
    return messages
```

处理规则：
- **message 条目**：直接取 data。
- **compaction 条目**：展开为 UserMessage（摘要）+ retained_tail 消息。
- **其他类型**（thinking_level_change / model_change / label）：跳过，不影响消息序列。

### 7.2 get_branch（回溯分支）

```python
def get_branch(self, from_id: str | None = None) -> list[SessionEntry]:
    leaf = from_id or self.leaf_id
    if leaf is None:
        return []
    path: list[SessionEntry] = []
    current: str | None = leaf
    while current:
        entry = self._storage.get_entry(current)
        if entry is None:
            break
        path.append(entry)
        if entry.type == "compaction":  # 到 compaction 就停止
            break
        current = entry.parent_id
    path.reverse()
    return path
```

- 从叶节点（或指定节点）沿 `parent_id` 回溯。
- 遇到 `compaction` 即停止（之前的历史已被压缩）。
- 返回时反转，得到从旧到新的顺序。

---

## 8. 关键类与关系

### 8.1 涉及的类

| 类 | 文件 | 职责 |
|----|------|------|
| `AgentState` | `types.py` | 运行时状态（messages/工具调用/error） |
| `Session` | `harness/session.py` | 会话树管理（追加/回溯/重建/fork） |
| `SessionEntry` | `harness/session.py` | 会话树的一个条目（8种类型） |
| `SessionStorage` | `harness/session.py` | 存储后端协议 |
| `InMemorySessionStorage` | `harness/session.py` | 内存存储实现 |
| `JsonlSessionStorage` | `harness/session.py` | JSONL 文件存储实现 |
| `CompactionSettings` | `harness/compaction.py` | 压缩配置 |
| `CompactionResult` | `harness/compaction.py` | 压缩结果 |
| `estimate_tokens` | `harness/compaction.py` | Token 估算函数 |
| `should_compact` | `harness/compaction.py` | 判断是否压缩 |
| `find_cut_point` | `harness/compaction.py` | 找切割点 |
| `generate_summary` | `harness/compaction.py` | LLM 生成摘要 |
| `compact` | `harness/compaction.py` | 执行完整压缩 |

### 8.2 类关系图

```
┌──────────────┐       ┌──────────────┐
│ AgentState   │──────▶│ Session      │ 追加消息/压缩点
│ - messages   │       │ - _storage   │
└──────────────┘       └──────┬───────┘
                              │ 读写
                    ┌─────────┴──────────┐
                    │  SessionStorage     │ (Protocol)
                    └─────────┬──────────┘
                    ┌─────────┴──────────┐
          ┌─────────┤                    └─────────┐
          ▼                                   ▼
┌──────────────────┐                 ┌──────────────────┐
│ InMemorySession  │                 │ JsonlSession     │
│ Storage          │                 │ Storage          │
└──────────────────┘                 └──────────────────┘

                    ┌──────────────────┐
                    │   compact()      │ 独立函数
                    │   - find_cut_point│
                    │   - generate_summary│
                    └─────────┬────────┘
                              │ 返回
                              ▼
                    ┌──────────────────┐
                    │ CompactionResult │
                    │ - summary        │
                    │ - retained_tail  │
                    │ - removed_count  │
                    └──────────────────┘
```

---

## 9. 流程图

### 9.1 完整压缩流程

```
AgentState.messages 持续增长
        │
        ▼
┌───────────────────────────────────┐
│ should_compact(context_tokens,    │
│           context_window)         │
└──────────────┬────────────────────┘
               │
        tokens >= 80%?
         ┌──────┴──────┐
        YES           NO
         │             │
         ▼             └─→ 不压缩，继续对话
┌───────────────────────────────────┐
│ compact(model, messages, settings)│
└──────────────┬────────────────────┘
               │
               ▼
┌───────────────────────────────────┐
│ find_cut_point(messages,          │
│         keep_recent_tokens)       │
│ 从末尾向前累加，找到切割点          │
└──────────────┬────────────────────┘
               │
    messages[:cut]   messages[cut:]
     (总结)          (保留)
         │              │
         ▼              │
┌──────────────────────┐ │
│ generate_summary()   │ │
│ 调用 LLM 生成摘要      │ │
└──────────┬───────────┘ │
           │             │
           └────┬────────┘
                ▼
┌───────────────────────────────────┐
│ CompactionResult                  │
│ - summary = LLM摘要               │
│ - retained_tail = messages[cut:]  │
│ - removed_count = cut             │
└──────────────┬────────────────────┘
               │
               ▼
┌───────────────────────────────────┐
│ session.append_compaction()       │
│ 追加 compaction 条目               │
│ AgentState.messages 更新为压缩后    │
└───────────────────────────────────┘
```

### 9.2 Session 树结构与分支

```
初始会话（线性）：
root → m1 → m2 → m3 → m4 → m5(leaf)

压缩后：
root → m1 → m2 → compaction(retained: m3,m4,m5)(leaf)

分支（fork）：
         ┌─ m6a → m7a (branch A, leaf)
root → ... → compaction → m3 → m4 → m5 ──┤
         └─ m6b → m7b (branch B, leaf)

build_context(branch A)：
  [compaction展开: summary + retained] + m6a + m7a
```

---

## 总结

Agent 记忆管理的关键设计：

1. **三层结构**：AgentState（运行时）→ Session（树）→ SessionStorage（持久化）。
2. **树形会话**：支持分支（fork）和切换（move_to），每个分支独立上下文。
3. **Compaction 断点**：回溯到 compaction 即停止，之前历史以摘要形式存在。
4. **启发式 token 估算**：`chars/4`，轻量快速，适合实时决策。
5. **80% 阈值触发**：提前压缩，预留 context 空间。
6. **保留最近对话**：`keep_recent_tokens` 确保上下文连续性。
7. **LLM 生成摘要**：独立会话、不缓存、限制长度。

---

> 本文档覆盖记忆管理与压缩。后续文档：消息管理、工具管理。
