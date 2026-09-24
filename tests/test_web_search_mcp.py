from __future__ import annotations

from types import SimpleNamespace

from nomenclature_matcher.web_search_mcp import MCPWebSearchLookup


def settings(**overrides):
    values = {
        "web_search_max_results": 6,
        "web_search_fetch_pages": 3,
        "web_search_fetch_chars": 5000,
        "web_search_timeout_seconds": 8,
        "web_search_region": "wt-wt",
        "web_search_mcp_command": "uvx",
        "web_search_mcp_package": "duckduckgo-mcp-server[browser]",
        "web_search_proxy_url": "",
        "web_search_circuit_breaker_failures": 3,
        "web_search_circuit_breaker_cooldown_seconds": 300,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_mcp_subprocess_receives_explicit_proxy(monkeypatch):
    monkeypatch.delenv("ALL_PROXY", raising=False)
    lookup = MCPWebSearchLookup(
        settings(web_search_proxy_url="socks5://proxy.example:1080")
    )

    env = lookup._server_environment()

    assert env["HTTP_PROXY"] == "socks5://proxy.example:1080"
    assert env["HTTPS_PROXY"] == "socks5://proxy.example:1080"
    assert env["ALL_PROXY"] == "socks5://proxy.example:1080"
    assert env["all_proxy"] == "socks5://proxy.example:1080"


def test_mcp_subprocess_falls_back_to_api_all_proxy(monkeypatch):
    monkeypatch.setenv("ALL_PROXY", "socks5://shared-proxy.example:1080")
    lookup = MCPWebSearchLookup(settings())

    env = lookup._server_environment()

    assert env["ALL_PROXY"] == "socks5://shared-proxy.example:1080"
    assert env["HTTPS_PROXY"] == "socks5://shared-proxy.example:1080"


def test_circuit_breaker_skips_lookup_after_repeated_failures():
    lookup = MCPWebSearchLookup(
        settings(
            web_search_circuit_breaker_failures=3,
            web_search_circuit_breaker_cooldown_seconds=300,
        )
    )
    lookup._register_failure()
    lookup._register_failure()
    lookup._register_failure()

    result = lookup.lookup("Кран шаровый VT.217.N.05")

    assert result.attempted is False
    assert result.accepted is False
    assert result.reason == "circuit_open_after_web_failures"


def test_web_lookup_timeout_is_fail_fast():
    lookup = MCPWebSearchLookup(settings(web_search_timeout_seconds=8))
    assert lookup.timeout_seconds == 8
