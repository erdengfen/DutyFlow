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

### 验收记录

- `uv run dutyflow --health`：通过。
- `PYTHONPATH=src uv run python -m dutyflow.app --health`：通过。
- `PYTHONPATH=src uv run python -m dutyflow.cli.main`：通过。
- `PYTHONPATH=src uv run python test/test_app_entry.py`：通过。
- `PYTHONPATH=src uv run python -m unittest discover -s test`：通过。
- 说明：首次在沙箱内执行 `uv run` 时，uv 缓存目录不可写；经授权使用 uv 正常运行后通过。

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

### 验收记录

- 2026-04-17 复测：此前测试结果因 `agent_state` 与 `agent_control_state` 命名边界调整，默认作废并重新执行。
- `env UV_CACHE_DIR=/tmp/dutyflow-uv-cache PYTHONDONTWRITEBYTECODE=1 uv run dutyflow --health`：通过。
- `env UV_CACHE_DIR=/tmp/dutyflow-uv-cache PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run python -m unittest discover -s test`：通过，累计 10 个测试。
- `env UV_CACHE_DIR=/tmp/dutyflow-uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python src/dutyflow/config/env.py`：通过。
- `env UV_CACHE_DIR=/tmp/dutyflow-uv-cache PYTHONDONTWRITEBYTECODE=1 uv run python src/dutyflow/storage/file_store.py`：通过。
- `env UV_CACHE_DIR=/tmp/dutyflow-uv-cache PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run python -m dutyflow.storage.markdown_store`：通过。
- `env UV_CACHE_DIR=/tmp/dutyflow-uv-cache PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run python -m dutyflow.logging.audit_log`：通过。
- 说明：本次复测使用 `/tmp/dutyflow-uv-cache` 作为 uv 缓存目录，未使用权限提升。
- 未验证项：真实模型 API 调用和真实飞书事件订阅尚未执行，等待开发者提供真实 `.env` 配置和飞书测试环境。

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

### 工具接入规范

新增工具当前最小流程：

1. 新增 contract 文件。
2. 新增 logic 文件。
3. 在 `src/dutyflow/agent/tools/registry.py` 中 import。
4. 手动加入 `TOOL_REGISTRY`。
5. 在 logic 声明执行字段：
   - `is_concurrency_safe`
   - `timeout_seconds`
   - `max_retries`
   - `retry_policy`
   - `idempotency`
   - `degradation_mode`
   - `fallback_tool_names`

当前分组约定：

- 开发阶段新增工具默认标记为“内部工具”。
- 除特殊测试工具和后续可能引入的 `bash` 工具外，其它内部工具默认可标记为安全，并由权限层直接放行。
- 开发期内部工具继续沿用当前目录结构，逻辑写在 `src/dutyflow/agent/tools/contracts/` 和 `src/dutyflow/agent/tools/logic/` 下；后续 `skill_loader` 也遵循这套结构。
- 内部工具继续沿用当前的显式注册方式；外部工具后续在另一层统一管理，不并入当前内部工具目录。

### 关键约束

- `AgentLoop` 不允许绕过 `ToolRegistry`、`ToolRouter`、`PermissionGate`、`ToolExecutor`。
- 工具重试只允许在 `ToolExecutor` 内发生。
- 参数错误、权限错误、审批拒绝、路由错误、非幂等副作用默认不自动重试。
- `RecoveryManager` 只做恢复决策和恢复描述，不做后台执行器。
- 审计日志必须可读、可解析、可脱敏，且写失败不能打崩主链路。
- Hook 当前只提供事件和 runner，不暴露真实执行时机。

### 已完成项

- [x] `Step 2.1` Agent State 读写、不变量、序列化、工具结果回写
- [x] `Step 2.2` ToolSpec / ToolCall / ToolResultEnvelope / Registry / Router / Executor
- [x] `Step 2.3` 工具控制层目录收束到 `src/dutyflow/agent/tools/`
- [x] `Step 2.4` CLI `/chat` 多轮调试接口
- [x] `Step 2.5` 工具超时、有限重试、退避、执行留痕
- [x] `Step 2.6` PermissionGate + CLI 审批入口
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
- [x] 除特殊测试工具和后续 `bash` 工具外，内部工具默认按安全工具处理。

