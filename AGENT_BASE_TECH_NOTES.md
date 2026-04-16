# AGENT_BASE_TECH_NOTES.md

本文档是临时技术梳理文档，用于讨论 DutyFlow 的 Agent 基架底层设计。  
本文档不等同于最终开发计划，暂不写入 `PLANS.md`。

参考材料来自 `docs/learn-claude-code`，重点参考：

- `s01-the-agent-loop.md`：Agent 主循环
- `s02-tool-use.md`：工具调用
- `s02a-tool-control-plane.md`：工具控制平面
- `s02b-tool-execution-runtime.md`：工具执行运行时
- `s05-skill-loading.md`：Skill 按需加载
- `s06-context-compact.md`：上下文压缩
- `s07-permission-system.md`：权限系统
- `s08-hook-system.md`：Hook 系统
- `s10a-message-prompt-pipeline.md`：消息与提示词管道
- `s11-error-recovery.md`：错误恢复
- `s17-autonomous-agents.md`：自治 Agent
- `s18-worktree-task-isolation.md`：工作区隔离
- `s19-mcp-plugin.md`：外部工具接入
- `data-structures.md`：核心数据结构总表

## 1. 对 DutyFlow 的适配原则

DutyFlow 不是通用 Coding Agent，也不是多 Agent 平台。Agent 基架只服务于办公协作场景中的权限感知判断闭环。

因此参考 Claude Code / learn-claude-code 时应保留这些底层思想：

- 模型意图不能直接执行，必须经过工具控制平面。
- 工具不只是 handler map，还需要注册表、执行上下文、权限判断、hook 和结果封装。
- Skill 不应全部塞进 prompt，而应先发现、再按需加载。
- 权限不是布尔值，而是 deny / mode / allow / ask 的决策管道。
- 错误恢复是主循环的一部分，不是外围 try/except。
- 上下文必须可压缩、可落盘、可继续。

以下能力在 Demo 期只作为预留接口，不作为建设重点：

- 多 Agent 团队协作
- worktree 隔离
- MCP 外部工具生态
- 长期记忆
- 自动任务认领

## 2. Agent 基架总览

DutyFlow 的 Agent 基架建议拆成以下底层层次：

```text
AgentRuntime
  |
  +-- ModelClient
  +-- PromptPipeline
  +-- MessageState
  +-- ToolControlPlane
  |     +-- ToolRegistry
  |     +-- ToolRouter
  |     +-- ToolExecutor
  |     +-- ToolUseContext
  |     +-- PermissionGate
  |     +-- HookRunner
  |
  +-- SkillRegistry
  +-- ContextManager
  +-- RecoveryManager
  +-- AuditLogger
```

核心流转：

```text
输入事件 / 用户请求
  -> 构建 prompt 与 messages
  -> 调用模型
  -> 模型返回普通文本或 tool_call
  -> tool_call 进入 ToolControlPlane
  -> 权限检查
  -> hook 检查
  -> 路由到内部工具或外部工具
  -> 执行器执行
  -> 结果封装为 ToolResultEnvelope
  -> 写回 messages
  -> 继续下一轮或结束
```

## 3. Agent 主循环

主循环需要显式维护运行状态，不应依赖零散局部变量。

最小状态包括：

- 当前消息列表
- 当前轮次
- 当前模型
- 当前权限模式
- 当前上下文压缩状态
- 当前恢复状态
- 上一轮继续的原因

主循环的关键规则：

- assistant 回复必须写回消息历史。
- tool result 必须写回消息历史。
- 每次模型调用前必须做消息规范化。
- 每次工具执行前必须进入工具控制平面。
- 每次错误都必须进入恢复分支，而不是直接崩溃或静默忽略。

## 4. Tool Call 全链路

Tool Call 不允许直接调用 handler。完整链路应为：

```text
ToolCall
  -> ToolRegistry 查找工具定义
  -> ToolRouter 判断能力来源
  -> PermissionGate 做权限决策
  -> HookRunner 执行 PreToolUse
  -> ToolExecutor 执行工具
  -> HookRunner 执行 PostToolUse
  -> ToolResultEnvelope 标准化结果
  -> AuditLogger 记录调用
  -> 写回 MessageState
```

### 4.1 ToolSpec

ToolSpec 是模型可见的工具说明。

候选字段：

- name：工具名
- description：工具说明
- input_schema：输入约束
- source：工具来源，如 native / skill / external
- risk_level：风险等级
- concurrency_safe：是否允许并发
- requires_approval：是否默认需要审批

### 4.2 ToolRegistry

ToolRegistry 负责注册和查询工具。

职责：

- 注册内部工具。
- 注册由 skill 暴露的工具。
- 预留外部工具接入位置。
- 输出给模型看的工具列表。
- 为 ToolRouter 提供工具元信息。

禁止：

- 在主循环里写工具名 if/else。
- 让工具绕过注册表直接暴露给模型。
- 让外部工具绕过权限层进入执行器。

### 4.3 ToolRouter

ToolRouter 负责按工具来源分发。

初版来源：

