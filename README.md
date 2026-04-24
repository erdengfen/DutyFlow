# DutyFlow

DutyFlow 是一个面向真实职场协作场景的权限感知型办公 Agent Demo。

它不是通用聊天助手，也不是自动化工具总线。项目目标是让 Agent 在处理办公消息、文件、审批和任务时，先理解“这是谁发来的、与我是什么关系、事情归谁负责、是否值得打断、是否需要审批”，再决定如何提醒、沉淀任务或请求用户确认。

## 为什么做这个项目

真实职场中的信息流并不只是“内容理解”问题。相同一句话，在不同来源下会产生完全不同的处理方式：

- 直属负责人发来的紧急变更，可能需要立即提醒。
- 普通群聊里的泛泛讨论，可能只适合进入摘要。
- 涉及代表用户表态、转发文件、回复飞书消息的动作，必须先审批。
- 一个看似简单的文件请求，如果来自关键项目、关键角色或关键阶段，就不能被当作普通通知处理。

DutyFlow 的核心优势在于把 Agent 的判断从“会不会回答”推进到“是否懂身份、责任、权限和打断价值”。

## 项目最终雏形

Demo 完整闭环目标如下：

```text
飞书事件输入
  -> 身份与来源补全
  -> 事项权重判断
  -> Agent State 硬规则检查
  -> 任务状态沉淀
  -> 必要时发起审批
  -> 用户在飞书端确认
  -> 恢复原任务链路
  -> 飞书回馈
  -> 本地 Markdown 留痕
  -> CLI 可观察与调试
```

长期形态中，飞书是主要用户前端，CLI 只作为本地开发、调试和观察窗口。系统会优先保障可解释、可审批、可追踪，而不是追求静默自动化。

## 当前 Demo 已实现

当前阶段已经完成以下基础能力：

- 本地单进程应用入口：`src/dutyflow/app.py`
- CLI 调试窗口：`src/dutyflow/cli/main.py`
- `.env` 统一配置读取，模型 key、base URL、模型名均来自 `.env`
- 本地 Markdown 存储与基础审计日志
- `data/state/agent_control_state.md` 运行状态快照初始化
- 纯内存 `AgentState`，支持多轮消息、工具结果回写、序列化
- 工具控制层：
  - `ToolSpec`
  - `ToolCall`
  - `ToolResultEnvelope`
  - `ToolRegistry`
  - `ToolRouter`
  - `ToolExecutor`
  - `ToolUseContext`
- 工具执行层支持串行与真实并发批次，并封装工具异常
- OpenAI-compatible 模型调用适配
- CLI `/chat` 多轮调试子会话：
  - 持续复用同一个 Agent State
  - 每轮输出模型结果、完整 Agent State、Tool Result
  - 支持 `/back` 返回主 CLI
  - 支持 `/exit` 退出程序
- 当前内置内部工具：
  - `load_skill`
  - `create_skill`
  - `open_cli_session`
  - `exec_cli_command`
  - `close_cli_session`

当前 `/chat` 是开发调试接口，不是最终面向用户的产品入口。它用于验证模型调用、Agent State、多轮上下文、工具调用和工具结果回写是否正确。

## Demo 期暂未完成

以下能力是 Demo 闭环目标的一部分，但当前阶段尚未完成真实接入：

- 飞书事件接收
- 飞书消息、文件、审批回馈
- 联系人身份索引与单人详情查询工具
- 事项权重 skill
- 权限核实与审批恢复链路
- 用户可查看的任务清单
- 上下文压缩与摘要落盘
- 完整任务审计报告

这些能力会在后续 step 中逐步实现。当前代码中已先完成 Agent State 和 Tool Call 控制面，为后续权限层、审批层和飞书接入提供基础。

## 目录结构

```text
DutyFlow/
  README.md
  PLANS.md
  AGENTS.md
  pyproject.toml
  .env.example
  Dockerfile
  docker-compose.yml

  docs/
    ARCHITECTURE.md
    DATA_MODEL.md
    TESTING.md
    CODE_STYLE.md

  src/
    dutyflow/
      app.py
      cli/
        main.py
      config/
        env.py
      storage/
        file_store.py
        markdown_store.py
      logging/
        audit_log.py
      agent/
        state.py
        loop.py
        model_client.py
        debug_tools.py
        tools/
          types.py
          registry.py
          router.py
          executor.py
          context.py

  test/
    test_*.py
```

运行产生的数据默认写入 `data/`。该目录用于本地日志、状态、任务、审批、上下文和报告留痕，不应提交真实运行数据。

## 环境配置

复制示例配置：

```bash
cp .env.example .env
```

至少需要填写模型配置：

```env
DUTYFLOW_MODEL_API_KEY=replace-with-model-api-key
DUTYFLOW_MODEL_BASE_URL=https://your-openai-compatible-endpoint
DUTYFLOW_MODEL_NAME=your-model-name
```

模型接口当前按 OpenAI-compatible `/chat/completions` 适配：

- 如果 `DUTYFLOW_MODEL_BASE_URL` 已以 `/chat/completions` 结尾，直接使用。
- 否则程序会自动追加 `/chat/completions`。

飞书配置字段已在 `.env.example` 中预留，真实接入时再按飞书开放平台配置确认。

## 本地运行

安装依赖并进入 CLI：

```bash
uv run src/dutyflow/app.py
```

启动后：

```text
DutyFlow CLI started. Type /help to list commands, /exit to quit.
DutyFlow>
```

常用命令：

```text
/help
/health
/chat
/exit
```

进入多轮调试：

```text
DutyFlow> /chat
Chat> 用一句话回复 ping
Chat> 继续上一轮，只回复 done
Chat> /back
DutyFlow> /exit
```

也可以带首条消息进入：

```text
DutyFlow> /chat 用一句话回复 ping
Chat> 继续上一轮，只回复 done
```

健康检查：

```bash
uv run src/dutyflow/app.py --health
```

只做启动检查、不进入持续 CLI：

```bash
uv run src/dutyflow/app.py --no-interactive
```

## 测试

运行完整测试：

```bash
UV_CACHE_DIR=/tmp/dutyflow-uv-cache PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run python -m unittest discover -s test
```

当前阶段已覆盖：

- 应用入口与健康检查
- `.env` 配置读取
- Markdown 存储
- 审计日志
- Agent State
- Tool Registry / Router / Executor
- Agent Loop
- CLI `/chat`
- 模型响应解析

## Docker 部署

项目支持 Docker 方式启动本地 Demo。

构建镜像：

```bash
docker build -t dutyflow-demo .
```

运行 CLI：

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

说明：

- `.env` 不会被复制进镜像，运行时通过 `--env-file .env` 或 Compose 的 `env_file` 注入。
- `data/` 建议挂载到宿主机，便于查看日志、状态和后续任务留痕。
- 当前 Demo 是本地单用户 CLI 调试形态，还不是云端多用户服务。

## 未来扩展方向

DutyFlow 后续扩展会围绕“权限感知办公 Agent”主线推进：

- 飞书事件订阅与消息回馈
- 联系人身份索引、部门、上下级、飞书 ID 精准匹配
- 权重 skill 与 Agent State 硬规则协同判断
- 审批 hook 与任务中断恢复
- 用户可查看的任务清单和状态流转
- 上下文摘要和 Markdown 审计报告
- 安全的外部工具注册、路由、执行和权限闸门
- 私域知识增强和长期记忆，但不抢占 Demo 主闭环

项目的目标不是让 Agent 自动做一切，而是让它在真实办公关系中知道什么时候该提醒、什么时候该沉淀、什么时候必须停下来请用户确认。
