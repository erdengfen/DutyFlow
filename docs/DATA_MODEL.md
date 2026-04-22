# DATA_MODEL.md

本文档记录 DutyFlow Demo 期的数据结构、文件存储约束和关键业务工具参数。

本项目使用本地 Markdown 文件作为主要持久化介质。Markdown 文件必须同时满足：

- 用户可读、可检查、可追溯。
- 工具可按稳定字段精准检索。
- 关键判断可回溯到具体文件和具体记录。
- 不存放密钥、token、api_key 或飞书认证敏感值。

## 1. 通用约束

### 1.1 文件格式

所有结构化 Markdown 文件采用以下形式：

```md
---
schema: dutyflow.<record_type>.v1
id: <stable_id>
updated_at: <ISO-8601>
---

# 可读标题

## Summary

面向人工检查的简短说明。

## Details

面向工具裁剪上下文的稳定段落。
```

约束：

- frontmatter 使用简单 YAML 子集，便于无额外依赖解析。
- `schema` 必须存在，用于区分记录类型和版本。
- `id` 必须稳定，不随显示名称变化。
- `updated_at` 使用 ISO-8601 字符串。
- 文件正文用于人工阅读和上下文片段抽取。
- 工具优先读取 frontmatter 和指定标题段落，不允许默认读取整份文件。

### 1.2 ID 命名

建议使用以下稳定 ID 前缀：

- `evt_`：事件记录
- `contact_`：联系人
- `source_`：信息来源
- `task_`：任务
- `approval_`：审批
- `ctx_`：上下文摘要
- `trace_`：决策留痕
- `tool_`：工具调用记录

ID 可以由时间戳、来源标识或短随机串组成，但创建后不得因名称变化而改变。

### 1.3 时间字段

统一字段：

- `created_at`
- `updated_at`
- `received_at`
- `decided_at`
- `completed_at`

时间统一使用 ISO-8601。不要混用本地自然语言时间。

### 1.4 文件路径

Demo 期数据文件主要位于：

```text
data/
  identity/
  state/
  events/
  contexts/
  approvals/
  tasks/
  reports/
  logs/
  plans/
```

源码不得把记录散落写入未约束目录。

## 2. Agent State

-Agent State 是运行控制面，不是模型自由判断结果。
-权重 skill、身份工具、来源工具补充的信息，都必须回到 Agent State 后再参与最终控制决策。
-当前阶段 `Agent State` 仍以内存结构为主；本节定义的数据模型用于约束内存结构、测试序列化和后续恢复能力，不代表当前已经全部落盘。

文件位置：

```text
data/state/agent_state.md
```

### 2.1 Frontmatter

```yaml
schema: dutyflow.agent_state.v1
id: agent_state_local_user
updated_at: 2026-04-16T00:00:00+08:00
current_model: ""
permission_mode: default
active_task_ids: ""
waiting_approval_task_ids: ""
last_event_id: ""
```

字段说明：

- `current_model`：当前模型配置名，不保存 api_key。
- `permission_mode`：`default`、`plan`、`auto` 之一。
- `active_task_ids`：当前正在推进的任务。
- `waiting_approval_task_ids`：等待用户飞书端审批的任务。
- `last_event_id`：最近处理的事件 ID。

### 2.2 正文结构

```md
# Agent State

## Runtime

- status:
- current_model:
- permission_mode:
- last_event:

## Task Control

| task_id | weight_level | attempt_count | approval_status | retry_status | next_action |
|---|---|---:|---|---|---|

## Recovery

| scope_id | continuation_attempts | compact_attempts | transport_attempts | tool_error_attempts | latest_interruption_reason | latest_resume_point |
|---|---:|---:|---:|---:|

## Recovery Scopes

| recovery_id | scope_type | scope_id | status | failure_kind | interruption_reason | strategy | attempt_count | next_retry_at | resume_point |
|---|---|---|---|---|---|---|---:|---|---|

## Notes

人工可读运行备注。
```

### 2.3 控制字段

任务控制面最少记录：

- `task_id`
- `weight_level`：`low`、`normal`、`high`、`critical`
- `attempt_count`
- `approval_status`：`none`、`waiting`、`approved`、`rejected`、`deferred`
- `retry_status`：`none`、`retrying`、`exhausted`
- `next_action`：下一步动作说明

