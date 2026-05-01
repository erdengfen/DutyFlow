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

当前阶段的可见控制快照文件位置：

```text
data/state/agent_control_state.md
```

### 2.1 Frontmatter

```yaml
schema: dutyflow.agent_control_state.v1
id: agent_control_state_local_user
updated_at: 2026-04-16T00:00:00+08:00
current_model: ""
permission_mode: default
active_task_ids: ""
waiting_approval_task_ids: ""
last_event_id: ""
```

说明：该文件由任务、审批和飞书事件链路汇总生成，用于人工检查和后续恢复入口；单次模型 loop 内仍使用内存 `AgentState`。

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

| task_id | status | weight_level | attempt_count | approval_status | retry_status | next_action |
|---|---|---|---:|---|---|---|

## Recovery

| scope_id | waiting_approval_tasks | blocked_tasks | expired_tasks | failed_tasks | latest_resume_point |
|---|---:|---:|---:|---:|---|

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

### 2.5 Runtime Context Working Set

`WorkingSet` 是 `RuntimeContextManager` 在模型调用前从 `AgentState` 确定性构造的当前工作集。它是内存结构，不是新的持久化文件；后续如写入上下文日志，必须通过 `Compression Journal` 或专门的 context 记录落盘。

第一版字段：

- `query_id`：当前 query 稳定 ID。
- `turn_count`：当前 AgentState 轮数。
- `transition_reason`：当前状态转移原因。
- `current_event_id`：当前飞书或本地事件锚点，可为空。
- `current_task_id`：当前任务锚点，可为空。
- `latest_user_text`：最近一条用户文本输入，用作当前目标的最小表达。
- `latest_assistant_text`：最近一条 assistant 文本输出，用于识别当前阶段状态。
- `pending_tool_use_ids`：仍在等待结果的工具调用 ID。
- `last_tool_result_ids`：最近一次写回的工具结果 ID。
- `recent_tool_use_ids`：最近若干工具调用 ID。
- `recent_tool_names`：最近若干工具名。
- `task_weight_level`：当前任务权重等级。
- `approval_status`：当前审批状态。
- `retry_status`：当前重试状态。
- `next_action`：当前控制面建议的下一步。
- `latest_interruption_reason`：最近中断原因。
- `latest_resume_point`：最近恢复点。
- `waiting_recovery_scope_ids`：处于等待状态的恢复 scope ID。

约束：

- `WorkingSet` 不保存完整长工具结果、文件正文或飞书原始 payload。
- `WorkingSet` 可以引用 ID 和短文本摘要，但不得替代审计记录、任务记录、审批记录或事件原文。
- 第一版 `WorkingSet` 构造不得调用模型，不得产生外部副作用。
- 后续压缩策略可以基于 `WorkingSet` 生成模型可见 messages，但不得丢失 `task_id`、`approval_id`、`perception_id`、`event_id`、`tool_use_id` 等关键锚点。

### 2.6 Runtime Context State Delta

`StateDelta` 是 `RuntimeContextManager` 对比上一次 `WorkingSet` 和当前 `WorkingSet` 后得到的增量视图。它是内存结构，不是新的持久化文件；后续如落盘，应写入上下文日志或压缩日志。

第一版字段：

- `query_id`：当前 query 稳定 ID。
- `previous_turn_count`：上一次 Working Set 的轮数；首次构造时为 0。
- `current_turn_count`：当前 Working Set 的轮数。
- `turn_advanced`：轮数是否推进。
- `transition_changed`：状态转移原因是否变化。
- `new_user_text`：最近用户文本相对上次是否新增或变化；无变化时为空。
- `new_assistant_text`：最近 assistant 文本相对上次是否新增或变化；无变化时为空。
- `current_event_id_changed`：当前事件锚点是否变化。
- `current_task_id_changed`：当前任务锚点是否变化。
- `new_pending_tool_use_ids`：本次新增的等待工具调用 ID。
- `resolved_tool_use_ids`：上次等待、本次已不等待的工具调用 ID。
- `new_tool_result_ids`：本次新增的工具结果 ID。
- `new_recent_tool_use_ids`：本次新增进入最近工具调用集合的工具调用 ID。
- `new_recent_tool_names`：本次新增进入最近工具集合的工具名。
- `task_control_changed_fields`：任务控制面发生变化的字段名。
- `recovery_changed_fields`：恢复控制面发生变化的字段名。
- `new_waiting_recovery_scope_ids`：本次新增的等待恢复 scope ID。

