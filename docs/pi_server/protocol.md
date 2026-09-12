# protocol.py - RPC 协议定义

## 📖 目录

- [功能概述](#功能概述)
- [通信协议概览](#通信协议概览)
- [类型定义](#类型定义)
- [类详解](#类详解)
- [函数详解](#函数详解)
- [请求/响应类型](#请求响应类型)
- [使用示例](#使用示例)

---

## 功能概述

`protocol.py` 定义了 pi_server 的 RPC 通信协议，包括：

- 实例状态类型
- 实例摘要数据结构
- JSONL 消息编解码
- 响应消息构造

**上游对应：** `packages/server/src/ipc/protocol.ts` + `types.ts`

---

## 通信协议概览

### 传输格式

- **格式：** JSONL（JSON Lines）
- **分隔符：** `\n`（换行符）
- **编码：** UTF-8

**示例：**

```
{"type":"list","ok":true,"instances":[]}
{"type":"spawn_result","ok":true,"instance":{"id":"abc123","status":"online",...}}
```

### 协议层次

```
┌─────────────────────────────────────────────────────┐
│                 上层协议（Server 控制）                │
│  spawn | list | stop | status | rpc | rpc_stream    │
├─────────────────────────────────────────────────────┤
│                 下层协议（Agent RPC）                  │
│  prompt | get_state | abort                         │
└─────────────────────────────────────────────────────┘
```

---

## 类型定义

### InstanceStatus

实例状态类型，定义为 Python 的 `Literal` 类型：

```python
InstanceStatus = Literal["starting", "online", "stopping", "stopped", "error"]
```

| 状态 | 说明 |
|------|------|
| `starting` | 实例正在启动，CodingAgent 正在初始化 |
| `online` | 实例在线，可以接受 RPC 请求 |
| `stopping` | 实例正在停止中 |
| `stopped` | 实例已停止 |
| `error` | 实例发生错误 |

**状态流转：**

```
starting ──→ online ──→ stopping ──→ stopped
   │             │              │
   └──→ error ───┴──────────────┘
```

---

## 类详解

### InstanceSummary

实例摘要类，用于所有 list/status/spawn 返回的视图。

#### 构造函数

```python
class InstanceSummary:
    def __init__(
        self,
        id: str,                  # 实例 ID（16 位十六进制）
        status: InstanceStatus,   # 实例状态
        cwd: str,                 # 工作目录
        label: str | None = None, # 用户自定义标签
        created_at: str = "",     # 创建时间（ISO 8601 格式）
    ) -> None:
```

#### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `id` | str | 唯一标识符，16 位十六进制字符串 |
| `status` | InstanceStatus | 当前状态 |
| `cwd` | str | 工作目录路径 |
| `label` | str \| None | 可选标签，用于标识实例用途 |
| `created_at` | str | 创建时间，UTC ISO 8601 格式 |

#### 方法

##### to_dict() → dict[str, Any]

将实例摘要转换为字典。

**返回值：** JSON 可序列化的字典

**示例：**

```python
from pi_server.protocol import InstanceSummary

summary = InstanceSummary(
    id="abc123",
    status="online",
    cwd="/project",
    label="test-agent",
    created_at="2026-09-01T00:00:00Z"
)

print(summary.to_dict())
# 输出:
# {
#     "id": "abc123",
#     "status": "online",
#     "cwd": "/project",
#     "label": "test-agent",
#     "created_at": "2026-09-01T00:00:00Z"
# }
```

---

## 函数详解

### encode_message(msg: Any) → bytes

将任意消息编码为 JSONL 行（带换行符）。

**参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `msg` | Any | 要编码的消息（dict、list、自定义对象等） |

**返回值：** bytes - UTF-8 编码的 JSONL 行

**序列化规则（_default_encoder）：**

1. 如果对象有 `to_dict()` 方法，调用该方法
2. 如果对象有 `model_dump()` 方法（Pydantic），调用该方法
3. 否则转换为字符串

**示例：**

```python
from pi_server.protocol import encode_message

msg = {"type": "list"}
data = encode_message(msg)
print(data)  # b'{"type": "list"}\n'
```

---

### parse_line(line: str | bytes) → dict[str, Any]

解析一行 JSON。

**参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `line` | str \| bytes | JSONL 行 |

**返回值：** dict[str, Any] - 解析后的字典

**行为：**
- 自动处理 bytes → str 解码（UTF-8）
- 自动去除首尾空白（包括换行符）
- 如果 JSON 无效，抛出 `json.JSONDecodeError`

**示例：**

```python
from pi_server.protocol import parse_line

line = '{"type": "list"}'
result = parse_line(line)
print(result)  # {"type": "list"}
```

---

### ok_response(resp_type: str, **extra: Any) → dict[str, Any]

构造成功响应。

**参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `resp_type` | str | 响应类型标识 |
| `**extra` | Any | 额外的响应数据 |

**返回值：** 包含 `type`、`ok: true` 和额外数据的字典

**示例：**

```python
from pi_server.protocol import ok_response

resp = ok_response("spawn_result", instance={"id": "abc"})
print(resp)
# {"type": "spawn_result", "ok": true, "instance": {"id": "abc"}}
```

---

### error_response(error: str) → dict[str, Any]

构造错误响应。

**参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `error` | str | 错误信息 |

**返回值：** 包含 `type: "error"`、`ok: false` 和错误信息的字典

**示例：**

```python
from pi_server.protocol import error_response

resp = error_response("Instance not found")
print(resp)
# {"type": "error", "ok": false, "error": "Instance not found"}
```

---

## 请求/响应类型

### 上层请求类型

| type | 说明 | 必要参数 |
|------|------|----------|
| `spawn` | 创建实例 | `model_obj` |
| `list` | 列出实例 | 无 |
| `status` | 查询状态 | `instanceId` |
| `stop` | 停止实例 | `instanceId` |
| `rpc` | RPC 命令 | `instanceId`, `command` |
| `rpc_stream` | 流式连接 | `instanceId` |

### 上层响应类型

| type | 说明 | ok |
|------|------|-----|
| `spawn_result` | 创建成功 | true |
| `list_result` | 实例列表 | true |
| `status_result` | 实例状态 | true |
| `stop_result` | 停止成功 | true |
| `rpc_result` | RPC 响应 | true |
| `rpc_ready` | 流式就绪 | true |
| `error` | 错误 | false |

### 下层 RPC 命令

| type | 说明 | 参数 |
|------|------|------|
| `prompt` | 发送消息 | `text` 或 `message` |
| `get_state` | 获取状态 | 无 |
| `abort` | 中断任务 | 无 |

---

## 使用示例

### 完整通信示例

```python
import json
from pi_server.protocol import encode_message, parse_line, ok_response, error_response

# 编码请求
request = {"type": "list"}
encoded = encode_message(request)
print("请求:", encoded)
# 请求: b'{"type": "list"}\n'

# 构造成功响应
response = ok_response("list_result", instances=[])
encoded_response = encode_message(response)
print("响应:", encoded_response)
# 响应: b'{"type": "list_result", "ok": true, "instances": []}\n'

# 解析响应
parsed = parse_line(encoded_response)
print("解析:", parsed)
# 解析: {"type": "list_result", "ok": true, "instances": []}

# 错误响应
error = error_response("Invalid request")
print("错误:", encode_message(error))
# 错误: b'{"type": "error", "ok": false, "error": "Invalid request"}\n'
```

---

## 注意事项

1. **JSONL 格式：** 每行必须是有效的 JSON 对象，以 `\n` 结尾。
2. **类型安全：** 使用 `Literal` 类型定义状态，便于类型检查和 IDE 补全。
3. **序列化兼容：** `encode_message` 支持自定义序列化策略，方便扩展。
4. **字符编码：** 所有通信使用 UTF-8 编码。

## 文件依赖关系

```
protocol.py
    ↑        (被以下模块导入)
    ├── ipc.py           (消息编码/解析、响应构造)
    ├── supervisor.py    (实例状态类型、实例摘要)
    └── __init__.py      (导出公共 API)
```
