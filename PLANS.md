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
- `src/dutyflow/agent/loop.py`
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
- `src/dutyflow/agent/loop.py`
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

## Step 5: 飞书接入层与原始事件入口

状态：进行中。已完成 `.env` 字段收敛、`EnvConfig` 扩展、fixture 事件输入、原始事件最小规范化、账号空间归属字段、`event_id/message_id` 去重、事件 Markdown 落盘、官方 `lark_oapi` sample 风格的默认长连接 wiring，以及 CLI `/feishu fixture`、`/feishu listen`、`/feishu latest`、`/feishu doctor` 本地调试入口。当前已支持 bootstrap 模式：收到私聊 `/bind` 后，会从 `im.message.receive_v1` 中提取 `tenant_key`、`sender open_id` 和 `chat_id`，回填 `.env` 对应字段，并由 Bot 回一条绑定成功消息。真实飞书人工验证已完成 p2p 私聊链路：长连接接入、原始事件打印、事件 Markdown 落盘、`/bind` 回填 `.env`、Bot 回信均已跑通；群聊 `@Bot` 和消息资源获取仍待补测。

### 最终效果

系统通过飞书开放平台应用和 Python SDK 长连接接收原始事件，完成最小接入闭环：建立应用身份、接收 Bot 可见消息事件、按账号空间归属原始事件、做最小去重并落盘，不在本阶段做语义解析、业务判断或 Agent Loop 注入。

本阶段初版边界固定为：

- 单 `app_id`
- 单 `tenant_key`
- 单 `owner_open_id`
- 单 Bot 汇报目标
- 只接收：
  - 用户与 Bot 的私聊消息
  - 群聊中 `@Bot` 的消息

本阶段必须显式预留后续扩展方向：

- 后续项目需要获取完整的“用户可见信息”，而不只是当前 Bot 可见信息。
- 该扩展不在本阶段实现，但账号空间、配置入口和接入抽象必须允许后续接入用户 OAuth 链路。

本阶段不允许按“读取本机飞书客户端登录态”设计；所有接入必须基于飞书开放平台应用、凭证、Token 和事件订阅。

### 验收标准

- 本地 Agent 可使用飞书 SDK 长连接接收原始事件，不依赖本机飞书客户端是否打开。
- 事件回调只做最小归属、去重、落盘和快速确认；不在回调中执行重逻辑。
- 初版只接收：
  - 用户与 Bot 的私聊消息
  - 群聊中 `@Bot` 的消息
- 原始事件记录落盘到 `data/events/`，保留最小接入字段和完整原始 payload。
- 接入层必须区分以下空间，不得混用：
  - `installation_scope = app_id + tenant_key`
  - `owner_profile = app_id + tenant_key + owner_open_id`
  - `sender_subject = app_id + tenant_key + sender_open_id`
  - `chat_binding = app_id + tenant_key + chat_id`
- 接入层必须至少实现两层去重：
  - `event_dedup_key = event_id`
  - `message_dedup_key = message_id`
- 如涉及消息资源拉取，必须以消息内原始资源标识做补充去重，例如 `message_id + file_key`。
- 所有显式配置项，包括飞书 Bot 和后续用户权限相关的 `app_id`、`secret`、验证字段、加密字段、Owner 标识、OAuth 配置、显式 Token/Refresh Token 占位，必须统一来自 `.env`，并通过 `config` 模块读取。
- 本阶段即使未接入完整用户 OAuth，也必须为后续“用户可见信息”能力预留配置链路和抽象边界。
- 接入层不做：
  - 身份解析
  - 联系人关系判断
  - 权重判断
  - 任务生成
  - Agent Loop 注入

### Step 5.1: 感知记录层设计

本小节定义 Step 5 之后、正式 Agent Loop 之前的“感知记录层”规划。该层的职责不是做业务判断，而是把飞书原始事件整理成后续 loop、责任工具和内容解析工具更容易消费的标准输入。

