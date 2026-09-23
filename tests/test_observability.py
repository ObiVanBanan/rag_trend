from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import nomenclature_matcher.observability as obs


def _reset_metrics() -> None:
    for metric in obs._METRICS:
        metric.values.clear()


def test_metrics_are_prefixed_and_do_not_use_request_id_labels():
    _reset_metrics()
    obs.record_match_item("MATCHED", 1.25)
    rendered = obs.render_metrics()

    assert 'rag_tender_match_items_total{status="MATCHED"} 1' in rendered
    assert "rag_tender_match_item_duration_seconds_bucket" in rendered
    assert "request_id=" not in rendered


def test_request_context_is_added_to_structured_trace(caplog):
    caplog.set_level(logging.INFO, logger="nomenclature_matcher.observability")
    token = obs.set_request_id("request-123")
    item_token = obs.set_item_index(2)
    try:
        obs.log_match_trace("rerank_completed", duration_ms=12.5, status="MATCHED")
    finally:
        obs.reset_item_index(item_token)
        obs.reset_request_id(token)

    payload = json.loads(caplog.records[0].message)
    assert payload["event"] == "match_trace"
    assert payload["request_id"] == "request-123"
    assert payload["item_index"] == 2
    assert payload["stage"] == "rerank_completed"


class Embedder:
    def embed_query(self, text):
        return [1.0, 2.0]


def test_observed_embedder_preserves_result_and_records_metrics():
    _reset_metrics()
    wrapped = obs.ObservedEmbedder(
        Embedder(), SimpleNamespace(match_trace_enabled=False)
    )

    assert wrapped.embed_query("x") == [1.0, 2.0]
    rendered = obs.render_metrics()
    assert "rag_tender_embedding_requests_total 1" in rendered
    assert "rag_tender_embedding_duration_seconds_count 1" in rendered



def test_attribute_diagnostic_surfaces_hard_conflict():
    diagnostic = obs.build_attribute_diagnostic(
        {"product_type": "ball_valve", "dn": 15},
        {"product_type": "ball_valve", "dn": 20, "body_material": "brass"},
        {"product_type": "ball_valve", "dn": 15},
    )

    assert diagnostic["changed_fields"] == ["dn", "body_material"]
    assert diagnostic["hard_conflict_fields"] == ["dn"]
    assert diagnostic["hard_conflicts"]["dn"] == {"hard": 15, "enriched": 20}


def test_web_enrichment_and_decision_metrics_are_bounded():
    _reset_metrics()
    obs.record_web_enrichment("accepted", 0.8)
    obs.record_web_enrichment("unexpected-value", 1.2)
    obs.record_enrichment_gate("pre_enrichment_no_product_identity")
    obs.record_attribute_conflicts(["dn", "unknown-field"])
    code = obs.record_match_decision(
        "NOT_FOUND",
        "HARD_CONSTRAINT_FILTER: no retrieved candidate satisfies all QUERY_CONSTRAINTS",
    )

    rendered = obs.render_metrics()
    assert code == "HARD_CONSTRAINT_FILTER"
    assert 'rag_tender_web_enrichment_total{status="accepted"} 1' in rendered
    assert 'rag_tender_web_enrichment_total{status="error"} 1' in rendered
    assert 'rag_tender_enrichment_gate_total{reason="no_identity"} 1' in rendered
    assert 'rag_tender_attribute_conflicts_total{field="dn"} 1' in rendered
    assert 'rag_tender_attribute_conflicts_total{field="other"} 1' in rendered
    assert (
        'rag_tender_match_decisions_total{status="NOT_FOUND",reason="HARD_CONSTRAINT_FILTER"} 1'
        in rendered
    )
