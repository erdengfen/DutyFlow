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
- 当前运行期 `MarkdownStore` 只支持字符串值；frontmatter 中不得使用 YAML 列表、嵌套对象或多行值。
- 多值字段在 v1 结构中统一使用英文逗号分隔字符串，或放入正文表格中，不使用 YAML 数组。
- `schema` 必须存在，用于区分记录类型和版本。
- `id` 必须稳定，不随显示名称变化。
- `updated_at` 使用 ISO-8601 字符串。
- 文件正文用于人工阅读和上下文片段抽取。
- 工具优先读取 frontmatter 和指定标题段落，不允许默认读取整份文件。
- 需要支持 `search / add / update` 的记录，必须具备稳定的 frontmatter 字段、稳定标题和稳定表格列，不能依赖自由文本整篇搜索。

### 1.2 ID 命名

建议使用以下稳定 ID 前缀：

- `evt_`：事件记录
- `per_`：感知记录
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
  knowledge/
  memory/
  state/
  events/
  perception/
  contexts/
  approvals/
  tasks/
  reports/
  logs/
  plans/
```

源码不得把记录散落写入未约束目录。

开发期默认工作区：

- 仓库根目录下的 `data/`、`skills/` 直接作为运行期结构化文件目录。

后续安装式工作区预留：

```text
<workspace_root>/
  data/
    identity/
    state/
    events/
    perception/
    contexts/
    approvals/
    tasks/
    reports/
    logs/
    plans/
  skills/
  tools/
  knowledge/
    contacts/
    sources/
  memory/
    index.md
    entries/
```

约束：

- 当前 Demo 期仍以仓库内 `data/` 为默认根路径。
- 后续如迁移到 `workspace_root`，业务层应通过统一配置切换，不允许把仓库相对路径硬编码进工具逻辑。
- 外部工具、skills、知识库、长期记忆与运行数据在未来都应落到统一 workspace 内，避免仓库源码目录和用户运行数据混写。
- workspace 化与沙箱边界调整属于同一批设计事项，当前阶段先只固定数据范式，不在此文档中展开执行策略。

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
aliases: 三哥, zhangsan
feishu_user_id: ""
feishu_open_id: ""
feishu_union_id: ""
department: 产品部
org_level: manager
role_title: 产品经理
relationship_to_user: manager
responsibility_scope: 需求确认, 项目排期
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
- `contact_detail` 是当前第一层联系人知识文件；后续补充知识应优先写入结构化补充记录，而不是不断堆积到单人详情正文中。

### 3.3 联系人知识补充记录

联系人基础身份和高频责任保留在 `contact_detail` 中；更细的协作习惯、偏好、注意事项等补充知识，后续使用独立记录承载。

文件位置：

```text
data/knowledge/contacts/contact_<id>/ckn_<id>.md
```

Frontmatter：

```yaml
schema: dutyflow.contact_knowledge_note.v1
id: ckn_001
contact_id: contact_001
topic: working_preference
keywords: review, feedback, async
confidence: medium
status: active
source_refs: evt_001, manual_input
created_at: 2026-04-16T00:00:00+08:00
updated_at: 2026-04-16T00:00:00+08:00
```

正文结构：

```md
# Contact Knowledge ckn_001

## Summary

一句话描述这条联系人知识。

## Structured Facts

| fact_key | fact_value | confidence | source_ref |
|---|---|---|---|
| review_style | 先异步评论后口头同步 | medium | evt_001 |

## Decision Value

说明这条知识会如何影响提醒、协作方式或责任判断。

## Change Log

