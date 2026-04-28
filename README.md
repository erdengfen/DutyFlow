# DutyFlow

DutyFlow 是一个面向真实职场协作场景的权限感知型办公 Agent Demo。

它不是通用聊天助手，也不是自动化工具总线。项目目标是让 Agent 在处理办公消息、文件、审批和任务时，先理解“这是谁发来的、与我是什么关系、事情归谁负责、是否值得打断、是否需要审批”，再决定如何提醒、沉淀任务或请求用户确认。
## 当前状态

项目目前已经具备真实飞书接入和正式 runtime 主链路：

- `app.py` 启动后会自动拉起飞书长连接监听。
- 支持飞书 Bot 私聊消息、群聊 `@Bot` 消息进入本地。
- 支持 `/bind` bootstrap，自动回填 `tenant_key`、`owner_open_id`、`owner_report_chat_id` 到 `.env`。
- 飞书原始事件会落盘到 `data/events/`。
- 感知后的标准输入会落盘到 `data/perception/`。
- 感知记录会进入正式 runtime queue，由正式 Agent Loop 消费。
- Agent 可通过统一反馈接口向飞书会话回文本消息。
- CLI 可查看飞书监听状态、最近事件和 doctor 诊断信息。

同时，项目已经完成以下本地能力：

- `.env` 统一配置读取。
- 本地 Markdown 存储与审计日志。
- OpenAI-compatible 模型调用。
- Agent 执行核心 `core_loop.py`。
- `ToolRegistry / ToolRouter / PermissionGate / ToolExecutor` 工具控制链。
- `SkillRegistry + load_skill`。
- 身份、来源、责任查询工具。
- 联系人知识查询与写入工具。
- 后台任务 Markdown 存储。
- 后台任务调度器。
- 审批记录存储。
- 任务中断记录。
- 后台任务入口工具。
- 审批创建工具。

## 当前边界

以下能力还没有完整闭环：

- 飞书审批卡片和按钮回调。
- 审批通过后的任务恢复执行。
- 后台任务 worker 的真实执行面。
- 文件、图片等消息资源的本体下载。
- 文件、图片、网页、飞书文档的内容解析。
- 用户视角的完整飞书消息感知。
- 权重判断、硬规则判断和决策留痕。

当前文件消息可以收到事件，也会保存 `file_key`、`file_name` 等线索，但不会下载文件本体。

## 目录结构

```text
DutyFlow/
  README.md
  PLANS.md
  AGENTS.md
  pyproject.toml
  .env.example

  docs/
    ARCHITECTURE.md
    DATA_MODEL.md
    TESTING.md
    CODE_STYLE.md

  skills/
    ...

  src/
    dutyflow/
      app.py
      cli/
      config/
      storage/
      logging/
      feishu/
      feedback/
      perception/
      identity/
      knowledge/
      approval/
      tasks/
      agent/
        core_loop.py
        runtime_loop.py
        runtime_service.py
        debug_chat_service.py
        model_client.py
        skills.py
        tools/

  data/
    events/
    perception/
    identity/
    knowledge/
    tasks/
    approvals/
    logs/

  test/
    test_*.py
```

`data/` 是本地运行数据目录，用于保存事件、感知记录、任务、审批、日志和测试样本。真实运行数据不要提交到远程仓库。

## 环境配置

复制示例配置：

```bash
cp .env.example .env
```

模型配置：

```env
DUTYFLOW_MODEL_API_KEY=replace-with-model-api-key
DUTYFLOW_MODEL_BASE_URL=https://your-openai-compatible-endpoint
DUTYFLOW_MODEL_NAME=your-model-name
```

`DUTYFLOW_MODEL_BASE_URL` 需要填写完整接口地址，程序不会自动追加 `/chat/completions`。

飞书配置：

```env
DUTYFLOW_FEISHU_APP_ID=replace-with-feishu-app-id
DUTYFLOW_FEISHU_APP_SECRET=replace-with-feishu-app-secret
DUTYFLOW_FEISHU_EVENT_VERIFY_TOKEN=replace-with-feishu-event-token
DUTYFLOW_FEISHU_EVENT_ENCRYPT_KEY=replace-with-feishu-encrypt-key
DUTYFLOW_FEISHU_EVENT_MODE=long_connection
```

首次绑定时可以先保留这些占位：

```env
DUTYFLOW_FEISHU_TENANT_KEY=replace-with-feishu-tenant-key
DUTYFLOW_FEISHU_OWNER_OPEN_ID=replace-with-owner-open-id
DUTYFLOW_FEISHU_OWNER_REPORT_CHAT_ID=replace-with-owner-report-chat-id
```

启动后，在飞书里私聊当前 Bot 发送：

```text
/bind
```

系统会从飞书事件中读取真实值并写回 `.env`。

## 本地运行

安装依赖：

```bash
uv sync
```

启动应用：

```bash
uv run src/dutyflow/app.py
```

启动后会自动拉起：

- 飞书长连接监听
- 正式 runtime worker
- CLI 控制台

健康检查：

```bash
uv run src/dutyflow/app.py --health
```

只做启动检查，不进入持续 CLI：

```bash
uv run src/dutyflow/app.py --no-interactive
```

## CLI 命令

```text
/help
/health
/feishu
/feishu status
/feishu latest
/feishu doctor
/feishu fixture 文本
/chat
/chat run 用户输入
/chat status
/chat latest
/exit
```

说明：

- `/feishu` 和 `/feishu status` 只查看当前监听状态。
- `/feishu listen` 已废弃；监听会在应用启动时自动拉起。
- `/feishu doctor` 用于查看长连接、事件计数和最近事件。
- `/chat` 是非阻塞调试入口，不是正式用户入口。
- `/chat 用户输入` 等同于 `/chat run 用户输入`。

## 飞书人工测试

1. 启动应用。
2. 在飞书里私聊当前 Bot 发送 `hello`。
3. 终端应出现飞书事件日志。
4. `data/events/` 下应出现对应事件文件。
5. `data/perception/` 下应出现对应感知记录。
6. Bot 应收到 runtime 回信。

绑定测试：

```text
/bind
```

群聊测试：

```text
@Bot test
```

文件测试：

- 私聊 Bot 发送文件。
- 检查事件文件中是否有 `message_type`、`file_key`、`file_name`。
- 当前不会下载文件本体。

## 测试

运行完整测试：

```bash
PYTHONPATH=src uv run python -m unittest discover -s test
```

也可以直接使用系统 Python：

```bash
python3 -m unittest discover -s test
```

当前测试覆盖主要包括：

- 应用入口与健康检查
- `.env` 配置读取
- Markdown 存储
- 审计日志
- 飞书接入与感知记录
- 正式 runtime loop
- 非阻塞 `/chat`
- 工具注册、路由、执行和权限
- 身份、来源、责任查询
- 联系人知识工具
- 后台任务、审批和中断记录

## Docker

构建镜像：

```bash
docker build -t dutyflow-demo .
```

运行：

```bash
docker run --rm -it \
  --env-file .env \
  -v "$(pwd)/data:/app/data" \
  dutyflow-demo
```

健康检查：

```bash
docker run --rm \
  --env-file .env \
  -v "$(pwd)/data:/app/data" \
  dutyflow-demo --health
```

使用 Docker Compose：

```bash
docker compose run --rm dutyflow
```

`.env` 通过运行参数注入，`data/` 建议挂载到宿主机，便于查看事件、任务、审批和日志。