### 主要注意事项

- 当前项目不是通用 agent 平台；Step 2 只要求“可控、可见、可追踪”的最小基架。
- `/chat` 是本地调试入口，不替代飞书前端。
- 结构化审计日志存放在 `data/logs/YYYY-MM-DD.md`。
- `pending_restarts` 只表示“当前进程内可恢复描述”，不代表已经有后台调度器。
- 当前注册表仍是手动登记；新增工具时不要假设只加文件即可自动接入。
- 内部 / 外部工具的稳定字段还未最终落稿；当前先按“开发期默认内部工具”执行。

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

### 收束验收记录

- `python3 -m unittest discover -s test`：通过，102 个测试。
- `python3 src/dutyflow/app.py --health`：通过。
- `PYTHONPATH=src python3 -m dutyflow.agent.state`：通过。
- `PYTHONPATH=src python3 -m dutyflow.agent.loop`：通过。
- `PYTHONPATH=src python3 -m dutyflow.agent.recovery`：通过。
- `PYTHONPATH=src python3 -m dutyflow.agent.hooks`：通过。
- `PYTHONPATH=src python3 -m dutyflow.logging.audit_log`：通过。
- `PYTHONPATH=src python3 -m dutyflow.cli.main`：通过。
- `git diff --check`：通过。
- `PYTHONPATH=src python3 -m dutyflow.agent.tools.executor`：通过。
- `PYTHONPATH=src python3 -m dutyflow.agent.loop`：通过。
- `PYTHONPATH=src python3 -m dutyflow.cli.main`：通过。
- `python3 -m unittest discover -s test -p 'test_audit_log.py'`：通过，3 个测试。
- `python3 -m unittest discover -s test -p 'test_agent_executor.py'`：通过，26 个测试。
- `python3 -m unittest discover -s test -p 'test_agent_loop.py'`：通过，9 个测试。
- `python3 -m unittest discover -s test -p 'test_cli_chat.py'`：通过，4 个测试。
- `python3 -m unittest discover -s test`：通过，99 个测试。
- `python3 src/dutyflow/app.py --health`：通过。
- `git diff --check`：通过。

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

### 验收记录

- `python3 -m unittest discover -s test -p 'test_agent_skills.py'`：通过，6 个测试。
- `python3 -m unittest discover -s test -p 'test_runtime_tool_registry.py'`：通过，4 个测试。
- `python3 -m unittest discover -s test -p 'test_agent_loop.py'`：通过，9 个测试。
- `python3 -m unittest discover -s test`：通过，108 个测试。
- `PYTHONPATH=src python3 -m dutyflow.agent.skills`：通过。
- `PYTHONPATH=src python3 -m dutyflow.agent.tools.contracts.load_skill_contract`：通过。
- `PYTHONPATH=src python3 -m dutyflow.agent.tools.logic.load_skill`：通过。
- `PYTHONPATH=src python3 -m dutyflow.agent.loop`：通过。
- `python3 src/dutyflow/app.py --health`：通过。
- `git diff --check`：通过。

## Step 3.1: 第一批内部 Tools 扩展

### 最终效果

在现有 agent 基架、权限层和工具控制链上，新增第一批真正服务于 Demo 主链路的内部工具。第一批先围绕 skill 自举能力展开，用一个内部工具支持创建新的 `skills/<skill_name>/SKILL.md`。

### 范围边界

- 仅新增内部工具，不处理外部工具生态。
- 工具继续沿用当前目录结构：
  - `src/dutyflow/agent/tools/contracts/`
  - `src/dutyflow/agent/tools/logic/`