状态：已完成第一版实现。当前 `FeishuIngressService` 会在原始事件落盘后同步生成 `data/perception/` 感知记录，并通过 `PerceptionRecordService.build_loop_input(...)` 向后续 loop 暴露标准读取接口。

#### 当前完成项

- [x] 新增 `src/dutyflow/perception/store.py`，落地 `PerceptionRecordService` 和 `PerceivedEventRecord`。
- [x] 感知记录按“一条有意义事件一个 Markdown 文件”落盘到 `data/perception/YYYY-MM-DD/per_<message_id>.md`。
- [x] 飞书接入层在写入 `data/events/` 原始事件后，会同步生成对应感知记录。
- [x] 感知记录已落地第一版 frontmatter：`schema / source_event_id / message_id / trigger_kind / chat_id / sender_open_id / message_type / raw_event_file`。
- [x] 感知记录已落地第一版正文 sections：`Summary / Extracted Text / Entities / Parse Targets / Lookup Hints / Raw Reference`。
- [x] 当前感知层已提取 `message_type`、`raw_text`、`content_preview`、`mentions_bot`、`mentioned_open_ids`。
- [x] 当前感知层已提取第一版 `parse_targets`，覆盖文件、图片、链接三类稳定线索。
- [x] 当前感知层已输出第一版确定性查询提示：`contact_lookup_hint`、`source_lookup_hint`、`responsibility_lookup_hint`。
- [x] 当前感知层已向后续 loop 暴露 `build_loop_input(record_id / message_id)` 标准读取接口。
- [x] `docs/DATA_MODEL.md` 已同步 `dutyflow.perceived_event.v1` 结构。
- [x] `test/test_feishu_perception.py` 已覆盖文本、文件、群聊 `@Bot` 三类感知记录场景。

#### 后续未完成项

- [ ] 正式 Step 6 事件驱动 loop 仍未接入感知记录读取接口。
- [ ] `im.chat.member.bot.added_v1` 等系统事件是否生成感知记录，仍待单独定规。
- [ ] 文档、飞书文档链接、更多消息类型的 `parse_targets` 细化规则仍待补充。
- [ ] 感知记录到任务层、上下文层的衔接尚未开始。

#### 设计目标

- 感知层只处理“已进入主链的有意义事件”，不把全部飞书事件都变成长期上下文。
- 感知层结果不能只保留在内存中，必须持久化为独立 Markdown 记录，便于后续 loop、任务层和人工检查复用。
- 后续 Agent Loop 默认读取感知记录，不直接读取飞书原始事件文件；原始事件文件只作为审计事实源、调试回溯和解析工具兜底输入。
- 感知层只做确定性结构提取和确定性改写，不做联系人关系推理、责任判断、权重判断、任务判断。

#### 层级边界

- 飞书接入层：
  - 负责 SDK、长连接、消息收发、原始事件落盘、资源下载接口占位。
  - 输出 `data/events/` 下的原始事件记录。
- 感知记录层：
  - 负责从原始事件中抽取稳定字段、触发类型、附件线索、mentions 和后续查询提示。
  - 输出 `data/perception/` 下的标准化感知记录。
- 内容解析层：
  - 负责按需下载和解析图片、文件、网页、飞书文档。
  - 不在感知层自动执行；后续按工具调用。
- Agent Loop：
  - 默认以感知记录为输入，再按需调用身份、责任、内容解析等工具。

#### 哪些事件生成感知记录

- 第一版只为“进入主链的关键输入”生成感知记录：
  - 用户私聊 Bot
  - 群聊 `@Bot`
  - 包含文件、图片、文档、链接等明确后续解析目标的 Bot 可见消息
- 纯噪声或当前不进入主链的事件可以只保留原始事件记录，不生成感知记录。
- `im.chat.member.bot.added_v1` 这类系统事件后续可决定是否生成独立感知记录；当前先不纳入第一版必需范围。

#### 文件划分方式

