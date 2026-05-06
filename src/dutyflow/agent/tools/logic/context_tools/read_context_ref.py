# 本文件实现 read_context_ref 工具：按本地稳定引用读取已落盘上下文摘要。

from __future__ import annotations

import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    _SRC_ROOT = Path(__file__).resolve().parents[5]
    if str(_SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(_SRC_ROOT))

from dutyflow.agent.tools.contracts.context_tools.read_context_ref_contract import (
    READ_CONTEXT_REF_TOOL_CONTRACT,
)
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, error_envelope
from dutyflow.context.context_ref_reader import ContextRefReader


class ReadContextRefTool:
    """读取本地上下文引用的只读内部工具。"""

    name = "read_context_ref"
    contract = READ_CONTEXT_REF_TOOL_CONTRACT
    is_concurrency_safe = True
    requires_approval = False
    timeout_seconds = 10.0
    max_retries = 0
    retry_policy = "none"
    idempotency = "read_only"
    degradation_mode = "none"
    fallback_tool_names = ()

    def handle(self, tool_call: ToolCall, tool_use_context) -> ToolResultEnvelope:
        """校验输入并读取本地上下文引用。"""
        ref_type = str(tool_call.tool_input.get("ref_type", "")).strip()
        ref_id = str(tool_call.tool_input.get("ref_id", "")).strip()
        if not ref_type:
            return error_envelope(tool_call, "invalid_context_ref_input", "ref_type 不能为空")
        if not ref_id:
            return error_envelope(tool_call, "invalid_context_ref_input", "ref_id 不能为空")
        result = ContextRefReader(tool_use_context.cwd).read(ref_type, ref_id)
        if not result.ok:
            return error_envelope(tool_call, result.status, _error_detail(result))
        return ToolResultEnvelope(
            tool_call.tool_use_id,
            tool_call.tool_name,
            True,
            json.dumps(result.to_payload(), ensure_ascii=False),
            attachments=tuple(_attachments(result)),
        )


def _attachments(result) -> tuple[str, ...]:
    """把可查看的本地详情文件挂到工具结果附件。"""
    detail_file = str(result.detail_file or "").strip()
    if detail_file:
        return (detail_file,)
    return ()


def _error_detail(result) -> str:
    """生成稳定错误信息，供模型和测试识别。"""
    return f"context ref {result.ref_type}:{result.ref_id} read failed: {result.status}"


def _self_test() -> None:
    """验证工具名与 contract 一致。"""
    assert ReadContextRefTool.name == ReadContextRefTool.contract["function"]["name"]


if __name__ == "__main__":
    _self_test()
    print("dutyflow read_context_ref logic self-test passed")
