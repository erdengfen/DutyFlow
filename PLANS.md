# PLANS.md

本文档记录 DutyFlow Demo 期分步开发计划、阶段验收标准、未敲定问题和人工确认事项。

每个阶段完成后必须更新本文档：

- 已完成项用 `[x]` 标记。
- 未完成项用 `[ ]` 标记。
- 阻塞项必须写明原因。
- 需要开发者手动确认、提供环境或提供真实配置时，必须记录在对应阶段的“人工确认”中。
- 每个阶段必须按 `docs/TESTING.md` 执行自测、功能分块测试和完整链路检查；未彻底通过不得标记阶段完成。

## 总体验收目标

Demo 期最终必须实现以下完整链路：

```text
本地/飞书事件输入
  -> 身份与来源补全
  -> 权重判断
  -> Agent State 硬规则检查
  -> 任务状态沉淀
  -> 必要时审批中断
  -> 用户在飞书端确认审批
  -> 恢复原任务链路
  -> 用户回馈
  -> 本地 Markdown 留痕
  -> CLI 可观察
```

最终验收必须满足：

- 可接收真实或本地模拟的飞书事件输入。
- 可通过 Markdown 身份索引和单人详情文件补全身份与来源语境。
- 可结合权重 skill、Agent State 和硬规则做可解释判断。
- 可生成任务状态并进入可查看的 Markdown 文件。
- 敏感动作可生成审批请求，任务进入 `waiting_approval`。
- 用户在飞书端确认后，系统可恢复原任务链路。
- 可通过飞书回馈层发送提醒、审批请求和状态更新；真实飞书未接入时必须有清晰占位接口。
- 所有关键产物以本地 Markdown 留痕。
- CLI 支持健康检查、模型查看/切换、日志、任务、审批、上下文清理和压缩等调试命令。
- Demo 不实现的能力必须有接口占位、字符串返回和清晰代码注释。

## 全局开发约束

- 所有代码修改必须遵守 `docs/CODE_STYLE.md`。
- 所有数据结构修改必须同步核对 `docs/DATA_MODEL.md`。
- 每个 `.py` 文件必须有 `if __name__ == "__main__":` 自测入口。
- 每个完整功能分块必须有 `test/` 下独立测试文件。
- 每个阶段必须执行完整链路检查。
- 所有密钥、api_key、base URL、飞书认证和用户配置只能来自 `.env`。
- 真实飞书 API、真实模型 API、真实外部能力未接入时，必须返回明确占位字符串，不得伪装为真实成功。

## 已确认的存储范式

- 当前开发期的结构化运行文件直接放在仓库内 `data/` 下。
- 当前项目内 skills 继续放在仓库内 `skills/` 下。
- 后续如采用类似 openclaw / hermes 的安装式运行，再引入独立 `workspace_root`，例如 `~/DutyFlow/workspace/`。
- 进入 workspace 模式后，外部工具、skills、知识库、长期记忆和运行数据统一迁移到 workspace 内，由统一配置决定根路径。
- workspace 化与沙箱边界调整属于同一批设计事项；当前阶段先固定结构化 Markdown 范式和工具 contract，不提前改运行形态。

## Step 0: 项目骨架与入口迁移

### 最终效果

项目目录与 `docs/ARCHITECTURE.md` 的初版目录结构对齐，根目录 `main.py` 不再作为长期入口，程序生命周期入口集中到 `src/dutyflow/app.py`，CLI 控制台实现集中到 `src/dutyflow/cli/main.py`。

### 验收标准

- `src/dutyflow/` 基础目录存在。
- `src/dutyflow/app.py` 可作为程序启动与生命周期入口。
- `src/dutyflow/cli/main.py` 可由 `app.py` 调用。
- 根目录 `main.py` 不再承担正式入口职责。
- 基础目录 `data/`、`skills/`、`test/` 存在。
- 完整链路检查至少能启动应用并返回健康状态占位结果。

### 涉及文件、类、方法、模块

- `src/dutyflow/app.py`
  - `DutyFlowApp`
  - `run`
  - `health_check`
- `src/dutyflow/cli/main.py`
  - `CliConsole`
  - `start`
  - `handle_command`
- `src/dutyflow/__init__.py`
- `main.py`
- `pyproject.toml`
- `.env.example`
- `test/test_app_entry.py`

### 未敲定问题

- 已决策：全部运行和调试入口采用 `uv run`。
- 已决策：删除根目录 `main.py`，后续以 `src/dutyflow/app.py` 和 `uv run dutyflow` 作为主入口。

### 任务清单

- [x] 创建 `src/dutyflow/` 包结构。
- [x] 创建 `src/dutyflow/app.py`。
- [x] 创建 `src/dutyflow/cli/main.py`。
- [x] 处理根目录 `main.py` 的长期入口职责。
- [x] 创建 `data/`、`skills/`、`test/` 基础目录。
- [x] 创建 `.env.example` 初版。
- [x] 为新增 `.py` 文件添加自测入口。
- [x] 编写 `test/test_app_entry.py`。
- [x] 执行本阶段完整链路检查。

### 人工确认

- [x] 已确认删除根目录 `main.py`。
- [x] 已确认采用 `uv run` 作为全部运行和调试入口，便于后续打包和 Docker 部署。

## Step 1: 配置入口、Markdown 存储与日志基础

### 最终效果

系统具备统一 `.env` 配置读取、本地 Markdown 存储读写、数据目录初始化、基础日志记录能力。所有配置只能通过配置模块读取。

### 验收标准

- 缺失必要配置时返回明确错误。
- `.env.example` 包含模型、飞书占位、本地存储和日志配置类别。
- Markdown frontmatter 可读写。
- 可初始化 `data/state/agent_control_state.md`、日志文件和基础目录。
- 日志不泄露密钥或用户私有配置。

### 涉及文件、类、方法、模块

- `src/dutyflow/config/env.py`
  - `EnvConfig`
  - `load_env_config`
  - `validate_env_config`
- `src/dutyflow/storage/file_store.py`
  - `FileStore`
  - `ensure_dir`
  - `read_text`
  - `write_text`
- `src/dutyflow/storage/markdown_store.py`
  - `MarkdownDocument`
  - `MarkdownStore`
  - `read_document`
  - `write_document`
  - `extract_section`
- `src/dutyflow/logging/audit_log.py`
  - `AuditLogger`
  - `record`
- `.env.example`
- `test/test_env_config.py`
- `test/test_file_store.py`
- `test/test_markdown_store.py`
- `test/test_audit_log.py`

### 未敲定问题

- 已决策：模型配置键名使用 `DUTYFLOW_MODEL_API_KEY`、`DUTYFLOW_MODEL_BASE_URL`、`DUTYFLOW_MODEL_NAME`；真实 key 和真实模型链路由开发者后续提供后补测。
- 已决策：飞书基础配置占位使用 `DUTYFLOW_FEISHU_APP_ID`、`DUTYFLOW_FEISHU_APP_SECRET`、`DUTYFLOW_FEISHU_EVENT_VERIFY_TOKEN`、`DUTYFLOW_FEISHU_EVENT_ENCRYPT_KEY`。
- 暂定字段：`DUTYFLOW_FEISHU_EVENT_CALLBACK_URL` 为项目接入需要的本地配置字段，需在真实飞书开放平台事件订阅配置时核实。
- 已决策：Markdown frontmatter 只允许简单 `key: value` 字符串，不允许复杂列表和嵌套。
- 已决策：日志按天一个 Markdown 文件，路径为 `data/logs/YYYY-MM-DD.md`。

### 任务清单

- [x] 实现 `.env` 统一读取。
- [x] 实现配置校验。
- [x] 实现 Markdown frontmatter 读写。
- [x] 实现指定 section 抽取。
- [x] 实现基础审计日志。
- [x] 初始化本地数据目录。
- [x] 初始化 `agent_control_state.md` 运行状态快照文件。
- [x] 为新增 `.py` 文件添加自测入口。
- [x] 编写对应测试文件。
- [x] 执行本阶段完整链路检查。

### 人工确认

- [x] 已确认模型 API 真实 key 后续提供；本阶段先建立配置入口和缺失配置提示。
- [x] 已确认飞书真实 API 字段未知时先网络检索；无法确定的项目接入字段标记为暂定。

## Step 2: Agent State 与 Agent 基架控制面

状态：已完成。Step 2 已完成最小 agent 基架控制面，包括 `AgentState`、工具控制链路、CLI `/chat` 调试入口、权限闸门、最小恢复、结构化审计和 Hook 预留接口。当前阶段目标是“控制链成立、状态可见、审批优先、恢复可描述”，不是通用 agent 平台。

### 核心功能

