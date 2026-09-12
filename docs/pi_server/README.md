# pi_server 模块文档（新人入门教程）

## 📖 目录

1. [概述](#概述)
2. [目录结构](#目录结构)
3. [快速开始](#快速开始)
4. [模块详解](#模块详解)
   - [config.py - 配置与路径管理](config.md)
   - [protocol.py - RPC 协议定义](protocol.md)
   - [ipc.py - IPC 服务端与客户端](ipc.md)
   - [supervisor.py - 实例管理器](supervisor.md)
5. [架构图](#架构图)
6. [通信协议](#通信协议)
7. [常见使用场景](#常见使用场景)
8. [与上游 TypeScript 版本的区别](#与上游-typescript-版本的区别)

---

## 概述

`pi_server` 是一个 **实验性 agent 服务化层**，提供 Unix socket + JSONL 协议来管理多个 agent 实例。

**核心功能：**
- 通过 Unix socket 提供网络接口
- 支持创建、查询、停止多个 agent 实例
- 提供 RPC（远程过程调用）能力
- 支持双向流式通信（rpc_stream）

**上游对应：** 对应 `earendil-works/pi` 项目的 `packages/server`（TypeScript 版），当前同步版本为 v0.84.1。

---

## 目录结构

```
pi/pi_server/
├── __init__.py      # 模块入口，导出公共 API
├── config.py        # 路径与配置管理
├── protocol.py      # RPC 协议定义（类型、编码）
├── ipc.py           # IPC 服务端/客户端实现
└── supervisor.py    # agent 实例管理器
```

---

## 快速开始

### 1. 启动服务

```python
import asyncio
from pi_server import serve

# 启动常驻服务，监听 Unix socket
asyncio.run(serve())
```

服务默认监听路径：`~/.pi/server/server.sock`

### 2. 发送请求（客户端）

```python
import asyncio
from pi_server import send_request

async def main():
    # 列出所有实例
    response = await send_request({"type": "list"})
    print(response)

asyncio.run(main())
```

### 3. 环境配置

可以通过环境变量覆盖默认路径：

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `PI_SERVER_DIR` | server 目录路径 | `~/.pi/server` |
| `PI_CONFIG_DIR` | config 目录路径 | `~/.pi` |

---

## 模块详解

各模块的详细文档请点击链接查看：

- [config.py](config.md) - 路径与配置管理
- [protocol.py](protocol.md) - RPC 协议定义
- [ipc.py](ipc.md) - IPC 服务端与客户端
- [supervisor.py](supervisor.md) - 实例管理器

---

## 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                        客户端 (CLI / SDK)                    │
│                     发送 JSONL 请求                          │
└────────────────────────┬────────────────────────────────────┘
                         │
                         │ Unix Socket (server.sock)
                         │
┌────────────────────────▼────────────────────────────────────┐
│                        ipc.py                               │
│                    (IPC 服务端)                              │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  _handle_connection()                                   │ │
│  │  ├─ 普通请求 → handle_request() → 一次性响应             │ │
│  │  └─ rpc_stream → _handle_rpc_stream() → 双向流          │ │
│  └────────────────────────────────────────────────────────┘ │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                     supervisor.py                            │
│                   (实例管理器 Supervisor)                     │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  AgentInstance × N                                      │ │
│  │  ├─ spawn_instance()    → 创建 agent                    │ │
│  │  ├─ stop_instance()     → 停止 agent                    │ │
│  │  ├─ handle_rpc()        → 转发 RPC 命令                 │ │
│  │  ├─ open_rpc_stream()   → 打开事件流订阅                │ │
│  │  └─ list_instances()    → 查询实例列表                  │ │
│  └────────────────────────┬───────────────────────────────┘ │
└───────────────────────────┼─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│                    CodingAgent (pi_coding_agent)              │
│                    (被管理的 agent 实例)                       │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  Model (pi_ai)                                          │ │
│  │  ├─ prompt()    → 发送消息                               │ │
│  │  ├─ abort()     → 中断执行                               │ │
│  │  └─ subscribe() → 订阅事件流                             │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## 通信协议

### 传输层
- **传输方式：** Unix socket
- **数据格式：** JSONL（JSON Lines，每行一个 JSON 对象，以 `\n` 分隔）
- **编码：** UTF-8

### 上层请求类型

| 类型 | 说明 | 参数 |
|------|------|------|
| `spawn` | 创建新 agent 实例 | `cwd`, `label`, `model_obj`, `api_key` |
| `list` | 列出所有实例 | 无 |
| `status` | 查询实例状态 | `instanceId` |
| `stop` | 停止实例 | `instanceId` |
| `rpc` | 发送 RPC 命令 | `instanceId`, `command` |
| `rpc_stream` | 建立双向流式连接 | `instanceId` |

### 下层 RPC 命令类型

| 类型 | 说明 |
|------|------|
| `prompt` | 发送消息给 agent |
| `get_state` | 获取 agent 状态 |
| `abort` | 中断当前任务 |

---

## 常见使用场景

### 场景 1：从 CLI 管理 agent

CLI 工具通过 `send_request()` 与 pi_server 通信，实现：

```bash
pi instance spawn --cwd=/project
pi instance list
pi instance stop <id>
pi rpc <id> "help me fix this bug"
```

### 场景 2：作为 SDK 服务层

在 IDE 插件或 Web 应用中，通过 Unix socket 与本地 pi_server 通信，实现多 agent 实例的并发管理。

### 场景 3：流式交互

使用 `rpc_stream` 建立持久连接，实时接收 agent 的事件推送（如 token 流、工具调用、状态变更）。

---

## 与上游 TypeScript 版本的区别

| 方面 | TypeScript 版（上游） | Python 版（当前） |
|------|----------------------|-------------------|
| 进程模型 | spawn 子进程 `pi --mode rpc` | 进程内直接管理 `CodingAgent` 实例 |
| 进程间通信 | JSONL over stdio | 直接方法调用 |
| 流式支持 | 通过 stdio 管道 | 通过事件订阅 + asyncio.Queue |
| 适用场景 | CLI 工具、独立服务 | SDK、嵌入式服务 |

---

## 全局 API 导出

```python
from pi_server import (
    __version__,        # "0.84.1"
    __upstream_ref__,   # "earendil-works/pi@v0.84.1"

    # 核心功能
    serve,              # 启动服务
    send_request,       # 发送请求（客户端）
    handle_request,     # 处理请求（服务端）

    # Supervisor
    supervisor,         # 全局单例
    Supervisor,         # 类
    AgentInstance,      # 实例类

    # 协议
    InstanceStatus,     # 状态类型
    InstanceSummary,    # 实例摘要类
    encode_message,     # 编码消息
    parse_line,         # 解析消息

    # 配置
    get_socket_path,    # 获取 socket 路径
    get_server_dir,     # 获取 server 目录
)
```

---

## 注意事项

1. **单例模式：** `supervisor` 是全局单例，整个进程共享。
2. **持久化：** 实例状态会写入 `~/.pi/server/instances.json`，用于跨重启可见性。
3. **socket 清理：** 服务启动时会删除旧的 socket 文件，关闭时也会清理。
4. **重启恢复：** server 重启后，所有 `online`/`starting` 状态的实例会被标记为 `stopped`。
