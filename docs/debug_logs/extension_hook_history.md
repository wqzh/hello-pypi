
```
[思考] 让我分析一下这个问题：

1. 用户问：能否在 hello_agent_loop.py 文件中添加 pi-agent 的 extension？
2. pi-agent 的 extension 原来是 TypeScript 的，但这是 Python 代码
3. 需要分析能否添加

我已经读取了：
- hello_agent_loop.py - 主程序
- pi_agent_core/agent_loop.py - agent 循环核心
- pi_agent_core/types.py - 类型定义
- pi_agent_core/agent.py - Agent 类
- pi_agent_core/harness/skills.py - skills 加载系统
- pi_agent_core/__init__.py - 模块导出

关键发现：

1. **Pi-agent 的 TypeScript 原版中，extension 是什么？**
   - 从 web_search 结果看，extension 是 TypeScript 文件，导出默认函数接收 ExtensionAPI
   - Extension 用于：添加可执行能力、hooks、集成、UI 行为
   - 例如 bash-spawn-hook.ts 可以调整 bash 命令执行

2. **当前 Python 代码中的钩子（hooks）机制：**
   - AgentLoopConfig 定义了多个钩子：
     - before_tool_call - 工具调用前
     - after_tool_call - 工具调用后
     - get_steering_messages - 获取 steering 消息
     - get_follow_up_messages - 获取 follow-up 消息
     - transform_context - 转换上下文
     - convert_to_llm - 转换为 LLM 消息
     - should_stop_after_turn - 每轮结束后是否停止
     - prepare_next_turn - 准备下一轮
   - AgentOptions 也暴露了类似钩子

3. **Python 代码中是否有 extension 机制？**
   - 从代码看，**没有明确的 ExtensionAPI 或 createExtension 接口**
   - 但有丰富的钩子系统，可以视为 extension 的 Python 实现方式

4. **能否在 hello_agent_loop.py 中实现 extension 功能？**
   - **可以！** 但需要理解：
     - Python 版没有 TypeScript 原版的 extension 加载机制（jiti 加载 .ts 文件）
     - 但可以通过现有的钩子（hooks）系统实现类似功能
     - 例如：在 AgentOptions 中传入 before_tool_call、after_tool_call 等钩子函数
     - 或者创建 Python 类的 extension 模块，通过导入方式扩展

5. **结论：**
   - 不能直接添加 TypeScript 的 extension
   - 但可以通过现有的钩子机制实现类似的 extension 功能
   - 需要在 hello_agent_loop.py 中定义自定义钩子函数，或创建 Python extension 模块
   - 如果要完全实现 TypeScript 版的 extension 系统，需要额外的开发工作（ExtensionAPI 类、extension 加载器等）

所以答案是：**是**，可以添加，但不是直接添加 TypeScript extension，而是通过 Python 钩子机制实现等效功能。

让我确认一下用户的问题：能否在 hello_agent_loop.py 文件添加 pi-agent 的 extension？

回答：
- 能（是），但需要以 Python 方式实现（钩子函数、自定义类）
- 不能直接加载 TypeScript extension
基于对代码的全面分析，我的结论是：

## **是**（但需要以 Python 方式实现）

### 详细分析：

**1. TypeScript 原版的 extension 机制：**
- Pi-agent TypeScript 原版使用 `ExtensionAPI` + `createExtension`，通过 jiti 动态加载 `.ts` extension 文件
- Extension 可以注入 hooks（如 `bash-spawn-hook.ts`）、工具、UI 行为等

**2. 当前 Python 代码的扩展能力：**

`hello_agent_loop.py` **已经具备了 extension 的核心能力**，通过现有的钩子（hooks）系统：

| TypeScript Extension | Python 等效方式 | 在 hello_agent_loop.py 中的位置 |
|---------------------|----------------|------------------------------|
| beforeToolCall hook | `before_tool_call` 钩子 | `AgentOptions()` |
| afterToolCall hook | `after_tool_call` 钩子 | `AgentOptions()` |
| transformContext | `transform_context` 钩子 | `AgentOptions()` |
| shouldStopAfterTurn | `should_stop_after_turn` 钩子 | `AgentOptions()` |
| getSteeringMessages | `get_steering_messages` | `AgentLoopConfig` |

**3. 如何在 hello_agent_loop.py 中添加 Python extension：**

```python
# 示例：在 create_agent() 中添加自定义 extension hooks

async def my_before_tool_call(ctx, cancel_event):
    # 在工具调用前执行自定义逻辑
    print(f"[Extension] 即将调用工具: {ctx['tool_call'].name}")
    return {}  # 不阻断

def my_after_tool_call(ctx, cancel_event):
    # 在工具调用后执行自定义逻辑
    print(f"[Extension] 工具完成: {ctx['tool_call'].name}")
    return {}

agent = Agent(AgentOptions(
    initial_state={...},
    before_tool_call=my_before_tool_call,  # ← 这就是 extension hook
    after_tool_call=my_after_tool_call,
))
```

**4. 限制：**
- ❌ 不能直接加载 TypeScript `.ts` extension 文件
- ✅ 可以通过 Python 钩子实现等效功能
- ✅ 可以创建 Python extension 模块（如 `extensions/my_extension.py`），通过 import 引入

### 总结：
**能添加**，但必须用 Python 钩子系统实现，而不是直接移植 TypeScript extension。当前代码架构已经支持这种扩展模式。 [思考完毕]


```