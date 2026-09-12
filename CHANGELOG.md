# Changelog


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