感知记录不按“每天一个文件”“每个联系人一个文件”或“每个群一个文件”聚合，而是：

- 一条有意义事件对应一个感知记录文件
- 日期仅用于目录分片，不承担语义聚合

建议路径：

```text
data/perception/YYYY-MM-DD/per_<message_id>.md
```

原因：

- 便于用 `message_id` 做稳定去重和稳定追溯。
- 便于后续 loop 只读取当前事件的最小上下文。
- 便于后续对单条感知记录重算、覆盖或补齐，而不污染其它事件。

#### 感知记录与原始事件记录的关系

- 原始事件文件：
  - 路径：`data/events/...`
  - 用途：保存完整原始 payload、最小 routing 字段、审计事实源
- 感知记录文件：
  - 路径：`data/perception/...`
  - 用途：保存后续 loop 和工具要消费的标准事件视图

约束：

- 感知记录必须保留指向原始事件文件的稳定引用。
- loop 默认读取感知记录；只有内容解析工具、调试工具或审计链路才回看原始事件文件。

#### 感知记录建议结构

建议 frontmatter：

```yaml
schema: dutyflow.perceived_event.v1
id: per_<message_id>
source_event_id: evt_<message_id>
message_id: <message_id>
received_at: <ISO-8601>
event_type: im.message.receive_v1
trigger_kind: p2p_text
chat_type: p2p
chat_id: <chat_id>
sender_open_id: <sender_open_id>
message_type: text
mentions_bot: true
has_attachment: false
attachment_kinds: ""
raw_event_file: data/events/YYYY-MM-DD/evt_<message_id>.md
status: perceived
updated_at: <ISO-8601>
```

第一版 `trigger_kind` 建议枚举：

- `p2p_text`
- `p2p_file`
- `p2p_image`
- `p2p_link`
- `group_at_bot_text`
- `group_at_bot_file`
- `group_at_bot_image`
- `group_at_bot_link`

正文建议：

```md
# Perceived Event per_<message_id>

## Summary

一句话说明这条输入是什么。

## Extracted Text

- raw_text:
- content_preview:
- mention_text:

## Entities

| kind | value | source |
|---|---|---|
| sender | <sender_open_id> | sender_open_id |
| chat | <chat_id> | chat_id |
| mention | <mentioned_open_id> | mentions |

## Parse Targets

| target_id | target_type | file_key | file_name | url | required_tool |
|---|---|---|---|---|---|

## Lookup Hints

- contact_lookup_hint:
- source_lookup_hint:
- responsibility_lookup_hint:
- followup_needed:

## Raw Reference

- event_record: data/events/.../evt_<message_id>.md
```

#### 感知层必须提取的字段

- 事件路由字段：
  - `event_id`
  - `message_id`
  - `chat_id`
  - `chat_type`
  - `sender_open_id`
  - `event_type`
- 消息基础字段：
  - `message_type`
  - `raw_text`
  - `content_preview`
  - `mentions_bot`
- 附件与解析线索：
  - `has_attachment`
  - `attachment_kinds`
  - `file_key`
  - `file_name`
  - `doc token`
  - `url`
- 后续查询提示：
  - `contact_lookup_hint`
  - `source_lookup_hint`
  - `responsibility_lookup_hint`
  - `parse_targets`

#### 感知层允许做的改写

- 把 `sender_open_id` 改写成稳定的 `contact_lookup_hint`
- 把 `chat_id + chat_type` 改写成稳定的 `source_lookup_hint`
- 把文件、图片、链接、文档线索改写成 `parse_targets`
- 把 `mentions` 改写成 `mentions_bot` 和 `Entities` 表格

#### 感知层明确不做的事情

- 不做联系人关系推理
- 不做责任归属判断
- 不做高低优先级判断
- 不做任务生成
- 不直接下载或解析附件本体
- 不把自由文本直接压成主观摘要

#### 后续 Loop 读取约束

