---
name: dutyflow_work_context_reader
description: 指导模型处理“今天有什么事项、有什么风险、项目卡在哪里”等短句时，优先读取 DutyFlow 本地已落盘工作上下文，而不是误判为缺少飞书权限。
---

# DutyFlow Work Context Reader

本 skill 用于处理用户用很短的话询问工作状态，例如：

- 今天有什么事项？
- 今天青桐有什么风险？
- 有什么需要我处理？
- 项目现在卡在哪里？
- 帮我看看最近有什么重要消息。
- 给我总结一下今天。

这些问题默认是在询问 DutyFlow 已经落盘的本地工作信息面，不是要求重新搜索互联网，也不是默认要求实时读取飞书。

## 核心判断

遇到这类问题时，先假设本地 `data/` 已经有可用上下文。不要在未检查本地信息前回复“缺少上下文查询权限”“缺少任务查询权限”“需要先开通工具”。

本地落盘上下文是 DutyFlow 的已授权运行产物。读取这些本地摘要和索引属于只读内部查询，不等同于新增飞书读取权限。

只有在用户明确要求读取尚未授权的飞书对话、群聊、云盘范围，或本地 scope 显示为 `candidate` 时，才进入权限审批链路。

## 优先读取顺序

如果当前对话、任务或系统输入中已经给出稳定引用，优先用 `read_context_ref` 读取：

1. `task`：读取任务状态和结果摘要。
2. `approval`：读取审批状态，确认是否被权限卡住。
3. `ambient_context`：读取私聊、群聊、用户云文档采集记录。
4. `evidence`：读取工具结果或文档正文摘录。
5. `perception`：读取飞书事件感知记录。

如果输入中包含 `context_refs`、`ambient record_id`、`task_id`、`approval_id`、`evidence_id`，必须先调用 `read_context_ref` 展开关键记录，再回答用户。

## 本地索引读取

如果没有稳定引用，先调用 `list_work_context` 枚举本地已落盘工作上下文。常用方式：

- 用户问“今天有什么事项”：`date=today`，必要时 `limit=20`。
- 用户问某个项目：把项目名写入 `query`。
- 用户只问消息：`source_types=direct_message,group_message,user_document`。
- 用户只问任务：`source_types=task`。
- 用户问权限卡在哪里：`source_types=approval` 或 `approval_status=waiting`。

`list_work_context` 返回轻量条目后，再选择最相关的 `ref_type/ref_id` 调用 `read_context_ref` 展开。不要把全部条目都展开，优先展开最相关的 3 到 8 条。

如果当前可用工具集中没有 `list_work_context`，但包含 CLI tools，并且当前是本地调试或 CLI `/chat` 场景，可以通过受控 CLI 读取项目内索引文件：

- `data/reports/`
- `data/tasks/`
- `data/tasks/results/`
- `data/ambient_context/index.md`
- `data/ambient_context/direct_message/index.md`
- `data/ambient_context/group_message/index.md`
- `data/ambient_context/user_document/index.md`
- `data/approvals/pending/`
- `data/approvals/completed/`

CLI 只允许做项目内只读查看。不要修改文件，不要访问项目外路径。

如果当前是正式飞书 runtime 且没有 `list_work_context`，也没有任何 `context_refs` 被注入，不要伪装已经完成完整查询。应说明“我现在没有拿到可枚举的本地上下文索引”，并建议系统补齐本地工作上下文枚举工具；不要把原因归咎为飞书权限不足。

## 回答组织

回答用户时要贴近办公场景，避免暴露内部实现细节。优先输出：

- 今天最重要的 3 到 5 件事。
- 哪些需要用户亲自处理。
- 哪些只是知晓。
- 明确的截止时间。
- 相关责任人。
- 如果有权限审批卡住，说明正在等哪个授权。

不要把工具名、文件路径、record_id 堆给普通用户，除非用户明确要调试信息。

## 文档正文读取

当 `ambient_context` 中包含 `doc_links` 或 `readable_doc_tokens` 时：

- 先根据 ambient 摘要判断是否需要正文。
- 只有确需正文才能调用 `feishu_read_doc`。
- `doc_token` 必须来自用户提供链接、ambient context 或工具返回结果，不允许猜测。
- 读取正文后优先引用 Evidence 摘要，不要把大段正文直接贴给用户。

## 权限处理

如果本地信息显示某个 scope 是 `candidate` 或某个任务 `waiting_approval`：

- 不要直接放弃回答。
- 先基于已授权本地信息给出当前能判断的部分。
- 再说明缺的是哪个对话、群聊或文档范围。
- 如果存在专用 scope 审批工具，应请求用户授权，文案使用：“DutyFlow向您请求*某对话/群聊*阅读权限”。

如果当前没有专用 scope 审批工具，只能说明“需要发起飞书阅读权限审批，但当前工具集中还没有 scope 审批入口”，不要误写成缺少本地查询权限。

## 任务处理

当用户说“提醒我”“帮我盯”“记成待办”“到点再看”时：

- 先从已读取上下文中提取事项、时间、责任人和成功标准。
- 再调用 `schedule_background_task` 或 `create_background_task`。
- 创建任务时尽量写入 `context_refs`。
- 如果能指定后台能力，优先包含：
  - `preferred_tools=read_context_ref`
  - 需要文档正文时再加 `feishu_read_doc`
  - 后续若存在本 skill 的后台可见能力，写入 `preferred_skills=dutyflow_work_context_reader`

## 禁止行为

- 不要在未检查本地上下文前说没有数据。
- 不要把“没有实时飞书权限”说成“没有本地任务查询权限”。
- 不要为了回答简单工作摘要就优先调用飞书搜索。
- 不要猜测飞书 token、doc token、scope_id。
- 不要代表用户向客户或群聊发送立场性消息；这类动作必须走审批。
