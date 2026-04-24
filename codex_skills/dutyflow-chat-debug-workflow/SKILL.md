---
name: dutyflow-chat-debug-workflow
description: Use when working in the DutyFlow repository and validating behavior through the local /chat debug path. Covers the current chat loop, what to inspect, and which tests to run.
---

# DutyFlow Chat Debug Workflow

Use this skill when validating DutyFlow behavior through the current local `/chat` debug path.

## Scope

- `/chat` is a local debug entry, not the Feishu frontend.
- This skill only describes the current CLI debug loop.
- Do not force `/chat` usage for purely static checks.

## Current `/chat` loop

The current minimal loop is:

`/chat` input
-> `AgentState`
-> model call
-> tool call extraction
-> `ToolRegistry`
-> `ToolRouter`
-> `PermissionGate`
-> `ToolExecutor`
-> `append_tool_results`
-> next model turn or stop

## Use cases

- Verify a new tool is visible to the model and can be triggered.
- Verify sensitive tools enter approval before execution.
- Verify multi-turn `AgentState` reuse inside the chat sub-session.
- Verify skill manifest injection and `load_skill` behavior.

## Ways to use it

1. Single turn: `/chat <input>`
2. Persistent sub-session:
   start with `/chat`
   continue in the `Chat>` prompt
   return with `/back`

## What to inspect

- `final_text`
- `stop_reason`
- `tool_results`
- `agent_state`
- approval prompt behavior for sensitive tools
- continuity of turns and context in multi-turn chat

## Suggested tests

Pick by impact:

- `test/test_cli_chat.py`
- `test/test_agent_loop.py`
- `test/test_agent_executor.py`
- `test/test_agent_permissions.py`
- `test/test_agent_cli_tools.py`

If the change affects skill injection, also run:

- `test/test_agent_skills.py`

## First files to inspect on failure

- `/chat` command parsing: `src/dutyflow/cli/main.py`
- loop and tool result append: `src/dutyflow/agent/loop.py`
- registry: `src/dutyflow/agent/tools/registry.py`
- execution and permission: `src/dutyflow/agent/tools/executor.py`
- tool context: `src/dutyflow/agent/tools/context.py`
