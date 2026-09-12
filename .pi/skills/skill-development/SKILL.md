---
name: skill-development
description: 为 agent 创建和注册 skill 的指南 — 结构、格式、最佳实践
---

Skill 是教 agent 如何处理特定任务类型的 markdown 文件。

## Skill 存放位置

- 项目级：`.pi/skills/<skill-name>/SKILL.md`
- 用户级：`~/.pi/agent/skills/<skill-name>/SKILL.md`

重名时项目级覆盖用户级。

## 文件格式

每个 skill 是一个带有 YAML frontmatter 的 `SKILL.md` 文件：

```markdown
---
name: my-skill
description: 简短描述供 agent 发现。1-2 句话，足够具体以匹配正确的任务。
---

Skill 内容：详细说明、示例、规则、引用。
使用清晰的 markdown。当任务匹配你的描述时，agent 会读取此文件。
```

## 字段规则

- **name**：kebab-case，仅小写字母、数字、连字符。不能有首尾连字符或连续连字符。最多 64 字符。在所有 skill 中必须唯一。

- **description**：必填。最多 1024 字符。这是 agent 决定是否使用你的 skill 的依据。具体说明它处理哪些任务。

  差的描述："帮助写作。"
  好的描述："用中英文编写和编辑技术文档。当用户询问文档、README 或技术写作时使用。"

## 内容最佳实践

1. 先说明触发条件："在...时使用此 skill"
2. 清晰列出规则或步骤，程序性内容使用编号列表
3. 必要时包含示例
4. 引用相对路径相对于 skill 目录：
   ```markdown
   参见 templates/ 目录下的示例文件。
   # 解析为：.pi/skills/my-skill/templates/
   ```

## 工作原理

启动时，`hello_agent_loop.py` 加载所有 skill 并注入 system prompt：

```xml
<available_skills>
  <skill>
    <name>my-skill</name>
    <description>...</description>
    <location>/abs/path/to/SKILL.md</location>
  </skill>
</available_skills>
```

当用户的请求匹配 skill 描述时，agent 会 `read` 完整的 SKILL.md 文件获取指令。

## 测试 skill

创建 skill 后：
1. 重启 agent（`python hello_agent_loop.py`）
2. 启动时看到 `[INFO] Loaded N skill(s)`
3. 问一个匹配 skill 描述的问题
4. 检查调试日志确认 agent 读取了 skill 文件