- 纯内存 `AgentState`，维护多轮 `messages`、`pending_tool_use_ids`、`task_control`、`recovery`、`transition_reason`。
- 标准工具控制链：
  `ToolCall -> ToolRegistry -> ToolRouter -> PermissionGate -> ToolExecutor -> ToolResultEnvelope -> append_tool_results`
- CLI `/chat` 多轮调试入口，可输出：
  - `final_text`
  - `stop_reason`
  - `tool_results`
  - `pending_restarts`
  - `agent_state`
- 权限系统支持 `permission_mode = default | plan | auto`，稳定返回：
  - `allow`
  - `deny`
  - `ask`
- CLI 审批入口已闭环；敏感工具执行前先在终端确认。
- 权限判断当前进入下一轮收束：
  - 保留 `permission_mode = default | plan | auto`
  - 不再仅依赖工具静态字段做审批
  - 对 CLI / 命令型工具新增“按本次 tool call 内容做危险命令解析”的判定层
- 最小恢复能力已接入：
  - 模型侧：`model_max_tokens`、`model_transport_error`、`context_overflow`
  - 工具侧：`tool_timeout`、`tool_transient_error`、`approval_waiting`、`approval_rejected`
- 结构化审计日志已接入 `ToolExecutor`、`AgentLoop`、`DutyFlowApp`。
- Hook 仅保留稳定事件类型和 `HookRunner`，未接入主循环。

### 当前控制链路

```text
用户输入 /chat
  -> AgentState 初始化或续写
  -> 模型调用
  -> assistant 写回 AgentState
  -> 提取 tool_use
  -> ToolRegistry / ToolRouter
  -> PermissionGate
  -> ToolExecutor
  -> ToolResultEnvelope
  -> append_tool_results
  -> 下一轮模型调用或结束
  -> CLI 输出调试结果
  -> AuditLogger 留痕
```

### 设计范式

- 先规则，再智能；先闭环，再扩展。
- 控制面与执行面分层：
  - `state.py` 管运行状态
  - `tools/` 管工具控制链
  - `permissions.py` 管 allow / deny / ask
  - `recovery.py` 管恢复决策，不直接执行恢复
  - `audit_log.py` 管结构化审计
  - `hooks.py` 只保留扩展接口，不接主循环
- 所有工具结果只能通过 `append_tool_results` 回写消息流。
- 权限和审批必须发生在真实工具执行前。
- `data/state/agent_control_state.md` 仅作可见性快照，不参与 loop 控制。
- 对命令型工具，审批粒度要下沉到“本次命令是否危险”，而不是只看工具类别。

### 工具接入规范

当前内部工具接入的稳定流程已迁移到 Codex skill `dutyflow-internal-tool-workflow`。

本节只保留仍影响架构和范围判断的约束。

当前分组约定：

- 开发阶段新增工具默认标记为“内部工具”。
- 内部工具后续默认不要求显式声明“危险工具”标签作为唯一审批依据。
- 外部工具后续单独接入更稳定的安全性声明字段；本阶段不在内部工具上先做复杂分层。
- 开发期内部工具继续沿用当前目录结构，逻辑写在 `src/dutyflow/agent/tools/contracts/` 和 `src/dutyflow/agent/tools/logic/` 下；后续 `skill_loader` 也遵循这套结构。
- 内部工具继续沿用当前的显式注册方式；外部工具后续在另一层统一管理，不并入当前内部工具目录。
- `requires_approval` 与 `idempotency` 仍保留：
  - `requires_approval`：布尔字段，取值为 `True / False`
  - `idempotency`：取值为 `read_only / idempotent / unsafe`
- 这两个字段后续不再承担“内部工具是否一律审批”的唯一判断职责，但保留为静态兜底和执行语义声明。

### 权限判断收束方案

当前权限层第一版只根据工具静态声明做判断：

- `requires_approval = True` 视为敏感工具
- `idempotency != read_only` 视为敏感工具
- 再结合 `permission_mode = default / plan / auto` 输出：
  - `allow`
  - `ask`
  - `deny`

当前已落地为“两段式判断”：

1. 先保留静态字段判断，但不再把它作为内部工具审批的唯一来源。
2. 对 CLI / 命令型工具，继续解析本次 `tool_call` 中的命令文本。
3. 如果命令解析发现危险动作，再把该次调用提升为敏感执行。
4. 提升后的行为仍服从现有模式规则：
   - `default` -> `ask`
   - `plan` -> `ask`
   - `auto` -> `deny`
5. 如果命令解析判断为只读且低风险，才允许直接 `allow`。

当前已覆盖的命令型工具：

- `exec_cli_command`

当前仍未覆盖、后续可扩展的对象：

- 其它携带明确命令文本、脚本片段或可执行指令字符串的内部工具

危险命令第一版已识别方向：

- 文件删除，如 `rm`
- 提交或改写 Git 状态，如 `git commit`
- 明确写入、覆盖、追加、移动、重命名等文件系统改动
- 其它会改变文件系统、Git 状态、系统环境或网络状态的命令

静态字段仍保留的边界：

- 对不依赖命令文本、但天然存在副作用的工具，仍允许通过静态字段直接要求审批。
- 例如：
  - `create_skill`
  - 未来可能出现的 `send_message`
  - 其它代表用户发言、写入外部状态、或无法通过命令解析充分证明安全性的工具
- 也就是说：命令解析用于细化 CLI / 命令型工具，不替代所有静态风险声明。

### 关键约束

- `AgentLoop` 不允许绕过 `ToolRegistry`、`ToolRouter`、`PermissionGate`、`ToolExecutor`。
- 工具重试只允许在 `ToolExecutor` 内发生。
- 参数错误、权限错误、审批拒绝、路由错误、非幂等副作用默认不自动重试。
- `RecoveryManager` 只做恢复决策和恢复描述，不做后台执行器。
- 审计日志必须可读、可解析、可脱敏，且写失败不能打崩主链路。
- Hook 当前只提供事件和 runner，不暴露真实执行时机。
- CLI / 命令型工具不得再仅因工具名本身而在多轮只读调用中反复审批。
- 危险命令识别必须发生在真实执行前，不能先执行再回溯判责。
- 当静态字段判断与命令解析结果冲突时，应按更保守的一侧处理。

### 已完成项

- [x] `Step 2.1` Agent State 读写、不变量、序列化、工具结果回写
- [x] `Step 2.2` ToolSpec / ToolCall / ToolResultEnvelope / Registry / Router / Executor
- [x] `Step 2.3` 工具控制层目录收束到 `src/dutyflow/agent/tools/`
- [x] `Step 2.4` CLI `/chat` 多轮调试接口
- [x] `Step 2.5` 工具超时、有限重试、退避、执行留痕
- [x] `Step 2.6` PermissionGate + CLI 审批入口
- [x] `Step 2.6a` PermissionGate 已从“仅静态字段审批”升级为“静态字段 + 命令级危险解析”的两段式判断
- [x] `Step 2.6b` `exec_cli_command` 已接入命令风险解析，并按 `default / plan / auto` 输出 `allow / ask / deny`
- [x] `Step 2.6c` 已补充只读命令、危险命令、模式差异和静态兜底相关测试
- [x] `Step 2.7` RecoveryManager 数据结构、状态回写、当前进程内 restart 描述
- [x] `Step 2.8` 结构化 AuditLogger 接入
- [x] Hook 预留接口与 `HookRunner`
- [x] 本阶段完整链路检查

### 未完成与后续延后项

- [ ] 工具自动发现 / 自动装载
- [ ] `ToolUseContext.agent_state` 的专门只读视图
- [ ] 内部工具 / 外部工具的稳定声明字段与自动注册分层
- [ ] 外部工具 transient error 更细分类
- [ ] 真正的当前进程后台调度 / runtime restart 执行器
- [ ] 进程退出后的恢复落盘与重启恢复
- [ ] 飞书侧审批消息与审批恢复闭环
- [ ] Hook 在 `AgentLoop` 中的真实暴露

### 已确认事项

- [x] Step 2 不实现通用 shell 工具；后续如需要，仅作为“内部工具”接入并受权限系统约束。
- [x] Step 2 的人工审批入口先使用 CLI；飞书审批留到后续步骤。
- [x] `permission_mode` 固定为 `default / plan / auto`。
- [x] `BASE_URL` 不在代码中自动拼接 `/chat/completions`，完全由环境控制。
- [x] 开发阶段新增工具默认归为内部工具。
- [x] 已确认后续 CLI / 命令型工具的审批粒度要下沉到命令级，而不是只按工具类型一刀切。
- [x] 已确认内部工具不再默认依赖静态“危险工具”标记决定是否审批；但静态字段保留为兜底。
- [x] 已确认外部工具的更完整安全性字段留到后续单独接入，不在本轮内部工具改造中展开。
- [x] 已确认 `exec_cli_command` 当前按命令文本危险性决定是否审批，不再因工具名本身一刀切审批。
- [x] 已确认 `open_cli_session` / `close_cli_session` 当前不再通过静态危险字段强制进入审批。