- native：项目内置工具
- skill：通过已加载 skill 暴露的能力
- external：未来外部工具预留

参考 MCP 的命名隔离思路，未来外部工具可使用前缀防冲突，例如：

```text
external__provider__tool
```

Demo 期不需要实现完整 MCP，但要保留“外部工具仍走同一权限管道”的架构位置。

### 4.4 ToolUseContext

ToolUseContext 是工具运行时总线，不是业务数据模型。

候选内容：

- workspace：当前工作区根目录
- permission_context：当前权限模式和规则
- app_state：运行态应用状态
- messages：当前消息状态引用或摘要
- config：从 `.env` 加载后的配置视图
- storage：本地文件存储入口
- audit_logger：审计日志入口
- hook_runner：hook 执行入口
- skill_registry：skill 注册表

设计要求：

- 工具 handler 接收 tool_input 和 ToolUseContext。
- 工具不得自行读取 `.env`。
- 工具不得绕过 storage 直接散落写文件。
- 工具不得绕过 audit_logger 执行敏感动作。

### 4.5 ToolResultEnvelope

工具结果必须统一封装，不直接返回裸字符串。

候选字段：

- ok：是否成功
- content：可给模型阅读的结果正文
- is_error：是否错误
- error_type：错误类别
- preview：给日志或 CLI 展示的短文本
- persisted_path：大结果落盘路径
- metadata：非敏感元信息
- context_modifiers：需要延迟合并的上下文修改

作用：

- 统一内部工具、skill 工具和未来外部工具的返回形态。
- 支持大输出落盘，只把预览写回上下文。
- 支持错误恢复和审计记录。

## 5. 工具执行运行时

learn-claude-code 的关键启发是：工具执行不是简单的 `handler(input)`。

需要考虑：

- 多个 tool_call 是否并发。
- 哪些工具必须串行。
- 执行中是否产生进度消息。
- 执行结果按什么顺序写回。
- 并发工具是否修改共享上下文。

DutyFlow 初版建议：

- 默认串行执行所有写入类、审批类、飞书回馈类工具。
- 只读类工具可标记为 concurrency_safe，但 Demo 初期可以先串行。
- context_modifiers 不直接乱序写入共享状态，统一由执行器按原始 tool_call 顺序合并。
- 每次工具执行都生成审计记录。

工具分类建议：

- read_only：只读，无外部状态改变。
- local_write：写本地文件。
- external_read：读取飞书或外部 API。
- external_write：回馈飞书或改变外部状态。
- approval_required：必须审批后才能执行。
- dangerous：默认拒绝或仅允许人工明确确认。

## 6. Skill 加载策略

Skill 采用两层加载：

```text
启动时加载轻量目录
  -> 只把 skill 名称和描述放入 prompt
  -> 模型需要时调用 load_skill
  -> 加载完整 skill 正文
  -> 正文进入当前上下文
```

### 6.1 SkillRegistry

职责：

- 扫描允许的 skill 目录。
- 读取 `SKILL.md`。
- 解析轻量元信息。
- 提供可用 skill 目录。
- 按名称加载完整正文。

安全要求：

- 只允许从受信任目录加载 skill。
- 不从网络动态下载 skill。
- 不执行 skill 文档中的命令。
- skill 正文只能作为知识注入，不能自动获得工具权限。
- load_skill 需要记录审计日志。

### 6.2 load_skill 工具

`load_skill` 是工具控制平面中的普通工具，必须：

- 注册到 ToolRegistry。
- 走 PermissionGate。
- 走 HookRunner。
- 返回 ToolResultEnvelope。
- 写入消息历史。

不允许：

- 模型直接读取任意路径作为 skill。
- skill 加载后自动修改权限规则。
- skill 加载后绕过审批调用工具。

## 7. 工作区与权限安全

工作区安全是 Agent 基架的底线。

基础规则：

- 所有文件路径必须限制在项目工作区或明确允许的数据目录内。
- 路径访问必须做 resolve 后的越界检查。
- 写操作必须区分源码写入和运行产物写入。
- `.env` 只能由 config 层读取。
- 工具不得输出密钥、token、飞书认证信息。
- 高风险命令默认拒绝。

命令执行建议：

- Demo 期尽量少提供通用 shell 工具。
- 如提供 shell 工具，必须先经过命令安全验证。
- 明确拒绝 `sudo`、递归删除、命令替换、可疑重定向、高危 shell 元字符拼接等模式。
- shell 工具默认需要审批。

权限模式建议：

- default：读操作可按规则放行，写操作和外部动作询问。
- plan：只允许读和分析，不允许写和外部回馈。
- auto：仅对低风险只读工具自动放行，其他仍询问。

权限决策顺序：

```text
tool_call
  -> deny rules
  -> mode check
  -> allow rules
  -> ask / approval
```

权限决策结果必须包含原因，不能只有 True / False。

## 8. Hook 机制

Hook 用于扩展关键时机，不应污染主循环。

初版 hook 事件：

- SessionStart：本地服务启动或会话开始。
- PreToolUse：工具执行前。
- PostToolUse：工具执行后。
- ToolError：工具执行失败后。
- ContextCompact：上下文压缩前后可再细分。