硬规则示例：

- 高权重任务不得直接忽略。
- 尝试轮数过多时进入审批、重试或降级。
- 外部写入、代表用户表达立场、飞书回馈类动作必须走审批。
- skill 判断与 Agent State 冲突时，以 Agent State 和硬规则为准。

### 2.4 Recovery 字段

`AgentRecoveryState` 分两层：

- 聚合计数层：服务运行观察和硬规则判断。
- scope 级恢复层：服务具体恢复对象的挂起、restart 和恢复点描述。

聚合计数字段最少包括：

- `continuation_attempts`
- `compact_attempts`
- `transport_attempts`
- `tool_error_attempts`
- `latest_interruption_reason`
- `latest_resume_point`

scope 级恢复记录建议结构：

```yaml
recovery_id: rec_001
scope_type: tool_call
scope_id: tool_123
status: waiting
failure_kind: tool_retry_exhausted
interruption_reason: wait_next_retry_window
strategy: retry_later
attempt_count: 3
max_attempts: 5
next_retry_at: 2026-04-16T00:10:00+08:00
resume_point: before_tool_execute
resume_payload:
  tool_name: lookup_source_context
  tool_use_id: tool_123
  query_id: query_001
last_error: upstream timeout
updated_at: 2026-04-16T00:00:00+08:00
```

字段说明：

- `recovery_id`：恢复记录稳定 ID。
- `scope_type`：`turn`、`tool_call`、`task` 之一。
- `scope_id`：恢复对象的稳定标识，例如 `tool_use_id` 或 `task_id`。
- `status`：`active`、`waiting`、`scheduled`、`resolved`、`exhausted` 之一。
- `failure_kind`：原始失败或中断来源。
- `interruption_reason`：当前任务为何挂起，等待后续 restart。
- `strategy`：`retry_now`、`retry_later`、`wait_approval`、`degrade`、`manual_review`、`abort` 之一。
- `attempt_count`：当前恢复 scope 已尝试次数。
- `max_attempts`：当前恢复 scope 可尝试上限。
- `next_retry_at`：下一次允许 restart 的时间；无调度要求时可为空。
- `resume_point`：后续恢复时从哪一步继续。
- `resume_payload`：恢复所需的最小可序列化上下文。
- `last_error`：最后一次错误摘要。
- `updated_at`：恢复状态最近更新时间。

约束：

- `resume_payload` 必须完全可序列化；禁止保存 Python 回调、future、线程对象或其他不可持久化引用。
- 对存在副作用不确定性的工具，`failure_kind=tool_side_effect_uncertain` 时不得默认自动重试。
- `interruption_reason` 用于描述任务为什么被挂起，不等同于 `failure_kind`。
- 当前阶段即使未落盘，也必须保证这些字段可以被 `to_dict` / `from_dict` 稳定表达。

### 2.5 中断原因与恢复点枚举

`failure_kind` 第一版建议值：

- `model_transport_error`
- `model_max_tokens`
- `context_overflow`
- `tool_timeout`
- `tool_transient_error`
- `tool_retry_exhausted`
- `tool_side_effect_uncertain`
- `permission_denied`
- `approval_waiting`
- `approval_rejected`
- `feedback_delivery_failed`
- `persistence_write_failed`

`interruption_reason` 第一版建议值：

- `wait_next_retry_window`
- `waiting_approval`
- `waiting_external_callback`
- `waiting_schedule`
- `waiting_manual_review`
- `context_compaction_pending`
- `runtime_restart_pending`
- `user_pause`

`resume_point` 第一版建议值：

- `before_model_call`
- `before_tool_execute`
- `after_tool_result`
- `after_approval`
- `before_feedback`

## 3. 联系人身份数据

联系人采用“文件夹索引 + 单人详情文件”。

```text
data/identity/contacts/
  index.md
  people/
    contact_<id>.md
```

### 3.1 联系人索引

文件位置：

```text
data/identity/contacts/index.md
```

用途：

- 供 `lookup_contact_identity` 快速定位联系人详情文件。
- 解决同名、别名和飞书 ID 匹配问题。
- 不承载完整联系人画像。

