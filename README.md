# DutyFlow

DutyFlow 是一个面向飞书工作流的本地优先、权限感知型办公 Agent 。

DutyFlow 围绕一个核心工程问题构建：在 Agent 对职场消息采取任何行动之前，必须先理解——*这是谁发来的、与用户是什么关系、事项归谁负责、是否值得打断、是否需要审批*。只有在这些判断完成之后，才决定如何回复、沉淀任务或请求用户确认。

---

## Core Design Philosophy

- Agent 决策必须基于身份、来源、责任和权限，而不只是消息内容。
- 运行时上下文是一个投影工作视图，不是原始消息历史。`AgentState.messages` 与模型输入在设计上是分离的。
- 长耗时任务不得污染主 Agent 上下文。后台 Subagent 独立执行，只向主链路返回摘要和锚点。
- 每一个被压缩或委托的结果必须保持可追溯。Tool Receipt、Evidence File 和 Compression Journal 共同保留责任链。
- 面向用户的写操作需要显式审批。不允许无声的破坏性执行。
- 本地文件是一等运行时产物。系统可在无外部工具的情况下被检查和审计。

---

## Architecture

```
Feishu Events / CLI Input
        │
        ▼
Ingress Layer          — 接收飞书事件，落盘到 data/events/
        │
        ▼
Perception Layer       — 标准化消息/文件/来源元数据，落盘到 data/perception/
        │
        ▼
Identity & Source Layer — 绑定发送者、会话、租户和 Owner 作用域
        │
        ▼
Weight / Permission Decision Layer — 评估重要性、紧急性、责任归属
        │
        ▼
Agent Loop
        ├── Tool Runtime              — 权限管控的工具执行链
        ├── Background Subagent Runtime — 隔离的长任务执行面
        ├── Runtime Context Manager   — 上下文投影、压缩、健康检查
        └── Approval Recovery Chain   — 中断、恢复、审批记录
        │
        ▼
Feedback Layer         — 以 Bot 身份向飞书会话发送文本或卡片
        │
        ▼
Local Evidence / Audit / Task Store  — 事件、任务、审批、收据、日志
```

---

## Key Technical Components

### Feishu Integration Layer

DutyFlow 通过飞书开放平台原生 SDK 以长连接 WebSocket（`long_connection` 模式）接收事件。Bot 身份与用户读取身份是分离的：Bot 负责发送反馈，用户资源访问通过 OAuth 授权通道处理。飞书是面向用户的协作前端，而不是一个 Webhook 接收端。

接入层捕获私聊消息、群聊 `@Bot` 提及和卡片按钮回调。原始事件在任何处理开始之前落盘，确保无论下游是否失败，完整 payload 都可供检查。

### Agent Runtime

DutyFlow 实现了自己的最小 Agent 运行时，而不依赖通用 Agent 框架。

核心循环（`core_loop.py`）驱动确定性控制流：准备状态 → 投影上下文 → 调用模型 → 路由工具调用 → 执行工具 → 追加结果 → 重复直到停止或达到轮数限制。每一步都是显式且可审计的。

核心结构：

- `AgentState` — 不可变会话状态，包含消息历史、恢复作用域和状态转移元数据
- `AgentLoop` — 多轮执行，含工具调用生命周期、最大轮数保护和恢复钩子
- `ToolRegistry / ToolRouter / PermissionGate / ToolExecutor` — 分层工具控制链
- `SkillRegistry` — 基于文件的 Skill 加载器，具备隔离执行作用域
- `ModelClient` — OpenAI 兼容模型适配器

工具结果通过结构化 Envelope 流转。写路径工具在执行前由 `PermissionGate` 查询当前权限模式和审批状态进行管控。

### Permission & Responsibility Layer

这是 DutyFlow 最核心的差异化能力。

在对任何消息采取行动之前，运行时评估：

- **Identity** — 消息发送者，映射到带有关系元数据的已知联系人
- **Source** — 消息来自哪个频道、群组或文档，以及该来源的可信度
- **Responsibility** — 该事项是否属于用户的所有权、共同责任或仅观察范围
- **Risk** — 该操作是只读、写路径，还是潜在破坏性

写路径工具执行需要用户显式审批。审批请求以飞书交互卡片发出，以 `approval_*.md` 记录持久化，Agent 暂停执行直到收到决策。审批状态（approved / rejected / deferred）被记录并与任务关联。

