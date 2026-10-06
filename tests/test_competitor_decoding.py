import json
from types import SimpleNamespace

from nomenclature_matcher.competitor_decoding import decode_competitor_query
from nomenclature_matcher.matcher import NomenclatureMatcher
from nomenclature_matcher.query_interpreter import DeepSeekQueryInterpreter
from nomenclature_matcher.query_signals import explicit_joining_type_from_query


def settings():
    return SimpleNamespace(
        hybrid_rerank_limit=20,
        query_interpreter_enabled=False,
        deepseek_api_key="x",
        deepseek_base_url="https://api.deepseek.com/v1",
        deepseek_model="deepseek-v4-flash",
        deepseek_timeout_seconds=20,
        match_trace_enabled=False,
    )


class FakeCompletions:
    def __init__(self, payload):
        self.payload = payload

    def create(self, **kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(self.payload, ensure_ascii=False)
                    )
                )
            ]
        )


class FakeClient:
    def __init__(self, payload):
        self.chat = SimpleNamespace(completions=FakeCompletions(payload))


def llm_payload(**overrides):
    constraints = {
        "product_type": "ball_valve",
        "dn": 25,
        "pn_min_mpa": 4.0,
        "joining_type": "threaded",
        "thread_type": None,
        "working_medium": None,
        "valve_type": "standard",
        "valve_designation": None,
        "body_material": "steel",
        "body_material_grade": "20",
        "bore_type": "full",
        "control": None,
        "catalog_scope": "in_scope",
        "ambiguous": False,
        "comment": "llm guess",
    }
    constraints.update(overrides)
    return {
        "searchable": True,
        "normalized_query": "wrong competitor interpretation",
        "reason": "ok",
        "constraints": constraints,
    }


def test_marshal_decoder_extracts_catalog_backed_dn_pn_joining_and_variant():
    decoded = decode_competitor_query(
        "Кран шаровой MARSHAL 11с67п GAS PRO 2ЦП.01.0.025.100"
    )

    assert decoded is not None
    assert decoded.manufacturer == "MARSHAL"
    assert decoded.attributes["dn"] == 100
    assert decoded.attributes["pn_min_mpa"] == 2.5
    assert decoded.attributes["joining_type"] == "welded"
    assert decoded.attributes["body_material_grade"] == "09Г2С"
    assert decoded.attributes["bore_type"] == "reduced"
    assert decoded.attributes["working_medium"] == "газ"
    assert decoded.attributes["valve_type"] == "gas"


def test_marshal_decoder_supports_full_bore_flanged_and_large_reduced_sizes():
    full = decode_competitor_query(
        "Кран шаровой MARSHAL 11с67п ЦФ.00.6.025.020"
    )
    reduced = decode_competitor_query(
        "Кран шаровой MARSHAL 11с67п 2ЦП.01.3.016.800"
    )

    assert full is not None
    assert full.attributes["dn"] == 20
    assert full.attributes["pn_min_mpa"] == 2.5
    assert full.attributes["joining_type"] == "flanged"
    assert full.attributes["body_material_grade"] == "20"
    assert full.attributes["bore_type"] == "full"

    assert reduced is not None
    assert reduced.attributes["dn"] == 800
    assert reduced.attributes["pn_min_mpa"] == 1.6
    assert reduced.attributes["joining_type"] == "welded"
    assert reduced.attributes["bore_type"] == "reduced"
    assert reduced.attributes["control"] == "gearbox"