- 工具继续手动注册到 `src/dutyflow/agent/tools/registry.py`。
- 工具仍需服从 Step 2 已落地的权限、恢复、审计和 `/chat` 调试链路。
- skill 可以在正文中引用工具名称、工具用途、参数约束和安全注意事项，但 skill 不直接执行工具；最终仍由模型根据当前上下文生成 tool call。
- tool 只负责确定性动作，不能把复杂判断逻辑写进工具；复杂使用策略应写在 skill 中。

### 第一批内部工具

- `create_skill`
  - 类型：内部工具。
  - 作用：按名称、描述和正文创建新的 `skills/<skill_name>/SKILL.md`。
  - 写入范围：仅允许写入 `skills/<skill_name>/SKILL.md`，不允许写入任意路径。
  - 安全级别：敏感内部工具。虽然属于内部工具，但会改变后续 agent 可见能力，必须走权限确认。
  - 权限建议：`requires_approval = True`。
  - 幂等约束：默认不覆盖已存在 skill；如后续需要覆盖，必须单独声明参数并再次审批。
  - 输入建议：`name`、`description`、`body`。
  - 输出建议：返回创建路径、manifest 摘要和是否成功。

### 预期验收方向

- 新增工具可被 `ToolRegistry` 正确发现并进入模型可见工具列表。
- 新增工具可通过 `/chat` 在当前 agent loop 下稳定触发。
- 安全工具与敏感工具的声明字段符合现有权限规范。
- 工具执行结果、失败留痕、审批分支和审计日志保持可见。
- `create_skill` 触发时必须能在 CLI 审批窗口中看到写入意图，按 Enter 后才允许写入。

### 后续待补内容

- [x] 已实现 `create_skill` 的 contract / logic。
- [x] 已接入 `ToolRegistry` 手动注册流程。
- [x] 已验证 `create_skill` 作为敏感内部工具会进入审批链路。
- [x] 已补充对应测试与阶段验收记录。

## Step 3.2: 第一批 Skills 内容扩展

### 最终效果

在已完成的 `SkillRegistry + load_skill` 基础上，新增第一批真正参与 Demo 判断链路的 skills。第一批先新增一个指导模型创建新 skill 的自举 skill，用于规范模型如何结合 `load_skill` 和 `create_skill` 完成 skill 创建。

### 范围边界

- skill 文档继续使用固定目录结构：`skills/<skill_name>/SKILL.md`
- frontmatter 当前最小必需字段仍是：
  - `name`
  - `description`
- 具体 skill 正文由 `load_skill` 按需加载，不直接全量塞入 system prompt。
- 本小节只处理 skills 内容扩展，不改动 `SkillRegistry` 的基础解析范式。
- skill 正文可以列出推荐工具和使用步骤，但不得把工具注册表复制成长期静态副本；工具真实 schema 仍以 `ToolRegistry` 为准。
- skill 只能引导模型生成 tool call，不能绕过权限层、审批层或工具执行层。

### Tool 与 Skill 协同范式

- system message 默认只暴露 skill manifest，使模型知道“有哪些能力说明可加载”。
- 当任务需要某个能力说明时，模型先调用 `load_skill(name=...)` 读取完整 skill 正文。
- skill 正文负责说明判断标准、执行步骤、推荐工具名称、参数组织方式和审批注意事项。
- 模型再根据 skill 正文和当前可见工具列表生成具体 tool call。
- 工具负责执行确定性动作，并通过 `ToolResultEnvelope` 返回结果；skill 不保存状态、不直接写文件、不直接修改工具行为。
- 对会改变后续 agent 能力的工具，例如 `create_skill`，即使是内部工具，也必须作为敏感动作进入审批。

### 第一批 Skills

- `skill_creator`
  - 路径：`skills/skill_creator/SKILL.md`
  - 作用：指导模型把用户的 skill 创建需求整理为标准 `SKILL.md`，并在用户确认后调用 `create_skill`。
  - 推荐工具：`load_skill`、`create_skill`。
  - 核心流程：先澄清 skill 名称、description 和正文目标；再生成待写入内容；最后调用 `create_skill`。
  - 安全要求：不得自行覆盖已有 skill；涉及写入时必须依赖 `create_skill` 的审批链路。
  - 当前不负责：不创建 tools、不修改 registry、不生成复杂外部集成逻辑。