### 主要注意事项

- 当前项目不是通用 agent 平台；Step 2 只要求“可控、可见、可追踪”的最小基架。
- `/chat` 是本地调试入口，不替代飞书前端。
- 结构化审计日志存放在 `data/logs/YYYY-MM-DD.md`。
- `pending_restarts` 只表示“当前进程内可恢复描述”，不代表已经有后台调度器。
- 当前注册表仍是手动登记；新增工具时不要假设只加文件即可自动接入。
- 内部 / 外部工具的稳定字段还未最终落稿；当前先按“开发期默认内部工具”执行。
- 命令级审批解析只适用于“风险可从本次命令文本中判断”的工具；不能把它扩展成所有副作用工具的唯一安全来源。

### 关键文件

- `src/dutyflow/agent/state.py`
- `src/dutyflow/agent/core_loop.py`
- `src/dutyflow/agent/permissions.py`
- `src/dutyflow/agent/recovery.py`
- `src/dutyflow/agent/hooks.py`
- `src/dutyflow/agent/tools/types.py`
- `src/dutyflow/agent/tools/registry.py`
- `src/dutyflow/agent/tools/router.py`
- `src/dutyflow/agent/tools/executor.py`
- `src/dutyflow/agent/tools/context.py`
- `src/dutyflow/logging/audit_log.py`
- `test/test_agent_state.py`
- `test/test_agent_executor.py`
- `test/test_agent_loop.py`
- `test/test_agent_permissions.py`
- `test/test_agent_recovery.py`
- `test/test_agent_hooks.py`
- `test/test_audit_log.py`

#### 本次不做

- [ ] 不实现审计日志索引器。
- [ ] 不实现 `/logs` 的复杂过滤查询。
- [ ] 不实现跨文件聚合报表。
- [ ] 不实现飞书回馈链路的完整审计闭环。

## Step 3: Skill Loader 与 Skill 注册层

### 最终效果

系统先实现 skill 层的独立注册器和一个用于加载完整 skill 正文的内部工具。当前阶段只建设 skill loader，不在本 step 中创建具体业务 skills；包括权重 skill 在内的具体 `SKILL.md` 内容留到后续步骤单独添加。

### 验收标准

- `SkillRegistry` 能从 `skills/<skill_name>/SKILL.md` 目录结构发现技能文档。
- skill 层区分轻量元信息和全量正文：
  - `SkillManifest`
  - `SkillDocument`
- `AgentLoop` 能稳定通过 skills 解析层注册表，把全部 skills manifest 注入模型侧 system message。
- 模型上下文默认只暴露 skill 元信息，不直接注入全量正文。
- `load_skill` 作为内部工具接入工具控制链，可按 name 返回完整 skill 文本。
- `load_skill` 走 `ToolRegistry`、`PermissionGate`、`HookRunner`（当前仅预留）和 `ToolExecutor`。
- skill 文档不会自动执行，不会自动获得工具权限。

### 设计范式

- 参考 `docs/learn-claude-code`，skill 层采用独立注册表，不与工具注册表混用。
- registry 内同时维护：
  - `SkillManifest`
    - `name`
    - `description`
  - `SkillDocument`
    - `manifest`
    - `body`
- registry 负责一次性扫描并缓存全量 `SKILL.md` 内容，但对模型侧默认只暴露 manifest 列表。
- 完整正文的注入仍通过工具逻辑完成，不在 system prompt 中直接塞全量 skills。

### 涉及文件、类、方法、模块

- `src/dutyflow/agent/skills.py`
  - `SkillManifest`
  - `SkillDocument`
  - `SkillRegistry`
  - `describe_available`
  - `load_full_text`
- `src/dutyflow/agent/core_loop.py`
- `src/dutyflow/agent/tools/context.py`
- `src/dutyflow/agent/tools/registry.py`
- `src/dutyflow/app.py`
- `src/dutyflow/agent/tools/contracts/` 下新增 `load_skill` contract
- `src/dutyflow/agent/tools/logic/` 下新增 `load_skill` logic
- `test/test_agent_skills.py`

### 已确认事项

- [x] Step 3 当前只做 skill loader 和 skill 注册层，不创建具体业务 skills。
- [x] 权重 skill 在后续步骤单独添加，不作为本 step 验收前提。
- [x] `load_skill` 依旧通过工具逻辑把完整 skill 文本注入上下文。
- [x] `load_skill` 作为内部工具处理，按当前工具分组规则默认视为安全工具。
- [x] skill 文档目录结构固定为 `skills/<skill_name>/SKILL.md`。
- [x] 解析层当前只解析 frontmatter 中必需的 `name` / `description`。
- [x] `SkillRegistry` 当前只做初始化加载，不做热重载。

### 未敲定问题

- 后续可以出现额外 manifest 字段，但不允许成为解析层的必要字段。
- 如在逻辑内预留额外字段解析，必须在代码旁用注释明确说明“仅为预留，不是当前必需字段”。

### 任务清单

- [x] 实现 `SkillManifest`、`SkillDocument`。
- [x] 实现 `SkillRegistry`，支持扫描 `skills_dir.rglob("SKILL.md")`。
- [x] 实现 `describe_available()`，供模型侧只读暴露元信息。
- [x] 实现 `load_full_text(name)`，返回完整 skill 正文。
- [x] 按当前内部工具目录结构新增 `load_skill` 内部工具，并接入工具控制面。
- [x] 通过现有 `ToolExecutor` 结构化审计，覆盖 `load_skill` 的工具执行留痕。
- [x] 让 `AgentLoop` 在模型调用前把 skills manifest 注入 system message。
- [x] 为新增 `.py` 文件添加自测入口。
- [x] 编写 `test/test_agent_skills.py`。
- [x] 执行本阶段完整链路检查。

## Step 3.1: 第一批内部 Tools 扩展

状态：已完成。当前已交付第一批内部工具与 CLI session 工具链，并稳定接入现有权限、审批、恢复、审计和 `/chat` 调试链路。

### 已交付内容

- `create_skill`
  - 受控创建 `skills/<skill_name>/SKILL.md`
  - 写入必须审批
- `open_cli_session`
  - 创建持久 bash 会话
- `exec_cli_command`
  - 在指定 session 中执行单条命令
  - 风险判定为“静态字段 + 命令内容”
- `close_cli_session`
  - 关闭 shell session 并回收资源

### 关键约束

- 工具继续手动注册到 `src/dutyflow/agent/tools/registry.py`
- CLI 工具当前只覆盖 Linux / WSL + bash
- `open_cli_session` / `close_cli_session` 不因工具名本身进入审批
- `exec_cli_command` 按命令内容决定 `allow / ask / deny`
- tool 只负责确定性动作；调用顺序和使用边界放到 skill 中约束

### 当前结果

- 已完成 `create_skill`、`open_cli_session`、`exec_cli_command`、`close_cli_session`
- 已完成对应 contract / logic / registry / 测试
- 当前 `/chat` 已可稳定触发这些工具，具体调试流程见 Codex skill `dutyflow-chat-debug-workflow`

## Step 3.2: 第一批 Skills 内容扩展

状态：已完成。当前已把第一批项目内 skill 内容接入 `SkillRegistry + load_skill` 体系。

### 已交付内容

- `skill_creator`
  - 约束模型整理 skill 需求并通过 `create_skill` 受控写入
- `cli_session_operator`
  - 约束模型按正确顺序使用 `open_cli_session`、`exec_cli_command`、`close_cli_session`
  - 明确只读命令和危险命令的审批边界

### 关键约束

- skill 目录继续固定为 `skills/<skill_name>/SKILL.md`
- system message 默认只暴露 manifest；正文通过 `load_skill` 按需加载
- skill 只能指导模型生成 tool call，不能绕过权限层、审批层或工具执行层

### 当前结果

- 已完成 `skill_creator`、`cli_session_operator`
- 已通过 `SkillRegistry` 扫描、`load_skill` 读取和对应测试验证

## Step 4: 身份、来源、责任 Markdown 数据与查询工具

状态：已完成。当前已具备身份、来源、责任主链路查询能力，以及联系人知识的两段式查询与受控写入能力。

### 最终效果

系统能从 Markdown 文件夹索引、单人详情文件和联系人补充知识记录中精准查询身份、来源和责任上下文，并以裁剪片段补充到当前上下文；同时为后续 `search / add / update` 型联系人知识工具固定文档结构和 contract。

### 查询与解析范式