- 正式 Agent Loop 默认只从感知记录层读取输入。
- raw event 文件不作为默认主输入。
- 内容解析工具、调试工具、审计链路允许按 `raw_event_file` 引用回看原始事件。
- 感知记录层一旦落成，应作为 Step 6 以后事件驱动 loop 的标准输入接口。

### 涉及文件、类、方法、模块

- `src/dutyflow/config/env.py`
  - `EnvConfig`
  - `load_env_config`
  - `validate_env_config`
- `.env.example`
- `src/dutyflow/feishu/events.py`
  - `FeishuEventAdapter`
  - `normalize_raw_event`
  - `build_event_envelope`
  - `create_local_fixture_event`
- `src/dutyflow/feishu/client.py`
  - `FeishuClient`
  - `connect_long_connection`
  - `fetch_message_resource`
  - `send_message`
- `src/dutyflow/feishu/runtime.py`
  - `FeishuIngressService`
  - `handle_raw_event`
  - `ack_event`
- `src/dutyflow/perception/store.py`
  - `PerceptionRecordService`
  - `PerceivedEventRecord`
  - `create_record`
  - `read_by_message_id`
  - `build_loop_input`
- `src/dutyflow/cli/main.py`
  - `/feishu fixture`
  - `/feishu listen`
  - `/feishu latest`
  - `/feishu doctor`
- `src/dutyflow/app.py`
  - `run_feishu_fixture_debug`
  - `start_feishu_listener_debug`
  - `get_latest_feishu_debug`
  - `start_feishu_doctor_debug`
  - `get_feishu_doctor_debug`
- `src/dutyflow/storage/markdown_store.py`
- `data/events/`
- `data/perception/`
- `test/test_feishu_events.py`
- `test/test_feishu_perception.py`

### 新增 `.env` 字段清单

本阶段对飞书接入配置统一收敛为以下类别。除非后续文档明确变更，字段名不再随实现过程临时改动。

- 已有模型与运行配置，继续保留：
  - `DUTYFLOW_MODEL_API_KEY`
  - `DUTYFLOW_MODEL_BASE_URL`
  - `DUTYFLOW_MODEL_NAME`
  - `DUTYFLOW_DATA_DIR`
  - `DUTYFLOW_LOG_DIR`
  - `DUTYFLOW_RUNTIME_ENV`
  - `DUTYFLOW_LOG_LEVEL`
  - `DUTYFLOW_PERMISSION_MODE`
- 飞书应用与事件接入初版必备：
  - `DUTYFLOW_FEISHU_APP_ID`
  - `DUTYFLOW_FEISHU_APP_SECRET`
  - `DUTYFLOW_FEISHU_EVENT_VERIFY_TOKEN`
  - `DUTYFLOW_FEISHU_EVENT_ENCRYPT_KEY`
  - `DUTYFLOW_FEISHU_EVENT_CALLBACK_URL`
  - `DUTYFLOW_FEISHU_EVENT_MODE`
  - `DUTYFLOW_FEISHU_TENANT_KEY`
  - `DUTYFLOW_FEISHU_OWNER_OPEN_ID`
  - `DUTYFLOW_FEISHU_OWNER_REPORT_CHAT_ID`
- 为后续“完整用户可见信息”能力预留但本阶段不启用：
  - `DUTYFLOW_FEISHU_OWNER_USER_ID`
  - `DUTYFLOW_FEISHU_OWNER_UNION_ID`
  - `DUTYFLOW_FEISHU_OAUTH_REDIRECT_URI`
  - `DUTYFLOW_FEISHU_OAUTH_DEFAULT_SCOPES`
  - `DUTYFLOW_FEISHU_OWNER_USER_ACCESS_TOKEN`
  - `DUTYFLOW_FEISHU_OWNER_USER_REFRESH_TOKEN`
  - `DUTYFLOW_FEISHU_OWNER_USER_TOKEN_EXPIRES_AT`

字段约定：

- `DUTYFLOW_FEISHU_EVENT_MODE` 第一版只允许：
  - `fixture`
  - `long_connection`
