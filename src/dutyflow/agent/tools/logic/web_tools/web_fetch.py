# 本文件实现 web_fetch 工具，读取单个页面正文并提取内部链接。

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from dutyflow.agent.tools.contracts.web_tools.web_fetch_contract import WEB_FETCH_TOOL_CONTRACT
from dutyflow.agent.tools.logic.web_tools.web_guard import check_url_safety
from dutyflow.agent.tools.logic.web_tools.page_session import register_page
from dutyflow.agent.tools.types import ToolCall, ToolResultEnvelope, error_envelope

# 关键开关：单次 fetch 最大读取字节数上限 1MB；超过截断。
MAX_BYTES_LIMIT = 1_048_576
# 关键开关：默认读取 200KB。
DEFAULT_MAX_BYTES = 204_800
# 关键开关：超时上限 30 秒。
MAX_TIMEOUT = 30.0
DEFAULT_TIMEOUT = 15.0
# 关键开关：重定向最多跟随 5 次，防止重定向链攻击。
MAX_REDIRECTS = 5
# 关键开关：模型上下文只保留正文前 1000 字，完整内容写入 Evidence。
PREVIEW_CHARS = 1000
# 关键开关：单页最多提取 50 条链接，避免链接索引撑大上下文。
MAX_LINKS = 50


class WebFetchTool:
    """读取单个 HTTP/HTTPS 页面，提取正文和内部链接，完整内容外置 Evidence。"""

    name = "web_fetch"
    contract = WEB_FETCH_TOOL_CONTRACT
    is_concurrency_safe = True
    requires_approval = False
    timeout_seconds = 35.0
    max_retries = 1
    retry_policy = "transient_only"
    idempotency = "read_only"
    degradation_mode = "none"
    fallback_tool_names = ()

    def handle(self, tool_call: ToolCall, tool_use_context) -> ToolResultEnvelope:
        """执行 URL 安全校验、HTTP 抓取、正文提取和 Evidence 落盘。"""
        url = str(tool_call.tool_input.get("url", "")).strip()
        if not url:
            return error_envelope(tool_call, "invalid_input", "url 不能为空")

        safety_error = check_url_safety(url)
        if safety_error:
            return error_envelope(tool_call, "url_blocked", safety_error)

        max_bytes = int(tool_call.tool_input.get("max_bytes", DEFAULT_MAX_BYTES))
        max_bytes = max(1, min(max_bytes, MAX_BYTES_LIMIT))

        timeout = float(tool_call.tool_input.get("timeout", DEFAULT_TIMEOUT))
        timeout = max(1.0, min(timeout, MAX_TIMEOUT))

        extract_links = bool(tool_call.tool_input.get("extract_links", True))

        try:
            fetch_result = _fetch(url, max_bytes, timeout)
        except Exception as exc:
            return error_envelope(tool_call, "fetch_failed", f"抓取失败：{exc}")

        # 重定向后的最终 URL 也要过安全校验
        if fetch_result["final_url"] != url:
            redirect_error = check_url_safety(fetch_result["final_url"])
            if redirect_error:
                return error_envelope(tool_call, "redirect_blocked", redirect_error)

        links: list[dict] = []
        if extract_links and fetch_result.get("html"):
            links = _extract_links(fetch_result["html"], fetch_result["final_url"])[:MAX_LINKS]

        page_id = f"page_{uuid4().hex[:8]}"
        if links:
            register_page(page_id, links)

        main_text = fetch_result.get("main_text", "")
        preview = main_text[:PREVIEW_CHARS]
        truncated = fetch_result.get("truncated", False) or len(main_text) > PREVIEW_CHARS

        # 把完整正文写入 Evidence Store（若有 cwd 可访问时）
        evidence_path = _save_evidence(tool_use_context, tool_call, fetch_result, url)

        payload = {
            "page_id": page_id,
            "status_code": fetch_result.get("status_code", 0),
            "final_url": fetch_result["final_url"],
            "content_type": fetch_result.get("content_type", ""),
            "title": fetch_result.get("title", ""),
            "main_text_preview": preview,
            "truncated": truncated,
            "evidence_path": evidence_path,
            "fetch_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "links": [
                {k: v for k, v in link.items() if k != "href_raw"}
                for link in links
            ],
        }
        return ToolResultEnvelope(
            tool_call.tool_use_id,
            tool_call.tool_name,
            True,
            json.dumps(payload, ensure_ascii=False),
        )


def _fetch(url: str, max_bytes: int, timeout: float) -> dict:
    """发起 HTTP 请求并返回状态码、最终 URL、内容类型和正文。"""
    import httpx

    headers = {
        "User-Agent": "DutyFlow-WebReader/1.0 (personal agent; read-only)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    with httpx.Client(
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
        timeout=timeout,
        headers=headers,
    ) as client:
        with client.stream("GET", url) as response:
            content_type = response.headers.get("content-type", "")
            raw_bytes = b""
            truncated = False
            for chunk in response.iter_bytes(chunk_size=8192):
                raw_bytes += chunk
                if len(raw_bytes) >= max_bytes:
                    truncated = True
                    break

    html = ""
    main_text = ""
    title = ""
    encoding = _detect_encoding(content_type, raw_bytes)

    if "text/html" in content_type or "application/xhtml" in content_type:
        html = raw_bytes.decode(encoding, errors="replace")
        main_text, title = _extract_main_text(html, str(response.url))
    elif "text/" in content_type:
        main_text = raw_bytes.decode(encoding, errors="replace")

    return {
        "status_code": response.status_code,
        "final_url": str(response.url),
        "content_type": content_type,
        "html": html,
        "main_text": main_text,
        "title": title,
        "truncated": truncated,
    }


