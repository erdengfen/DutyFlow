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

### 最终效果

系统具备最小 Agent Runtime、Agent State、工具注册表、工具路由、工具执行器、权限闸门、Hook Runner、恢复管理器。模型调用和真实工具执行可以先占位，但控制链路必须成立。

### 当前边界

- `Agent State` 是 agent 基架和 loop 内部的运行数据结构，负责多轮更新、流程判断、状态控制和上下文携带。
- `data/state/agent_control_state.md` 是本地运行状态快照文件，只用于可见性和日志型记录，不参与 agent loop 的条件判断、状态控制或上下文传递。
- 工具层必须分清注册表层、路由层和执行层；不得把工具 handler map 直接塞进 agent loop。
- ToolExecutor 是工具真实执行和结果回写前的最后一道运行时边界，必须严格校验 ToolCall、执行顺序、错误封装、结果顺序和上下文修改。
- 工具控制层文件统一收缩到 `src/dutyflow/agent/tools/` 包下，避免 `agent/` 根目录堆积 registry、router、executor 等平级文件。
- Agent Loop 必须基于已完成的 Agent State 和 Tool Call 控制链路封装，不允许绕过 ToolRegistry、ToolRouter 或 ToolExecutor 直接执行 handler。
- 多轮对话入口必须能使用真实模型 key 验证最小链路，但不得在代码中硬编码任何 key、base URL 或模型名。
- Step 2 设计参考 `docs/learn-claude-code` 中 `s01-the-agent-loop.md`、`s02-tool-use.md`、`s02a-tool-control-plane.md`、`s02b-tool-execution-runtime.md`、`s00a-query-control-plane.md` 和 `data-structures.md`。

### 验收标准

- Agent Runtime 能加载 Agent State。
- Tool Call 不允许绕过 ToolRegistry、ToolRouter、PermissionGate、HookRunner、ToolExecutor。
- ToolResultEnvelope 统一封装成功、失败和占位结果。
- 权限拒绝、hook 拦截、工具错误都可记录到审计日志。
- Agent State 可记录任务权重、尝试轮数、审批状态、重试状态。
- Agent State 能维护多轮 `messages`，并把工具结果作为下一轮可见输入写回消息流。
- Agent State 能记录每轮继续原因 `transition_reason`，禁止无原因地进入下一轮。
- Agent State 的流程状态不得写入或依赖 `data/state/agent_control_state.md`。

### 涉及文件、类、方法、模块

- `src/dutyflow/agent/runtime.py`
  - `AgentRuntime`
  - `run_once`
  - `handle_tool_call`
- `src/dutyflow/agent/state.py`
  - `AgentState`
  - `AgentMessage`
  - `AgentContentBlock`
  - `AgentTaskControl`
  - `AgentRecoveryState`
  - `create_initial_agent_state`
  - `append_user_message`
  - `append_assistant_message`
  - `append_tool_results`
  - `mark_transition`
  - `increment_turn`
  - `load_agent_state`
  - `save_agent_state`
- `src/dutyflow/agent/tools/types.py`
  - `ToolSpec`
  - `ToolCall`
  - `ToolResultEnvelope`
- `src/dutyflow/agent/tools/registry.py`
  - `ToolRegistry`
- `src/dutyflow/agent/tools/router.py`
  - `ToolRouter`
- `src/dutyflow/agent/tools/executor.py`
  - `ToolExecutor`
- `src/dutyflow/agent/tools/context.py`
  - `ToolUseContext`
- `src/dutyflow/agent/model_client.py`
  - `ModelClient`
  - `ModelResponse`
- `src/dutyflow/agent/loop.py`
  - `AgentLoop`
  - `run_turn`
  - `run_until_stop`
- `src/dutyflow/agent/permissions.py`
  - `PermissionGate`
  - `PermissionDecision`
- `src/dutyflow/agent/hooks.py`
  - `HookRunner`
  - `HookEvent`
  - `HookResult`
- `src/dutyflow/agent/recovery.py`
  - `RecoveryManager`
- `test/test_agent_runtime.py`
- `test/test_agent_state.py`
- `test/test_agent_tools.py`
- `test/test_agent_permissions.py`
- `test/test_agent_hooks.py`
- `test/test_agent_recovery.py`

### 未敲定问题