- `DUTYFLOW_FEISHU_OAUTH_DEFAULT_SCOPES` 使用逗号分隔字符串。
- `DUTYFLOW_FEISHU_OWNER_USER_TOKEN_EXPIRES_AT` 使用 ISO-8601 字符串。
- 后续即使启用用户 OAuth，也不额外引入散落在其它文件中的显式 Token；显式凭证入口仍集中在 `.env`。

### `EnvConfig` 目标字段表

| `.env` 键 | `EnvConfig` 字段 | Step 5 初版 | 说明 |
| --- | --- | --- | --- |
| `DUTYFLOW_MODEL_API_KEY` | `model_api_key` | 保留 | 现有模型调用配置，非飞书专属，但继续由统一配置模块管理。 |
| `DUTYFLOW_MODEL_BASE_URL` | `model_base_url` | 保留 | 现有模型调用地址。 |
| `DUTYFLOW_MODEL_NAME` | `model_name` | 保留 | 现有模型名称。 |
| `DUTYFLOW_FEISHU_APP_ID` | `feishu_app_id` | 必需 | 飞书开放平台应用标识。 |
| `DUTYFLOW_FEISHU_APP_SECRET` | `feishu_app_secret` | 必需 | 飞书开放平台应用密钥。 |
| `DUTYFLOW_FEISHU_EVENT_VERIFY_TOKEN` | `feishu_event_verify_token` | 必需 | 事件订阅校验字段。 |
| `DUTYFLOW_FEISHU_EVENT_ENCRYPT_KEY` | `feishu_event_encrypt_key` | 必需 | 事件加解密字段。 |
| `DUTYFLOW_FEISHU_EVENT_CALLBACK_URL` | `feishu_event_callback_url` | 预留 | 为后续 webhook/控制台配置保留；长连接初版不强依赖。 |
| `DUTYFLOW_FEISHU_EVENT_MODE` | `feishu_event_mode` | 必需 | 接入模式开关，第一版仅允许 `fixture` 或 `long_connection`。 |
| `DUTYFLOW_FEISHU_TENANT_KEY` | `feishu_tenant_key` | 必需 | 单租户初版的安装空间标识。 |
| `DUTYFLOW_FEISHU_OWNER_OPEN_ID` | `feishu_owner_open_id` | 必需 | 本地 Agent 服务对象的 owner 标识。 |
| `DUTYFLOW_FEISHU_OWNER_REPORT_CHAT_ID` | `feishu_owner_report_chat_id` | 必需 | Bot 默认回馈到 owner 的会话标识。 |
| `DUTYFLOW_FEISHU_OWNER_USER_ID` | `feishu_owner_user_id` | 预留 | 为后续用户 OAuth 与身份对齐保留。 |
| `DUTYFLOW_FEISHU_OWNER_UNION_ID` | `feishu_owner_union_id` | 预留 | 为后续跨应用/跨标识对齐保留。 |
| `DUTYFLOW_FEISHU_OAUTH_REDIRECT_URI` | `feishu_oauth_redirect_uri` | 预留 | 用户 OAuth 跳转地址。 |
| `DUTYFLOW_FEISHU_OAUTH_DEFAULT_SCOPES` | `feishu_oauth_default_scopes` | 预留 | 用户 OAuth 默认 scope，代码内解析成字符串列表。 |
| `DUTYFLOW_FEISHU_OWNER_USER_ACCESS_TOKEN` | `feishu_owner_user_access_token` | 预留 | owner 用户授权 access token，Step 5 不消费。 |
| `DUTYFLOW_FEISHU_OWNER_USER_REFRESH_TOKEN` | `feishu_owner_user_refresh_token` | 预留 | owner 用户授权 refresh token，Step 5 不消费。 |
| `DUTYFLOW_FEISHU_OWNER_USER_TOKEN_EXPIRES_AT` | `feishu_owner_user_token_expires_at` | 预留 | owner 用户 token 过期时间，代码内保留原始字符串。 |
| `DUTYFLOW_DATA_DIR` | `data_dir` | 保留 | 本地数据根目录。 |
| `DUTYFLOW_LOG_DIR` | `log_dir` | 保留 | 本地日志目录。 |
| `DUTYFLOW_RUNTIME_ENV` | `runtime_env` | 保留 | 本地运行环境标记。 |
| `DUTYFLOW_LOG_LEVEL` | `log_level` | 保留 | 日志级别。 |
| `DUTYFLOW_PERMISSION_MODE` | `permission_mode` | 保留 | 现有权限模式。 |

