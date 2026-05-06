# 本文件实现 list_work_context 工具：只读枚举本地工作上下文 refs。

from __future__ import annotations

import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    _SRC_ROOT = Path(__file__).resolve().parents[5]
    if str(_SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(_SRC_ROOT))

from dutyflow.agent.tools.contracts.context_tools.list_work_context_contract import (
    LIST_WORK_CONTEXT_TOOL_CONTRACT,
)
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, error_envelope
from dutyflow.context.work_context_index import WorkContextIndexService, query_from_tool_input


class ListWorkContextTool:
    """枚举项目内已落盘工作上下文的只读内部工具。"""

    name = "list_work_context"
    contract = LIST_WORK_CONTEXT_TOOL_CONTRACT
    is_concurrency_safe = True
    requires_approval = False
    timeout_seconds = 10.0
    max_retries = 0
    retry_policy = "none"
    idempotency = "read_only"
    degradation_mode = "none"
    fallback_tool_names = ()

    def handle(self, tool_call: ToolCall, tool_use_context) -> ToolResultEnvelope:
        """按过滤条件枚举本地工作上下文。"""
        try:
            query = query_from_tool_input(tool_call.tool_input)
            payload = WorkContextIndexService(tool_use_context.cwd).list_context(query).to_payload()
        except Exception as exc:  # noqa: BLE001
            return error_envelope(tool_call, "list_work_context_failed", str(exc))
        return ToolResultEnvelope(
            tool_call.tool_use_id,
            tool_call.tool_name,
            True,
            json.dumps(payload, ensure_ascii=False),
        )


def _self_test() -> None:
    """验证工具名与 contract 一致。"""
    assert ListWorkContextTool.name == ListWorkContextTool.contract["function"]["name"]


if __name__ == "__main__":
    _self_test()
    print("dutyflow list_work_context logic self-test passed")