- 面向模型的工具按“业务数据族”暴露，不直接暴露“通用 Markdown 浏览工具”。
- `identity / source / responsibility` 继续使用业务专用查询工具，不拆成通用 header/detail。
- `contact_knowledge` 采用两段式工具：
  - 第一轮先获取轻量 header 结果
  - 第二轮再按稳定 `note_id` 读取 detail
- 后续 `long_term_memory` 与 `contact_knowledge` 复用相同的两段式查询范式。
- 面向模型的第一轮工具只返回足够做下一步选择的轻量字段，不默认展开正文全文。
- 面向模型的第二轮工具只返回允许 section 的裁剪结果，不返回整份 Markdown 原文。

### 内部通用解析层

当前规划中，模型不会直接调用“通用 frontmatter 解析器”；但代码内部应沉淀一层结构化 Markdown 解析与更新能力，供联系人知识、长期记忆等工具复用。

建议内部职责拆分如下：

- `SchemaRegistry`
  - 维护 `schema`、允许路径、允许 section、必需字段和 detail 定位规则。
- `FrontmatterParser`
  - 负责解析当前 `MarkdownStore` 兼容的简单字符串 frontmatter。
- `RecordLocator`
  - 负责按数据族定位候选记录。
  - 有 `index.md` 的数据族优先走索引。
  - 当前无 `index.md` 的数据族，按 `DATA_MODEL` 约定目录扫描 frontmatter。
- `SectionExtractor`
  - 只抽取允许返回的 section，例如 `Summary`、`Structured Facts`、`Decision Value`。
- `SnippetBuilder`
  - 把 frontmatter 和允许 section 拼成稳定返回结构。
- `StructuredRecordUpdater`
  - 负责新增、更新、回写 `Change Log`，并在存在索引时同步更新索引。

内部逻辑链路约定：

1. `search headers`
   - 工具接收查询条件
   - `RecordLocator` 定位候选
   - `FrontmatterParser` 读取轻量字段
   - `SnippetBuilder` 组装 header 结果
2. `get detail`
   - 工具按稳定 ID 定位唯一文件
   - `FrontmatterParser` 读取 frontmatter
   - `SectionExtractor` 抽取允许 section
   - `SnippetBuilder` 返回 detail 结构
3. `add / update`
   - 工具先校验 schema 所需字段
   - `StructuredRecordUpdater` 写入 detail 文件
   - 如存在索引则同步更新索引
   - 写回 `Change Log`

### 验收标准

- `lookup_contact_identity` 支持按 contact_id、飞书 ID、姓名、别名、部门匹配。
- 同名或模糊命中必须返回 ambiguous。
- `lookup_source_context` 可定位来源上下文。
- `lookup_responsibility_context` 可结合联系人、来源、事项类型返回责任片段。
- 查询工具不返回整份 Markdown 文档。
- 示例联系人、来源和责任 fixture 可支持完整链路测试。
- 联系人知识补充记录采用独立 Markdown 结构，不与 `contact_detail` 混写。
- 后续联系人知识维护工具只能按稳定字段和固定 section 更新，不允许自由改写整个联系人目录。

### 涉及文件、类、方法、模块

- `src/dutyflow/identity/contact_resolver.py`
  - `ContactResolver`
  - `resolve_contact`
- `src/dutyflow/identity/source_context.py`
  - `SourceContextResolver`
- `src/dutyflow/agent/tools/contracts/lookup_contact_identity_contract.py`
- `src/dutyflow/agent/tools/contracts/lookup_source_context_contract.py`
- `src/dutyflow/agent/tools/contracts/lookup_responsibility_context_contract.py`
- `src/dutyflow/agent/tools/logic/lookup_contact_identity.py`
  - `LookupContactIdentityTool`
- `src/dutyflow/agent/tools/logic/lookup_source_context.py`
  - `LookupSourceContextTool`
- `src/dutyflow/agent/tools/logic/lookup_responsibility_context.py`
  - `LookupResponsibilityContextTool`
- `src/dutyflow/knowledge/contact_knowledge.py`
  - `search_contact_knowledge_headers`
  - `get_contact_knowledge_detail`
  - `add_contact_knowledge`
  - `update_contact_knowledge`
- `src/dutyflow/storage/structured_markdown.py`
  - `SchemaRegistry`
  - `FrontmatterParser`
  - `RecordLocator`
  - `SectionExtractor`
  - `SnippetBuilder`
  - `StructuredRecordUpdater`
- `data/identity/contacts/index.md`
- `data/identity/contacts/people/contact_<id>.md`
- `data/knowledge/contacts/contact_<id>/ckn_<id>.md`
- `data/identity/sources/index.md`
- `test/identity_fixture_data.py`
- `test/test_identity_lookup.py`
- `test/test_identity_source_context.py`

### 未敲定问题

- 飞书实际用户 ID、open_id、union_id 的字段来源。
- 联系人详情文件中上下级字段的最终形式。
- 联系人补充知识是否需要再区分“偏好 / 风险 / 协作习惯”等更细 topic 枚举。
- 联系人补充知识在当前 `DATA_MODEL` 未定义独立 `index.md` 的前提下，首版是否直接按固定目录扫描 frontmatter，还是先补索引文件。

### 任务清单

- [x] 创建联系人索引 fixture。
- [x] 创建单人详情 fixture。
- [x] 创建来源索引 fixture。
- [x] 实现 ContactResolver。
- [x] 实现 SourceContextResolver。
- [x] 实现三类 lookup 工具。
- [x] 创建联系人知识补充记录 fixture。
- [x] 实现结构化 Markdown 内部解析层，至少覆盖 frontmatter 解析、候选定位、section 抽取和稳定返回组装。
- [x] 为联系人知识记录补充两段式工具：
  - `search_contact_knowledge_headers`
  - `get_contact_knowledge_detail`
  - `add_contact_knowledge`
  - `update_contact_knowledge`
- [x] 让联系人知识工具与 `DATA_MODEL` 当前结构保持一致；如未新增索引文件，首版按固定目录扫描 frontmatter。
- [x] 接入工具控制面。
- [x] 为新增 `.py` 文件添加自测入口。
- [x] 编写对应测试文件。
- [x] 执行本阶段完整链路检查。

### 人工确认

- [ ] 提供 Demo 联系人样例。
- [ ] 提供 Demo 来源样例，如群、文档、文件或私聊。
- [ ] 确认第一版联系人补充知识 topic 范围，例如偏好、风险、协作习惯。

## Step 5: 飞书接入与感知记录层

状态：进行中。当前已完成真实飞书 p2p 链路、群聊 `@Bot` 链路、原始事件落盘、`/bind` bootstrap、CLI `/feishu` 调试入口，以及第一版感知记录层。Step 5 的目标是把飞书可见输入稳定接进本地，并整理成后续 loop 可直接消费的标准输入，不在本阶段做身份推理、权重判断、任务生成或正式 loop 编排。

### 核心边界

- 接入范围当前只覆盖 Bot 可见输入：
  - 用户私聊 Bot
  - 群聊中 `@Bot`
- 所有飞书显式配置统一来自 `.env` 和 `config` 模块。
- 飞书接入必须基于开放平台应用、凭证、长连接事件订阅和 Bot 回馈，不读取本机飞书客户端登录态。
- 本阶段只做：
  - 原始事件接收
  - 最小归属与去重
  - 原始事件落盘
  - 感知记录生成
  - `/bind` 初始化
- 本阶段不做：
  - 联系人关系判断
  - 责任归属判断
  - 高低优先级判断
  - 任务生成
  - 正式 Agent Loop 注入

### 当前已完成

- [x] 扩展 `EnvConfig` 和 `.env.example`，收敛飞书接入配置，并为后续完整用户可见信息/OAuth 预留字段。
- [x] 落地 `FeishuEventAdapter`、`FeishuClient`、`FeishuIngressService`，支持 fixture 和真实 `lark_oapi` 长连接。
- [x] 完成账号空间归属和 `event_id / message_id` 两层去重。
- [x] 支持原始事件落盘到 `data/events/`，保留最小路由字段和原始 payload。
- [x] 提供 `/feishu fixture`、`/feishu listen`、`/feishu latest`、`/feishu doctor` 本地调试入口。
- [x] 完成 `/bind` bootstrap：从私聊事件提取 `tenant_key / owner_open_id / owner_report_chat_id`，回填 `.env` 并由 Bot 回复绑定成功。
- [x] 已完成真实 p2p 私聊链路人工验证。
- [x] 已完成群聊 `@Bot` 链路人工验证。
- [x] 第一版感知记录层已落地：
  - 原始事件落盘后同步生成 `data/perception/YYYY-MM-DD/per_<message_id>.md`
  - 感知层只做确定性提取和改写
  - 已向后续 loop 暴露 `build_loop_input(record_id / message_id)` 标准读取接口
