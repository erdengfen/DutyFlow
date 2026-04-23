---
name: skill_creator
description: 指导模型整理并创建新的 DutyFlow skill，写入动作必须通过 create_skill 审批链路完成。
---

# Skill Creator

本 skill 用于把用户提出的能力扩展需求整理为标准 `SKILL.md`，并在需要写入文件时引导模型调用内部工具 `create_skill`。

## 使用边界

- 只负责创建新的 skill 文档，不创建 tools。
- 不修改 `ToolRegistry`、`SkillRegistry` 或其它 Python 代码。
- 不覆盖已有 skill；如果目标 skill 已存在，应停止并向用户说明。
- 不绕过权限系统；真正写入必须通过 `create_skill` 的审批链路。

## 推荐工具

- `load_skill`：当需要读取已有 skill 正文作为参考时使用。
- `create_skill`：当用户确认要创建新 skill 时使用。

## 工作流程

1. 先确认新 skill 的 `name`、`description` 和目标用途。
2. 如果用户需求不清晰，先用自然语言澄清，不要直接写入。
3. 生成正文时只写可执行的使用说明、边界、步骤和注意事项。
4. 确认正文不要求模型绕过权限、审批或工具执行层。
5. 调用 `create_skill`，参数为 `name`、`description`、`body`。
6. 等待工具结果，再向用户汇报创建路径和后续使用方式。

## `create_skill` 参数要求

- `name`：小写字母、数字、下划线或连字符，建议使用短横线或下划线分词。
- `description`：一句话说明 skill 的用途。
- `body`：完整正文，不包含 frontmatter。

## 输出要求

创建前，先向用户简要说明将创建的 skill 名称和用途。

创建后，汇报：
- skill 名称
- 创建路径
- 后续如何通过 `/chat` 或 `load_skill` 验证