建议结构：

```md
---
schema: dutyflow.contact_index.v1
id: contact_index
updated_at: 2026-04-16T00:00:00+08:00
---

# Contact Index

| contact_id | display_name | aliases | feishu_user_id | feishu_open_id | department | org_level | detail_file |
|---|---|---|---|---|---|---|---|
| contact_001 | 张三 | 三哥, zhangsan | ou_xxx | open_xxx | 产品部 | manager | people/contact_001.md |
```

字段说明：

- `contact_id`：本地稳定 ID。
- `display_name`：显示名。
- `aliases`：别名列表，用英文逗号分隔。
- `feishu_user_id`：飞书用户 ID，按授权实际返回填写。
- `feishu_open_id`：飞书 open_id，按授权实际返回填写。
- `department`：部门。
- `org_level`：组织层级或上下级级别。
- `detail_file`：详情文件相对路径。

匹配优先级：

1. `contact_id`
2. `feishu_user_id`
3. `feishu_open_id`
4. 其他飞书稳定标识
5. 姓名 + 部门
6. 别名 + 部门
7. 姓名或别名单独匹配

仅命中第 7 类时，工具必须返回“可能匹配”，不得直接认定唯一身份。

### 3.2 单人详情文件

文件位置：

```text
data/identity/contacts/people/contact_<id>.md
```

Frontmatter：

```yaml
schema: dutyflow.contact_detail.v1
id: contact_001
display_name: 张三
aliases: ["三哥", "zhangsan"]
feishu_user_id: ""
feishu_open_id: ""
feishu_union_id: ""
department: 产品部
org_level: manager
role_title: 产品经理
relationship_to_user: manager
responsibility_scope: ["需求确认", "项目排期"]
trust_level: normal
updated_at: 2026-04-16T00:00:00+08:00
```

字段说明：

- `relationship_to_user`：与用户关系，如 `manager`、`peer`、`direct_report`、`external_partner`。
- `responsibility_scope`：该联系人常见责任范围。
- `trust_level`：仅用于谨慎程度，不代表安全放行。

正文结构：

```md
# 张三

## Identity Summary

一句话身份摘要。

## Organization Context

- department:
- org_level:
- role_title:
- manager:
- reports:

## Relationship To User

说明与用户的责任、协作、汇报关系。

## Responsibility Context

| scope | description | default_weight |
|---|---|---|

## Matching Notes

用于说明重名、别名、飞书 ID 等匹配注意事项。

## Decision Snippets

供工具裁剪给模型的短片段。
```

工具读取规则：

- 默认只读取 frontmatter、`Identity Summary`、`Relationship To User`、`Decision Snippets`。
- 需要责任判断时再读取 `Responsibility Context`。
- 不返回整份联系人详情文件。

## 4. 来源与责任数据

来源用于描述“信息来自哪里”，责任用于描述“用户是否需要处理”。

### 4.1 来源索引

文件位置：

```text
data/identity/sources/index.md
```

建议结构：

```md
---
schema: dutyflow.source_index.v1
id: source_index
updated_at: 2026-04-16T00:00:00+08:00
---

# Source Index

| source_id | source_type | feishu_id | display_name | owner_contact_id | default_weight | notes |
|---|---|---|---|---|---|---|
| source_chat_001 | chat | oc_xxx | 项目群 | contact_001 | high | 核心项目群 |
```

字段说明：

- `source_id`：本地来源 ID。
- `source_type`：`chat`、`doc`、`file`、`direct_message`、`calendar` 等。
- `feishu_id`：飞书侧资源 ID。
- `owner_contact_id`：来源责任人。
- `default_weight`：默认权重倾向，不是最终判断。

### 4.2 责任上下文

责任上下文可以先存放在联系人详情和来源索引中，Demo 期不单独建设复杂责任库。

最小责任记录包含：

- `responsibility_scope`
- `owner_contact_id`
- `related_source_id`
- `default_weight`
- `requires_user_attention`
- `notes`

工具 `lookup_responsibility_context` 必须结合联系人、来源和事项类型返回裁剪后的责任片段。

## 5. 事件记录

文件位置：

```text
data/events/evt_<id>.md
```

Frontmatter：

