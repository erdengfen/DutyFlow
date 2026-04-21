# 本文件实现 fail_tool 工具的实际执行逻辑。

from __future__ import annotations

from dutyflow.agent.tools.contracts.fail_tool_contract import FAIL_TOOL_CONTRACT


class FailTool:
    """测试用失败工具，验证异常封装和错误结果回写。"""

    name = "fail_tool"
    contract = FAIL_TOOL_CONTRACT
    is_concurrency_safe = False
    requires_approval = False
    timeout_seconds = 30.0
    max_retries = 0
    retry_policy = "none"
    idempotency = "unsafe"
    degradation_mode = "escalate"
    fallback_tool_names = ()

    def handle(self, tool_call, tool_use_context):
        """始终抛出异常，供执行层封装为 error envelope。"""
        reason = str(tool_call.tool_input.get("reason", "fail_tool triggered"))
        raise RuntimeError(reason)


def _self_test() -> None:
    """验证 FailTool 名称与 contract 一致。"""
    assert FailTool.name == FailTool.contract["function"]["name"]


if __name__ == "__main__":
    _self_test()
    print("dutyflow agent fail_tool logic self-test passed")
