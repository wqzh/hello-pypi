import asyncio


from pi.pi_ai import Model, TextContent, UserMessage
from pi.pi_agent_core import Agent, AgentOptions, AgentToolResult
from load_env import load_env_config

API_KEY, MODEL_CONFIG = load_env_config()


## 注册模型
### model = Model(**MODEL_CONFIG)
model = Model(
    id=MODEL_CONFIG['id'],
    name=MODEL_CONFIG['name'],  
    base_url=MODEL_CONFIG['base_url'],  
    
    api=MODEL_CONFIG['api'],       # 用内置的 OpenAI/Anthropic 兼容实现
    provider=MODEL_CONFIG['provider'],   # 自定义名称，任意字符串
    input=["text"],
    context_window=MODEL_CONFIG['context_window'],
    max_tokens=MODEL_CONFIG['max_tokens'],
    reasoning=MODEL_CONFIG['reasoning'],    # 启用思考过程
)
print(f"[INFO] 模型配置: {model.id} @ {model.base_url}")


## 注册可以使用的工具
class WeatherTool:
    name = "get_weather"
    description = "查询天气"
    label = "Weather"
    parameters = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
    async def execute(self, tool_call_id, params, cancel_event=None, on_update=None):
        # 需替换成调用真实的 tool, 通过访问真实的api 获取天气
        
        city_weather_map = {
            '北京': '晴转多云 25°C',
            '天津': '小雨 20~23°C',
        }
        city = params['city']
        if city in city_weather_map:
            weather_result = city_weather_map[city]
        else:
            weather_result = f"{city} 天气，我猜可能是 小雪，-5°C"

        print(f"\n[工具执行] weather_result")
        return AgentToolResult(content=[TextContent(text=weather_result)])


async def create_agent():
    """创建单例 agent。该 agent 会在内部维护历史对话上下文 (state.messages)。"""
    return Agent(AgentOptions(
        initial_state={
            "system_prompt": "你是一个智能助手，你必须用用户提问对应的语种进行思考和回答！。你可以调用你掌握的工具来辅助自己。",
            "model": model,
            "tools": [WeatherTool()],  # 告诉模型可以使用哪些工具
            "thinking_level": None  # "low",  # 开启思考过程，好像无效？
        },
        get_api_key=lambda p: API_KEY,
    ))


async def main():
    print("=" * 50)
    print("欢迎使用智能助手！输入 'quit' 或 'exit' 退出。")
    print("=" * 50)

    # 1. 在 while 循环之外只创建一次 agent
    #    Agent 内部会自动维护 state.messages，记录所有历史对话上下文
    agent = await create_agent()

    # 2. 定义事件监听器（只需订阅一次）
    last_msg_text = ""

    def on_event(ev, sig):
        nonlocal last_msg_text

        # message_update：流式文本增量
        if ev.type == "message_update":
            if ev.assistant_message_event:
                aev = ev.assistant_message_event
                if aev.type == "text_delta":
                    print(aev.delta, end="", flush=True)
                    last_msg_text += aev.delta
                elif aev.type in ("text_start", "text_end"):
                    pass
                elif aev.type == "thinking_start":
                    print("\n[思考] ", end="", flush=True)
                elif aev.type == "thinking_delta":
                    print(aev.delta, end="", flush=True)
                elif aev.type == "thinking_end":
                    print(" [思考完毕]\n")
            elif ev.message and ev.message.role == "assistant" and ev.message.content:
                for block in ev.message.content:
                    if block.type == "text" and block.text:
                        print(block.text, end="", flush=True)
                        last_msg_text = block.text
            return

        # message_end：本轮消息结束
        if ev.type == "message_end":
            # 打印错误信息（如果有）
            if ev.message and getattr(ev.message, 'error_message', None):
                print(f"\n[ERROR] {ev.message.error_message}", flush=True)
            # 兜底输出完整内容
            if ev.message and ev.message.role == "assistant" and ev.message.content:
                if not last_msg_text:
                    for block in ev.message.content:
                        if block.type == "text" and block.text:
                            print(block.text, end="", flush=True)
            print()
            last_msg_text = ""
            return

        # tool_execution_start
        if ev.type == "tool_execution_start":
            print(f"\n[调用工具] {ev.tool_name}({ev.args})", flush=True)
            return

        # tool_execution_end
        if ev.type == "tool_execution_end":
            if ev.result:
                for block in ev.result.content:
                    if block.type == "text":
                        print(f"\n[工具结果] {block.text}", flush=True)
            return

    agent.subscribe(on_event)

    # 3. 多轮对话循环：每次调用 agent.prompt() 时，
    #    agent 会自动把历史消息 (state.messages) 传入 agent_loop，
    #    所以 LLM 能看见完整的上下文。
    while True:
        try:
            user_input = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "退出"):
            print("再见！")
            break

        print("助手: ", end="", flush=True)
        try:
            # agent.prompt() 会把用户消息追加到 state.messages，
            # 并在内部创建 context snapshot (含所有历史消息) 传给 agent_loop
            await agent.prompt(user_input)

            # 调试：查看当前历史消息数量
            print(f"\n[DEBUG] 当前历史消息数: {len(agent.state.messages)}", flush=True)
        except RuntimeError as e:
            # Agent 正在处理中时的提示
            print(f"\n[DEBUG] {e}", flush=True)
        except Exception as e:
            print(f"\n[DEBUG] prompt error: {e}", flush=True)
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