- [x] `docs/DATA_MODEL.md` 已同步 `dutyflow.perceived_event.v1`。
- [x] 接入层和感知层测试已覆盖文本、文件、群聊 `@Bot` 等主场景。

### 设计结果

- 原始事件和感知记录分层存储：
  - `data/events/` 保存原始事实和审计依据
  - `data/perception/` 保存后续 loop 可直接消费的标准输入
- 感知记录按“一条有意义事件一个文件”组织，不按天/用户/群聊聚合；日期只用于目录分片。
- 后续正式 Agent Loop 默认读取感知记录，不直接读取原始事件文件。
- 文件、图片、链接等解析目标当前只保留在线索层，后续再由内容解析工具按需消费。

### 当前未完成

- [x] 正式 Step 6 事件驱动 loop 已接入感知记录读取接口。
- [ ] 消息资源本体下载和本地资源存储仍未实现；当前只保存资源线索和原始事件。
- [ ] 文档、飞书文档链接、更多消息类型的解析目标细化规则仍待补充。
- [ ] 感知记录到任务层、上下文层的衔接尚未开始。

### 风险与待收口问题

- `im.chat.member.bot.added_v1` 已可收到，但当前没有对应处理器；拉机器人入群时会出现 `processor not found` 日志。
- `owner_report_chat_id` 可长期保存使用，但更适合作为可重新 `/bind` 刷新的会话标识。
- Step 5 当前只覆盖 Bot 可见输入；后续若要拿到完整“用户可见信息”，需要单独接入用户 OAuth 能力。

## Step 6: 正式 Agent Loop 与常驻运行编排

状态：已完成。Step 6 已完成正式 Agent Loop 与常驻运行编排，包括正式 runtime service 骨架、应用启动自动拉起后台服务、感知记录接入 runtime queue、统一反馈接口、正式 loop 接到现有 Agent 基架能力、`/feishu` 命令迁移为状态查看/诊断语义、`/chat` 迁移为非阻塞 debug 任务入口，以及共享执行核心迁移到 `src/dutyflow/agent/core_loop.py`。原“第一版权重判断、面向用户的硬规则判断和决策留痕”依赖后续用户视角飞书事件接入，当前不纳入 Step 6 实现范围，延后到后续阶段收口。

### 最终效果

应用启动后即进入正式运行态：

- 飞书长连接自动启动并持续监听。
- CLI 与正式 runtime 并存，不再依赖 `/feishu listen` 临时拉起监听。
- 飞书感知记录进入正式 runtime queue，由独立 worker 串行消费。
- 正式 loop 默认只读取 `data/perception/`，不直接读取原始事件文件。
- 私聊 Bot 和群聊 `@Bot` 的关键输入可触发完整 agent 执行链，而不是只做 `/chat` 调试。
- 长任务先即时回复用户，再由后台任务继续推进并在后续回传结果。

### 核心设计边界

- 当前正式 loop 触发范围只包括：
  - 用户私聊 Bot
  - 群聊中 `@Bot`
- 后续“用户与用户聊天中的重点/紧急信息感知”属于后续扩展，本阶段只在文档中保留，不进入实现范围。
- 正式 loop 复用 Step 5 已完成的飞书长连接与心跳保活，不重复创建第二套监听主链。
- 正式 loop 与 `/chat` 调试共享 `src/dutyflow/agent/core_loop.py` 中的执行核心。
- 正式 runtime 的编排模块继续独立维护，不与 `/chat` 的任务入口混写。
- 正式主 loop 当前加载项目内全部 skills，并注册除 CLI tools 外的全部 runtime tools。
- 当前阶段仅把 `open_cli_session`、`exec_cli_command`、`close_cli_session` 排除在正式主 loop 外；其它 tools 与 skills 暂按项目内默认能力面暴露。
- 工具与 skill 的能力边界仍优先通过 registry / skill 面隔离实现，而不是依赖更详细的 description 去“劝阻”模型少用。
- 后续若需要“后台挂起任务”能力面，应单独定义独立 registry / runtime profile，可按白名单接入更强工具，但不默认继承主 loop 的工具集，也不默认接入开发期 CLI tools。

### 线程与运行模型

- 主线程：
  - `DutyFlowApp`
  - CLI 控制台
- 飞书监听线程：
  - 复用当前 Step 5 的长连接线程
- 正式 runtime worker：
  - 单消费者线程
  - 串行消费感知记录，避免过早引入并发状态竞争
- 可选 debug chat worker：
  - 服务 `/chat` 调试任务
  - 不再抢占主 CLI 的 `stdin`

约束：

- 回调线程不直接跑模型和工具，只负责接收、落盘、感知和入队。
- 当前阶段不做多 worker 并发消费。
- CLI 不再通过后台线程抢 `input()` 做审批或多轮对话。

### 正式 Loop 输入与输出

- 输入：
  - 统一来自 `PerceptionRecordService.build_loop_input(...)`
  - raw event 文件仅作为审计、调试和解析工具兜底输入
- 输出：
  - 一切面向用户的发送能力统一收束为单独反馈接口
  - 不通过模型自由调用“发消息工具”

当前设计要求：

- 新增统一 `FeedbackService` 或 `FeishuFeedbackGateway`
- 正式 loop、审批流、任务状态层统一通过该接口给用户发消息
- 该接口后续需支持文本、回复、卡片、状态更新、审批请求等动作
- 正式主 loop 使用用户态受限工具集；开发调试工具集与后台挂起任务工具集后续单独维护。

### 权限与审批边界

- 正式 runtime 不再沿用 CLI `input()` 作为审批方式。
- 敏感动作在正式 loop 中只能进入“等待审批/等待确认”状态。
- 具体审批记录、恢复链路和飞书端确认闭环在 Step 7 完整实现。
- Step 6 先明确：
  - 正式 loop 不允许直接阻塞当前 CLI 等待人工输入
  - 审批请求只能通过统一反馈接口发送给用户

说明：

- 飞书 Bot 后续可通过消息卡片和交互回调承接审批确认。
- Step 6 先把正式 loop 的中断边界和反馈接口收束好。
- Step 7 再接审批回调、恢复和状态流转。

### CLI 调整方向

- `/feishu` 相关命令从“启动监听”改为“查看状态 / 控制状态”：
  - 查看当前连接状态
  - 查看最近事件
  - 查看诊断快照
  - 手动重连、暂停、恢复等
- `/chat` 从当前阻塞式子会话，迁移为非阻塞调试任务入口：
  - 提交 debug 任务
  - 查看 debug 结果
  - 不再在同一终端内开启第二条抢占式交互输入线程

### 验收标准

- `app.py` 启动后，飞书监听和正式 runtime 自动进入运行态。
- CLI 在正式 runtime 运行期间保持可用。
- 正式 loop 只读取感知记录，不直接读取 raw event。
- 私聊 Bot 和群聊 `@Bot` 的感知记录可触发完整 agent 执行链。
- 正式 loop 可复用现有 Agent 基架能力：
  - 多轮模型调用
  - 多轮 tool call
  - 权限判断
  - 恢复/中断
- 长任务可先即时回复，再进入后台继续推进。
- 正式 runtime 不再通过 CLI `input()` 做审批。
- 用户发送能力统一经过独立反馈接口，而不是模型自由调用工具。
- 共享执行核心与 `/chat` 调试任务解耦后，正式 runtime 仍保持独立编排。

### 涉及文件、类、方法、模块

- `src/dutyflow/app.py`
- `src/dutyflow/cli/main.py`
- `src/dutyflow/feishu/runtime.py`
- `src/dutyflow/perception/store.py`
- `src/dutyflow/agent/core_loop.py`
  - 作为正式 runtime 与 `/chat` debug 共享的执行核心
- 预期新增：
  - `src/dutyflow/agent/runtime_loop.py`
  - `src/dutyflow/agent/runtime_service.py`
  - `src/dutyflow/feedback/gateway.py`
- `data/reports/`
- `test/test_runtime_loop.py`
- `test/test_runtime_service.py`

### 未敲定问题

- `/chat` 最终调试命令形态，是 `run/status/latest` 风格还是其它形式。
- runtime queue 是否只保留内存队列，还是提前落轻量待消费记录。
- 长任务“先回复、后完成”的第一版用户文案和状态对象形式。
- “后台挂起任务”是否需要单独能力面，以及那一层是否需要接入更强工具或独立 skill。
- 后续用户视角飞书事件接入后，`weight_level` 到提醒策略的具体映射。
- 后续用户视角飞书事件接入后，权重 skill 输出格式。
- 后续用户视角飞书事件接入后，硬规则阈值，如尝试轮数上限。

### 分点开发顺序

本阶段必须按单功能点推进，一次只开发一个主功能点，不在一轮内同时改动多块主链文件。

### 任务清单

