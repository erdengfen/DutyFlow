# 本文件实现 web_search 工具，通过 DuckDuckGo 搜索关键词并返回候选 URL 列表。

from __future__ import annotations

import json
from datetime import datetime, timezone

from dutyflow.agent.tools.contracts.web_tools.web_search_contract import WEB_SEARCH_TOOL_CONTRACT
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, error_envelope

# 关键开关：单次搜索最多返回 10 条结果，防止过多候选撑大上下文。
MAX_RESULTS_LIMIT = 10
# 关键开关：默认返回 5 条结果。
DEFAULT_MAX_RESULTS = 5


class WebSearchTool:
    """通过 DuckDuckGo 搜索并返回候选 URL 列表，不直接读取页面全文。"""

    name = "web_search"
    contract = WEB_SEARCH_TOOL_CONTRACT
    is_concurrency_safe = True
    requires_approval = False
    timeout_seconds = 20.0
    max_retries = 1
    retry_policy = "transient_only"
    idempotency = "read_only"
    degradation_mode = "none"
    fallback_tool_names = ()

    def handle(self, tool_call: ToolCall, tool_use_context) -> ToolResultEnvelope:
        """执行搜索并返回结构化候选列表。"""
        query = str(tool_call.tool_input.get("query", "")).strip()
        if not query:
            return error_envelope(tool_call, "invalid_input", "query 不能为空")

        max_results = int(tool_call.tool_input.get("max_results", DEFAULT_MAX_RESULTS))
        max_results = max(1, min(max_results, MAX_RESULTS_LIMIT))

        allowed_domains: list[str] = list(tool_call.tool_input.get("allowed_domains") or [])
        blocked_domains: list[str] = list(tool_call.tool_input.get("blocked_domains") or [])
        time_range: str = str(tool_call.tool_input.get("time_range") or "").strip()

        try:
            results = _run_search(query, max_results, time_range)
        except Exception as exc:
            return error_envelope(tool_call, "search_failed", f"搜索失败：{exc}")

        results = _filter_by_domains(results, allowed_domains, blocked_domains)
        retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        payload = {
            "query": query,
            "result_count": len(results),
            "retrieved_at": retrieved_at,
            "results": [
                {
                    "rank": i + 1,
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                    "source": _domain(r.get("href", "")),
                }
                for i, r in enumerate(results)
            ],
        }
        return ToolResultEnvelope(
            tool_call.tool_use_id,
            tool_call.tool_name,
            True,
            json.dumps(payload, ensure_ascii=False),
        )


def _run_search(query: str, max_results: int, time_range: str) -> list[dict]:
    """调用 DuckDuckGo 搜索并返回原始结果列表。"""
    from duckduckgo_search import DDGS  # type: ignore[import-untyped]

    kwargs: dict = {"max_results": max_results}
    if time_range and time_range in {"d", "w", "m", "y"}:
        kwargs["timelimit"] = time_range

    with DDGS() as ddgs:
        return list(ddgs.text(query, **kwargs))


def _filter_by_domains(
    results: list[dict],
    allowed: list[str],
    blocked: list[str],
) -> list[dict]:
    """按域名白名单和黑名单过滤搜索结果。"""
    out = []
    for r in results:
        domain = _domain(r.get("href", ""))
        if allowed and not any(domain.endswith(a) for a in allowed):
            continue
        if any(domain.endswith(b) for b in blocked):
            continue
        out.append(r)
    return out


def _domain(url: str) -> str:
    """从 URL 中提取域名，解析失败时返回空字符串。"""
    try:
        from urllib.parse import urlparse
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def _self_test() -> None:
    """验证 WebSearchTool 在没有网络时返回 error_envelope 而不是抛出异常。"""
    from unittest.mock import patch

    call = ToolCall("tid_1", "web_search", {"query": "python httpx"}, 0, 0)
    context = object()

    # 模拟搜索成功
    fake_results = [
        {"title": "httpx docs", "href": "https://www.python-httpx.org/", "body": "snippet"},
    ]
    with patch(
        "dutyflow.agent.tools.logic.web_tools.web_search._run_search",
        return_value=fake_results,
    ):
        result = WebSearchTool().handle(call, context)
    assert result.ok
    payload = json.loads(result.content)
    assert payload["result_count"] == 1
    assert payload["results"][0]["url"] == "https://www.python-httpx.org/"

    # 空 query
    bad_call = ToolCall("tid_2", "web_search", {"query": "  "}, 0, 0)
    result2 = WebSearchTool().handle(bad_call, context)
    assert not result2.ok
    assert result2.error_kind == "invalid_input"


if __name__ == "__main__":
    _self_test()
    print("dutyflow web_search logic self-test passed")
