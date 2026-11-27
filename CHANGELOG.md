# Changelog

## [2026-11-27 Afternoon]

refactor(web): 统一 Web 代码到 `web/` 目录，修复工具调用展示与历史回显

- 目录重组（`hello_agent_loop_web.py` 仅保留启动入口）：
  - `web/app.py`：FastAPI 路由、WebSocket、Origin 校验
  - `web/sessions.py`：会话状态、展示消息、JSONL 持久化、多连接广播
  - `web/events.py`：Agent 事件 → 前端 JSON 协议
  - `web/agent.py`：Agent 工厂、上下文压缩
  - `web/config.py`：路径 / 模型 / 工具 / 提示词配置
  - `web/static/`：`index.html`、`app.css`、`app.js`
- 修复：
  - 工具块判别改为 `toolCall` / `toolResult`（原 `tool_call` / `tool_result` 永不命中，历史工具卡不出现）
  - 事件协议携带 `tool_call_id`、`duration_ms`、`is_error`，并转发 `tool_execution_update`
  - 每轮结束把完整 transcript（含 toolCall / toolResult）写回 JSONL，刷新/重启后可恢复
  - 一个会话支持多个 WebSocket 订阅，断线后自动重连并对齐历史
  - 错误 / 中断时清空流式缓冲，避免下一轮回答串台
  - 删除会话时终止 Agent 并清理文件，避免幽灵会话
  - 历史时间改用文件 mtime，标题写入 label 持久化
  - 上下文压缩切点保留 toolResult 配对，压缩结果落盘为 compaction 条目
  - 天气 / 联网搜索工具增加超时，避免工具卡长期停在「运行中」
- UI（参考 NanoMate ExpertWorkbench）：
  - 欢迎页 + 快捷卡片、头像气泡、可折叠思考过程
  - ToolRunCard：状态徽章、耗时、参数 chips、结果折叠 / 复制、天气专用卡
  - 圆角 composer + 圆形发送按钮 + 停止生成
  - 助手文本轻量 Markdown 渲染（先转义，支持加粗 / 行内代码 / 代码块）
  - 自动滚动仅在贴近底部时生效；窄屏（<1080px）隐藏侧栏

## [2026-09-17 Noon]
feat(main-ui): overhaul web session handling and chat UI

feat: 全面重构 Web 会话处理与聊天界面

- hello_agent_loop_web.py:
  - 新增通用提取工具 _get / _extract_blocks / _extract_tool_calls / _extract_tool_results，兼容属性访问与 dict 访问及多种 content 形态
  - 重写 load_session：健壮版历史加载，支持 tool_call/tool_result 块解析与匹配
  - WebSession 内聚 on_event 回调，移除 ws_endpoint 中闭包事件处理
  - 新增工具调用消息展示（tool_start/tool_end）
  - sessions 列表新增 preview（首条用户消息摘要）字段
  - 修复 thinking_end 后文本被吞的 bug（bufferedText 机制）

- index.html:
  - 会话列表显示消息预览 + 创建时间
  - 新增工具消息渲染（appendTool），历史工具块正常回显
  - thinking/thinking_end 逻辑修复，thinking 结束后文本正确追加到 assistant 消息
  - CSS 精简，清理冗余注释

- imgs/pypi-main-ui.png: 更新 UI 截图


## [2026-09-12 Night]

feat(compaction): integrate context compaction and basic file-processing tools
feat: 集成上下文压缩功能和基础文件读写查操作

- hello_agent_loop.py:
  - 新增上下文压缩逻辑（CompactionSettings），支持阈值触发、保留tokens、保留最近消息
  - prompt运行后自动检测token消耗，超过阈值时触发摘要压缩
  - 导入并注册 PI_BUILTIN_TOOLS（BashTool, EditTool, FindTool, GrepTool, LsTool, ReadTool, WriteTool）

- pi/pi_agent_core/harness/compaction.py: 修复导入路径 (pi_ai → pi.pi_ai)
- pi/pi_ai/providers/openai_provider.py: 环境变量 OPENAI_API_KEY 改为 PI_OPENAI_API_KEY
- .env.example: 补充 PI_OPENAI_API_KEY 配置说明（用于压缩）
- README.md: 修正错别字
- BUG: TODO: prompt方法结束后才压缩。中途某一步可能就超出token限制了


## [2026-09-12 Noon]

### Added

feat(skills): integrate Skill loading system with 3 initial skills

feat: 集成 Skill 加载系统和3个初始 skill
- 集成 Skill 加载系统：启动时自动加载项目级 (`.pi/skills/`) 和用户级 (`~/.pi/agent/skills/`) skills，注入 system prompt
- 新增 skill: `tool-development` — AgentTool 实现指南（schema、返回格式、错误处理）
- 新增 skill: `skill-development` — Skill 创建和注册指南
- 新增 skill: `life-hacks` — 烹饪、旅行、健康、购物生活建议



## [2026-09-12 AM]

### Added

feat(tools): add weather and web search tools

feat(tools): 新增天气查询和网页搜索工具
- pi/pi_tools/weather.py: 
  - WeatherDemoTool: 示例工具（硬编码天气数据）
  - GetCityWeatherTool: 通过 wttr.in API 查询实时天气
  - GetCityWeatherTool_v1: 和风天气 API 实现（弃用，留作参考）
- pi/pi_tools/web_search.py: WebSearchTavilyTool 通过 Tavily API 联网搜索
- hello_agent_loop.py: 注册新工具
- .env.example / requirements.txt: 新增环境变量说明和依赖
- debug_logs/tool_call_history.md: 工具调用日志示例


## [2026-09-11] First Commit