- `ToolUseContext` 内部字段是否全部落正式数据模型。
- Hook 初版是否允许外部脚本；当前建议只做内置 Python hook。
- 权限规则是否允许写回本地配置。
- Agent State 是否需要在 Demo 期做磁盘持久化仍未敲定；当前约束为代码内 runtime state，可通过 `to_dict`/`from_dict` 支持测试和未来恢复，但不得与 `agent_control_state.md` 混用。
- 模型 API 的真实 response block 结构未敲定；Step 2.1 先使用项目内规范化 `AgentMessage`/`AgentContentBlock`，后续模型适配层负责转换。
- 已决策：ToolExecutor 使用真实并发执行 concurrency-safe 批次。
- 已决策：原生工具 handler 的最终函数签名为 `handler(tool_call, tool_use_context)`。

### 任务清单

- [x] Step 2.1：实现 Agent State 读写、不变量、序列化和工具结果回写。
- [x] Step 2.2：实现 ToolSpec、ToolCall、ToolResultEnvelope、ToolRegistry、ToolRouter、ToolExecutor。
- [x] Step 2.3：将工具控制层收缩到 `src/dutyflow/agent/tools/`，并完成 import 与测试更新。
- [x] Step 2.4：实现 CLI `/chat` 多轮调试接口，返回模型结果、完整 Agent State 和 Tool Result。
- [ ] 实现 PermissionGate。
- [ ] 实现 HookRunner。
- [ ] 实现 RecoveryManager。
- [ ] 接入 AuditLogger。
- [ ] 为新增 `.py` 文件添加自测入口。
- [ ] 编写对应测试文件。
- [ ] 执行本阶段完整链路检查。

### 人工确认

- [ ] 确认是否需要通用 shell 工具；默认不实现真实 shell 执行。

### Step 2.1 Agent State

状态：已完成。`src/dutyflow/agent/state.py` 实现纯内存 Agent State，维护多轮 `messages`、`pending_tool_use_ids`、任务控制字段、恢复字段和序列化能力。

关键约束：

- Agent State 是 agent loop 内部运行结构，不读写 `data/state/agent_control_state.md`。
- `append_tool_results` 是工具结果写回 Agent State 的唯一入口。
- `tool_result.tool_use_id` 必须匹配当前未完成工具调用。
- `messages` 是下一轮模型调用上下文，不是展示用聊天记录。

测试记录：`test/test_agent_state.py`、`python -m dutyflow.agent.state`、`python -m unittest discover -s test` 已通过。

### Step 2.2 Tool Call 控制链路

状态：已完成。工具链路为 `ToolCall.from_agent_block -> ToolRegistry -> ToolRouter -> ToolExecutor -> ToolResultEnvelope -> append_tool_results`。

关键约束：

- Registry 只注册、查找和校验输入，不执行工具。
- Router 只输出 `ToolRoute`，未实现来源返回明确占位路线。
- Executor 接收 `ToolRoute`，执行前二次校验，使用真实并发处理 concurrency-safe batch，exclusive batch 串行执行。
- Executor 不修改 Agent State，只返回 `ToolResultEnvelope`；所有异常必须封装为错误信封。
- Handler 签名固定为 `handler(tool_call, tool_use_context)`，共享内容通过 `ToolUseContext.tool_content` 显式传入。

测试记录：`test/test_agent_tools.py`、`test/test_agent_registry.py`、`test/test_agent_router.py`、`test/test_agent_executor.py` 已通过；完整单测 38 个通过。

### Step 2.3 工具控制层目录整理

状态：已完成。工具控制层已收缩到统一包：

```text
src/dutyflow/agent/tools/
  __init__.py
  __main__.py
  types.py
  registry.py
  router.py
  context.py
  executor.py
```

变更结果：

- 删除旧入口 `src/dutyflow/agent/tools.py`、`registry.py`、`router.py`、`context.py`、`executor.py`。
- 更新测试 import，后续代码必须从 `dutyflow.agent.tools.*` 引入工具控制层模块。
- `dutyflow.agent.tools` 仅懒导出稳定协议类型，不承载执行逻辑。

测试记录：

- `test/test_agent_tools.py`：通过，4 个测试。
- `test/test_agent_registry.py`：通过，4 个测试。
- `test/test_agent_router.py`：通过，3 个测试。
- `test/test_agent_executor.py`：通过，9 个测试。
- `python -m dutyflow.agent.tools`、`types`、`registry`、`router`、`context`、`executor`：通过。
- 直接运行 `src/dutyflow/agent/tools/*.py` 自测入口：通过；已处理 `types.py` 直接运行时遮蔽标准库 `types` 的路径问题。
- `python -m unittest discover -s test`：通过，38 个测试。
- `uv run dutyflow --health`：通过。

