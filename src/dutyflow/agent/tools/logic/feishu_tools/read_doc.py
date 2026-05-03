# 本文件实现 feishu_read_doc 工具：读取飞书 docx 正文并写入 Evidence Store。

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from dutyflow.agent.tools.contracts.feishu_tools.read_doc_contract import (
    FEISHU_READ_DOC_TOOL_CONTRACT,
)
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, error_envelope

# 关键开关：模型上下文只保留文档正文前 1000 字，完整内容写入 Evidence Store。
PREVIEW_CHARS = 1000


class FeishuReadDocTool:
    """读取飞书 docx 文档正文，完整内容外置到 Evidence Store。"""

    name = "feishu_read_doc"
    contract = FEISHU_READ_DOC_TOOL_CONTRACT
    is_concurrency_safe = True
    requires_approval = False
    timeout_seconds = 30.0
    max_retries = 1
    retry_policy = "transient_only"
    idempotency = "read_only"
    degradation_mode = "none"
    fallback_tool_names = ()

    def handle(self, tool_call: ToolCall, tool_use_context) -> ToolResultEnvelope:
        """校验输入，读取文档正文，落盘 Evidence，返回预览摘要。"""
        doc_token = str(tool_call.tool_input.get("doc_token", "")).strip()
        if not doc_token:
            return error_envelope(tool_call, "invalid_input", "doc_token 不能为空")

        client = _build_client(tool_use_context.cwd)
        result = client.read_doc(doc_token)

        if not result.ok:
            return error_envelope(tool_call, result.status, result.detail)

        evidence_path = _save_evidence(tool_use_context, tool_call, result)
        preview = result.content[:PREVIEW_CHARS]
        truncated = len(result.content) > PREVIEW_CHARS

        payload = {
            "doc_token": result.doc_token,
            "title": result.title,
            "content_preview": preview,
            "truncated": truncated,
            "evidence_path": evidence_path,
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


def _save_evidence(tool_use_context, tool_call: ToolCall, result) -> str:
    """把文档完整正文写入 Evidence Store，返回相对路径；失败时返回空字符串。"""
    try:
        from dutyflow.context.evidence_store import EvidenceStore

        store = EvidenceStore(tool_use_context.cwd)
        title_hint = result.title or result.doc_token
        summary = f"飞书文档：{title_hint}（token={result.doc_token}）"
        record = store.save_content(
            source_type="tool_result",
            source_id=tool_call.tool_use_id,
            content=result.content,
            summary=summary,
            tool_use_id=tool_call.tool_use_id,
            tool_name=tool_call.tool_name,
            content_format="text",
        )
        return record.relative_path
    except Exception:  # noqa: BLE001
        return ""


def _self_test() -> None:
    """验证 token 缺失时返回 token_missing 错误，不抛出异常。"""
    from unittest.mock import MagicMock, patch
    from dutyflow.feishu.user_resource import DocReadResult

    call = ToolCall("tid_1", "feishu_read_doc", {"doc_token": "doxcnXXX"}, 0, 0)
    ctx = MagicMock()
    ctx.cwd = Path("/tmp")

    fake_result = DocReadResult(
        ok=False, status="token_missing", doc_token="doxcnXXX",
        title="", content="", fetched_at="", detail="尚未完成 OAuth 授权",
    )
    with patch(
        "dutyflow.agent.tools.logic.feishu_tools.read_doc._build_client"
    ) as mock_build:
        mock_client = MagicMock()
        mock_client.read_doc.return_value = fake_result
        mock_build.return_value = mock_client
        envelope = FeishuReadDocTool().handle(call, ctx)

    assert not envelope.ok
    assert envelope.error_kind == "token_missing"


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu_read_doc logic self-test passed")