- [x] 第 1 点：新增正式 runtime service 骨架，只建立 queue、worker 和最小状态对象，不接业务判断。
- [x] 第 2 点：让 `app.py` 启动后自动拉起飞书监听和正式 runtime worker。
- [x] 第 3 点：把感知记录接入 runtime queue，只做入队与消费占位，不接完整 agent 执行。
- [x] 第 4 点：新增统一反馈接口，收口文本回复和状态回馈，不让模型自由调用发消息工具。
- [x] 第 5 点：把正式 loop 接到现有 Agent 基架能力上，支持多轮模型调用和多轮 tool call。
- [x] 第 6 点：迁移 `/feishu` 命令为状态查看/控制类命令，不再承担启动监听语义。
- [x] 第 7 点：迁移 `/chat` 为非阻塞 debug 任务入口，并将正式 runtime 与 debug loop 的共享执行核心迁移到 `src/dutyflow/agent/core_loop.py`。
- [x] 第 8 点：确认“第一版权重判断、面向用户的硬规则判断和决策留痕”依赖后续用户视角飞书事件接入，当前移出 Step 6 实现范围，延后到后续阶段。
- [x] 第 9 点：为新增 `.py` 文件添加自测入口。
- [x] 第 10 点：编写对应测试文件。
- [x] 第 11 点：执行本阶段完整链路检查。

### 人工确认

- [x] 确认 `/chat` 调试入口最终命令形态。
  - 当前第一版采用 `run/status/latest` 风格，并保留 `/chat 用户输入` 作为 `/chat run 用户输入` 简写。
- [ ] 确认第一版长任务即时回复文案。
- [ ] 确认高权重、紧急、责任三类规则的第一版阈值。

## Step 7: 任务状态、审批中断与恢复

状态：进行中。已完成前 10 个功能点：`TaskStore + task_<id>.md` 最小任务状态存储、`TaskSchedulerService` 到时扫描与入队信号发出、审批记录存储、任务中断记录、后台任务入口工具、审批创建工具、审批恢复工具、后台任务 worker 独立执行面、飞书卡片/按钮审批入口与恢复链、Agent State 控制快照接入。本阶段测试已通过；真实后台 agent 执行器仍作为后续功能点单独推进。

### 最终效果

系统可以把适合即时完成的请求在前台直接处理，把长线任务、定时任务和等待审批的任务沉淀为后台任务；主链路先向用户返回“已开始处理 / 已安排执行”的自然语言确认，再由后台任务系统继续推进，并在完成、等待审批或恢复时通过飞书回馈用户。

### 验收标准

- 可区分“前台即时完成”和“后台挂起任务”两类请求。
- 任务 Markdown 文件创建、更新和状态流转正常。
- 到时任务可被后台调度器发现并入后台执行队列。
- 审批请求写入 `data/approvals/pending/`，审批完成后写入或移动到 `data/approvals/completed/`。
- 后台任务命中敏感动作时，任务进入 `waiting_approval`，并通过飞书卡片/按钮向用户发起审批。
- 审批通过后可恢复原任务链路；`rejected`、`deferred`、`expired` 不执行原动作。
- 主链路不因某个后台任务等待审批、等待时间或长时间执行而停止。
- 用户在任务创建时收到自然语言确认；后台完成后可再次收到状态更新或结果回推。

### 核心设计边界

- 当前 Step 7 先支持两类后台任务：
  - 一次性定时任务
  - 等待审批后恢复的长线任务
- 当前不实现：
  - 周期任务
  - DAG / 多阶段编排
  - 模型自由拆分多子任务
  - 开发期 CLI tools 进入后台任务默认能力面
- “只是回答问题、查资料、几轮 tool call 即可完成”的请求，不创建后台任务，仍由前台正式 loop 直接完成并回信。
- 后台任务能力面与主 loop 能力面分离；后续后台 profile 可单独白名单接入更强工具，但不默认继承主 loop 的全部能力。
- 任务、审批和恢复以本地 Markdown 文件为锚点，不允许只存在于内存结构中。
- 【已实现】Agent State 接入当前采用可见控制快照方式：
  `data/state/agent_control_state.md` 由任务、审批和飞书事件链路同步生成。
- 本节如与旧任务状态或旧审批链文案冲突，以当前讨论结果为准；具体字段和枚举在实现前同步更新 `docs/DATA_MODEL.md`。

### 运行模型

- 前台正式 loop：
  - 判断请求是即时完成还是后台挂起。
  - 即时完成则继续当前多轮执行链。
  - 需要后台处理时，创建任务并给用户即时确认。
- 后台调度器：
  - 独立于飞书监听和前台 runtime worker。
  - 周期扫描 `data/tasks/`，发现到时、可恢复、可继续推进的任务后入后台队列。
- 后台任务 worker：
  - 串行或低并发消费后台队列。
  - 按任务携带的 execution profile 执行。
  - 命中审批则挂起任务并发送审批卡片。
- 审批恢复链：
  - 用户在飞书端点击按钮或完成交互回调。
  - 系统更新审批记录与任务状态。
  - 任务按 `resume_point + resume_payload` 恢复。

### 任务对象与状态

- 任务主锚点：
  - `data/tasks/task_<id>.md`
- 审批锚点：
  - `data/approvals/pending/approval_<id>.md`
  - `data/approvals/completed/approval_<id>.md`
- 第一版任务状态建议以当前讨论为准：
  - `queued`
  - `scheduled`
  - `running`
  - `waiting_approval`
  - `blocked`
  - `completed`
  - `failed`
  - `cancelled`
  - `expired`
- 第一版任务对象除现有 `task_state.v1` 字段外，预计还需要补充：
  - `run_mode`
  - `scheduled_for`
  - `execution_profile`
  - `requested_capabilities`
  - `resolved_skills`
  - `resolved_tools`
  - `resume_point`
  - `resume_payload`
  - `last_result_summary`
  - `next_retry_at`

### 后台任务入口

- 后台任务入口应作为高层业务 tool 暴露给模型，而不是裸 `spawn_subagent(...)`。
- 第一版建议以：
  - `create_background_task`
  - `schedule_background_task`
  作为主要入口。
- 该类 tool 允许模型表达：
  - 任务目标
  - 成功标准
  - 是否需要后台执行
  - 何时执行
  - 希望优先使用哪些能力类别、skills、tools
- 但系统必须做二次裁决：
  - 校验 capability / skill / tool 是否在后台 profile 白名单内
  - 自动解析真正的 execution profile
  - 拒绝开发期 CLI tools 或不允许的能力面
- 模型只能“建议能力面”，不能直接操纵 raw registry、raw subagent 或任意工具集。

### 用户回馈与审批方式

- 创建后台任务后，用户应先收到“任务已开始 / 已安排在某时执行”的即时确认。
- 该确认文案不使用单条硬编码语句：
  - 系统先把任务创建结果和状态注入上下文
  - 再由模型生成自然语言回复
  - 统一反馈接口负责真正发送
- 同时保留模板 fallback，避免模型失败时用户收不到确认。
- 第一版后台任务即时确认文案风格固定为：
  - 简洁
  - 稳重
  - 不夸张承诺
- 后台任务命中敏感动作后，不再走 CLI `input()`：
  - 创建审批记录
  - 更新任务为 `waiting_approval`
  - 通过飞书卡片 / 按钮 / 交互回调向用户发起审批
- 第一版飞书审批卡片统一使用最基础、简洁的固定样式。
- 所有审批卡片共用同一套样式约束，不按任务类型自由变化。
- 审批卡片的结构、按钮动作和恢复入口由系统固定，不交给模型自由生成。

### 恢复边界

- 审批恢复围绕任务执行进行，而不是围绕单次 tool call 直接恢复。
- 恢复最小锚点包括：
  - `task_id`
  - `approval_id`
  - `resume_point`
  - `resume_payload`
  - `execution_profile`
- 只有 `approved` 可以恢复原任务链路。
- `rejected`、`deferred`、`expired` 只更新任务状态，不继续执行原动作。
- 审批超时后：
  - 审批记录标记为 `expired`
  - 任务状态和下一步动作必须写回持久化 Markdown
  - 第一版按“类似定时任务”的方式继续保留在任务系统中
  - 在用户下一次与系统发生消息交互时，系统发现存在“审批超时但未最终处置”的任务，应再次询问用户是继续执行还是弃用该任务
  - 在用户重新明确前，不自动恢复原动作

### Agent State 接入