| at | action | note |
|---|---|---|
| 2026-04-16T00:00:00+08:00 | created | 初次记录 |
```

约束：

- 一条补充记录只承载一个稳定主题，不把多种无关事实塞进同一文件。
- `search` 工具优先读 frontmatter、`Summary`、`Structured Facts` 和 `Decision Value`。
- `add / update` 工具必须按 `ckn_<id>` 定位，做字段级或 section 级修改，不允许模糊改写整份联系人目录。
- 已过期或被推翻的信息通过 `status` 和 `Change Log` 表达，不直接静默删除历史。

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

## 5.1 感知记录

感知记录位于飞书接入层和后续 Agent Loop 之间，用于把原始事件转换成更稳定的标准输入。

文件位置：

```text
data/perception/YYYY-MM-DD/per_<message_id>.md
```

约束：

- 一条进入主链的有意义事件对应一条感知记录。
- 感知记录不按天、联系人或群聊聚合；日期目录仅用于分片。
- 后续 Agent Loop 默认读取感知记录，不直接读取 `data/events/` 下的原始事件文件。
- 感知层只做确定性结构提取和确定性改写，不做责任判断、权重判断和任务生成。

Frontmatter：

```yaml
schema: dutyflow.perceived_event.v1
id: per_om_xxx
source_event_id: evt_om_xxx
message_id: om_xxx
received_at: 2026-04-28T12:00:00+08:00
event_type: im.message.receive_v1
trigger_kind: p2p_text
chat_type: p2p
chat_id: oc_xxx
sender_open_id: ou_xxx
message_type: text
mentions_bot: true
has_attachment: false
attachment_kinds: ""
raw_event_file: data/events/evt_om_xxx.md
status: perceived
updated_at: 2026-04-28T12:00:00+08:00
```

第一版 `trigger_kind` 建议值：

- `p2p_text`
- `p2p_file`
- `p2p_image`
- `p2p_link`
- `group_at_bot_text`
- `group_at_bot_file`
- `group_at_bot_image`
- `group_at_bot_link`

正文结构：

```md
# Perceived Event per_om_xxx

## Summary

一句话描述当前输入是什么。

## Extracted Text

- raw_text:
- content_preview:
- mention_text:

## Entities

| kind | value | source |
|---|---|---|
| sender | ou_xxx | sender_open_id |
| chat | oc_xxx | chat_id |
| mention | ou_bot_xxx | mentions |

## Parse Targets

| target_id | target_type | file_key | file_name | url | required_tool |
|---|---|---|---|---|---|

## Lookup Hints

- contact_lookup_hint:
- source_lookup_hint:
- responsibility_lookup_hint:
- followup_needed:

## Raw Reference

- event_record: data/events/evt_om_xxx.md
```

字段说明：

- `source_event_id`：指向对应原始事件记录 ID。
- `raw_event_file`：指向 `data/events/` 下原始事件记录的稳定相对路径。
- `attachment_kinds`：多值字段，使用英文逗号分隔字符串。
- `Entities`：保存 sender、chat、mentions 等稳定实体，不做关系推理。
- `Parse Targets`：保存后续内容解析工具可能消费的资源线索，不在感知层直接执行下载或解析。
- `Lookup Hints`：保存后续身份、来源、责任工具可直接消费的稳定提示。

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
event_ids: evt_001
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

## 6.1 长期记忆的边界

- 长期记忆只保存“跨会话仍有价值、且不能轻易从当前仓库状态或当前任务状态直接重新推导”的信息。
- 当前任务进度、临时上下文、原始聊天流水、完整飞书原文和代码结构说明，不应写入长期记忆。
- 长期记忆不是上下文摘要的延长版；上下文摘要服务当前任务，长期记忆服务后续会话和稳定偏好。

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
- 联系人详情文件是否需要继续细化固定 section 和固定表格列。
- `weight_level` 与具体提醒策略的映射。
- 审批过期时间和恢复 token 的生命周期。
- Markdown 表格解析失败时的降级策略。
- `workspace_root` 的配置方式，以及从仓库内 `data/` 迁移到独立 workspace 的具体步骤。

## 13. 长期记忆结构

长期记忆当前不接入 Demo 主链路，但结构需要先固定，便于后续实现 `search / add / update` 工具。

文件位置：

```text
data/memory/index.md
data/memory/entries/memory_<id>.md
```

### 13.1 记忆索引

```md
---
schema: dutyflow.memory_index.v1
id: memory_index
updated_at: 2026-04-16T00:00:00+08:00
---

# Memory Index

| memory_id | title | memory_type | scope_type | scope_id | keywords | status | confidence | detail_file |
|---|---|---|---|---|---|---|---|---|
| memory_001 | 张三偏好先异步评审 | preference | contact | contact_001 | review, async | active | medium | entries/memory_001.md |
```

### 13.2 单条长期记忆

Frontmatter：

```yaml
schema: dutyflow.long_term_memory.v1
id: memory_001
title: 张三偏好先异步评审
memory_type: preference
scope_type: contact
scope_id: contact_001
status: active
importance: normal
confidence: medium
keywords: review, async, feedback
source_refs: evt_001, ckn_001
created_at: 2026-04-16T00:00:00+08:00
updated_at: 2026-04-16T00:00:00+08:00
last_verified_at: 2026-04-16T00:00:00+08:00
```

正文结构：

```md
# Memory memory_001

