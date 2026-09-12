# config.py - 配置与路径管理

## 📖 目录

- [功能概述](#功能概述)
- [常量定义](#常量定义)
- [API 详解](#api-详解)
- [使用示例](#使用示例)
- [注意事项](#注意事项)

---

## 功能概述

`config.py` 负责管理 pi_server 的路径配置，包括：

- server 目录路径
- Unix socket 文件路径
- 实例状态持久化文件路径

**上游对应：** `packages/server/src/config.ts`

---

## 常量定义

| 常量 | 值 | 说明 |
|------|-----|------|
| `CONFIG_DIR_NAME` | `".pi"` | 默认配置目录名 |
| `ENV_SERVER_DIR` | `"PI_SERVER_DIR"` | 环境变量名：自定义 server 目录 |
| `ENV_CONFIG_DIR` | `"PI_CONFIG_DIR"` | 环境变量名：自定义 config 目录 |

---

## API 详解

### get_server_dir() → str

获取 server 目录的绝对路径。

**优先级顺序：**

```
1. 环境变量 $PI_SERVER_DIR（最高优先级）
2. 环境变量 $PI_CONFIG_DIR + "/server"
3. 默认 "~/.pi/server"（最低优先级）
```

**参数：** 无

**返回值：** str - server 目录的绝对路径

**示例：**

```python
from pi_server.config import get_server_dir

print(get_server_dir())
# 默认输出: /Users/username/.pi/server
```

---

### get_socket_path() → str

获取 Unix socket 文件的完整路径。

**参数：** 无

**返回值：** str - socket 文件路径（server 目录 + `server.sock`）

**默认路径：** `~/.pi/server/server.sock`

**示例：**

```python
from pi_server.config import get_socket_path

print(get_socket_path())
# 输出: /Users/username/.pi/server/server.sock
```

---

### get_instances_path() → str

获取实例状态持久化文件的完整路径。

**参数：** 无

**返回值：** str - instances.json 文件路径

**默认路径：** `~/.pi/server/instances.json`

**说明：** 该文件存储所有 agent 实例的状态信息，用于 CLI 查询和跨重启可见性。

**示例：**

```python
from pi_server.config import get_instances_path

print(get_instances_path())
# 输出: /Users/username/.pi/server/instances.json
```

---

### ensure_server_dir() → None

确保 server 目录存在，如果不存在则创建。

**参数：** 无

**返回值：** None

**行为：**
- 检查 server 目录是否存在
- 如果不存在，递归创建所有父目录
- 如果已存在，不执行任何操作

**示例：**

```python
from pi_server.config import ensure_server_dir

ensure_server_dir()  # 确保 ~/.pi/server 存在
```

---

## 使用示例

### 示例 1：查看当前配置

```python
from pi_server import (
    get_server_dir,
    get_socket_path,
    get_instances_path,
)

print(f"Server 目录: {get_server_dir()}")
print(f"Socket 路径: {get_socket_path()}")
print(f"实例文件: {get_instances_path()}")
```

输出：

```
Server 目录: /Users/username/.pi/server
Socket 路径: /Users/username/.pi/server/server.sock
实例文件: /Users/username/.pi/server/instances.json
```

### 示例 2：自定义 server 目录

```python
import os

# 方法 1：通过环境变量（推荐）
os.environ["PI_SERVER_DIR"] = "/custom/path/to/pi_server"

# 方法 2：直接修改环境变量后再导入
from pi_server.config import get_socket_path
print(get_socket_path())  # 输出: /custom/path/to/pi_server/server.sock
```

### 示例 3：检查实例状态文件

```python
import json
from pi_server.config import get_instances_path

path = get_instances_path()
if os.path.exists(path):
    with open(path) as f:
        data = json.load(f)
    print(f"当前实例数: {len(data.get('instances', []))}")
    for inst in data.get("instances", []):
        print(f"  - {inst['id']}: {inst['status']}")
```

---

## 注意事项

1. **路径展开：** 所有路径中的 `~` 会被正确展开为 home 目录。
2. **跨平台：** 虽然当前使用 Unix socket，但路径处理函数是跨平台的（使用 `os.path`）。
3. **环境变量优先级：** 如果同时设置了 `PI_SERVER_DIR` 和 `PI_CONFIG_DIR`，`PI_SERVER_DIR` 优先级更高。
4. **权限问题：** server 目录需要有读写权限，否则 socket 创建和实例持久化会失败。

## 文件依赖关系

```
config.py
    ↑        (被以下模块导入)
    ├── ipc.py           (获取 socket 路径、确保目录存在)
    ├── supervisor.py    (获取 instances 路径、确保目录存在)
    └── __init__.py      (导出公共 API)
```
