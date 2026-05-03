# 本文件实现 web_read_link 工具，从已抓取页面的链接中选择一个继续读取。

from __future__ import annotations

import json

from dutyflow.agent.tools.contracts.web_tools.web_read_link_contract import WEB_READ_LINK_TOOL_CONTRACT
from dutyflow.agent.tools.logic.web_tools.page_session import resolve_link
from dutyflow.agent.tools.logic.web_tools.web_fetch import WebFetchTool
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, error_envelope


class WebReadLinkTool:
    """从已抓取页面的链接列表中按 page_id + link_id 跳转读取，强制 URL 溯源。"""

    name = "web_read_link"
    contract = WEB_READ_LINK_TOOL_CONTRACT
    is_concurrency_safe = True
    requires_approval = False
    timeout_seconds = 35.0
    max_retries = 1
    retry_policy = "transient_only"
    idempotency = "read_only"
    degradation_mode = "none"
    fallback_tool_names = ()

    def handle(self, tool_call: ToolCall, tool_use_context) -> ToolResultEnvelope:
        """查找页面会话中的 link_id，解析 URL 后复用 web_fetch 逻辑执行读取。"""
        page_id = str(tool_call.tool_input.get("page_id", "")).strip()
        link_id = str(tool_call.tool_input.get("link_id", "")).strip()

        if not page_id or not link_id:
            return error_envelope(tool_call, "invalid_input", "page_id 和 link_id 均不能为空")

        url = resolve_link(page_id, link_id)
        if url is None:
            return error_envelope(
                tool_call,
                "link_not_found",
                f"在页面 {page_id!r} 中未找到链接 {link_id!r}；"
                "只能跳转到已抓取页面中真实存在的链接。",
            )

        # 复用 web_fetch 的安全校验、抓取和 Evidence 落盘逻辑
        fetch_call = ToolCall(
            tool_call.tool_use_id,
            "web_fetch",
            {"url": url},
            tool_call.source_message_index,
            tool_call.call_index,
        )
        result = WebFetchTool().handle(fetch_call, tool_use_context)

        # 在结果中补充溯源信息
        if result.ok:
            try:
                payload = json.loads(result.content)
                payload["from_page_id"] = page_id
                payload["from_link_id"] = link_id
                return ToolResultEnvelope(
                    tool_call.tool_use_id,
                    tool_call.tool_name,
                    True,
                    json.dumps(payload, ensure_ascii=False),
                )
            except Exception:
                pass
        return ToolResultEnvelope(
            result.tool_use_id,
            tool_call.tool_name,
            result.ok,
            result.content,
            is_error=result.is_error,
            error_kind=result.error_kind,
        )


def _self_test() -> None:
    """验证 link_id 不存在时返回错误，存在时复用 web_fetch 逻辑。"""
    from unittest.mock import MagicMock, patch
    from dutyflow.agent.tools.logic.web_tools.page_session import clear, register_page

    clear()

    # link_id 不存在
    call = ToolCall("tid_1", "web_read_link", {"page_id": "page_999", "link_id": "link_001"}, 0, 0)
    result = WebReadLinkTool().handle(call, object())
    assert not result.ok
    assert result.error_kind == "link_not_found"

    # link_id 存在时复用 web_fetch
    register_page("page_001", [{"link_id": "link_001", "url": "https://example.com/next"}])
    fake_payload = {
        "page_id": "page_abc",
        "status_code": 200,
        "final_url": "https://example.com/next",
        "content_type": "text/html",
        "title": "Next Page",
        "main_text_preview": "content",
        "truncated": False,
        "evidence_path": "",
        "fetch_time": "2026-01-01T00:00:00+00:00",
        "links": [],
    }
    fake_result = ToolResultEnvelope(
        "tid_2", "web_fetch", True, json.dumps(fake_payload, ensure_ascii=False)
    )
    call2 = ToolCall("tid_2", "web_read_link", {"page_id": "page_001", "link_id": "link_001"}, 0, 0)
    ctx = MagicMock()
    with patch.object(WebFetchTool, "handle", return_value=fake_result):
        result2 = WebReadLinkTool().handle(call2, ctx)
    assert result2.ok
    payload = json.loads(result2.content)
    assert payload["from_page_id"] == "page_001"
    assert payload["from_link_id"] == "link_001"

    clear()


if __name__ == "__main__":
    _self_test()
    print("dutyflow web_read_link logic self-test passed")
