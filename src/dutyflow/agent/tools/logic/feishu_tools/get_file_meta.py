# 本文件实现 feishu_get_file_meta 工具：读取飞书云盘文件元信息。

from __future__ import annotations

import json
from pathlib import Path

from dutyflow.agent.tools.contracts.feishu_tools.get_file_meta_contract import (
    FEISHU_GET_FILE_META_TOOL_CONTRACT,
)
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, error_envelope

_VALID_FILE_TYPES = frozenset({"doc", "docx", "sheet", "bitable", "folder", "file"})


class FeishuGetFileMetaTool:
    """读取飞书云盘文件或文档的元信息，不读取正文。"""

    name = "feishu_get_file_meta"
    contract = FEISHU_GET_FILE_META_TOOL_CONTRACT
    is_concurrency_safe = True
    requires_approval = False
    timeout_seconds = 15.0
    max_retries = 1
    retry_policy = "transient_only"
    idempotency = "read_only"
    degradation_mode = "none"
    fallback_tool_names = ()

    def handle(self, tool_call: ToolCall, tool_use_context) -> ToolResultEnvelope:
        """校验输入，查询文件元信息，返回结构化结果。"""
        file_token = str(tool_call.tool_input.get("file_token", "")).strip()
        file_type = str(tool_call.tool_input.get("file_type", "")).strip()

        if not file_token:
            return error_envelope(tool_call, "invalid_input", "file_token 不能为空")
        if file_type not in _VALID_FILE_TYPES:
            return error_envelope(
                tool_call,
                "invalid_input",
                f"file_type 无效：{file_type!r}，合法值：{sorted(_VALID_FILE_TYPES)}",
            )

        client = _build_client(tool_use_context.cwd)
        result = client.get_file_meta(file_token, file_type)

        if not result.ok:
            return error_envelope(tool_call, result.status, result.detail)

        payload = {
            "file_token": result.file_token,
            "file_type": result.file_type,
            "title": result.title,
            "owner_id": result.owner_id,
            "create_time": result.create_time,
            "edit_time": result.edit_time,
            "fetched_at": result.fetched_at,
        }
        return ToolResultEnvelope(
            tool_call.tool_use_id,
            tool_call.tool_name,
            True,
            json.dumps(payload, ensure_ascii=False),
        )


def _build_client(cwd: Path):
    """从项目根目录加载当前配置并构造资源客户端。"""
    from dutyflow.config.env import load_env_config
    from dutyflow.feishu.oauth import FeishuOAuthManager
    from dutyflow.feishu.user_resource import FeishuUserResourceClient

    config = load_env_config(cwd)
    manager = FeishuOAuthManager(config, cwd)
    return FeishuUserResourceClient(manager)


def _self_test() -> None:
    """验证 token 缺失和 file_type 非法时均返回错误，不抛出异常。"""
    from unittest.mock import MagicMock, patch
    from dutyflow.feishu.user_resource import FileMetaResult

    ctx = MagicMock()
    ctx.cwd = Path("/tmp")

    # file_type 非法
    call_bad = ToolCall("tid_1", "feishu_get_file_meta", {"file_token": "box", "file_type": "unknown"}, 0, 0)
    envelope = FeishuGetFileMetaTool().handle(call_bad, ctx)
    assert not envelope.ok
    assert envelope.error_kind == "invalid_input"

    # token_missing
    call_ok = ToolCall("tid_2", "feishu_get_file_meta", {"file_token": "boxcnXXX", "file_type": "file"}, 0, 0)
    fake_result = FileMetaResult(
        ok=False, status="token_missing", file_token="boxcnXXX", file_type="file",
        title="", owner_id="", create_time="", edit_time="", fetched_at="",
        detail="尚未完成 OAuth 授权",
    )
    with patch(
        "dutyflow.agent.tools.logic.feishu_tools.get_file_meta._build_client"
    ) as mock_build:
        mock_client = MagicMock()
        mock_client.get_file_meta.return_value = fake_result
        mock_build.return_value = mock_client
        envelope2 = FeishuGetFileMetaTool().handle(call_ok, ctx)

    assert not envelope2.ok
    assert envelope2.error_kind == "token_missing"


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu_get_file_meta logic self-test passed")