### Step 2.4 Agent Loop 与最小多轮对话入口

状态：已完成自动验收；真实 key 链路待开发者填入 `.env` 后人工测试。
范围：基于已完成的 Agent State 和 Tool Call 控制层，实现 CLI `/chat` 调试接口。此接口不是最终生产形态的 agent loop，只用于本地操作窗口验证多轮模型调用、工具调用和状态回写是否可见。

#### 目标

`/chat` 内部执行一条最小多轮调试链路：

```text
CLI /chat 用户输入
  -> create_initial_agent_state / append_user_message
  -> 模型调用
  -> assistant message 写回 Agent State
  -> 提取 tool_use
  -> ToolRegistry / ToolRouter / ToolExecutor
  -> ToolResultEnvelope -> AgentContentBlock(type="tool_result")
  -> append_tool_results 写回 Agent State
  -> 下一轮模型调用
  -> 无 tool_use 时结束
  -> CLI 输出模型最终文本、完整 Agent State、Tool Result 列表
```

#### 实现边界

- 必须使用真实模型 key 做最终人工验收；key、base URL、模型名全部来自 `.env`。
- 代码不得硬编码任何模型 API key、base URL 或模型名。
- `/chat` 只服务本地开发调试，不替代飞书用户前端，也不代表最终 agent loop 形态。
- 初版只实现单用户单 query 的多轮调试，不实现后台任务、多 agent、权限审批和长期记忆。
- 初版工具仍使用假工具验证链路，不接真实 shell、飞书、MCP 或外部工具。
- Agent Loop 不直接执行工具 handler，必须通过 ToolRegistry、ToolRouter、ToolExecutor。
- Agent Loop 不直接 append tool_result，必须通过 `append_tool_results`。
- Agent Loop 必须设置 `max_turns`，防止无限循环。
- 模型返回结构需要适配为内部 `AgentContentBlock`；真实 provider 字段不稳定时，适配层必须隔离在 `model_client.py`。
- CLI 输出必须包含完整当前 Agent State 和本次 tool result，便于调试窗口直接检查状态变化。

#### 涉及文件

- `src/dutyflow/agent/loop.py`
  - `AgentLoop`
  - `run_turn`
  - `run_until_stop`
  - `extract_tool_calls`
- `src/dutyflow/agent/model_client.py`
  - `ModelClient`
  - `ModelResponse`
  - `OpenAICompatibleModelClient`
  - `call_model`
- `src/dutyflow/agent/debug_tools.py`
  - `create_debug_tool_registry`
- `src/dutyflow/agent/tools/`
- `src/dutyflow/agent/state.py`
- `src/dutyflow/app.py`
- `src/dutyflow/cli/main.py`
- `test/test_agent_loop.py`
- `test/test_model_client.py`
- `test/test_cli_chat.py`

#### 程序端入口

初版只提供 CLI 操作窗口入口：

- `uv run src/dutyflow/app.py`：启动当前 Demo 主程序并默认进入持续 CLI。
- `uv run src/dutyflow/app.py --no-interactive`：仅用于脚本检查，启动后立即退出。
- `/chat`：进入持续多轮调试子会话，提示符为 `Chat>`。
- `/chat 用户输入`：以首条消息进入持续多轮调试子会话。
- `Chat> /back`：返回主 CLI；`Chat> /exit`：退出程序。

约束：

- 入口只用于本地开发调试，不替代飞书用户前端。
- 入口输出不得打印 `.env` 中的 key、token、secret。
- 模型调用失败必须返回明确错误，不得伪装成功。
- 输出必须包含 `final_text`、`agent_state`、`tool_results`、`stop_reason`、`turn_count`。

#### 数据结构要求

- `ModelResponse`
  - `assistant_blocks`
  - `stop_reason`
  - `raw_provider`
- `AgentLoopResult`
  - `state`
  - `final_text`
  - `stop_reason`
  - `turn_count`
  - `tool_results`
  - `tool_result_count`
- `ChatDebugSession`
  - `state`
  - `run_turn`

#### 第一批测试