约束：

- `StateDelta` 不保存长文本、长工具结果或外部 payload。
- `StateDelta` 只描述变化，不作为完整状态来源；完整状态仍以 `AgentState`、任务文件、审批文件和审计日志为准。
- 第一版 `StateDelta` 构造不得调用模型，不得产生外部副作用。
- `StateDelta` 的 ID 字段必须保持原始锚点值，不得摘要化或重写。

### 2.7 Runtime Context Tool Receipt

`ToolReceipt` 是旧工具结果进入上下文压缩链路前的短收据。它是内存结构，第一版不新增持久化文件；后续如落盘，应写入 `Compression Journal` 或 evidence 相关记录。

第一版字段：

- `tool_use_id`：模型侧工具调用 ID。
- `tool_name`：工具名。
- `status`：工具结果状态，建议值为 `success`、`error`、`waiting_approval`、`rejected`、`unknown`。
- `ok`：工具执行是否成功。
- `is_error`：工具结果是否为错误结果。
- `error_kind`：错误类型；非错误可为空。
- `summary`：可进入模型上下文的短摘要。
- `full_result_ref`：完整结果当前位置或重取句柄，例如 `agent_state_tool_result:<tool_use_id>` 或 evidence 文件路径。
- `retryable`：是否可重试。
- `retry_exhausted`：是否已耗尽重试。
- `attempt_count`：工具实际尝试次数。
- `attachments`：工具结果携带的附件路径或文件锚点。
- `context_modifier_types`：工具结果携带的控制提示类型。
- `task_id`：相关任务锚点，可为空。
- `event_id`：相关事件锚点，可为空。
- `approval_ids`：相关审批 ID。
- `perception_ids`：相关感知记录 ID。
- `file_paths`：相关文件路径。
- `impacts_current_decision`：该收据是否仍影响当前决策。

约束：

- `ToolReceipt` 不保存完整长工具结果，只保存短摘要、状态和可追溯锚点。
- `ToolReceipt` 必须保留 `tool_use_id`、`tool_name` 和 `status`。
- `summary` 可以有损，`tool_use_id`、`task_id`、`approval_id`、`perception_id`、`event_id` 和文件路径不得有损。
- 第一版构造器只做确定性解析，不调用模型，不产生外部副作用。

#### Micro Compact 投影规则

旧 `tool_result` 的 micro-compact 是运行时投影规则，不是新的持久化数据结构。

第一版规则：

- 只在 `RuntimeContextManager` 生成模型可见 messages 时执行。
- 不修改 canonical `AgentState.messages`，只返回可供模型客户端消费的投影副本。
- 最近刚写回、下一轮模型必须消费的 `tool_result` 原文必须保留。
- 已被后续用户消息或 assistant 消息越过的旧 `tool_result` 可以替换为 `ToolReceipt.to_context_text()`。
- 替换后仍保持 `AgentContentBlock.type=tool_result`、`tool_use_id`、`tool_name` 和 `is_error`，避免破坏模型客户端的工具结果协议。
- 已经收据化的 `tool_result` 必须保持幂等，不得重复包裹成新的 receipt。
- 第一版 micro-compact 不调用模型、不落盘、不外置文件；Evidence Store 和 Compression Journal 后续接入。

### 2.8 Runtime Context Evidence Record

`EvidenceRecord` 是运行时上下文把长工具结果或大对象摘要外置到本地 Markdown 后形成的证据记录。它只保存调用方显式传入的内容，不主动扫描、复制或索引飞书原始事件、感知记录、审批记录和任务记录。

存储位置：

```text
data/contexts/evidence/evid_<id>.md
```

Frontmatter 字段：