```yaml
schema: dutyflow.event_record.v1
id: evt_001
received_at: 2026-04-16T00:00:00+08:00
source_type: chat
source_id: source_chat_001
sender_contact_id: contact_001
feishu_event_id: ""
event_kind: message
task_id: ""
```

正文结构：

```md
# Event evt_001

## Raw Summary

不保存敏感大原文，只保存必要摘要。

## Extracted Signals

- sender:
- source:
- mentioned_user:
- file_or_doc:
- action_hint:

## Processing Status

- identity_completed:
- weighting_completed:
- approval_required:
- task_created:
```

约束：

- 飞书原始事件如包含敏感内容，应只保存必要摘要和定位信息。
- 事件记录必须能关联任务、联系人和来源。

## 6. 上下文摘要

文件位置：

```text
data/contexts/ctx_<id>.md
```

Frontmatter：

```yaml
schema: dutyflow.context_summary.v1
id: ctx_001
task_id: task_001
event_ids: ["evt_001"]
created_at: 2026-04-16T00:00:00+08:00
compact_level: short
```

正文结构：

```md
# Context ctx_001

## Current Goal

当前事项目标。

## Known Facts

- 已确认事实

## Identity Context

联系人和来源裁剪片段。

## Decision Context

权重和审批相关上下文。

## Next Step

下一步建议。
```

约束：

- 上下文摘要用于继续任务，不是长期记忆。
- 摘要必须保留当前目标、关键事实、责任关系和下一步。

## 7. 任务状态

文件位置：

```text
data/tasks/task_<id>.md
```

Frontmatter：

```yaml
schema: dutyflow.task_state.v1
id: task_001
title: ""
status: pending
weight_level: normal
source_event_id: evt_001
sender_contact_id: contact_001
source_id: source_chat_001
approval_id: ""
created_at: 2026-04-16T00:00:00+08:00
updated_at: 2026-04-16T00:00:00+08:00
```

状态枚举：

- `pending`
- `in_progress`
- `waiting_approval`
- `deferred`
- `completed`
- `rejected`
- `failed`

正文结构：

```md
# Task task_001

## Summary

任务摘要。

## Current State

- status:
- weight_level:
- attempt_count:
- retry_status:
- approval_status:

## Identity And Responsibility

身份、来源、责任裁剪片段。

## Decision Trace

关联 `trace_` 记录。

## Next Action

下一步动作。
```

约束：

- 任务是审批和恢复的核心锚点。
- 审批只暂停任务或动作，不暂停 Agent 主链路。
- 任务进入 `waiting_approval` 时必须能恢复原动作。

## 8. 审批记录与任务中断

审批分为待审批和已完成：

```text
data/approvals/pending/approval_<id>.md
data/approvals/completed/approval_<id>.md
```

### 8.1 审批记录

Frontmatter：

```yaml
schema: dutyflow.approval_record.v1
id: approval_001
task_id: task_001
status: waiting
requested_at: 2026-04-16T00:00:00+08:00
resolved_at: ""
requested_action: feishu_feedback
risk_level: high
resume_token: ""
```

审批状态：

- `waiting`
- `approved`
- `rejected`
- `revised`
- `deferred`
- `expired`

正文结构：

```md
# Approval approval_001

## Request

Agent 想做什么。

## Reason

为什么需要审批。

## Risk

可能的风险。

## Resume Context

- task_id:
- original_action:
- original_tool_name:
- original_tool_input_preview:
- context_id:
- trace_id:

## User Decision

- result:
- decided_by:
- decided_at:
- comment:
```

### 8.2 任务中断记录

任务级中断记录是审批恢复的关键。最小恢复信息：

- `approval_id`
- `task_id`
- `original_tool_name`
- `original_tool_input_preview`
- `original_action_kind`
- `context_id`
- `trace_id`
- `resume_token`
- `created_at`
- `expires_at`

约束：

- 不保存密钥、完整 token 或敏感大文本。
- `resume_token` 只用于本地恢复关联，不作为安全凭证。
- 用户在飞书端确认后，通过审批回调或事件处理入口恢复原任务链路。
- 如果中断记录缺失，审批结果不得被当作成功执行。

## 9. 决策留痕

文件位置：