- fake model 第一轮返回 `tool_use`，第二轮返回 text，loop 能完成两轮。
- fake model 返回纯 text，loop 一轮结束。
- fake model 连续返回 tool_use 超过 `max_turns` 时失败并返回明确错误。
- loop 调用工具必须经过 ToolRegistry、ToolRouter、ToolExecutor。
- executor envelope 必须通过 `append_tool_results` 回写 Agent State。
- CLI `/chat` 在 fake model 模式下可执行。
- CLI `/chat` 必须进入持续子会话，并复用同一个 Agent State。
- CLI `/chat` 输出必须包含 `final_text`、完整 `agent_state` 和 `tool_results`。
- `.env` 缺少模型配置时，真实模型入口返回明确缺失配置。

#### 验收命令

- `env UV_CACHE_DIR=/tmp/dutyflow-uv-cache PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run python test/test_agent_loop.py`
- `env UV_CACHE_DIR=/tmp/dutyflow-uv-cache PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run python test/test_model_client.py`
- `env UV_CACHE_DIR=/tmp/dutyflow-uv-cache PYTHONDONTWRITEBYTECODE=1 uv run src/dutyflow/app.py`
- `env UV_CACHE_DIR=/tmp/dutyflow-uv-cache PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run python -m unittest discover -s test`

#### 真实 key 验收

开发者已提供真实 `.env`，已补跑：

- `uv run src/dutyflow/app.py --interactive` 后执行 `/chat 用一句话回复 ping`。
- `uv run src/dutyflow/app.py --interactive` 后执行 `/chat 请调用 echo_text 工具，参数 text 为 hello，然后根据工具结果回答`。

验收标准：

- [x] 能调用真实模型。
- [x] 能返回 assistant 文本。
- [x] 如模型触发 fake tool，则工具链路可完成回写并继续下一轮。
- [x] CLI 可见完整当前 Agent State 和 Tool Result。
- [x] 日志不泄露 key、token、secret。

#### Step 2.4 自动验收记录

- 修复：`uv run src/dutyflow/app.py` 直接运行时，`src/dutyflow/logging` 遮蔽 Python 标准库 `logging`，导致 `concurrent.futures` 报错；已在 `app.py` 脚本入口启动时修正 `sys.path`。
- 修复：`uv run src/dutyflow/app.py` 默认只输出 CLI ready 后退出，无法继续输入 `/chat`；已改为默认进入持续 CLI，新增 `--no-interactive` 作为启动后立即退出的脚本检查入口。
- 修复：`/chat` 原实现只执行单条 query 并输出一次 Agent State，未进入持续多轮对话；已新增 `ChatDebugSession`，`/chat` 进入 `Chat>` 子会话，每轮复用同一个 Agent State，`/back` 返回主 CLI。
- 修复：`Chat>` 内再次输入 `/chat 用户输入` 时会按当前 Chat State 继续一轮，不再作为普通文本或异常命令处理。
- 修复：Chat 子会话单轮模型/API异常会封装为 `chat_turn_failed` JSON 输出，CLI 不因第二轮异常直接退出。
- `uv run src/dutyflow/app.py`：通过，进入持续 CLI，提示 `DutyFlow> `，可继续输入 `/help`、`/chat`、`/exit`。
- `uv run src/dutyflow/app.py --no-interactive`：通过，输出 ready 后退出。
- `uv run src/dutyflow/app.py --health`：通过。
- `uv run python src/dutyflow/app.py --health`：通过。
- `uv run src/dutyflow/app.py --interactive` 后输入 `/help`：通过，可见 `/chat`。
- PTY 真实终端模拟 `uv run src/dutyflow/app.py` 后输入 `/help` 和 `/exit`：通过。
- 默认入口真实模型 `/chat 用一句话回复 ping`：通过，返回模型文本、完整 `agent_state`、空 `tool_results`。
- 默认入口真实模型 `/chat` 子会话连续两轮：通过，第二轮复用同一 `query_id`，`agent_state.messages` 同时包含第一轮和第二轮 user/assistant，`turn_count` 更新为 2。
- 默认入口真实模型 `Chat>` 内输入 `/chat second`：通过，按同一会话第二轮执行，复用同一 `query_id`。
- 沙箱内首次真实 `/chat` 请求因网络权限返回 `Operation not permitted`；已按权限流程放行网络后复测通过。
- 真实模型 `/chat 用一句话回复 ping`：通过，返回模型文本、完整 `agent_state`、空 `tool_results`。
- 真实模型 `/chat 请调用 echo_text 工具，参数 text 为 hello，然后根据工具结果回答`：通过，模型触发 `echo_text`，`tool_results` 含 1 条成功结果，Agent State 中包含 tool_use、tool_result 和第二轮 assistant 文本。
- `test/test_agent_loop.py`：通过，5 个测试。
- `test/test_model_client.py`：通过，3 个测试。
- `test/test_cli_chat.py`：通过，4 个测试。
- `python -m dutyflow.agent.loop`：通过。
- `python -m dutyflow.agent.model_client`：通过。
- `python -m dutyflow.agent.debug_tools`：通过。
- `python -m dutyflow.cli.main`：通过。
- `python -m dutyflow.app --health`：通过。
- `python -m unittest discover -s test`：通过，51 个测试。
- `uv run dutyflow --health`：通过。
- `uv run dutyflow --interactive` 后输入 `/help`：通过，可见 `/chat`。
- `python -m dutyflow.app --interactive` 后输入 `/chat ping` 且无 `.env`：通过，返回缺失模型配置错误，不伪装成功。
- `git diff --check`：通过。

