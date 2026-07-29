# Skill Template

Skills are instruction and metadata surfaces. They guide the LLM and UI but do not bypass the canonical tool runtime.

## Structure

```text
agent/skills/<name>/
  SKILL.md
  skill.yaml
```

## SKILL.md

```markdown
# Skill: My Feature

## When To Use
Use this skill when the user asks for the specific business outcome.

## Required Inputs
- workspace_id
- source file, device asset, or user-provided text

## Tool Strategy
- Prefer existing canonical tools.
- Cite retrieved evidence.
- Stop before destructive operations unless approval is granted.

## Output
Return a concise, verified answer with warnings separated from facts.
```

## skill.yaml

```yaml
skill_id: my_feature
name: My Feature
version: "1.0.0"
status: enabled
description: "Instruction layer for a current business capability."
related_tools:
  - workspace.file
  - text.analyze
safety_rules:
  - no_unapproved_destructive_actions
  - cite_sources_when_using_retrieval
```

`related_tools` must use canonical IDs.