### Runtime Context Manager

`AgentState.messages` 是规范会话历史，**不会**直接发送给模型。

在每次模型调用前，`RuntimeContextManager.project_state_for_model()` 构建投影工作视图：

- **Working Set** — 识别哪些消息处于活跃、已压缩或可收据化状态
- **State Delta** — 追踪自上次投影以来发生了什么变化
- **Tool Receipt** — 用确定性的 `ToolReceipt(tool_use_id, status, summary)` 替换旧工具结果，保留最新结果原文
- **Evidence Store** — 将大型工具结果卸载到 `data/contexts/evidence/`，在投影消息中以 ID 引用
- **Context Budget** — 在不发起真实模型调用的情况下估算投影消息的 Token 用量
- **Phase Summary** — 生成已完成工作阶段的 LLM 摘要，存储为 `ctx_*.md`，作为紧凑上下文锚点注入
- **Compression Journal** — 将每次投影变更、压缩事件和健康检查结果记录到 `data/contexts/journal/ctxj_*.md`
- **Context Health Check** — 在模型调用前验证投影状态；失败时触发应急压缩

> DutyFlow 将上下文压缩视为责任链保留，而不仅仅是 Token 优化。

在 `context_overflow` 时，运行时应用应急压缩并重试一次。若压缩失败，循环以可恢复的状态记录干净退出。

### Background Subagent Runtime

主 Agent 循环不内联执行长耗时任务。当任务需要较长执行时间时，将其委托给后台 Subagent 运行时：

- 主 Agent 创建 `task_*.md` 记录后立即返回
- Task Scheduler（`TaskSchedulerService`）监控已调度任务，在 `scheduled_for` 到达时分发
- Background Worker（`BackgroundTaskWorker`）消费队列中的任务，通过具有受限工具面的隔离 Subagent 执行
- Subagent 将结果写入 `data/tasks/results/result_task_*.md`
- Feedback Gateway 将结果发回发起任务的飞书会话

主 Agent 上下文只持有任务状态、结果摘要和锚点，从不持有完整的 Subagent 执行轨迹。

### Local Evidence and Audit Store

DutyFlow 不把本地文件仅当作日志。它们是运行时产物。

每个重要的运行时事件都生成一个结构化 Markdown 文件：

| Artifact | Path | 用途 |
|---|---|---|
| Raw events | `data/events/evt_*.md` | 完整飞书事件 payload |
| Perception records | `data/perception/YYYY-MM-DD/per_*.md` | 标准化 Agent 输入 |
| Task records | `data/tasks/task_*.md` | 任务状态、调度时间、审批关联 |
| Task results | `data/tasks/results/result_*.md` | Subagent 执行输出 |
| Approval records | `data/approvals/pending/` 和 `completed/` | 审批状态和决策记录 |
| Interrupt records | `data/approvals/interrupts/` | Agent 暂停/恢复锚点 |
| Evidence files | `data/contexts/evidence/evid_*.md` | 卸载的大型工具结果 |
| Phase summaries | `data/contexts/ctx_*.md` | LLM 生成的上下文锚点 |
| Compression journal | `data/contexts/journal/ctxj_*.md` | 上下文投影审计轨迹 |
| Audit log | `data/logs/YYYY-MM-DD.md` | 运行时事件日志 |

> 运行时产物可由用户直接检查，无需数据库控制台。

---

## Runtime Flow

```
1.  飞书事件通过长连接 WebSocket 到达。
2.  Ingress Layer 校验并将原始事件落盘到 data/events/。
3.  Perception Layer 标准化消息文本、来源元数据和文件线索。
4.  Identity Layer 绑定发送者 open_id、chat_id、tenant_key 和 Owner 作用域。
5.  Decision Layer 评估消息重要性、紧急性和责任归属。
6.  RuntimeContextManager 基于该会话的 AgentState 构建投影工作视图。
7.  AgentLoop 以投影消息和可用工具调用模型。
8.  Tool Runtime 执行被许可的调用；写路径调用进入审批链。
9.  长耗时任务委托给后台 Subagent，不在主循环内联执行。
10. Feedback Gateway 以 Bot 身份向飞书会话发送文本或卡片回复。
11. Evidence、Receipt、Journal、任务状态和审计日志本地落盘。
```