- `schema`：固定为 `dutyflow.context_evidence.v1`。
- `id`：证据 ID，格式为 `evid_<id>`。
- `source_type`：来源类型，第一版建议值为 `tool_result`、`file_result`、`observation`、`manual`。
- `source_id`：来源对象 ID，例如 `tool_use_id`、文件 ID 或外部观察 ID。
- `tool_use_id`：相关工具调用 ID，可为空。
- `tool_name`：相关工具名，可为空。
- `task_id`：相关任务 ID，可为空。
- `event_id`：相关事件 ID，可为空。
- `source_path`：来源文件或对象路径，可为空。
- `content_format`：内容格式，第一版建议值为 `text`、`json`、`markdown`。
- `content_size`：原始内容字符数。
- `content_sha256`：原始内容 SHA-256，用于校验证据内容是否变化。
- `created_at`：创建时间。
- `summary_preview`：短摘要预览，仅用于列表查看。

正文结构：

```markdown
# Evidence evid_xxx

## Summary

短摘要。

## Source

- source_type: tool_result
- source_id: tool_1
- tool_use_id: tool_1
- tool_name: lookup_contact_identity
- task_id: task_1
- event_id: evt_1
- source_path:
- content_format: json
- content_size: 1234
- content_sha256: ...

## Content

<!-- dutyflow:evidence-content:start -->
完整长内容
<!-- dutyflow:evidence-content:end -->
```

约束：

- Evidence Store 只负责外置调用方传入的长内容，不主动建立全局索引。
- `content_sha256` 必须基于原始 content 计算，不能基于摘要计算。
- `summary_preview` 可以有损，`id`、`source_id`、`tool_use_id`、`task_id`、`event_id`、`source_path` 和 `content_sha256` 不得有损。
- 证据正文可以保存完整长工具结果；模型上下文只引用 `evidence:data/contexts/evidence/evid_<id>.md` 一类句柄。
- 第一版 Evidence Store 不调用模型、不做摘要生成；摘要由调用方提供，缺省时使用确定性截断。

### 2.9 Runtime Context Budget

`ContextBudgetReport` 是 `RuntimeContextManager` 对模型可见 messages 做出的上下文预算估算。它是内存结构，第一版不新增持久化文件；后续如落盘，应写入 `Compression Journal` 或 context 预算日志。

第一版只做估算和分类，不改变 messages，不触发压缩，不调用模型。

字段：

- `total_estimated_tokens`：总估算 token 数。
- `total_chars`：参与估算的可见文本字符数。
- `message_count`：参与估算的消息数。
- `block_count`：参与估算的内容块数。
- `lane_usages`：按上下文 lane 聚合的估算用量。
- `largest_items`：估算 token 最高的若干 message/block 条目，用于后续可视化和定位膨胀来源。
- `estimator_version`：估算器版本，例如 `heuristic_cjk_v1`。

`ContextBudgetLaneUsage` 字段：

- `lane`：上下文 lane，第一版建议值为 `system_instructions`、`latest_user_input`、`active_tool_result`、`tool_receipt`、`assistant_context`、`history`、`unknown`。
- `estimated_tokens`：该 lane 的估算 token 数。
- `chars`：该 lane 的文本字符数。
- `message_count`：该 lane 涉及的消息数。
- `block_count`：该 lane 涉及的内容块数。

`ContextBudgetItem` 字段：

- `message_index`：消息序号。
- `block_index`：block 序号。
- `role`：消息角色。
- `block_type`：block 类型。
- `lane`：该条目归属 lane。
- `tool_use_id`：相关工具调用 ID，可为空。
- `tool_name`：相关工具名，可为空。
- `estimated_tokens`：该条目估算 token 数。
- `chars`：该条目可见文本字符数。
- `preview`：短预览，用于调试和可视化。

估算规则：

- 中文、日文、韩文等 CJK 字符按 1 字符约 1 token 估算。
- 非 CJK 文本按约 4 字符 1 token 估算。
- 每条 message 和每个 block 叠加固定结构开销，反映 provider 消息包装成本。
- 估算结果只用于预算、可视化和后续触发压缩的参考，不作为计费或模型真实 token 统计。

约束：

- `ContextBudgetReport` 只能基于模型可见 messages 计算，不应读取未投影的完整历史。
- 第一版不得因为预算超限自动删除内容；压缩动作由后续 Step 8 的 health check、journal 和 recovery 链路统一接入。
- `preview` 可以有损，`message_index`、`block_index`、`tool_use_id` 和 `tool_name` 不得有损。