- 【已实现】`src/dutyflow/agent/control_state_store.py` 负责刷新 `data/state/agent_control_state.md`，该文件是人工可读控制快照，不替代单次 loop 内的内存 `AgentState`。
- 【已实现】快照 frontmatter 记录 `current_model`、`permission_mode`、`active_task_ids`、`waiting_approval_task_ids`、`last_event_id`。
- 【已实现】快照正文记录 `Runtime`、`Task Control`、`Recovery`、`Recovery Scopes`，其中任务控制表从 `data/tasks/task_<id>.md` 汇总生成。
- 【已实现】同步点包括后台任务创建、定时任务到时入队、后台 worker 任务状态变化、审批进入等待、审批恢复决策、飞书主链事件与审批卡片事件处理。
- 【边界】当前不让业务工具直接修改 `agent/state.py` 中的内存态；工具链仍通过 `ToolResultEnvelope` 和现有 AgentLoop 规则回写消息流。

### 人工测试待修复问题

- 【已修复】飞书审批卡片按钮曾返回错误 `200671`；根因是 SDK 长连接忽略 `card` frame，且真实测试中 `card.action.trigger` 也可能以 `event` frame 投递，SDK typed dispatcher 会因毫秒级或微秒级时间戳反序列化抛出 `year 58295 is out of range`。当前新旧卡片回调都会先识别原始 payload，再绕过 SDK typed dispatcher 进入接入层；人工复测已通过，`approval_a53408ffe67e` 已从 pending 转入 completed，关联任务 `task_5ad1cabd1a74` 已记录 `approval_status=approved`。
- 【已修复】模型曾生成过去时间作为 `scheduled_for`，导致定时任务被调度器立即入队并进入 `blocked`；当前正式 runtime 已向模型注入消息接收时间和当前本地系统时间，`schedule_background_task` contract 已要求带时区的未来绝对时间，后台任务入口服务层也会拒绝非未来时间，定时任务摘要会写入绝对计划时间。
- 【待修复】审批 `expires_at` 仍需要同样补充未来时间校验，避免审批有效期被模型写成过去时间。
- 【待修复】模型可能先调用 `create_approval_request` 并传入不存在的 task_id，再补创建任务；后续需要约束“先创建任务，再基于真实 task_id 发审批”的调用顺序。
- 【待修复】后台任务即时回复存在过度承诺，例如“审批通过后将自动执行”，但真实后台 agent 执行器尚未接入；后续需要收紧 system prompt 或 fallback 文案边界。

### 涉及文件、类、方法、模块

- `src/dutyflow/tasks/task_state.py`
  - `TaskState`
  - `TaskStore`
  - `create_task`
  - `update_task_status`
- `src/dutyflow/tasks/task_scheduler.py`
  - `TaskSchedulerService`
  - `scan_due_tasks`
  - `enqueue_due_task`
- `src/dutyflow/approval/approval_flow.py`
  - `ApprovalRecord`
  - `ApprovalService`
  - `create_approval`
  - `resolve_approval`
- `src/dutyflow/approval/task_interrupt.py`
  - `TaskInterrupt`
  - `create_interrupt`
  - `resume_interrupt`
- `src/dutyflow/agent/background_task_worker.py`
  - `BackgroundTaskWorker`
  - `run_task`
  - `resume_task`
- `src/dutyflow/agent/control_state_store.py`
  - `AgentControlStateStore`
  - `sync_agent_control_state`
- `src/dutyflow/agent/tools/approval_tools.py`
  - `create_approval_request`
  - `resume_after_approval`
- `src/dutyflow/agent/tools/task_tools.py`
  - `create_background_task`
  - `schedule_background_task`
- `src/dutyflow/feedback/gateway.py`
- `data/tasks/`
- `data/approvals/pending/`
- `data/approvals/completed/`
- `test/test_task_scheduler.py`
- `test/test_task_state.py`
- `test/test_approval_flow.py`
- `test/test_task_interrupt.py`

### 未敲定问题

- 第一版后台任务是否允许低并发，还是继续只保留单 worker。
- `create_background_task` / `schedule_background_task` 的最终 contract。
- `resume_token` 的生成方式和生命周期。

### 任务清单

- [x] 实现任务状态存储。
- [x] 实现后台任务调度器。
- [x] 实现审批记录存储。
- [x] 实现任务中断记录。
- [x] 实现后台任务入口工具。
- [x] 实现审批创建工具。
- [x] 实现审批恢复工具。
- [x] 将后台任务 worker 接入正式 runtime 之外的独立执行面。
- [x] 将飞书卡片/按钮审批接入反馈与恢复链。
- [x] 接入 Agent State。
- [x] 为新增 `.py` 文件添加自测入口。
- [x] 编写对应测试文件。
- [x] 执行本阶段完整链路检查。

### 人工确认

- [x] 确认飞书端审批卡片形式。
  - 第一版统一使用最基础、简洁的固定卡片样式，所有审批卡片保持同一套样式。
- [x] 确认审批超时策略。
  - 审批超时后写入持久化 Markdown，审批记录标记为 `expired`，任务继续保留在任务系统中；下一次模型发送消息时发现存在未最终处置的超时任务，应再次询问用户是继续执行还是弃用。
- [x] 确认第一版后台任务即时确认文案风格。
  - 风格为简洁、稳重。

## Step 8: 上下文摘要、清理与压缩

### 最终效果

系统可保存近场上下文、生成轻量摘要、处理 `/clear` 和 `/compress` CLI 命令，并保证上下文压缩不会丢失任务目标、关键事实、责任关系和下一步。

### 验收标准

- 上下文摘要写入 `data/contexts/`。
- `/compress` 可生成或刷新上下文摘要。
- `/clear` 只清理允许清理的临时上下文，不删除审计链路。
- 压缩结果保留当前目标、已知事实、身份责任、决策上下文和下一步。

### 涉及文件、类、方法、模块

- `src/dutyflow/context/short_context.py`
  - `ContextSummary`
  - `ContextManager`
  - `save_summary`
  - `compress_context`
  - `clear_transient_context`
- `src/dutyflow/cli/main.py`
- `data/contexts/`
- `test/test_context_short_context.py`

### 未敲定问题

- 是否调用真实模型生成摘要，还是先使用规则化字符串摘要。
- 清理上下文的边界清单。

### 任务清单

- [ ] 实现上下文摘要文件。
- [ ] 实现压缩逻辑。
- [ ] 实现安全清理逻辑。
- [ ] 接入 CLI `/compress`。
- [ ] 接入 CLI `/clear`。
- [ ] 为新增 `.py` 文件添加自测入口。
- [ ] 编写测试文件。
- [ ] 执行本阶段完整链路检查。

### 人工确认

- [ ] 确认是否允许真实模型参与上下文压缩。

## Step 9: CLI 控制台与可观察性

### 最终效果

应用启动后在同一终端提供开发者 CLI 控制台，支持 `/...` 命令进行调试观察，不提供模型对话功能。

### 验收标准

- `/health` 返回配置、存储、`agent_control_state.md` 快照文件和日志状态。
- `/model` 可查看或切换当前模型配置名。
- `/logs` 可查看日志摘要。
- `/tasks` 可查看任务状态。
- `/approvals` 可查看审批状态。
- `/clear`、`/compress` 已接入上下文管理。
- CLI 敏感命令遵守权限和安全约束。

### 涉及文件、类、方法、模块

- `src/dutyflow/cli/main.py`
  - `CliConsole`
  - `parse_command`
  - `handle_health`
  - `handle_model`
  - `handle_logs`
  - `handle_tasks`
  - `handle_approvals`
  - `handle_clear`
  - `handle_compress`
- `src/dutyflow/app.py`
- `src/dutyflow/logging/audit_log.py`
- `test/test_cli_commands.py`

### 未敲定问题

- `/model` 是否只切换配置名，还是立即影响运行中 Agent。
- CLI 是否需要历史命令记录。

### 任务清单

- [ ] 实现命令解析。
- [ ] 实现 `/health`。
- [ ] 实现 `/model`。
- [ ] 实现 `/logs`。
- [ ] 实现 `/tasks`。
- [ ] 实现 `/approvals`。
- [ ] 接入 `/clear` 和 `/compress`。
- [ ] 为新增 `.py` 文件添加自测入口。
- [ ] 编写测试文件。
- [ ] 执行本阶段完整链路检查。

### 人工确认

- [ ] 确认 CLI 命令最终命名。

## Step 10: 用户回馈层与飞书占位/真实切换

### 最终效果

系统可以生成提醒、摘要、审批请求和状态更新的回馈内容。真实飞书未接入时，通过占位接口返回明确字符串；真实接入条件满足后可替换为飞书发送。

### 验收标准

- 回馈内容能解释为什么提醒、为什么审批、当前状态是什么。
- 占位回馈不伪装真实发送。
- 真实飞书接口留有方法和配置入口。
- 回馈动作如代表用户表达立场，必须审批。

### 涉及文件、类、方法、模块

- `src/dutyflow/feishu/feedback.py`
  - `FeishuFeedbackService`
  - `send_reminder`
  - `send_summary`
  - `send_approval_request`
  - `send_status_update`