def test_also_decoder_handles_compact_symbolic_codes():
    welded = decode_competitor_query("Кран шаровой ALSO КШ.ППР.200.25-01")
    flanged = decode_competitor_query("Кран шаровой ALSO КШ.ФПЗР.050.25-02")
    gas = decode_competitor_query("Кран шаровой ALSO КШ.К.GAS.200.25-01")

    assert welded is not None
    assert welded.attributes["dn"] == 200
    assert welded.attributes["pn_min_mpa"] == 2.5
    assert welded.attributes["joining_type"] == "welded"
    assert welded.attributes["bore_type"] == "full"
    assert welded.attributes["body_material_grade"] == "20"

    assert flanged is not None
    assert flanged.attributes["dn"] == 50
    assert flanged.attributes["joining_type"] == "flanged"
    assert flanged.attributes["bore_type"] == "full"
    assert flanged.attributes["body_material_grade"] == "09Г2С"

    assert gas is not None
    assert gas.attributes["dn"] == 200
    assert gas.attributes["pn_min_mpa"] == 2.5
    assert gas.attributes["joining_type"] is None
    assert gas.attributes["bore_type"] == "reduced"
    assert gas.attributes["working_medium"] == "газ"


def test_source_decoder_overrides_wrong_llm_or_web_attributes_and_rebuilds_query():
    interpreter = DeepSeekQueryInterpreter(
        settings(),
        client=FakeClient(llm_payload()),
    )

    result = interpreter.interpret(
        "Кран шаровой MARSHAL 11с67п GAS PRO 2ЦП.01.0.025.100"
    )

    assert result.constraints.dn == 100
    assert result.constraints.pn_min_mpa == 2.5
    assert result.constraints.joining_type == "welded"
    assert result.constraints.body_material_grade == "09Г2С"
    assert result.constraints.bore_type == "reduced"
    assert result.constraints.working_medium == "газ"
    assert "Ду100" in result.normalized_query
    assert "Ру2,5МПа" in result.normalized_query
    assert "Приварное" in result.normalized_query
    assert "09Г2С" in result.normalized_query
    assert "неполный проход" in result.normalized_query
    assert "MARSHAL" not in result.normalized_query.upper()


def test_direct_query_facts_override_model_decode_when_explicitly_conflicting():
    interpreter = DeepSeekQueryInterpreter(
        settings(),
        client=FakeClient(llm_payload()),
    )

    result = interpreter.interpret(
        "Кран шаровой MARSHAL 11с67п 2ЦП.01.0.025.100 "
        "DN80 PN16 фланцевый"
    )

    assert result.constraints.dn == 80
    assert result.constraints.pn_min_mpa == 1.6
    assert result.constraints.joining_type == "flanged"


def test_welded_body_does_not_override_explicit_flanged_connection():
    assert (
        explicit_joining_type_from_query(
            "Кран шаровой цельносварной полнопроходной фланцевый DN80 PN25"
        )
        == "flanged"
    )
    assert (
        explicit_joining_type_from_query(
            "Кран шаровой цельносварной под приварку DN80 PN25"
        )
        == "welded"
    )


def test_matcher_uses_decoded_technical_query_as_primary_retrieval_query():
    interpreter = DeepSeekQueryInterpreter(
        settings(),
        client=FakeClient(llm_payload()),
    )

    class Hybrid:
        def __init__(self):
            self.calls = []

        def search(self, query, limit, canonical_query=None):
            self.calls.append((query, limit, canonical_query))
            return []

    hybrid = Hybrid()
    matcher = NomenclatureMatcher(
        None,
        None,
        settings(),
        hybrid_retriever=hybrid,
        query_interpreter=interpreter,
    )

    result = matcher.match_one_hybrid_with_rerank(
        "Кран шаровой MARSHAL 11с67п GAS PRO 2ЦП.01.0.025.100"
    )

    assert result.status == "NOT_FOUND"
    assert hybrid.calls
    retrieval_query, limit, _ = hybrid.calls[0]
    assert limit == 20
    assert "Ду100" in retrieval_query
    assert "Ру2,5МПа" in retrieval_query
    assert "Приварное" in retrieval_query
    assert "MARSHAL" not in retrieval_query.upper()
    trace = result.query_interpretation["retrieval_trace"]
    assert trace["strategy"] == "competitor_decoded_technical_plus_catalog"
    assert result.query_interpretation["source_decode"]["manufacturer"] == "MARSHAL"