### 配置校验策略

- `EnvConfig` 继续作为唯一配置读取入口。
- `validate_env_config` 后续按模式分层校验：
  - 基础层：模型与本地运行目录字段继续保持现有校验。
  - `feishu_event_mode=fixture`：允许缺失真实飞书凭证，但要求事件模式字段合法。
  - `feishu_event_mode=long_connection`：要求飞书应用凭证、`tenant_key`、`owner_open_id`、`owner_report_chat_id` 齐全。
  - 用户 OAuth 相关字段默认不参与 Step 5 初版强校验；只有未来显式启用“完整用户可见信息”能力时再纳入必填。

### 未敲定问题

- 群聊 `@Bot` 事件的真实样例与第一版白名单范围补测。
- `im.chat.member.bot.added_v1` 当前已能在真实长连接中收到原始事件帧，但接入层尚未注册处理器；拉机器人入群时会出现 `processor not found` 错误日志，后续需决定是显式接入还是稳定忽略。
- Bot 拉取消息内图片、文件、音视频资源时所需的最小权限集合与真实响应结构。
- 后续从“Bot 可见信息”扩展到“完整用户可见信息”时，用户 OAuth 的落地边界和授权流程。

### 任务清单

- [x] 扩展 `.env.example` 与 `EnvConfig`，将飞书接入显式配置统一收口到 `.env`。
- [x] 增加 Owner、租户、Bot、用户 OAuth 预留字段的配置校验和读取链路。
- [x] 实现基于飞书 SDK 的长连接接入骨架。
- [x] 实现原始事件最小规范化，不做业务解析，只抽取接入层 routing 所需字段。
- [x] 实现账号空间归属：
  - `installation_scope`
  - `owner_profile`
  - `sender_subject`
  - `chat_binding`
- [x] 实现按 `event_id` 和 `message_id` 的最小去重逻辑。
- [x] 实现原始事件 Markdown 落盘。
- [x] 实现消息内原始资源获取接口。
- [x] 保留 Bot 发消息接口，但不在本阶段承接完整回馈逻辑。
- [x] 保留本地 fixture 事件输入，用于无真实飞书环境时测试接入层。
- [x] 为新增 `.py` 文件添加自测入口。
- [x] 编写 `test/test_feishu_events.py`。
- [x] 执行本阶段完整链路检查。

### 人工确认

- [x] 已提供飞书应用凭证，并确认事件订阅采用长连接模式完成真实 API 接入。
- [x] 已确认初版只接收 Bot 私聊和群聊 `@Bot` 消息，不纳入“别人私聊用户”的事件面。
- [x] 已通过私聊 `/bind` 回填 `tenant_key`、`owner_open_id` 和默认汇报目标。
- [x] 已获取真实 `im.message.receive_v1` 事件样例，并完成 p2p 私聊链路人工验证。
- [ ] 仍需补测群聊 `@Bot` 事件和消息资源获取场景。

## Step 6: 权重决策、硬规则与决策留痕

### 最终效果

系统可以基于事件、身份、来源、责任、上下文摘要、权重 skill 和 Agent State 硬规则，形成可解释决策，并写入决策留痕。

### 验收标准

- 权重 skill 只作为提示词补充。
- Agent State 和硬规则负责最终控制决策。
- 高权重任务不得直接忽略。
- 尝试轮数过多进入审批、重试或降级。
- 决策 trace 写入 `data/reports/trace_<id>.md`。

