# Changelog

## [2026-11-27 Evening 3]

feat(web): 模型页支持「获取模型」；修复思考型模型空回复；思考流式显示并自动折叠

- 模型页新增「获取模型」按钮（`POST /api/models/{key}/remote`）：
  - 直接 `GET {base_url}/models`，展开显示该地址下全部模型（id + owned_by），带延迟与鉴权方式
  - 同样按 `Bearer` / 裸 `KEY` / `api-key` 依次尝试；再次点击可收起
  - 失败时展示状态码与上游错误原文
- 思考型模型（如 `meituan/LongCat-2.5`）回复为空的体验问题：
  - 实测：LongCat 把内容放在 `reasoning_content`（thinking），正文 `content` 偶发为空流（`stop_reason=None`），
    旧实现因 `text`/`thinking` 都为空而不生成展示消息，界面一片空白
  - 前端：本轮只有思考、没有正文时自动展开思考区，并插入黄色提示「该轮模型只返回了思考内容，没有生成回答」；
    连思考都没有时显示错误卡片「模型本轮没有返回任何内容」
  - 后端：`handle_user_message` 结束时检测本轮无 assistant 展示消息，写入 `WARNING` 日志便于排查
- 思考流式显示与折叠：
  - `thinking_delta` 实时追加到折叠区（可见的增长文本）
  - `thinking_end` 立即折叠（摘要显示「思考 N s」），不再等整轮结束；用户可随时展开
  - 历史回显同样默认折叠；只有思考无正文的轮次自动展开并附提示

## [2026-11-27 Evening 2]

feat(web): 新增「模型」页与连通性测试；修复消息时间戳与多模型识别

- 模型页（左侧新增「对话 / 模型」导航）：
  - 每行展示一个已加载模型：`供应商/模型名`、模型 ID、供应商、`BASE_URL`、是否配置 KEY
  - 行尾「测试连接」按钮，另提供「全部测试」（串行，避免打满上游限流）
  - 结果以徽章展示：可用时 `✓ 可用 · HTTP 200 · 28ms · Bearer`，失败时显示状态码与原因
    （`401 鉴权失败`、`403 无权限`、`404 接口不存在`、`429 触发限流`、`ERR 连接失败` 等），
    下方附上游返回的原始错误信息
- 后端 `POST /api/models/{key}/test`：
  - 先 `GET {base_url}/models`，按 `Authorization: Bearer` → 裸 `Authorization: KEY` →
    `api-key` 三种鉴权依次尝试，兼容「需要 Bearer」与「直接给 KEY」两类服务
  - `/models` 返回 404/405 时退回 `POST /chat/completions`（`max_tokens=1`）探测
  - 返回 `ok / status / auth / endpoint / latency_ms / detail / tried`
  - 超时可用 `PI_MODEL_TEST_TIMEOUT`（默认 15s）调整
- 修复：用户消息与回答时间戳相同
  - `Agent._normalize_input` 改用墙钟毫秒（原为 event loop 单调时钟，写进会话的值不可读）
  - `pi_coding_agent/sdk.py` 同类问题一并修正
  - 展示层 `_message_ts()` 优先用消息自带的 Unix 毫秒，避免整轮批量落盘时时间被压成同一时刻
- 修复：`.env` 配了多个 `PI_LLM_*` 只识别一个
  - `load_models()` 自动扫描 `PI_LLM_<组名>_*` 分组（如 `PI_LLM_MEITUAN_BASE_URL`），
    无需额外声明；未写的字段回落到全局 `PI_LLM_*`
  - 仍支持 `PI_MODELS` + `PI_MODEL_<KEY>_*` 显式列表
- `model_public_list()` 增加 `base_url` 与 `has_key`，供模型页展示

## [2026-11-27 Evening]

feat(web): 会话时间与 token 统计、步骤折叠、多模型选择、工具清单

- 会话列表：卡片显示「年月日-时分」，按最近交互（`updated_at`）降序排列，
  模型信息放进 hover 提示；生成中的会话标记「生成中」
- 消息统计：右侧每条消息显示「年月日-时分秒」；assistant 消息额外显示
  输入 / 输出 token 与缓存命中率（`cache_read / (input + cache_read)`）
  - `web/events.py`：`usage_dict()` 输出 `input / output / cache_read / cache_write /
    total / cache_hit_rate`，`message_end` 携带 `model` 与 `provider`
  - `web/sessions.py`：展示消息与 JSONL 重建都保留 `usage`、`model`
- 步骤折叠：一轮对话的思考过程 + 工具调用收进 `<details class="steps">`，
  摘要显示「执行了 N 个步骤」（失败时圆点变红），可逐项展开；回答气泡才是最终结果
  - 流式过程中自动展开，本轮结束后自动折叠；历史回显默认折叠
- 多模型：`.env` 支持 `PI_MODELS` + `PI_MODEL_<KEY>_*`（兼容旧 `PI_LLM_*`）
  - `load_env.py`：新增 `load_models()`，解析多模型并按 `PI_DEFAULT_MODEL` 排序
  - `web/config.py`：模型注册表 `MODELS / MODEL_INDEX`，对外 `model_public_list()`
  - `web/agent.py`：`ModelHolder` 持有当前模型，`get_api_key` 按会话取 key
  - `web/sessions.py`：`switch_model()` 热切换，模型键写入会话元数据，重启后恢复
  - `web/app.py`：新增 `GET /api/models`、WebSocket `set_model` 消息与 `model` 事件
  - 前端：composer 内模型下拉，显示「供应商/模型名」；生成中禁止切换
- 工具清单：点击「N 个工具」弹出清单（名称 + 代码名 + 说明），`/api/meta` 返回
  `tools` 描述数组
- 部署提示：新增 `.env.example` 多模型配置示例

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


