# 本文件实现 feishu_search_drive 工具：在飞书云盘按关键词搜索文档和文件。

from __future__ import annotations

import json
from pathlib import Path

from dutyflow.agent.tools.contracts.feishu_tools.search_drive_contract import (
    FEISHU_SEARCH_DRIVE_TOOL_CONTRACT,
)
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, error_envelope

# 读取类文件（docx/doc/wiki）的下一步推荐工具。
_DOC_TYPES = frozenset({"docx", "doc", "wiki"})
# 单次搜索最大允许 count，防止调用方无意中请求过多条目。
_MAX_COUNT = 20


class FeishuSearchDriveTool:
    """在飞书云盘按关键词搜索用户可见的文档和文件。"""

    name = "feishu_search_drive"
    contract = FEISHU_SEARCH_DRIVE_TOOL_CONTRACT
    is_concurrency_safe = True
    requires_approval = False
    timeout_seconds = 20.0
    max_retries = 1
    retry_policy = "transient_only"
    idempotency = "read_only"
    degradation_mode = "none"
    fallback_tool_names = ()

    def handle(self, tool_call: ToolCall, tool_use_context) -> ToolResultEnvelope:
        """校验输入，调用云盘搜索，返回匹配文件列表及推荐下一步工具。"""
        query = str(tool_call.tool_input.get("query", "")).strip()
        if not query:
            return error_envelope(tool_call, "invalid_input", "query 不能为空")

        raw_count = tool_call.tool_input.get("count", 10)
        try:
            count = max(1, min(int(raw_count), _MAX_COUNT))
        except (TypeError, ValueError):
            count = 10

        client = _build_client(tool_use_context.cwd)
        result = client.search_drive(query, count)

        if not result.ok:
            return error_envelope(tool_call, result.status, result.detail)

        files_payload = [
            {
                "name": f.name,
                "token": f.token,
                "type": f.file_type,
                "url": f.url,
                "modified_time": f.modified_time,
                # 告知 LLM 针对此文件类型的推荐下一步工具，减少推断负担。
                "next_tool": "feishu_read_doc" if f.file_type in _DOC_TYPES else "feishu_get_file_meta",
            }
            for f in result.files
        ]
        payload = {
            "query": result.query,
            "total": result.total,
            "has_more": result.has_more,
            "count": len(result.files),
            "files": files_payload,
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
    """验证 query 为空时返回 invalid_input 错误，不抛出异常。"""
    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.cwd = Path("/tmp")

    call = ToolCall("tid_1", "feishu_search_drive", {"query": ""}, 0, 0)
    envelope = FeishuSearchDriveTool().handle(call, ctx)
    assert not envelope.ok
    assert envelope.error_kind == "invalid_input"


if __name__ == "__main__":
    _self_test()
    print("dutyflow feishu_search_drive logic self-test passed")