def _detect_encoding(content_type: str, raw_bytes: bytes) -> str:
    """从 Content-Type 或 BOM 检测编码，fallback 到 utf-8。"""
    import re
    match = re.search(r"charset=([^\s;]+)", content_type, re.IGNORECASE)
    if match:
        return match.group(1).strip().lower()
    if raw_bytes[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    return "utf-8"


def _extract_main_text(html: str, url: str) -> tuple[str, str]:
    """用 trafilatura 提取主正文，失败时 fallback 到 BeautifulSoup 文本。"""
    title = ""
    try:
        import trafilatura  # type: ignore[import-untyped]
        text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True) or ""
        # 提取标题
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)
        return text, title
    except Exception:
        pass
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True), title
    except Exception:
        return html[:2000], ""


def _extract_links(html: str, base_url: str) -> list[dict]:
    """从页面 HTML 提取内部和外部链接，并标注属性。"""
    try:
        from bs4 import BeautifulSoup
        from urllib.parse import urljoin, urlparse

        soup = BeautifulSoup(html, "html.parser")
        base_domain = urlparse(base_url).netloc

        links = []
        seen: set[str] = set()
        for i, tag in enumerate(soup.find_all("a", href=True)):
            href = str(tag.get("href", "")).strip()
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue
            abs_url = urljoin(base_url, href)
            parsed = urlparse(abs_url)
            if parsed.scheme not in {"http", "https"}:
                continue
            if abs_url in seen:
                continue
            seen.add(abs_url)
            anchor = tag.get_text(strip=True)[:120]
            same_domain = parsed.netloc == base_domain
            likely_next = _is_likely_next_page(anchor, abs_url)
            links.append({
                "link_id": f"link_{i + 1:03d}",
                "anchor_text": anchor,
                "url": abs_url,
                "same_domain": same_domain,
                "likely_next_page": likely_next,
            })
        return links
    except Exception:
        return []


def _is_likely_next_page(anchor: str, url: str) -> bool:
    """启发式判断链接是否是"下一页"类导航。"""
    next_keywords = {"next", "下一页", "下一章", "下一篇", "page 2", "›", "»", "→"}
    anchor_lower = anchor.lower()
    url_lower = url.lower()
    return any(kw in anchor_lower for kw in next_keywords) or "page=2" in url_lower


def _save_evidence(tool_use_context, tool_call: ToolCall, fetch_result: dict, url: str) -> str:
    """将完整正文写入 Evidence Store，返回相对路径；失败时返回空字符串。"""
    try:
        cwd = getattr(tool_use_context, "cwd", None)
        if cwd is None:
            return ""
        from dutyflow.context.evidence_store import EvidenceStore
        store = EvidenceStore(Path(cwd))
        main_text = fetch_result.get("main_text", "")
        if not main_text:
            return ""
        summary = f"页面：{fetch_result.get('title', url)}（{fetch_result.get('final_url', url)}）"
        record = store.save_content(
            source_type="tool_result",
            source_id=tool_call.tool_use_id,
            content=main_text,
            summary=summary,
            tool_use_id=tool_call.tool_use_id,
            tool_name=tool_call.tool_name,
            content_format="text",
        )
        return record.relative_path
    except Exception:
        return ""


def _self_test() -> None:
    """验证 URL 安全校验和正文提取路径（不发起真实网络请求）。"""
    from unittest.mock import MagicMock, patch

    # 私有 IP 被拦截
    call = ToolCall("tid_1", "web_fetch", {"url": "http://192.168.1.1/admin"}, 0, 0)
    result = WebFetchTool().handle(call, object())
    assert not result.ok
    assert result.error_kind == "url_blocked"

    # 空 URL
    call2 = ToolCall("tid_2", "web_fetch", {"url": ""}, 0, 0)
    result2 = WebFetchTool().handle(call2, object())
    assert not result2.ok
    assert result2.error_kind == "invalid_input"

    # 正常 URL mock
    fake_fetch = {
        "status_code": 200,
        "final_url": "https://example.com/",
        "content_type": "text/html",
        "html": "<html><head><title>Example</title></head><body><p>Hello</p><a href='/page2'>Next</a></body></html>",
        "main_text": "Hello",
        "title": "Example",
        "truncated": False,
    }
    call3 = ToolCall("tid_3", "web_fetch", {"url": "https://example.com/"}, 0, 0)
    ctx = MagicMock()
    ctx.cwd = None
    with patch(
        "dutyflow.agent.tools.logic.web_tools.web_fetch._fetch",
        return_value=fake_fetch,
    ):
        result3 = WebFetchTool().handle(call3, ctx)
    assert result3.ok
    payload = json.loads(result3.content)
    assert payload["title"] == "Example"
    assert "page_id" in payload
    assert isinstance(payload["links"], list)


if __name__ == "__main__":
    _self_test()
    print("dutyflow web_fetch logic self-test passed")