- `src/dutyflow/feishu/client.py`
- `src/dutyflow/approval/approval_flow.py`
- `test/test_feishu_feedback.py`

### 未敲定问题

- 飞书消息卡片或文本消息格式。
- 飞书审批确认交互方式。
- 飞书真实 API 字段。

### 任务清单

- [ ] 实现提醒回馈占位。
- [ ] 实现摘要回馈占位。
- [ ] 实现审批请求回馈占位。
- [ ] 实现状态更新回馈占位。
- [ ] 将敏感回馈接入审批层。
- [ ] 为新增 `.py` 文件添加自测入口。
- [ ] 编写测试文件。
- [ ] 执行本阶段完整链路检查。

### 人工确认

- [ ] 提供真实飞书应用和测试会话前，不接真实发送。

## Step 11: Demo 不实现能力的接口占位

### 最终效果

Demo 期不实现的能力在程序中留有接口，但不接入真实数据，不执行真实能力，只返回明确字符串占位，并在代码注释中说明“Demo 期不实现”。其中长期记忆先固定结构化 Markdown 范式和 `search / add / update` 工具 contract，但仍不接入 Demo 主链路。

### 长期记忆工具范式

- `long_term_memory` 后续采用与 `contact_knowledge` 一致的两段式工具设计：
  - `search_long_term_memory_headers`
  - `get_long_term_memory_detail`
  - `add_long_term_memory`
  - `update_long_term_memory`
- 第一轮查询优先读取 `data/memory/index.md`，返回轻量 header。
- 第二轮再按 `memory_id` 打开 `data/memory/entries/memory_<id>.md`，只返回允许 section。
- 长期记忆相关工具后续复用 Step 4 规划的内部通用解析层，不再为 memory 单独复制一套 Markdown 解析逻辑。

### 验收标准

- 长期记忆接口存在但返回占位。
- RAG/知识库接口存在但返回占位。
- MCP/外部工具接口存在但返回占位。
- 多 Agent 接口存在但返回占位。
- 完整联系人画像接口存在但返回占位。
- 复杂规划接口存在但返回占位。
- 长期记忆的数据目录、索引结构、单条记录结构和维护工具 contract 已在文档中固定。
- 未来 workspace 模式的路径规划已确认，但当前运行期仍不切换到独立 workspace。
- 测试验证这些占位接口不会被 Demo 主链路误调用。

### 涉及文件、类、方法、模块

- `src/dutyflow/agent/placeholders.py`
  - `LongTermMemoryPlaceholder`
  - `RagPlaceholder`
  - `McpPlaceholder`
  - `MultiAgentPlaceholder`
  - `ContactProfilePlaceholder`
  - `PlanningPlaceholder`
- `src/dutyflow/agent/tools/memory_tools.py`
  - `search_long_term_memory_headers`
  - `get_long_term_memory_detail`
  - `add_long_term_memory`
  - `update_long_term_memory`
- `src/dutyflow/storage/structured_markdown.py`
- `test/test_demo_placeholders.py`

### 未敲定问题

- 占位接口最终是否单独放在 `agent/placeholders.py`，或分散到对应模块。
- 长期记忆工具在未来接入时，是单独归到 `memory/` 模块，还是继续通过内部工具目录统一管理。
- `workspace_root` 切换后，哪些目录继续保留在仓库内作为 fixture，哪些彻底迁到用户工作区。
- 长期记忆进入真实实现时，`Memory Body` 返回是否默认裁剪，还是仅在明确需要时返回。

### 任务清单

- [ ] 实现长期记忆占位接口。
- [ ] 实现 RAG 占位接口。
- [ ] 实现 MCP 占位接口。
- [ ] 实现多 Agent 占位接口。
- [ ] 实现完整联系人画像占位接口。
- [ ] 实现复杂规划占位接口。
- [ ] 每个占位接口返回明确字符串。
- [ ] 每个占位接口有中文注释说明 Demo 期不实现。
- [ ] 为长期记忆补充两段式工具 contract：
  - `search_long_term_memory_headers`
  - `get_long_term_memory_detail`
  - `add_long_term_memory`
  - `update_long_term_memory`
- [ ] 为未来 `workspace_root` 目录结构补充统一配置入口设计说明。
- [ ] 编写测试文件。
- [ ] 执行本阶段完整链路检查。

### 人工确认

- [ ] 确认占位接口命名是否需要与未来真实能力保持一致。
- [ ] 确认未来安装式运行时的默认工作区是否采用 `~/DutyFlow/workspace/`。

## Step 12: 完整 Demo 链路验收

### 最终效果

使用本地 fixture 或真实飞书测试事件，跑通 Demo 期完整闭环。

### 验收标准

完整链路必须证明：

1. 事件可进入系统。
2. 身份与来源可补全。
3. 权重可判断。
4. Agent State 硬规则可参与最终决策。
5. 任务可创建和查看。
6. 敏感动作可进入审批。
7. 审批结果可恢复原任务链路。
8. 用户回馈可生成。
9. 本地 Markdown 留痕完整。
10. CLI 可查看健康、日志、任务、审批和上下文状态。

### 涉及文件、类、方法、模块

- `src/dutyflow/app.py`
- 所有 `src/dutyflow/` Demo 主链路模块
- `test/test_full_chain.py`
- `docs/TESTING.md`

### 未敲定问题

- 是否使用真实飞书事件作为最终验收输入。
- 是否使用真实模型 API 做权重解释和上下文压缩。

### 任务清单

- [ ] 准备完整 fixture 数据。
- [ ] 跑通本地完整链路。
- [ ] 记录所有输出 Markdown 文件。
- [ ] 验证 CLI 可观察。
- [ ] 验证占位接口不会伪装真实功能。
- [ ] 验证所有阶段测试通过。
- [ ] 更新测试结果记录。
- [ ] 标记 Demo 闭环完成或记录阻塞。

### 人工确认

- [ ] 如需真实飞书验收，开发者提供飞书应用、测试会话和授权环境。
- [ ] 如需真实模型验收，开发者提供模型 API 配置。

## 当前阻塞与风险记录

- [ ] Step 5 已完成真实 p2p 私聊接入与 `/bind` bootstrap，但群聊 `@Bot` 事件和消息资源获取仍待人工补测；`DUTYFLOW_FEISHU_EVENT_CALLBACK_URL` 仍为预留字段。
- [ ] Step 5 真实长连接已确认会收到 `im.chat.member.bot.added_v1`，但当前未注册处理器，拉机器人入群时会刷出 `processor not found` 错误日志；需在后续决定接入或显式忽略。
- [ ] 模型 API 的具体 provider、base URL、模型名和调用格式未敲定；真实 key 提供后需要补跑完整链路。
- [ ] 权重 skill 第一版提示词和输出格式未敲定。
- [ ] 审批在飞书端的交互形式未敲定。
- [ ] Step 2 的权限模式边界、CLI 审批默认拒绝策略和最小恢复落点仍需在实现时进一步收紧。

## 阶段完成记录

| step | status | completed_at | notes |
|---|---|---|---|
| Step 0 | completed | 2026-04-17 | 已完成项目骨架、入口迁移、uv run 入口和 Step 0 测试。 |
| Step 1 | completed | 2026-04-17 | 已完成配置入口、Markdown 存储、按天审计日志、运行目录初始化和 Step 1 测试；真实模型与飞书链路待配置后补测。 |
| Step 2 | completed | 2026-04-23 | 已完成最小 agent 基架控制面、权限、恢复、审计和 Hook 预留接口，并通过阶段回归。 |
| Step 3 | completed | 2026-04-23 | 已完成 skills 解析层、`load_skill` 内部工具、system message manifest 注入和阶段测试。 |
| Step 4 | completed | 2026-04-25 | 已完成身份、来源、责任查询主链路，以及联系人知识两段式工具与对应测试。 |
| Step 5 | in_progress | 2026-04-27 | 已完成真实 p2p 私聊链路、`/bind` 回填 `.env`、Bot 回信与 `/feishu doctor` 诊断；群聊 `@Bot` 与消息资源获取仍待补测。 |
| Step 6 | completed | 2026-04-28 | 已完成正式 Agent Loop 与常驻运行编排；面向用户的硬规则判断与决策留痕延后到后续用户视角飞书事件接入后收口。 |
| Step 7 | in_progress | 2026-04-29 | 已完成前 9 个功能点：任务存储、调度器、审批记录、中断记录、后台任务入口工具、审批创建工具、审批恢复工具、后台任务 worker 独立执行面和飞书卡片/按钮审批恢复链。 |
| Step 8 | pending |  |  |
| Step 9 | pending |  |  |
| Step 10 | pending |  |  |
| Step 11 | pending |  |  |
| Step 12 | pending |  |  |