#### Step 2.4 待人工确认

- [ ] 确认真实模型 base URL 是否为 OpenAI-compatible `/chat/completions` 结构；当前适配层会在 base URL 后追加 `/chat/completions`，若已包含该路径则不重复追加。

## Step 3: Skill 加载与权重 Skill 占位

### 最终效果

系统支持 skill 轻量发现与按需加载。权重 skill 以 `skills/event_weighting/SKILL.md` 形式存在，只作为提示词补充和判断框架，不拥有最终权限决策权。

### 验收标准

- SkillRegistry 能发现 skill 名称和描述。
- `load_skill` 走 ToolRegistry、PermissionGate、HookRunner 和 ToolExecutor。
- `event_weighting` skill 可被加载并返回正文。
- 权重 skill 结果必须回到 Agent State 和硬规则再决策。
- skill 文档不会被执行，不会自动获得工具权限。

### 涉及文件、类、方法、模块

- `src/dutyflow/agent/skills.py`
  - `SkillManifest`
  - `SkillDocument`
  - `SkillRegistry`
  - `load_skill`
- `skills/event_weighting/SKILL.md`
- `src/dutyflow/agent/tools/registry.py`
- `src/dutyflow/agent/tools/executor.py`
- `test/test_agent_skills.py`

### 未敲定问题

- Skill frontmatter 最终字段。
- 权重 skill 的提示词格式和输出格式。
- `load_skill` 是否需要审批；当前建议只记录审计。

### 任务清单

- [ ] 创建 `skills/event_weighting/SKILL.md`。
- [ ] 实现 SkillRegistry。
- [ ] 实现 `load_skill` 工具。
- [ ] 将 `load_skill` 接入工具控制面。
- [ ] 记录 skill 加载审计。
- [ ] 为新增 `.py` 文件添加自测入口。
- [ ] 编写 `test/test_agent_skills.py`。
- [ ] 执行本阶段完整链路检查。

### 人工确认

- [ ] 确认权重 skill 的第一版判断维度和用语。

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

- `src/dutyflow/tools/identity_lookup.py`
  - `lookup_contact_identity`
- `src/dutyflow/tools/source_lookup.py`
  - `lookup_source_context`
- `src/dutyflow/tools/responsibility_lookup.py`
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
- `src/dutyflow/tools/decision_trace.py`
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
- `src/dutyflow/tools/approval_tools.py`
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
- [ ] Demo 期是否提供通用 shell 工具未敲定；默认不提供真实通用 shell 执行。

## 阶段完成记录

| step | status | completed_at | notes |
|---|---|---|---|
| Step 0 | completed | 2026-04-17 | 已完成项目骨架、入口迁移、uv run 入口和 Step 0 测试。 |
| Step 1 | completed | 2026-04-17 | 已完成配置入口、Markdown 存储、按天审计日志、运行目录初始化和 Step 1 测试；真实模型与飞书链路待配置后补测。 |
| Step 2 | pending |  |  |
| Step 3 | pending |  |  |
| Step 4 | pending |  |  |
| Step 5 | pending |  |  |
| Step 6 | pending |  |  |
| Step 7 | pending |  |  |
| Step 8 | pending |  |  |
| Step 9 | pending |  |  |
| Step 10 | pending |  |  |
| Step 11 | pending |  |  |
| Step 12 | pending |  |  |
