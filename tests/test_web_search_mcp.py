from __future__ import annotations

from types import SimpleNamespace

from nomenclature_matcher.query_signals import product_identity_anchors
from nomenclature_matcher.web_search_mcp import (
    MCPWebSearchLookup,
    WebPageEvidence,
    WebSearchLookupResult,
    _evidence_matches_identity,
    _search_failure_reason,
    extract_identity_fetch_targets,
)


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


def test_product_identity_anchor_prefers_specific_model_code():
    anchors = product_identity_anchors("Кран шаровой ALSO КШ.К.050.25-01")
    assert anchors[0] == "кшк0502501"


def test_neighboring_web_sku_does_not_verify_identity():
    anchors = product_identity_anchors("Кран шаровой ALSO КШ.К.050.25-01")

    assert _evidence_matches_identity(
        "ALSO КШ.К.050.25-01 Ду50 Ру25",
        anchors,
    )
    assert not _evidence_matches_identity(
        "ALSO КШ.Ф.050.40-01 Ду50 Ру40",
        anchors,
    )


def test_generic_family_page_does_not_verify_specific_marshal_variant():
    anchors = product_identity_anchors(
        "Кран шаровой MARSHAL 11с67п ЦФ.01.7.025.150"
    )

    assert not _evidence_matches_identity(
        "11с67п — кран шаровой стальной фланцевый Маршал",
        anchors,
    )
    assert _evidence_matches_identity(
        "11с67п ЦФ.01.7.025.150 Ду150 Ру25",
        anchors,
    )


def test_identity_targets_prefer_exact_result_block():
    anchors = product_identity_anchors("Кран шаровой ALSO КШ.К.050.25-01")
    search_text = """
1. Neighbor
   URL: https://example.test/neighbor
   Summary: ALSO КШ.Ф.050.40-01 Ду50 Ру40

2. Exact
   URL: https://example.test/exact
   Summary: ALSO КШ.К.050.25-01 Ду50 Ру25
"""
    assert extract_identity_fetch_targets(search_text, anchors, 3) == [
        "https://example.test/exact"
    ]


def test_prompt_context_exposes_only_verified_pages_not_raw_search_results():
    result = WebSearchLookupResult(
        attempted=True,
        accepted=True,
        reason="web_identity_verified",
        query="Кран шаровой ALSO КШ.К.050.25-01",
        search_query="search",
        search_results="neighboring unverified result",
        pages=[
            WebPageEvidence(
                target="https://example.test/exact",
                text="ALSO КШ.К.050.25-01 Ду50 Ру25",
            )
        ],
        identity_anchors=["кшк0502501"],
        identity_verified=True,
    )

    debug = result.debug_payload()
    context = result.prompt_context()

    assert debug["search_results"] == "neighboring unverified result"
    assert debug["identity_verified"] is True
    assert context["search_results"] == ""
    assert context["identity_verified"] is True
    assert context["pages"][0]["text"].startswith("ALSO КШ.К.050.25-01")


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
