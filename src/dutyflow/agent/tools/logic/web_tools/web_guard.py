# 本文件实现 web 工具的 URL 安全校验，在发起任何网络请求前执行。

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

# 只允许 http 和 https；file://, ftp://, gopher:// 等一律拒绝。
ALLOWED_SCHEMES = frozenset({"http", "https"})

# 拒绝的私有 hostname 关键词，补充 IP 段检查的盲区。
PRIVATE_HOSTNAMES = frozenset({
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "broadcasthost",
})

# RFC 1918 / RFC 3927 / loopback / link-local 私有地址段。
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),      # loopback
    ipaddress.ip_network("10.0.0.0/8"),       # RFC 1918
    ipaddress.ip_network("172.16.0.0/12"),    # RFC 1918
    ipaddress.ip_network("192.168.0.0/16"),   # RFC 1918
    ipaddress.ip_network("169.254.0.0/16"),   # link-local
    ipaddress.ip_network("::1/128"),           # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),          # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),         # IPv6 link-local
]


def check_url_safety(url: str) -> str:
    """对 URL 执行安全校验，返回错误描述；安全时返回空字符串。

    只检查可在不发起网络请求的情况下确定的风险；不做 DNS 解析。
    """
    try:
        parsed = urlparse(url)
    except Exception as exc:
        return f"URL 解析失败：{exc}"

    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        return f"不允许的 scheme：{scheme!r}，只允许 http 或 https"

    host = (parsed.hostname or "").lower().strip()
    if not host:
        return "URL 缺少 host"

    if host in PRIVATE_HOSTNAMES:
        return f"不允许访问私有 host：{host}"

    # 尝试按 IP 解析——若 host 本身就是 IP 地址则直接校验。
    try:
        addr = ipaddress.ip_address(host)
        if _is_private_addr(addr):
            return f"不允许访问私有 IP：{host}"
    except ValueError:
        # host 是域名而非 IP，无需 DNS 解析；私有域名只通过关键词拦截。
        pass

    return ""


def _is_private_addr(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """判断 IP 地址是否属于私有地址段。"""
    return any(addr in net for net in _PRIVATE_NETWORKS)


def _self_test() -> None:
    assert check_url_safety("https://example.com") == ""
    assert check_url_safety("http://example.com/path") == ""
    assert "scheme" in check_url_safety("file:///etc/passwd")
    assert "scheme" in check_url_safety("ftp://example.com")
    assert "私有" in check_url_safety("http://localhost/api")
    assert "私有" in check_url_safety("http://127.0.0.1/secret")
    assert "私有" in check_url_safety("http://192.168.1.1/admin")
    assert "私有" in check_url_safety("http://10.0.0.1/internal")
    assert "私有" in check_url_safety("http://169.254.169.254/metadata")
    assert check_url_safety("http://example.com") == ""


if __name__ == "__main__":
    _self_test()
    print("dutyflow web_guard self-test passed")
