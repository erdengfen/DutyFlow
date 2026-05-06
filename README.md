# DutyFlow

DutyFlow 是一个面向飞书工作流的本地优先、权限感知型办公 Agent。

围绕一个核心工程问题构建：在 Agent 对职场消息采取任何行动之前，必须先理解——*这是谁发来的、与用户是什么关系、事项归谁负责、是否值得打断、是否需要审批*。只有在这些判断完成之后，才决定如何回复、沉淀任务或请求用户确认。

---

## 快速开始

### 前置条件

- Python 3.11+，[uv](https://github.com/astral-sh/uv)
- 飞书开放平台自建应用（需开启 Bot 能力 + 长连接事件订阅）
- 支持 OpenAI API 格式的模型端点（如 Anthropic、OpenAI、本地部署模型）

### 第一步：安装依赖

```bash
git clone <repo-url>
cd DutyFlow
uv sync
```

### 第二步：配置 .env

```bash
cp .env.example .env
```

打开 `.env`，填写以下必填字段：

```env
# 模型配置
DUTYFLOW_MODEL_API_KEY=sk-...
DUTYFLOW_MODEL_BASE_URL=https://api.anthropic.com/v1  # 或其他兼容端点
DUTYFLOW_MODEL_NAME=claude-sonnet-4-6                  # 或其他模型名

# 飞书应用配置（在飞书开放平台 -> 凭证与基础信息 中获取）
DUTYFLOW_FEISHU_APP_ID=cli_...
DUTYFLOW_FEISHU_APP_SECRET=...
DUTYFLOW_FEISHU_EVENT_MODE=long_connection             # 生产使用长连接

# OAuth 回调（用于主动感知授权，/oauth 流程使用）
DUTYFLOW_FEISHU_OAUTH_REDIRECT_URI=http://127.0.0.1:9768/feishu/oauth/callback
DUTYFLOW_FEISHU_OAUTH_DEFAULT_SCOPES="docx:document:readonly drive:drive:readonly im:message:readonly im:message im:message.group_msg:get_as_user im:chat:read im:chat:readonly"
```

下面几个字段留空，启动后通过 `/bind` 和 `/oauth` 自动写入：

```env
DUTYFLOW_FEISHU_TENANT_KEY=           # /bind 后自动写入
DUTYFLOW_FEISHU_OWNER_OPEN_ID=        # /bind 后自动写入
DUTYFLOW_FEISHU_OWNER_REPORT_CHAT_ID= # /bind 后自动写入
DUTYFLOW_FEISHU_OWNER_USER_ACCESS_TOKEN=   # /oauth 后自动写入
DUTYFLOW_FEISHU_OWNER_USER_REFRESH_TOKEN=  # /oauth 后自动写入
DUTYFLOW_FEISHU_OWNER_USER_TOKEN_EXPIRES_AT= # /oauth 后自动写入
```

### 第三步：运行健康检查

```bash
uv run src/dutyflow/app.py --health
```

期望输出：`status=ok`，`data_dir_exists=True`。

### 第四步：启动应用

```bash
uv run src/dutyflow/app.py
```

应用启动后会同时拉起：飞书长连接监听、后台 Worker、定时调度器、主动感知服务，进入交互 CLI。

### 第五步：绑定 owner 身份（/bind）

在飞书中，找到你的 Bot，向它**私聊**发送：

```
/bind
```

Bot 收到后自动从飞书事件中提取 `tenant_key`、`owner_open_id`、`owner_report_chat_id`，写回本地 `.env`，并立即回复确认消息。同时将该私聊会话注册为 `p2p_chat` scope（enabled 状态），后续消息采集即从此会话开始。

`/bind` 只需执行一次，重启后配置从 `.env` 持久加载。

### 第六步：完成用户 OAuth 授权（/oauth）

主动感知功能（群聊消息采集、云盘文档采集）需要以**用户身份**访问飞书资源，这要求完成一次 OAuth 授权。

向 Bot **私聊**发送：

```
/oauth
```

Bot 回复一条飞书授权链接：

```
请在 5 分钟内用浏览器打开以下链接完成飞书授权：
https://open.feishu.cn/open-apis/authen/v1/authorize?...
```

在浏览器中打开链接，确认授权后，本地 OAuth callback server（端口 9768）自动接收授权码，换取 `user_access_token`，写入 `.env`。Bot 发送确认消息：

```
OAuth 授权完成，已记录用户身份和访问凭证（N 个字段已更新）。
```

**注意：**
- `.env` 中 `DUTYFLOW_FEISHU_OAUTH_REDIRECT_URI` 必须与飞书开放平台应用设置的重定向 URL 一致（默认 `http://127.0.0.1:9768/feishu/oauth/callback`）。
- 使用 Docker 时须确保宿主机 9768 端口可达（见下方 Docker 说明）。
- Token 有效期约 2 小时，到期后系统自动用 `refresh_token`（有效期 30 天）静默刷新；refresh_token 过期后需重新 `/oauth`。

### 第七步：发现和授权采集范围

完成 OAuth 后，通过 CLI 或主动感知自动发现可采集的群组和云盘范围。

**通过 CLI 手动操作：**

```
# 发现你所在的飞书群组
/feishu discover groups

# 查看已发现但待审批的 candidate scope
/feishu scopes candidates

# 向自己发送飞书审批卡片请求授权某个群聊
/feishu request <scope_id>

# 或直接在本地批准（仅限调试场景）
/feishu approve <scope_id>
```

**主动感知自动流程：**

应用启动后每 60 分钟自动发现新群组，每 24 小时对未请求过的 candidate scope 发送飞书审批卡片。收到审批卡片后，在飞书中点击确认即可。

---

## 使用 Docker

```bash
# 构建镜像
docker compose build

# 启动（挂载 ./data 和 ./skills 目录，暴露 9768 端口用于 OAuth）
docker compose up

# 后台运行
docker compose up -d
```

**OAuth 回调注意：** `/oauth` 流程的 callback server 监听在 9768 端口。容器内监听 `127.0.0.1:9768`，`docker-compose.yml` 已将其映射到宿主机同端口。`DUTYFLOW_FEISHU_OAUTH_REDIRECT_URI` 应设为 `http://127.0.0.1:9768/feishu/oauth/callback`，浏览器在宿主机上打开授权链接即可正常回调。

---

## CLI 命令

进入交互 CLI 后，可使用以下命令：

```
/health                          — 应用健康检查

/feishu                          — 当前飞书监听状态
/feishu doctor                   — 进入飞书长连接诊断模式
/feishu latest                   — 最近一条飞书接入事件
/feishu dm                       — 拉取 owner 私信（collector 调试）
/feishu gm [秒数]                — 拉取 enabled 群消息（collector 调试）
/feishu docs                     — 拉取 enabled 云盘清单（collector 调试）
/feishu discover groups          — 发现群组，写入 candidate scope
/feishu scopes                   — 查看所有 scope 注册表
/feishu scopes candidates        — 只看 candidate scope
/feishu request <scope_id>       — 发送飞书审批卡片请求启用 scope
/feishu approve <scope_id>       — 本地直接批准并启用 scope（调试用）
/feishu disable <scope_id>       — 禁用 scope

/feishu proactive status         — 主动感知调度服务状态
/feishu proactive ambient        — 最近 24h ambient_context 记录摘要
/feishu proactive tasks          — 最近主动感知创建的后台任务（总结/分析）
/feishu proactive approvals      — 各 scope 的审批请求记录
/feishu proactive once           — 手动触发一次完整调度 tick

/chat run <输入>                 — 提交一条非阻塞 Agent 调试任务
/chat status                     — 调试 worker 状态
/chat latest                     — 最近一条调试任务结果

/agent state                     — 运行时 AgentState、投影消息、Token Budget

/context clear                   — 清空运行时上下文投影缓存
/context compress                — 手动触发 LLM Phase Summary

/exit                            — 退出 CLI
```

---

## 工作流概览

```
飞书私聊 / 群聊 / 云盘事件
        │
        ▼
Ingress Layer         — 长连接 WebSocket 接收事件，落盘到 data/events/
        │
        ▼
Perception Layer      — 标准化消息/文件/来源元数据，落盘到 data/perception/
        │
        ▼
Identity & Source     — 绑定发送者、会话、租户、Owner 作用域
        │
        ▼
Weight / Permission   — 评估重要性、紧急性、责任归属
        │
        ▼
Agent Loop
        ├── Tool Runtime              — 权限管控工具执行链
        ├── Background Subagent       — 隔离长任务执行面
        ├── Runtime Context Manager   — 上下文投影、压缩、健康检查
        └── Approval Recovery Chain   — 中断、恢复、审批记录
        │
        ▼
Feedback Layer        — 以 Bot 身份向飞书发送文本或卡片
        │
        ▼
Local Evidence / Audit / Task Store  — 事件、任务、审批、收据、日志
```

### 主动感知调度层

应用启动后，`FeishuProactiveService` 后台常驻运行，按固定间隔驱动以下流程：

| 动作 | 间隔 |
|---|---|
| 发现新群组 / 云盘根目录 | 60 分钟 |
| 采集 enabled scope 消息和文档线索 | 5 分钟 |
| 向 candidate scope 发送审批卡片 | 5 分钟检查，24 小时冷却 |
| 新增 ambient_context 送入 Agent 分析队列 | 每 tick |
| 创建周期总结后台任务（私聊/群聊/文档/综合） | 60 分钟，20 小时冷却 |

---

## 架构说明

### 飞书接入层

Bot 身份与用户读取身份**严格分离**：

- **Bot 身份**（App ID + App Secret）：接收事件、发送消息和卡片。
- **用户身份**（OAuth user_access_token）：读取用户授权范围内的私聊、群聊消息和云盘文档。

飞书不是一个 Webhook 接收端，而是面向用户的协作前端。

### Agent 运行时

自研最小 Agent 运行时，不依赖通用 Agent 框架。核心控制流是显式确定性的：准备状态 → 投影上下文 → 调用模型 → 路由工具调用 → 执行工具 → 追加结果 → 重复直到停止。

核心结构：

- `AgentState` — 不可变会话状态，包含消息历史和恢复作用域
- `AgentLoop` — 多轮执行，含工具调用生命周期、最大轮数保护、恢复钩子
- `ToolRegistry / ToolRouter / PermissionGate / ToolExecutor` — 分层工具控制链
- `SkillRegistry` — 基于文件的 Skill 加载器
- `ModelClient` — OpenAI 兼容模型适配器

### 权限与审批层

写路径工具执行需要用户显式审批。审批请求以飞书交互卡片发出，以 `approval_*.md` 持久化，Agent 暂停执行直到收到决策。

### 运行时上下文管理

`AgentState.messages` 是规范会话历史，**不会**直接发送给模型。每次模型调用前，`RuntimeContextManager` 构建投影工作视图，包含：Working Set、State Delta、Tool Receipt 替换、Evidence Store 卸载、Context Budget 估算、Phase Summary 摘要、Compression Journal 审计。

### 后台 Subagent 运行时

主 Agent 不内联执行长耗时任务。委托链路：`task_*.md` 落盘 → `TaskSchedulerService` 按时分发 → `BackgroundTaskWorker` 消费队列 → 隔离 Subagent 执行 → 结果写入 `data/tasks/results/` → `FeedbackGateway` 回推飞书。

### 本地运行时产物

| 产物 | 路径 | 用途 |
|---|---|---|
| 原始事件 | `data/events/evt_*.md` | 完整飞书事件 payload |
| 感知记录 | `data/perception/YYYY-MM-DD/per_*.md` | 标准化 Agent 输入 |
| Ambient Context | `data/ambient_context/{type}/` | 主动采集的消息/文档线索 |
| 任务记录 | `data/tasks/task_*.md` | 任务状态、调度、审批关联 |
| 任务结果 | `data/tasks/results/result_*.md` | Subagent 执行输出 |
| 审批记录 | `data/approvals/pending/` 和 `completed/` | 审批状态和决策 |
| Evidence 文件 | `data/contexts/evidence/evid_*.md` | 卸载的大型工具结果 |
| Phase Summary | `data/contexts/ctx_*.md` | LLM 生成上下文锚点 |
| 压缩日志 | `data/contexts/journal/ctxj_*.md` | 上下文投影审计轨迹 |
| 审计日志 | `data/logs/YYYY-MM-DD.md` | 运行时事件日志 |

所有产物可直接用文本编辑器检查，无需数据库控制台。

---

## 项目目录结构

```
src/dutyflow/
  app.py              应用入口、生命周期编排、后台服务启动
  cli/                CLI 控制台、命令路由、调试接口
  agent/              Agent 循环、模型客户端、状态机、工具生命周期、后台 Subagent
  context/            运行时上下文投影、压缩、Evidence、Phase Summary、Compression Journal
  feishu/             飞书接入、长连接事件接收、OAuth、主动感知调度、卡片分发
    collectors/       私信 / 群消息 / 云文档采集器
    ambient_context.py  采集记录存储与扫描
    proactive_service.py  主动感知调度层
    summary_task_intake.py  周期总结任务创建
  perception/         消息与来源标准化、感知记录存储
  identity/           身份查询、来源上下文、责任上下文
  knowledge/          联系人知识查询与写入
  approval/           审批请求、卡片回调、中断与恢复链
  tasks/              后台任务状态、调度器、Worker、Subagent 运行时
  feedback/           飞书文本和卡片回复统一反馈网关
  storage/            本地 Markdown 与文件持久化原语
  logging/            审计日志写入器
  config/             .env 配置加载、系统提示词

docs/
  ARCHITECTURE.md     系统架构与层职责说明
  DATA_MODEL.md       所有运行时产物类型的文件 Schema 定义
  TESTING.md          测试策略与验收标准
  CODE_STYLE.md       代码规范

skills/               运行时加载的 Skill 文件定义
test/                 测试套件（unittest，708 项）
data/                 本地运行时产物（不提交）
```

---

## 安全边界

- **读写身份分离。** Bot 身份负责发送反馈；用户资源访问需要 OAuth 授权，且只能访问用户显式授权的范围。
- **审批管控执行。** 任何修改外部状态、代表用户表达立场或存在误操作风险的操作，必须在执行前通过审批链。
- **密钥不写入产物。** `app_secret`、`access_token`、`refresh_token`、`api_key` 从所有事件记录、感知记录和日志中排除。
- **本地文件可审计。** 每个运行时产物使用稳定 Schema，包含 `id`、`schema` 和时间戳，无需运行中的进程即可查阅。
- **不允许隐式破坏性操作。** Agent 不能静默删除、覆写或代替用户发送内容，所有此类操作需要显式审批并留有决策记录。

---

## 测试

```bash
# 运行全部 708 项测试
uv run python -m unittest discover -s test
```

测试覆盖：Agent 状态机、Agent 循环、工具控制链、运行时上下文管理、后台 Subagent、审批流、飞书接入、主动感知调度、周期总结任务、CLI 命令、本地持久化。

---

## 已完成能力

- 飞书长连接事件接入（私聊、群聊 `@Bot`、卡片回调）
- `/bind` 自动绑定 owner 身份，写回 `.env`
- `/oauth` 完成用户 OAuth 授权，自动获取 user_access_token
- 原始事件落盘与感知记录标准化
- 身份、来源、责任查询工具
- 联系人知识查询与写入工具
- Agent 运行时：`AgentState`、`AgentLoop`、`ModelClient`、工具调用生命周期
- `ToolRegistry / ToolRouter / PermissionGate / ToolExecutor` 控制链
- Skill Registry 与基于文件的加载器
- Runtime Context Manager（投影、Working Set、State Delta、Tool Receipt、Evidence Store、Context Budget、Phase Summary、Compression Journal、Context Health Check）
- 后台任务运行时（Task Store、Scheduler、Worker、Subagent Executor、结果回馈）
- 审批流（请求、飞书卡片、按钮回调、恢复链、审计记录）
- 主动感知调度层（群组发现、scope 审批卡片、DM/群消息/云文档采集、ambient 分析入队、周期总结任务）
- 本地工作上下文枚举工具 `list_work_context`
- 业务 Skill：`dutyflow_work_context_reader`
- CLI 调试接口（完整 `/feishu`、`/chat`、`/agent`、`/context` 命令树）
- 所有运行时产物类型的本地 Markdown 落盘

## 计划中

- 权重与优先级决策层（重要性、紧急性、责任评分，含显式决策留痕）
- 业务 Skills 补齐（总结执行规范、任务操作规范、权限操作、关系权重）
- 联系人与责任画像
- 多账号工作空间隔离
- 外部 MCP 工具集成
