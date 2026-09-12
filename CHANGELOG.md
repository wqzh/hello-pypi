# Changelog



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