Hook 返回语义建议：

- continue：继续。
- block：阻止当前动作。
- inject_message：注入补充消息。
- modify_input：修改工具输入，需审计。

安全要求：

- Hook 默认不执行外部脚本。
- 如未来允许外部 hook，必须有工作区信任标记、超时、输出限制和审计。
- Hook 不能绕过 PermissionGate。
- Hook 的阻止或修改必须记录原因。

## 9. 上下文与 Prompt 管道

模型输入不只是 system prompt。

应拆为：

- PromptBlocks：稳定系统规则、工具说明、skill 目录、动态限制。
- Messages：用户输入、assistant 回复、tool_result。
- Attachments：必要上下文、近场摘要、审批上下文。
- Reminders：当前轮临时提醒，如权限模式、当前模型。

进入模型前必须做 normalize：

- 移除内部字段。
- 保证 tool_call 和 tool_result 配对。
- 合并或整理不符合 API 要求的消息形态。
- 控制大输出只保留预览。

上下文压缩策略：

- 大工具结果落盘，只把预览写回上下文。
- 旧工具结果可替换为占位摘要。
- 完整压缩必须保留当前目标、已完成动作、关键决定、任务状态、下一步。

## 10. 错误恢复

错误恢复是主循环的一部分。

最小恢复路径：

- 输出截断：注入续写提示，有限次数继续。
- 上下文过长：触发压缩后重试。
- 网络、超时、限流：指数退避后重试。
- 工具执行失败：返回结构化错误给模型，并记录审计。
- 权限拒绝：将拒绝原因作为 tool_result 写回。

恢复状态至少需要记录：

- continuation_attempts
- compact_attempts
- transport_attempts
- tool_error_attempts

所有恢复路径必须有次数预算，禁止无限循环。

## 11. Agent 自治

DutyFlow Demo 期不建设多 Agent 自治系统，但单 Agent 仍需要有限自治能力。

这里的自治不是“自由行动”，而是：

- 在收到飞书事件后按固定链路推进。
- 在上下文不足时主动请求或读取必要上下文。
- 在敏感动作前主动进入审批。
- 在失败可恢复时按恢复策略重试。
- 在任务状态变化后主动落盘和回馈。

禁止：

- 空闲时主动扫描用户全部资源。
- 未授权读取大范围飞书资源。
- 未审批执行外部写入或代表用户发言。
- 自行扩展工具权限。

可预留：

- 后续从任务清单中自动挑选可处理事项。
- 后续根据责任层规则决定是否主动提醒。
- 后续建立受限后台任务，但必须有可见状态和停止方式。

## 12. 与 DutyFlow 三个核心业务层的关系

### 12.1 身份与来源补全层

这一层不应作为普通模型自由判断，而应作为主链路中的结构化步骤。

Agent 基架需要为其提供：

- 读取最小事件上下文的工具。
- 查询联系人关系或来源规则的工具。
- 将补全结果写入任务上下文的工具。
- 审计补全依据的记录。

### 12.2 事件权重决策层

权重决策应优先规则化，模型只能作为辅助。

Agent 基架需要为其提供：

- 读取身份层结果。
- 读取近场上下文摘要。
- 输出可解释的权重判断。
- 将判断结果转为提醒、摘要、审批或忽略。

### 12.3 权限核实与审批层

审批层应接入 ToolControlPlane，而不是散落在业务代码中。

凡是改变外部状态、代表用户表达立场、或可能误操作的工具调用，都必须：

- 先生成审批请求。
- 等待用户确认。
- 记录审批结果。
- 再允许执行。

## 13. 建议的 Agent 基架模块边界

后续目录可围绕以下模块讨论：

```text
src/dutyflow/agent/
  runtime.py          # Agent 主循环和生命周期
  model.py            # 模型调用封装
  messages.py         # 消息状态与规范化
  prompts.py          # Prompt 管道
  tools.py            # ToolSpec、ToolCall、ToolResultEnvelope
  registry.py         # ToolRegistry
  router.py           # ToolRouter
  executor.py         # ToolExecutor
  context.py          # ToolUseContext
  skills.py           # SkillRegistry 与 load_skill
  permissions.py      # PermissionGate
  hooks.py            # HookRunner
  recovery.py         # RecoveryManager
  workspace.py        # 工作区路径与安全边界
```

以上只是技术讨论草案，不代表最终文件目录。

## 14. 待讨论问题

- DutyFlow 初版是否提供通用 shell 工具，还是只提供受限专用工具。
- 内部工具和飞书工具的风险等级如何划分。
- Skill 目录来源是项目内固定目录，还是允许用户级目录。
- load_skill 是否需要审批，还是只记录审计。
- Hook 初版是否只做内置 Python hook，不允许外部脚本。
- 权限规则是否只存在会话内，还是可以写回本地配置。
- 审批结果是否能生成“本次总是允许”的临时规则。
- 工具调用审计使用 Markdown 还是 JSONL，或两者分工。
- Agent 自治的最大边界：只处理事件驱动，还是允许后台扫描任务清单。