```text
data/reports/trace_<id>.md
```

Frontmatter：

```yaml
schema: dutyflow.decision_trace.v1
id: trace_001
event_id: evt_001
task_id: task_001
created_at: 2026-04-16T00:00:00+08:00
decision_kind: weighting
```

正文结构：

```md
# Decision Trace trace_001

## Inputs

- event:
- contact:
- source:
- context:

## Identity Resolution

身份补全依据。

## Weighting

- importance:
- urgency:
- responsibility:
- interruption_need:
- actionability:

## Agent State Check

- weight_level:
- attempt_count:
- approval_status:
- retry_status:
- hard_rule_result:

## Final Decision

- decision:
- reason:
- next_action:
```

约束：

- 每个关键判断都应有 trace。
- 权重 skill 的输出必须记录为输入之一，但不得作为唯一依据。
- 最终决策必须包含 Agent State 或硬规则检查结果。

## 10. 非通用重要工具参数

以下是业务工具的初版参数约束。具体函数签名后续在开发计划中细化。

### 10.1 `lookup_contact_identity`

用途：按稳定参数查询联系人身份。

输入：

```yaml
contact_id: ""
feishu_user_id: ""
feishu_open_id: ""
name: ""
alias: ""
department: ""
source_id: ""
```

输出：

```yaml
match_status: unique | ambiguous | not_found
contact_id: ""
confidence: high | medium | low
matched_by: ""
source_file: ""
context_snippet: ""
ambiguous_candidates: []
```

约束：

- 命中多个候选时必须返回 `ambiguous`。
- 不得只因姓名相同直接返回唯一联系人。
- 返回片段必须裁剪。

### 10.2 `lookup_source_context`

输入：

```yaml
source_id: ""
source_type: ""
feishu_id: ""
display_name: ""
```

输出：

```yaml
match_status: unique | ambiguous | not_found
source_id: ""
source_type: ""
owner_contact_id: ""
default_weight: ""
context_snippet: ""
source_file: ""
```

### 10.3 `lookup_responsibility_context`

输入：

```yaml
contact_id: ""
source_id: ""
matter_type: ""
task_id: ""
```

输出：

```yaml
responsibility_found: true
responsibility_scope: []
relationship_to_user: ""
requires_user_attention: true
context_snippet: ""
source_files: []
```

### 10.4 `create_approval_request`

输入：

```yaml
task_id: ""
action_kind: ""
tool_name: ""
tool_input_preview: ""
risk_level: ""
reason: ""
context_id: ""
trace_id: ""
```

输出：

```yaml
approval_id: ""
status: waiting
approval_file: ""
resume_token: ""
```

约束：

- 工具只创建审批，不执行原动作。
- 创建审批后任务状态切换为 `waiting_approval`。

### 10.5 `resume_after_approval`

输入：

```yaml
approval_id: ""
task_id: ""
result: approved | rejected | revised | deferred | expired
decided_by: ""
comment: ""
```

输出：

```yaml
resumed: true
task_id: ""
next_status: ""
message: ""
```

约束：

- 只有 `approved` 可以恢复执行原动作。
- `rejected`、`deferred`、`expired` 必须更新任务状态，不得执行原动作。

### 10.6 `record_decision_trace`

输入：

```yaml
event_id: ""
task_id: ""
identity_refs: []
source_refs: []
context_id: ""
weighting_result: {}
agent_state_check: {}
final_decision: ""
reason: ""
```

输出：

```yaml
trace_id: ""
trace_file: ""
```

## 11. 日志与报告

日志文件建议按日期分 Markdown：

```text
data/logs/YYYY-MM-DD.md
```

日志条目最小结构：

```md
## 10:30:00 tool_call

- tool:
- task_id:
- result:
- trace_id:
- note:
```

约束：

- 日志不得泄露密钥和用户隐私配置。
- 失败、审批、恢复、权限拒绝必须记录。

## 12. 后续待细化

- 飞书实际返回的用户标识字段如何映射到联系人字段。
- 联系人详情文件是否需要更严格的 frontmatter 列表格式。
- `weight_level` 与具体提醒策略的映射。
- 审批过期时间和恢复 token 的生命周期。
- Markdown 表格解析失败时的降级策略。