### 预期验收方向

- 新增 skills 能被 `SkillRegistry` 初始化扫描并进入 manifest 列表。
- `AgentLoop` system message 能稳定暴露新增 skills 的元信息。
- 模型可通过 `load_skill` 读取指定 skill 的完整正文。
- 新增 skills 的命名、描述和正文内容与 Demo 目标一致，不偏离“身份层 + 权重层 + 审批流 + 任务可见性”。
- 模型在使用 `skill_creator` 时，能明确先整理内容，再通过 `create_skill` 触发受控写入。

### 后续待补内容

- [x] 已新增 `skills/skill_creator/SKILL.md` 正文。
- [x] 已验证 `skill_creator` 可被 `SkillRegistry` 扫描并进入 system message manifest。
- [x] 已补充对应测试与阶段验收记录。

### 验收记录

- `python3 -m unittest discover -s test -p 'test_agent_skills.py'`：通过，13 个测试。
- `python3 -m unittest discover -s test -p 'test_runtime_tool_registry.py'`：通过，4 个测试。
- `PYTHONPATH=src python3 -m dutyflow.agent.tools.contracts.create_skill_contract`：通过。
- `PYTHONPATH=src python3 -m dutyflow.agent.tools.logic.create_skill`：通过。
- `PYTHONPATH=src python3 -m dutyflow.agent.tools.registry`：通过。
- `python3 -m unittest discover -s test`：通过，115 个测试。
- `python3 src/dutyflow/app.py --health`：通过。
- `env UV_CACHE_DIR=/tmp/dutyflow-uv-cache PYTHONPATH=src uv run dutyflow --health`：通过。
- `git diff --check`：通过。

## Step 4: 身份、来源、责任 Markdown 数据与查询工具

### 最终效果

系统能从 Markdown 文件夹索引和单人详情文件中精准查询身份、来源和责任上下文，并以裁剪片段补充到当前上下文。

### 验收标准

- `lookup_contact_identity` 支持按 contact_id、飞书 ID、姓名、别名、部门匹配。
- 同名或模糊命中必须返回 ambiguous。
- `lookup_source_context` 可定位来源上下文。
- `lookup_responsibility_context` 可结合联系人、来源、事项类型返回责任片段。
- 查询工具不返回整份 Markdown 文档。
- 示例联系人、来源和责任 fixture 可支持完整链路测试。

### 涉及文件、类、方法、模块

- `src/dutyflow/agent/tools/identity_lookup.py`
  - `lookup_contact_identity`
- `src/dutyflow/agent/tools/source_lookup.py`
  - `lookup_source_context`
- `src/dutyflow/agent/tools/responsibility_lookup.py`
  - `lookup_responsibility_context`
- `src/dutyflow/identity/contact_resolver.py`
  - `ContactResolver`
  - `resolve_contact`
- `src/dutyflow/identity/source_context.py`
  - `SourceContextResolver`
- `data/identity/contacts/index.md`
- `data/identity/contacts/people/contact_<id>.md`
- `data/identity/sources/index.md`
- `test/test_identity_lookup.py`
- `test/test_identity_source_context.py`

### 未敲定问题

- 飞书实际用户 ID、open_id、union_id 的字段来源。
- 联系人详情文件中上下级字段的最终形式。
- 责任范围是否需要独立文件。

### 任务清单

- [ ] 创建联系人索引 fixture。
- [ ] 创建单人详情 fixture。
- [ ] 创建来源索引 fixture。
- [ ] 实现 ContactResolver。
- [ ] 实现 SourceContextResolver。
- [ ] 实现三类 lookup 工具。
- [ ] 接入工具控制面。
- [ ] 为新增 `.py` 文件添加自测入口。
- [ ] 编写对应测试文件。
- [ ] 执行本阶段完整链路检查。

### 人工确认

- [ ] 提供 Demo 联系人样例。
- [ ] 提供 Demo 来源样例，如群、文档、文件或私聊。

