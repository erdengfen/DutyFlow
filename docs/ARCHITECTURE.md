# ARCHITECTURE.md

本文档记录项目整体技术链路、系统模块划分、Demo 期架构边界和初版文件目录规划。

本文档只约束系统职责和模块关系，不定义具体数据字段、函数签名、类设计或飞书 API 细节。数据结构设计读 `docs/DATA_MODEL.md`，测试与验收读 `docs/TESTING.md`，代码规范读 `docs/CODE_STYLE.md`。

## 1. 当前技术链路

当前已确定的技术链路如下：

```text
飞书事件 / 飞书交互
        |
        v
本地 Python 单进程服务
        |
        v
事件入口 -> 身份与来源补全 -> 权重决策 -> 上下文处理 / 审批判断
        |
        v
任务状态更新 -> 飞书回馈 -> 本地文件留痕
        |
        v
CLI 调试与观察
```

- 运行形态：本地单用户、单进程常驻运行。
- 开发语言：Python 3.11+。
- 环境管理：uv。
- Agent 方案：自研实现，不使用大型通用 Agent 框架作为主骨架。
- 飞书接入：飞书开放平台负责事件输入和用户回馈。
- 配置来源：所有密钥、飞书账号、认证和用户配置统一来自 `.env`。
- 持久化：本地文件系统，服务于事件、日志、计划、任务、审批、上下文摘要和报告留痕。
- 调试入口：CLI 负责本地观察、日志查看、事件检查和人工辅助调试，不承担模型对话功能。

## 2. Demo 期必须实现的系统模块

Demo 期只实现能形成闭环的最小系统。

### 2.1 事件入口层

职责：

- 接收飞书消息事件。
- 接收文件或文档相关线索。
- 将外部输入统一送入本地主流程。

边界：

- 不追求覆盖飞书全部 API。
- 不把飞书接入层扩展成通用 API 网关。

### 2.2 身份与来源补全层

职责：

- 为事件补充联系人关系、来源类型、事项类型和责任语境。
- 为后续权重判断提供最小必要上下文。

边界：

- 只维护 Demo 所需的最小身份与来源信息。
- 不实现完整联系人画像系统。

### 2.3 事件权重决策层

职责：

- 基于身份层和权重层判断事项处理方式。
- 决定即时提醒、进入摘要、等待审批、形成任务或暂不打断。

边界：

- 先规则，再智能。
- 不引入复杂 Agent Loop、长期规划或多 Agent 协同。

### 2.4 上下文保存与压缩层

职责：

- 保存近场必要上下文。
- 生成轻量上下文摘要，供提醒、审批和任务状态使用。

边界：

- 不实现长期向量记忆库。
- 不实现大规模 RAG 或知识库检索。

### 2.5 权限核实与审批层

职责：

- 对敏感动作发起用户确认。
- 记录审批结果。
- 阻止高风险动作静默执行。

边界：

- 不允许先执行再补审批。
- 不扩大自动执行能力。

### 2.6 任务清单与状态层

职责：

- 将已识别事项沉淀为用户可查看的任务或状态。
- 维护事项当前阶段和处理结果。

边界：

- 不建设多用户协作任务平台。
- 不引入数据库作为 Demo 主存储。

### 2.7 用户回馈层

职责：

- 通过飞书返回提醒、摘要、审批请求和任务状态更新。
- 保证反馈能解释为什么提醒、为什么需要审批、当前状态是什么。

边界：

- 飞书是主要用户前端。
- CLI 只作为开发调试和观察入口。

### 2.8 CLI 调试与控制层

职责：

- 提供 `/...` 风格的本地控制命令。
- 支持切换模型、清理上下文、压缩上下文、查看日志、健康检查等开发与运行控制能力。
- 支持观察系统状态和辅助排查问题。

边界：

- CLI 不做模型对话功能。
- CLI 不替代飞书作为主要用户前端。
- CLI 中涉及敏感动作的命令仍必须遵守审批和安全约束。

初版命令能力：

- `/model`：切换或查看当前模型配置。
- `/clear`：清理当前可清理上下文。
- `/compress`：触发上下文压缩。
- `/logs`：查看运行日志或审计记录。
- `/health`：执行健康检查。
- `/tasks`：查看当前任务状态。
- `/approvals`：查看待审批或历史审批记录。

建议补充能力：

- `/config-check`：检查 `.env` 必需配置是否齐全，不输出密钥明文。
- `/storage-check`：检查本地文件存储目录是否可读写。
- `/replay-event`：用本地已记录事件进行回放调试。

### 2.9 本地持久化与审计层

职责：

- 以文件形式保留关键运行产物。
- 支持人工检查、问题追踪和 Demo 验收。
- 日志、计划、任务、审批、上下文摘要、报告等面向人工检查的内容优先使用 Markdown 存储。

边界：

- 不引入数据库、消息队列或外部存储服务。
- 具体文件格式和字段设计归 `docs/DATA_MODEL.md` 约束。

## 3. 配置入口约束