---

## Implementation Status

### Completed

- 飞书长连接事件接入（私聊、群聊 `@Bot`、卡片回调）
- 原始事件落盘与感知记录标准化
- 身份、来源、责任查询工具
- 联系人知识查询与写入工具
- Agent 运行时：`AgentState`、`AgentLoop`、`ModelClient`、工具调用生命周期
- `ToolRegistry / ToolRouter / PermissionGate / ToolExecutor` 控制链
- Skill Registry 与基于文件的加载器
- Runtime Context Manager：投影消息、Working Set、State Delta、Tool Receipt、Evidence Store、Context Budget、Phase Summary、Compression Journal、Context Health Check、context overflow 恢复
- 后台任务运行时：Task Store、Scheduler、Worker、Subagent Executor、结果回馈
- 审批流：请求、飞书卡片、按钮回调、恢复链、审计记录
- 跨飞书消息的 per-chat 会话状态持久化
- 所有运行时产物类型的本地 Markdown 落盘
- CLI 调试接口：`/feishu`、`/chat`、`/agent`、`/context`
- 353 项测试，覆盖 Agent 运行时、上下文管理、工具链、飞书接入、审批流和后台 Subagent

### In Progress

- 权重与优先级决策层（重要性、紧急性、责任评分，含显式决策留痕）
- 审批拒绝和稍后处理后的任务完整恢复链
- 飞书文件和图片内容感知

### Planned

- 联系人与责任画像
- Agent Wiki / 本地持久记忆
- 飞书文档与富内容感知
- 多账号工作空间隔离
- 外部 MCP 工具集成

---

## Repository Layout

```
src/dutyflow/
  app.py              应用入口、生命周期编排、后台服务启动
  cli/                CLI 控制台、命令路由、调试接口
  agent/              核心 Agent 循环、模型客户端、状态机、工具生命周期、后台 Subagent
  context/            运行时上下文投影、压缩、Evidence、Phase Summary、Compression Journal
  feishu/             飞书 OpenAPI 接入、长连接事件接收、卡片分发
  perception/         消息与来源标准化、感知记录存储
  identity/           身份查询、来源上下文、责任上下文
  knowledge/          联系人知识查询与写入工具
  approval/           审批请求、卡片回调、中断与恢复链
  tasks/              后台任务状态、调度器、Worker、Subagent 运行时
  feedback/           飞书文本和卡片回复的统一反馈网关
  storage/            本地 Markdown 与文件持久化原语
  logging/            审计日志写入器
  config/             .env 配置加载、系统提示词配置

docs/
  ARCHITECTURE.md     系统架构与层职责说明
  DATA_MODEL.md       所有运行时产物类型的文件 Schema 定义
  TESTING.md          测试策略与验收标准
  CODE_STYLE.md       代码规范

skills/               运行时加载的 Skill 文件定义
test/                 测试套件（unittest）
data/                 本地运行时产物（不提交）
```

---

## Local-first Runtime Artifacts

DutyFlow 不使用数据库。运行时状态以结构化 Markdown 文件保存在本地文件系统中。

这是一个有意为之的设计选择：每个重要的 Agent 决策、工具执行、审批事件和上下文压缩都生成一个可被直接读取、对比和审计的文件——不需要查询控制台，不需要日志聚合器，不需要运行中的进程。

产物类型构成完整的审计链：

- **Event records** 在任何处理开始之前捕获原始飞书 payload
- **Perception records** 保存标准化的 Agent 输入，包含来源线索和查询元数据
- **Task records** 追踪从调度、执行到结果的完整生命周期
- **Approval records** 保留决策、决策者和时间戳
- **Tool receipts** 在压缩旧工具结果的同时保持 tool_use_id 可追溯
- **Evidence files** 保存过大而无法内联的完整工具输出
- **Compression journal** 记录每次投影变更和健康检查，含压缩前后的消息数量
- **Phase summaries** 以可读 Markdown 存储 LLM 生成的上下文锚点

---

## Security and Permission Boundaries

