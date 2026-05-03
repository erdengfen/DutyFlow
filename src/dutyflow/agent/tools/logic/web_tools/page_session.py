# 本文件维护 web_fetch 产生的页面会话，供 web_read_link 溯源跳转。

from __future__ import annotations

# 关键开关：单进程内最多缓存 100 个页面会话，超出后淘汰最旧的条目。
_MAX_PAGE_SESSIONS = 100

# 格式：page_id → {link_id → url}
_SESSIONS: dict[str, dict[str, str]] = {}


def register_page(page_id: str, links: list[dict[str, str]]) -> None:
    """注册页面链接会话，供后续 web_read_link 按 link_id 溯源。

    links 元素需包含 link_id 和 url 两个字段。
    """
    if len(_SESSIONS) >= _MAX_PAGE_SESSIONS:
        oldest = next(iter(_SESSIONS))
        del _SESSIONS[oldest]
    _SESSIONS[page_id] = {item["link_id"]: item["url"] for item in links if "link_id" in item and "url" in item}


def resolve_link(page_id: str, link_id: str) -> str | None:
    """按 page_id + link_id 解析 URL，页面或链接不存在时返回 None。"""
    page = _SESSIONS.get(page_id)
    if page is None:
        return None
    return page.get(link_id)


def clear() -> None:
    """清空所有页面会话，仅供测试使用。"""
    _SESSIONS.clear()


def _self_test() -> None:
    clear()
    register_page("page_001", [
        {"link_id": "link_001", "url": "https://example.com/a"},
        {"link_id": "link_002", "url": "https://example.com/b"},
    ])
    assert resolve_link("page_001", "link_001") == "https://example.com/a"
    assert resolve_link("page_001", "link_999") is None
    assert resolve_link("page_999", "link_001") is None
    clear()


if __name__ == "__main__":
    _self_test()
    print("dutyflow page_session self-test passed")
