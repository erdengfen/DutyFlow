---
name: dutyflow-internal-tool-workflow
description: Use when working in the DutyFlow repository and adding or changing an internal tool. Covers the manual contract/logic/registry flow, required runtime fields, and minimum verification steps.
---

# DutyFlow Internal Tool Workflow

Use this skill only when editing the DutyFlow repository's internal tool layer.

## Scope

- Applies to DutyFlow internal tools only.
- Does not define external tool discovery or long-term tool platform design.
- Does not replace `PLANS.md` stage goals; it only carries the already-stable workflow.

## Current registration flow

DutyFlow internal tools currently use explicit registration:

1. Add a contract file under `src/dutyflow/agent/tools/contracts/`.
2. Add a logic file under `src/dutyflow/agent/tools/logic/`.
3. Import the tool in `src/dutyflow/agent/tools/registry.py`.
4. Add it to `TOOL_REGISTRY`.
5. Keep execution on the standard control chain:
   `ToolRegistry -> ToolRouter -> PermissionGate -> ToolExecutor`.

## Required runtime fields

At minimum, declare these fields in the tool logic class:

- `is_concurrency_safe`
- `requires_approval`
- `timeout_seconds`
- `max_retries`
- `retry_policy`
- `idempotency`
- `degradation_mode`
- `fallback_tool_names`

## Constraints

- New tools default to internal tools during the current phase.
- The registry is not auto-discovery based; adding files alone is not enough.
- Permission and approval must happen before real execution.
- Retries only happen inside `ToolExecutor`.
- Validation errors, permission errors, approval rejections, routing errors, and non-idempotent side effects do not auto-retry by default.
- Tools should do deterministic actions; complex usage strategy belongs in skills or prompts, not in tool bodies.

## Minimum verification

After adding or changing a tool, check:

1. The runtime registry exposes it.
2. It can be triggered through `/chat` when needed.
3. Sensitive tools enter the approval path.
4. Matching tests are added or updated.
5. Self-test entry points for touched modules still run.

## Suggested tests

Pick by impact:

- `test/test_runtime_tool_registry.py`
- `test/test_agent_tools.py`
- `test/test_agent_executor.py`
- `test/test_agent_permissions.py`
- `test/test_agent_cli_tools.py`
- `test/test_agent_loop.py`

If the tool interacts with skills, also run:

- `test/test_agent_skills.py`
