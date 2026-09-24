from __future__ import annotations

from types import SimpleNamespace

from nomenclature_matcher.web_search_mcp import MCPWebSearchLookup, _search_failure_reason


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



def test_mcp_runtime_includes_socks_support():
    lookup = MCPWebSearchLookup(settings())
    args = lookup._server_args()

    assert "socksio>=1,<2" in args
    assert "duckduckgo-mcp-server" in args


def test_bot_detection_text_is_not_accepted_as_web_evidence():
    text = (
        "No results were found for your search query. "
        "This could be due to DuckDuckGo's bot detection."
    )
    assert _search_failure_reason(text) == "mcp_bot_detection"


def test_plain_no_results_is_not_web_evidence():
    assert (
        _search_failure_reason("No results were found for your search query.")
        == "no_web_evidence"
    )



def test_warmup_uses_startup_timeout(monkeypatch):
    lookup = MCPWebSearchLookup(
        settings(web_search_startup_timeout_seconds=30)
    )
    seen = {}

    def fake_ensure_worker(*, startup_timeout_seconds=None):
        seen["timeout"] = startup_timeout_seconds

    monkeypatch.setattr(lookup, "_ensure_worker", fake_ensure_worker)

    lookup.warmup()

    assert seen["timeout"] == 30


def test_failed_warmup_opens_circuit(monkeypatch):
    lookup = MCPWebSearchLookup(
        settings(
            web_search_startup_timeout_seconds=30,
            web_search_circuit_breaker_failures=3,
            web_search_circuit_breaker_cooldown_seconds=300,
        )
    )

    def fail(*, startup_timeout_seconds=None):
        raise RuntimeError("startup failed")

    monkeypatch.setattr(lookup, "_ensure_worker", fail)

    import pytest
    with pytest.raises(RuntimeError, match="startup failed"):
        lookup.warmup()

    assert lookup._circuit_is_open() is True
