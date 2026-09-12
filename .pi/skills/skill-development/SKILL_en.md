---
name: skill-development
description: Guide for creating and registering skills for this agent — structure, format, best practices
---

Skills are markdown files that teach the agent how to handle specific task types.

## Where skills live

- Project-level: `.pi/skills/<skill-name>/SKILL.md`
- User-level: `~/.pi/agent/skills/<skill-name>/SKILL.md`

Project-level overrides user-level on name collision.

## File format

Every skill is a single `SKILL.md` with YAML frontmatter:

```markdown
---
name: my-skill
description: Short description for agent discovery. 1-2 sentences, specific enough to match the right tasks.
---

Skill content: detailed instructions, examples, rules, references.
Use clear markdown. The agent reads this when a task matches your description.
```

## Field rules

- **name**: kebab-case, lowercase letters, digits, hyphens only. No leading/trailing/consecutive hyphens. Max 64 chars. Must be unique across all skills.

- **description**: Required. Max 1024 chars. This is how the agent decides when to use your skill. Be specific about what tasks it handles.

  Bad: "Helps with writing."
  Good: "Write and edit technical documentation in Chinese and English. Use when user asks for docs, READMEs, or technical writing."

## Content best practices

1. Start with what triggers the skill: "Use this skill when..."
2. List rules or steps clearly, use numbered lists for procedures.
3. Include examples when helpful.
4. Reference relative paths against the skill directory:
   ```markdown
   See templates/ for example files.
   # resolves to: .pi/skills/my-skill/templates/
   ```

## How it works

At startup, `hello_agent_loop.py` loads all skills and injects them into the system prompt:

```xml
<available_skills>
  <skill>
    <name>my-skill</name>
    <description>...</description>
    <location>/abs/path/to/SKILL.md</location>
  </skill>
</available_skills>
```

When a user's request matches a skill description, the agent will `read` the full SKILL.md file to get instructions.

## Testing a skill

After creating a skill:
1. Restart the agent (`python hello_agent_loop.py`)
2. You'll see `[INFO] Loaded N skill(s)` on startup
3. Ask a question that matches the skill's description
4. Check debug logs to confirm the agent read the skill file
