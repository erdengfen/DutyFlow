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

完成 OAuth 后，主动感知会自动发现可采集的群组和云盘范围。CLI 端仅保留本地测试指令，用于开发阶段人工触发发现、查看 scope 和验证审批链路。

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

## CLI 调试入口

交互 CLI 预留了健康检查、飞书接入、主动感知、后台任务和 Agent Loop 的本地测试指令，便于开发阶段观察链路状态和人工触发关键流程。

---

## 工作流概览

```
飞书私聊 / 群聊 / 云盘事件 / 定时调度任务
        │
        ▼
Ingress Layer         — 接收长连接事件、卡片回调、定时任务触发，落盘到 data/events/
        │
        ▼
Perception Layer      — 标准化消息、文件、来源、触发方式，落盘到 data/perception/
        │
        ▼
Identity & Source     — 补全联系人、会话、群组、文档、责任上下文
        │
        ▼
Weight / Permission   — 评估重要性、紧急性、责任归属、是否需要审批
        │
        ▼
Agent Loop
        ├── Runtime Context Manager   — 构建模型可见工作视图，压缩长上下文
        ├── Skill Selection           — 按任务类型选择读取、总结、任务、审批等业务 Skill
        ├── Model Turn                — 生成下一步意图：回复、读上下文、建任务、请求审批
        ├── Tool Runtime              — 路由工具、校验权限、执行只读或审批后的写操作
        ├── Evidence / Receipt Store  — 大结果卸载为引用，工具结果写入可审计收据
        ├── Background Subagent       — 将长任务、定时总结、延迟提醒交给隔离执行面
        └── Approval Recovery Chain   — 权限不足时挂起，飞书卡片审批后恢复执行
        │
        ▼
Feedback Layer        — 以 Bot 身份向飞书发送文本或卡片
        │
        ▼
Local Evidence / Audit / Task Store  — 事件、任务、审批、收据、日志
```

Agent Loop 的一次执行并不是简单地把所有历史消息塞给模型，而是按固定顺序推进：

1. **归一输入。** 飞书事件、主动感知采集记录、后台任务和 CLI 调试输入都会先转成统一的运行时输入，保留来源、时间、触发方式和可追溯 ID。
2. **加载状态。** 运行时读取当前任务状态、审批状态、最近工具收据、上下文引用和必要的身份信息，形成本轮可用的执行边界。
3. **投影上下文。** `RuntimeContextManager` 只把模型需要的工作视图放入上下文窗口；完整事件、文档正文、长工具结果以 `context_ref` 或 evidence 文件保存，按需读取。
4. **选择 Skill。** Agent 根据输入意图选择工作上下文读取、周期总结、任务操作、权限审批、关系权重等业务 Skill，用它们约束工具使用顺序和回答格式。
5. **模型决策。** 模型只决定下一步动作：直接回复、调用只读工具、创建后台任务、发起审批、读取上下文引用或等待更多信息。
6. **工具执行。** `ToolRouter` 找到工具，`PermissionGate` 判断是否允许执行；只读且已授权的工具直接运行，涉及外部状态变更、敏感读取或未批准 scope 时转入审批。
7. **写入收据。** 工具结果不会无约束累积在对话中，而是写入 receipt、evidence、ambient context、task result 或 approval record，再把短引用交还给 Agent。
8. **循环或挂起。** 如果工具结果足够，Agent 进入下一轮推理；如果等待用户审批或定时时间未到，任务挂起并保留恢复点。
9. **反馈与落盘。** 最终结果通过飞书 Bot 回复用户，同时把任务状态、审批结果、摘要报告和审计日志落盘，便于事后检查。

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
