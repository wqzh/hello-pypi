"""Web 入口。

启动::

    conda activate zwq-kit
    python hello_agent_loop_web.py

Web 相关实现全部位于 ``web/`` 目录：

    web/app.py       FastAPI 路由与 WebSocket
    web/sessions.py  会话状态与持久化
    web/events.py    事件协议
    web/agent.py     Agent 工厂与压缩
    web/config.py    路径 / 模型 / 工具配置
    web/static/      前端页面与资源
"""

from web.app import app, run

__all__ = ["app", "run"]

if __name__ == "__main__":
    run()