### 涉及文件、类、方法、模块

- `src/dutyflow/decision/weighting.py`
  - `WeightingDecision`
  - `evaluate_weight`
- `src/dutyflow/decision/rules.py`
  - `DecisionRuleEngine`
  - `apply_hard_rules`
- `src/dutyflow/agent/tools/decision_trace.py`
  - `record_decision_trace`
- `src/dutyflow/agent/state.py`
- `data/reports/`
- `test/test_decision_weighting.py`
- `test/test_decision_rules.py`

### 未敲定问题

- `weight_level` 到提醒策略的具体映射。
- 权重 skill 输出格式。
- 硬规则阈值，如尝试轮数上限。

### 任务清单

- [ ] 实现权重判断结构。
- [ ] 实现硬规则引擎。
- [ ] 接入 Agent State。
- [ ] 实现决策留痕工具。
- [ ] 写入 trace Markdown。
- [ ] 为新增 `.py` 文件添加自测入口。
- [ ] 编写对应测试文件。
- [ ] 执行本阶段完整链路检查。

### 人工确认

- [ ] 确认高权重、紧急、责任三类规则的第一版阈值。

## Step 7: 任务状态、审批中断与恢复

### 最终效果

系统可以把事项沉淀为任务；敏感动作生成审批请求；任务进入 `waiting_approval`；用户在飞书端确认后，系统恢复原任务链路。

### 验收标准

- 任务 Markdown 文件创建和更新正常。
- 审批请求写入 `data/approvals/pending/`。
- 审批完成后移动或写入 `data/approvals/completed/`。
- `create_approval_request` 不执行原动作，只创建审批。
- `resume_after_approval` 只有在 approved 时恢复原动作。
- rejected、deferred、expired 不执行原动作。
- 主链路不因某个任务等待审批而停止。

### 涉及文件、类、方法、模块

- `src/dutyflow/tasks/task_state.py`
  - `TaskState`
  - `TaskStore`
  - `create_task`
  - `update_task_status`
- `src/dutyflow/approval/approval_flow.py`
  - `ApprovalRecord`
  - `ApprovalService`
  - `create_approval`
  - `resolve_approval`
- `src/dutyflow/approval/task_interrupt.py`
  - `TaskInterrupt`
  - `create_interrupt`
  - `resume_interrupt`
- `src/dutyflow/agent/tools/approval_tools.py`
  - `create_approval_request`
  - `resume_after_approval`
- `data/tasks/`
- `data/approvals/pending/`
- `data/approvals/completed/`
- `test/test_task_state.py`
- `test/test_approval_flow.py`
- `test/test_task_interrupt.py`

### 未敲定问题

- 审批过期时间。
- `resume_token` 的生成方式和生命周期。
- 飞书端真实确认交互形式。

### 任务清单

- [ ] 实现任务状态存储。
- [ ] 实现审批记录存储。
- [ ] 实现任务中断记录。
- [ ] 实现审批创建工具。
- [ ] 实现审批恢复工具。
- [ ] 接入 Agent State。
- [ ] 为新增 `.py` 文件添加自测入口。
- [ ] 编写对应测试文件。
- [ ] 执行本阶段完整链路检查。

### 人工确认

- [ ] 确认飞书端审批消息形式。
- [ ] 确认审批超时策略。

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
| Step 4 | pending |  |  |
| Step 5 | in_progress | 2026-04-27 | 已完成真实 p2p 私聊链路、`/bind` 回填 `.env`、Bot 回信与 `/feishu doctor` 诊断；群聊 `@Bot` 与消息资源获取仍待补测。 |
| Step 6 | pending |  |  |
| Step 7 | pending |  |  |
| Step 8 | pending |  |  |
| Step 9 | pending |  |  |
| Step 10 | pending |  |  |
| Step 11 | pending |  |  |
| Step 12 | pending |  |  |