## Step 5: 事件入口与飞书占位适配层

### 最终效果

系统可以接收本地 fixture 事件，并预留真实飞书事件接入接口。真实飞书未接入前，所有飞书 API 调用返回明确字符串占位。

### 验收标准

- 本地事件可进入主流程。
- 事件记录落盘到 `data/events/`。
- 飞书 client 有真实接口方法名，但 Demo 未接入时返回占位字符串。
- 占位接口有中文注释说明“Demo 期未接真实飞书 API”。
- 占位接口不会伪装真实发送成功。

### 涉及文件、类、方法、模块

- `src/dutyflow/feishu/events.py`
  - `FeishuEventAdapter`
  - `parse_event`
  - `create_local_fixture_event`
- `src/dutyflow/feishu/client.py`
  - `FeishuClient`
  - `fetch_context_placeholder`
  - `send_message_placeholder`
- `src/dutyflow/feishu/feedback.py`
  - `FeishuFeedbackService`
- `src/dutyflow/storage/markdown_store.py`
- `data/events/`
- `test/test_feishu_events.py`

### 未敲定问题

- 飞书真实事件 payload 结构。
- 飞书消息、文档、文件 API 权限和字段。
- 飞书回调、认证、加解密方式。

### 任务清单

- [ ] 实现本地 fixture 事件输入。
- [ ] 实现事件解析占位结构。
- [ ] 实现 FeishuClient 占位方法。
- [ ] 实现 FeedbackService 占位方法。
- [ ] 事件写入 Markdown。
- [ ] 为新增 `.py` 文件添加自测入口。
- [ ] 编写 `test/test_feishu_events.py`。
- [ ] 执行本阶段完整链路检查。

### 人工确认

- [ ] 提供飞书应用凭证和权限范围前，不接真实 API。
- [ ] 提供真实事件样例前，继续使用 fixture。

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

Demo 期不实现的能力在程序中留有接口，但不接入真实数据，不执行真实能力，只返回明确字符串占位，并在代码注释中说明“Demo 期不实现”。

### 验收标准

- 长期记忆接口存在但返回占位。
- RAG/知识库接口存在但返回占位。
- MCP/外部工具接口存在但返回占位。
- 多 Agent 接口存在但返回占位。
- 完整联系人画像接口存在但返回占位。
- 复杂规划接口存在但返回占位。
- 测试验证这些占位接口不会被 Demo 主链路误调用。

### 涉及文件、类、方法、模块

- `src/dutyflow/agent/placeholders.py`
  - `LongTermMemoryPlaceholder`
  - `RagPlaceholder`
  - `McpPlaceholder`
  - `MultiAgentPlaceholder`
  - `ContactProfilePlaceholder`
  - `PlanningPlaceholder`
- `test/test_demo_placeholders.py`

### 未敲定问题

- 占位接口最终是否单独放在 `agent/placeholders.py`，或分散到对应模块。

### 任务清单

- [ ] 实现长期记忆占位接口。
- [ ] 实现 RAG 占位接口。
- [ ] 实现 MCP 占位接口。
- [ ] 实现多 Agent 占位接口。
- [ ] 实现完整联系人画像占位接口。
- [ ] 实现复杂规划占位接口。
- [ ] 每个占位接口返回明确字符串。
- [ ] 每个占位接口有中文注释说明 Demo 期不实现。
- [ ] 编写测试文件。
- [ ] 执行本阶段完整链路检查。

### 人工确认

- [ ] 确认占位接口命名是否需要与未来真实能力保持一致。

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

- [ ] 飞书真实 API 结构、权限、事件 payload、回馈方式未敲定；`DUTYFLOW_FEISHU_EVENT_CALLBACK_URL` 仍为项目暂定字段。
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
| Step 5 | pending |  |  |
| Step 6 | pending |  |  |
| Step 7 | pending |  |  |
| Step 8 | pending |  |  |
| Step 9 | pending |  |  |
| Step 10 | pending |  |  |
| Step 11 | pending |  |  |
| Step 12 | pending |  |  |