## Summary

一句话说明这条长期记忆保留了什么。

## Memory Body

跨会话仍有价值的稳定描述。

## Structured Facts

| fact_key | fact_value | confidence | source_ref |
|---|---|---|---|
| review_style | 先异步评论，再决定是否开会 | medium | evt_001 |

## Retrieval Hints

- related_contacts:
- related_sources:
- related_tasks:

## Validation

- verification_status:
- stale_after:
- overwrite_policy:

## Change Log

| at | action | note |
|---|---|---|
| 2026-04-16T00:00:00+08:00 | created | 初次记录 |
```

约束：

- 一条长期记忆只表达一个原子主题，避免把整段项目历史堆成单个文件。
- `memory_type` 第一版建议值：`preference`、`relationship`、`decision`、`process`、`risk`、`project_fact`。
- `scope_type` 第一版建议值：`global`、`contact`、`source`、`task`、`project`。
- `search` 工具优先查 `index.md`，再按需读取单条记忆的 frontmatter、`Summary` 和 `Structured Facts`。
- `add / update` 工具必须按 `memory_id` 定位；对 `status`、`confidence`、`last_verified_at`、`Structured Facts` 的修改应可追溯。
- 长期记忆可能过期；与当前观察冲突时，应优先相信当前事件、当前文件和当前上下文。

## 14. 面向结构化知识与记忆的工具参数草案

以下工具当前只定义文档级 contract，不代表已经接入 Demo 主链路。

### 14.1 `search_contact_knowledge`

用途：搜索联系人补充知识记录。

输入：

```yaml
contact_id: ""
name: ""
topic: ""
keywords: ""
query: ""
status: active
```

输出：

```yaml
match_status: unique | ambiguous | multiple | not_found
contact_id: ""
note_ids: []
matched_by: ""
snippets: []
source_files: []
```

约束：

- 优先按 `contact_id` 定位，再按 `topic / keywords / query` 缩小范围。
- 返回结果必须是裁剪片段和文件定位，不直接返回整份笔记。

### 14.2 `add_contact_knowledge`

输入：

```yaml
contact_id: ""
topic: ""
keywords: ""
summary: ""
structured_facts_markdown: ""
decision_value: ""
source_refs: ""
```

输出：

```yaml
note_id: ""
status: created
file_path: ""
```

约束：

- 每次调用只新增一条 `ckn_<id>`。
- 新记录必须符合 `dutyflow.contact_knowledge_note.v1`。

### 14.3 `update_contact_knowledge`

输入：

```yaml
note_id: ""
summary: ""
structured_facts_markdown: ""
decision_value: ""
status: ""
confidence: ""
change_note: ""
```

输出：

```yaml
note_id: ""
status: updated
file_path: ""
```

约束：

- 必须先按 `note_id` 命中唯一文件后再修改。
- 更新时必须同步追加 `Change Log` 条目。

### 14.4 `search_long_term_memory`

用途：按类型、作用域和关键词检索长期记忆。

输入：

```yaml
memory_id: ""
memory_type: ""
scope_type: ""
scope_id: ""
keywords: ""
query: ""
status: active
```

输出：

```yaml
match_status: unique | multiple | not_found
memory_ids: []
matched_by: ""
snippets: []
source_files: []
```

约束：

- 优先查 `memory/index.md`，只在候选集上打开明细文件。
- 返回结果必须包含命中依据，避免模型把长期记忆当成无来源结论。

### 14.5 `add_long_term_memory`

输入：

```yaml
title: ""
memory_type: ""
scope_type: ""
scope_id: ""
importance: ""
confidence: ""
keywords: ""
summary: ""
memory_body: ""
structured_facts_markdown: ""
source_refs: ""
```

输出：

```yaml
memory_id: ""
status: created
file_path: ""
```

约束：

- 当前任务状态、短期摘要和大段原文日志不得直接写入长期记忆。
- 新记录必须同步更新 `data/memory/index.md`。

### 14.6 `update_long_term_memory`

输入：

```yaml
memory_id: ""
summary: ""
memory_body: ""
structured_facts_markdown: ""
status: ""
confidence: ""
last_verified_at: ""
change_note: ""
```

输出：

```yaml
memory_id: ""
status: updated
file_path: ""
```

约束：

- 必须保留 `Change Log`。
- 当事实被推翻时，优先更新 `status` 和验证信息，不直接删除旧记录。
