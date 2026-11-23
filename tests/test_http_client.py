import httpx
import pytest

from bili_cli import runtime


def test_http_client_tolerates_ipv6_cidr_in_no_proxy(monkeypatch):
    """Issue #28: Tailscale writes IPv6 CIDRs into NO_PROXY; httpx 0.28 rejects them with InvalidURL."""
    monkeypatch.setenv("NO_PROXY", "localhost,fd7a:115c:a1e0::/48,127.0.0.1")
    monkeypatch.setenv("no_proxy", "localhost,fd7a:115c:a1e0::/48,127.0.0.1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)

    client = runtime.http_client(timeout=15)
    try:
        patterns = {p.pattern for p in client._mounts}
        assert "https://" in patterns
        assert "all://localhost" in patterns
        assert "all://127.0.0.1" in patterns
        assert not any("fd7a:115c" in p for p in patterns)
    finally:
        client._transport and None


def test_web_client_tolerates_ipv6_cidr_only_no_proxy(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "fd7a:115c:a1e0::/48")
    monkeypatch.setenv("no_proxy", "fd7a:115c:a1e0::/48")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)

    client = runtime.web_client(timeout=10)
    assert isinstance(client, httpx.AsyncClient)
    assert client.headers["Referer"] == "https://www.bilibili.com/"


def test_http_client_still_raises_for_invalid_base_url(monkeypatch):
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    with pytest.raises(httpx.InvalidURL):
        runtime.http_client(base_url="http://[::1:bad")