- **读写身份分离。** Bot 身份负责发送反馈；用户资源访问需要 OAuth 授权，且只能访问用户显式授权的范围。
- **审批管控执行。** 任何修改外部状态、代表用户表达立场或存在误操作风险的操作，必须在执行前通过审批链。不允许无声的写路径操作。
- **密钥不写入产物。** `app_secret`、`access_token`、`refresh_token` 和 `api_key` 值从所有事件记录、感知记录和日志中排除。
- **本地文件可审计。** 每个运行时产物使用稳定 Schema，包含 `id`、`schema` 和时间戳，无需运行系统即可查阅。
- **不允许隐式破坏性操作。** Agent 不能静默删除、覆写或代替用户发送内容。所有此类操作需要显式审批并留有决策记录。

---

## Development Setup

**依赖：** Python 3.11+，[uv](https://github.com/astral-sh/uv)

```bash
# 安装依赖
uv sync

# 复制配置模板
cp .env.example .env
# 编辑 .env：填写 DUTYFLOW_MODEL_API_KEY、DUTYFLOW_MODEL_BASE_URL、DUTYFLOW_MODEL_NAME
# 以及飞书相关配置

# 运行健康检查
uv run src/dutyflow/app.py --health

# 启动 Agent（飞书监听 + 后台 Worker + CLI）
uv run src/dutyflow/app.py

# 运行测试套件
python3 -m unittest discover -s test
```

**飞书配置：** 在飞书开放平台创建自定义应用，开启 Bot 能力和长连接事件订阅。首次启动后，在飞书中私聊 Bot 发送 `/bind`，系统会从飞书事件中读取 `tenant_key`、`owner_open_id` 和 `owner_report_chat_id` 并写回 `.env`。

**关键 `.env` 字段：**

```env
DUTYFLOW_MODEL_API_KEY=
DUTYFLOW_MODEL_BASE_URL=          
DUTYFLOW_MODEL_NAME=
DUTYFLOW_FEISHU_APP_ID=
DUTYFLOW_FEISHU_APP_SECRET=
DUTYFLOW_FEISHU_EVENT_MODE=long_connection
DUTYFLOW_FEISHU_TENANT_KEY=       # /bind 后自动写入
DUTYFLOW_FEISHU_OWNER_OPEN_ID=    # /bind 后自动写入
DUTYFLOW_FEISHU_OWNER_REPORT_CHAT_ID=  # /bind 后自动写入
```

**CLI 命令：**

```
/feishu             — 监听状态
/feishu doctor      — 长连接诊断
/feishu latest      — 最近接入结果
/chat run <text>    — 提交非阻塞调试任务
/chat latest        — 最近调试结果
/agent state        — 运行时 AgentState、投影消息、Token Budget
/context clear      — 清空运行时上下文投影缓存
/context compress   — 手动触发 LLM Phase Summary
/health             — 应用健康检查
```

---

## Testing Strategy

测试套件共 353 项，覆盖：

- **Agent state** — `AgentState` 状态转移、消息追加、恢复作用域
- **Agent loop** — 多轮执行、工具调用生命周期、最大轮数保护、context overflow 恢复
- **Tool runtime** — Registry、Router、Permission Gate、Executor、Skill 加载
- **Runtime context** — 投影、Working Set、State Delta、micro-compact、Tool Receipt、Evidence Store、Context Budget、Phase Summary、Compression Journal、Context Health Check
- **Background subagent** — Task Worker、Scheduler、Subagent 链路、结果回馈
- **Approval flow** — 请求创建、卡片回调、中断/恢复、状态转移
- **Feishu integration** — 事件解析、感知记录标准化、接入 Fixture
- **CLI commands** — `/chat`、`/context clear`、`/context compress`、`/agent state`、`/feishu`
- **Local persistence** — Markdown Store、File Store、审计日志、结构化 Markdown Schema

```bash
python3 -m unittest discover -s test
```

---

## Roadmap

### Near-term

- 权重与优先级决策层，含每条消息的显式决策留痕
- 审批拒绝和稍后处理后的完整任务恢复链
- 飞书文件和图片内容感知（内容提取，不只是元数据）

### Mid-term

- 联系人与责任画像，含结构化知识记录
- Agent Wiki — 跨会话本地持久记忆
- 飞书文档与富内容感知

### Long-term

- 多账号工作空间隔离
- 外部 MCP 工具集成
- 基于结构化输出格式的丰富职场工作流自动化
