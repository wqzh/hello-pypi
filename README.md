

# 简介
一个基于Python实现的pi-agent-core核心代码的二次开发项目。可自行注册业务所需的tool共agent调用.

读者需自行评估python和typescript的运行性能差异。


本仓库的实现有：
- [X] pi统一打包本地源码安装，可以直接`from pi.py_ai import *`使用所需的包
- [X] 增加`docs/`目录，python代码解读
- [X] 增加一个可以多轮运行的示例, 包含模型注册、工具注册 `hello_agent_loop.py`
- [] 更多真实的Tool的注册
- [] Skill的加载
- [] Extension加载
- [] 运行时compact
- [] session持久化，恢复


# 立刻运行agent_loop项目
``` shell
git clone https://github.com/wqzh/hello-pypi.git && cd hello-pypi

# 本地源码安装，-e 参数，允许你修改'pi/'目录下的源码，立即生效
pip install -e pi

cp .env.example .env
# 必须修改.env中3个变量： PI_LLM_BASE_URL、PI_LLM_MODEL_ID 、PI_LLM_API_KEY

# 单agent,多轮对话
python hello_agent_loop.py

```



# 本地源码安装
```shell
# 重新安装（修改依赖后）DEBUG mode：
pip install -e pi

#生产部署：去掉 -e 参数
pip install pi

# 卸载：
pip uninstall pi

```


# 致谢
- 本仓库参考了 [encyc/pi-py 仓库](https://github.com/encyc/pi-py) pi(v0.84.1)核心模块 的python实现.

- https://github.com/earendil-works/pi
- https://github.com/encyc/pi-py

# 许可证
MIT