所有运行配置统一来自 `.env`，不得散落在源码、测试数据或本地临时文件中。

`.env` 至少需要覆盖以下配置类别：

- Agent 内部模型调用：`api_key`、base URL、模型名称或模型选择相关配置。
- 飞书接入：飞书应用凭证、认证、事件订阅、回调或加解密相关配置。
- 本地存储：日志、计划、任务、审批、上下文摘要、报告等文件存储位置。
- 运行控制：本地运行环境、调试开关、日志等级等非敏感运行配置。

飞书配置当前只确认需要预留应用凭证和访问凭证相关配置；具体字段命名、事件订阅参数和授权方式需在接入飞书时再按官方文档确认。

## 4. 入口约束

- 不保留根目录 `main.py` 作为长期程序入口。
- 程序启动与生命周期入口集中到 `src/dutyflow/app.py`。
- CLI 控制台实现集中到 `src/dutyflow/cli/main.py`，由 `app.py` 启动和管理。
- CLI 不作为独立业务服务，不绕过 `app.py` 直接驱动核心模块。
- 根目录只保留项目配置、文档和必要环境文件。

## 5. 主流程约束

系统主流程必须保持以下顺序：

1. 接收飞书输入。
2. 补全身份与来源语境。
3. 进行权重判断。
4. 按需保存或压缩上下文。
5. 按需触发审批。
6. 更新任务状态。
7. 向用户回馈。
8. 落盘关键产物。

禁止出现以下流程倒置：

- 先自动执行，再补审批。
- 先建设复杂知识检索，再完成权重判断。
- 先建设长期记忆，再完成 Demo 闭环。
- 先追求对话体验，再忽略任务状态和审计留痕。

## 6. 初版文件目录架构

以下是初版建议目录，用于后续讨论和实现时保持模块边界清晰。

```text
DutyFlow/
  AGENTS.md
  README.md
  PLANS.md
  pyproject.toml
  .python-version
  .env
  .env.example

  docs/
    ARCHITECTURE.md
    DATA_MODEL.md
    TESTING.md
    CODE_STYLE.md

  src/
    dutyflow/
      __init__.py
      app.py
      config/
        env.py
      agent/
        tools.py
        skills.py
        safety.py
      feishu/
        events.py
        client.py
        feedback.py
      identity/
        source_context.py
      decision/
        weighting.py
      context/
        short_context.py
      approval/
        approval_flow.py
      tasks/
        task_state.py
      storage/
        file_store.py
      cli/
        main.py
      logging/
        audit_log.py

  data/
    events/
    contexts/
    approvals/
    tasks/
    reports/
    logs/
    plans/

  test/
    test_agent_tools.py
    test_agent_skills.py
    test_agent_safety.py
    test_feishu_events.py
    test_identity_source_context.py
    test_decision_weighting.py
    test_context_short_context.py
    test_approval_flow.py
    test_task_state.py
    test_file_store.py
    test_full_chain.py
```

目录职责：

- `src/dutyflow/app.py`：本地单进程主流程编排入口。
- `src/dutyflow/config/`：统一读取 `.env`，不得散落读取配置。
- `src/dutyflow/agent/`：Agent 基层工具调用、技能加载和安全边界。
- `src/dutyflow/feishu/`：飞书事件接收、授权范围读取、用户回馈。
- `src/dutyflow/identity/`：身份、来源、责任语境补全。
- `src/dutyflow/decision/`：事项权重判断。
- `src/dutyflow/context/`：近场上下文保存和轻量压缩。
- `src/dutyflow/approval/`：审批请求、审批结果和敏感动作确认。
- `src/dutyflow/tasks/`：任务清单和状态流转。
- `src/dutyflow/storage/`：本地文件读写与关键产物留痕。
- `src/dutyflow/cli/`：本地调试、观察和人工检查入口。
- `src/dutyflow/logging/`：运行日志与审计记录。
- `data/`：本地运行产物目录，不存放源码；日志、计划、报告等人工检查内容优先为 Markdown。
- `test/`：按功能分块维护独立测试，完整链路测试放在 `test_full_chain.py`。

## 7. Demo 期验收对应关系

- 飞书真实输入：事件入口层。
- 联系人、来源、责任差异反馈：身份与来源补全层 + 事件权重决策层。
- 重要事项提醒：事件权重决策层 + 用户回馈层。
- 敏感动作审批：权限核实与审批层。
- 任务可见：任务清单与状态层。
- 结果回传：用户回馈层。
- 文件留痕：本地持久化与审计层。

## 8. 后续重点讨论问题

- 飞书事件接收的本地运行方式。
- `.env` 中配置项的命名和分组。
- 本地文件持久化的具体格式与目录命名。
- Agent 基层工具调用的允许范围、调用记录和失败处理。
- `load skill` 或类似技能加载能力的安全边界、来源限制和启停机制。
- 权限层的判断逻辑、审批触发条件和可追踪记录方式。
- 责任层的联系人关系、来源角色、责任归属的数据结构设计。
- CLI 第一批命令的最终命名和参数形式。