### 2.10 中断原因与恢复点枚举

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
summary_kind: phase_summary
phase: completed_context_lookup
trigger_reason: phase_boundary_budget
trigger_mode: normal
source_query_id: query_001
source_message_count: 12
estimated_tokens: 7200
soft_token_limit: 6000
hard_token_limit: 10000
phase_boundary_detected: true
requires_llm: true
anchor_task_ids: task_001
anchor_event_ids: evt_001
anchor_tool_use_ids: tool_001
anchor_approval_ids: approval_001
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
- `summary_kind` 第一版可取 `phase_summary`，表示由运行时阶段边界或预算触发生成。
- `trigger_reason` 第一版可取 `context_overflow`、`budget_hard_limit`、`phase_boundary_budget`、`manual_compress`、`phase_boundary_only`、`none`。
- `trigger_mode` 第一版可取 `emergency`、`normal`、`manual`、`record_only`、`none`。
- `phase_boundary_only` 只记录边界，不要求 LLM 摘要正文。
- `anchor_*` 字段必须保留可追溯锚点；摘要可以压缩文本，但不能丢失 `task_id`、`event_id`、`tool_use_id`、`approval_id` 等稳定引用。
- Context Health Check 完成前，LLM 生成的阶段摘要只允许记录和调试查看，不直接替换下一轮模型上下文。

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
status: queued
weight_level: normal
source_event_id: evt_001
sender_contact_id: contact_001
source_id: source_chat_001
approval_id: ""
run_mode: async_now
scheduled_for: ""
execution_profile: background_async_default
requested_capabilities: ""
resolved_skills: ""
resolved_tools: ""
resume_point: ""
resume_payload: ""
next_retry_at: ""
created_at: 2026-04-16T00:00:00+08:00
updated_at: 2026-04-16T00:00:00+08:00
```

状态枚举：

- `queued`
- `scheduled`
- `running`
- `waiting_approval`
- `blocked`
- `completed`
- `failed`
- `cancelled`
- `expired`

运行字段说明：

- `run_mode`：`async_now` 或 `run_at`。
- `source_event_id` / `source_id`：从正式 runtime 感知上下文写入；第一版后台任务完成回推时，`source_id` 优先保存飞书 `chat_id`。
- `scheduled_for`：`run_at` 任务的绝对执行时间，必须是带时区 ISO-8601，且创建时必须晚于当前时间。
- `execution_profile`：后台执行面裁决后的能力 profile，例如 `background_async_default`、`background_scheduled_selected`。
- `requested_capabilities`：模型建议的能力类别，使用英文逗号分隔。
- `resolved_skills` / `resolved_tools`：系统校验后的后台可用技能和工具，使用英文逗号分隔。
- `resume_point` / `resume_payload`：审批、恢复或后续后台执行器继续任务所需的最小上下文。
- `next_retry_at`：下一次允许恢复或重试的时间；无要求时为空。

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
- scheduled_for:
- last_result_summary:

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

### 7.1 后台任务结果

文件位置：

```text
data/tasks/results/result_<task_id>.md
```

Frontmatter：

```yaml
schema: dutyflow.task_result.v1
id: result_task_001
task_id: task_001
status: placeholder
task_status: queued
source_task_file: data/tasks/task_001.md
created_at: 2026-04-16T00:00:00+08:00
updated_at: 2026-04-16T00:00:00+08:00
```

状态枚举：

- `placeholder`
- `running`
- `completed`
- `blocked`
- `failed`

正文结构：

```md
# Task Result result_task_001

## Summary

结果摘要或当前处理状态。

## User Visible Final Text

可直接通过飞书回推给用户的最终文本。

## Execution Metadata

- stop_reason:
- tool_result_count:
- query_id:

## Raw Result

后台 subagent 原始执行结果或调试摘要。
```

约束：

- 创建 `task_<id>.md` 时同步创建结果占位文件。
- 后台 subagent 执行中更新同一份结果文件，不新建多份结果。
- 结果文件是完成后飞书回推和人工审计的主要依据。
- `task_status` 记录写结果时任务主文件的状态；结果本身是否完成以 `status` 为准。

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
