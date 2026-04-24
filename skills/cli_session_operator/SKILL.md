---
name: cli_session_operator
description: 指导模型在 DutyFlow 中安全使用 open_cli_session、exec_cli_command 和 close_cli_session 三个 CLI 工具，保持会话状态并遵守命令级审批约束。
---

# CLI Session Operator

本 skill 用于指导模型在 DutyFlow 中使用持久 bash 会话相关工具：

- `open_cli_session`
- `exec_cli_command`
- `close_cli_session`

## 使用边界

- 仅适用于当前项目内的 CLI session tools。
- 只负责指导工具调用顺序、参数组织和收尾原则，不直接执行命令。
- 不绕过权限系统；真正是否审批由权限层根据工具静态声明和命令内容决定。
- 不把复杂业务判断写进命令本身；命令应尽量短、单一、可解释。

## 三个工具的职责

### `open_cli_session`

作用：
- 创建一个持久 bash 会话
- 返回 `session_id`
- 建立后续命令共享的目录和环境上下文

关键输入：
- `cwd`
- `timeout`
- `shell_type`：当前仅支持 `bash`

适用场景：
- 需要连续执行多条相关命令
- 需要保留 `cd` 后的目录状态
- 需要保留 `export` 后的环境变量

### `exec_cli_command`

作用：
- 在指定 `session_id` 中执行一条单行命令
- 返回结构化结果，包括：
  - `exit_code`
  - `stdout`
  - `stderr`
  - `cwd_after`
  - `duration_ms`
  - `timed_out`
  - `truncated`

关键输入：
- `session_id`
- `command`
- `timeout`

关键特点：
- 一次 tool call 只执行一条单行命令
- 会继承当前 session 的目录和环境状态
- 当前权限层会解析 `command` 内容
  - 只读低风险命令可直接放行
  - 危险命令会进入审批或拒绝

### `close_cli_session`

作用：
- 关闭指定 `session_id`
- 清理 bash 会话资源

关键输入：
- `session_id`

使用原则：
- 完成一组命令后应主动关闭
- 不要无理由保留空闲 session

## 标准使用顺序

1. 先调用 `open_cli_session`
2. 保存返回的 `session_id`
3. 用 `exec_cli_command` 逐条执行命令
4. 根据返回结果决定是否继续下一条
5. 完成后调用 `close_cli_session`

不要在未打开 session 时直接调用 `exec_cli_command`。

## 命令编写约束

- 命令必须保持单行
- 优先使用短小、明确、可复现的命令
- 优先先读后写，能先检查就不要直接修改
- 不要把多步复杂逻辑塞进一条难以审计的命令

推荐的只读命令示例：
- `pwd`
- `ls`
- `find . -maxdepth 2 -type f`
- `rg "pattern" src`
- `git status`
- `git diff --stat`
- `cat README.md`

高风险命令示例：
- `rm ...`
- `git commit ...`
- 会写文件的重定向或覆盖命令
- 会改动 Git 状态、文件系统、系统环境或网络状态的命令

## 状态保持规则

同一个 session 内，以下状态会延续：

- `cd` 改变后的当前目录
- `export` 设置后的环境变量

因此：
- 如果后续命令依赖当前目录或环境变量，应继续复用同一个 `session_id`
- 如果只是一次独立检查，不要重复创建多个 session

## 结果处理原则

- 先看 `exit_code`
- 再分别查看 `stdout` 和 `stderr`
- 关注 `cwd_after`，确认当前目录是否符合预期
- 如果 `timed_out = true`，应视为本次命令失败，并停止假设原 session 还能继续使用

## 推荐工作流

### 只读检查

1. `open_cli_session`
2. `exec_cli_command` 执行只读命令
3. 解释结果
4. `close_cli_session`

### 连续目录操作

1. `open_cli_session`
2. `exec_cli_command("cd ...")`
3. `exec_cli_command("pwd")` 确认目录
4. 继续后续命令
5. `close_cli_session`

### 环境变量验证

1. `open_cli_session`
2. `exec_cli_command("export KEY=value")`
3. `exec_cli_command('printf "$KEY"')`
4. `close_cli_session`

## 何时不要使用

- 只是读取项目中一个已知文件内容时，不必先开 CLI session
- 只是做字符串推理、代码解释或文档整理时，不必调用 CLI tools
- 用户未要求执行命令，且本地静态阅读已足够时，不要优先走 CLI tools
