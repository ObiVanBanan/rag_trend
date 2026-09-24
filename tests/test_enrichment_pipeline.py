import json
from types import SimpleNamespace

from nomenclature_matcher.matcher import NomenclatureMatcher
from nomenclature_matcher.models import SearchCandidate
from nomenclature_matcher.query_interpreter import DeepSeekQueryInterpreter, QueryInterpretation
from nomenclature_matcher.query_signals import explicit_dn_from_query, has_product_identity


def settings():
    return SimpleNamespace(
        hybrid_rerank_limit=20,
        query_interpreter_enabled=False,
        deepseek_api_key="x",
        deepseek_base_url="https://api.deepseek.com/v1",
        deepseek_model="deepseek-v4-flash",
        deepseek_timeout_seconds=20,
    )


def interpretation(*, searchable=True, scope="in_scope", product_type="ball_valve", dn=25, joining="threaded", thread="male_female", material=None, bore=None, control=None, normalized="normalized"):
    return QueryInterpretation.model_validate(
        {
            "searchable": searchable,
            "normalized_query": normalized,
            "reason": "ok" if searchable else "reject",
            "constraints": {
                "product_type": product_type,
                "dn": dn,
                "pn_min_mpa": None,
                "joining_type": joining,
                "thread_type": thread,
                "working_medium": None,
                "valve_type": "standard" if product_type == "ball_valve" else None,
                "valve_designation": None,
                "body_material": material,
                "body_material_grade": None,
                "bore_type": bore,
                "control": control,
                "catalog_scope": scope,
                "ambiguous": False,
                "comment": "",
            },
        }
    )


class FakeCompletions:
    def __init__(self, payload):
        self.payload = payload

    def create(self, **kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False)))]
        )


class FakeClient:
    def __init__(self, payload):
        self.chat = SimpleNamespace(completions=FakeCompletions(payload))


def test_shared_identity_supports_long_numeric_article_and_explicit_inch_dn():
    assert has_product_identity("Кран шаровый Ду 150 ANSI 1500 № 2378929") is True
    assert explicit_dn_from_query('Кран шаровый со сгоном 1" ВР/НР IVR 60') == 25


def test_interpreter_overrides_wrong_llm_inch_dn_and_rescues_numeric_article():
    wrong_dn = interpretation(dn=15).model_dump()
    result = DeepSeekQueryInterpreter(settings(), client=FakeClient(wrong_dn)).interpret(
        'Кран шаровый со сгоном 1" ВР/НР IVR 60'
    )
    assert result.constraints.dn == 25

    numeric_article = interpretation(searchable=False, dn=150, joining=None, thread=None).model_dump()
    numeric_article["constraints"]["catalog_scope"] = "in_scope"
    result = DeepSeekQueryInterpreter(settings(), client=FakeClient(numeric_article)).interpret(
        "Кран шаровый Ду 150 ANSI 1500 № 2378929"
    )
    assert result.searchable is True


def test_out_of_scope_first_pass_never_calls_enrichment():
    class Interpreter:
        def interpret(self, query, competitor_context=None):
            return interpretation(searchable=False, scope="out_of_scope", product_type="other", dn=None, joining=None, thread=None)

    class Lookup:
        def lookup(self, query):
            raise AssertionError("web lookup must not run")

    class Hybrid:
        def search(self, *args, **kwargs):
            raise AssertionError("retrieval must not run")

    matcher = NomenclatureMatcher(None, None, settings(), hybrid_retriever=Hybrid(), query_interpreter=Interpreter(), competitor_lookup=Lookup())
    result = matcher.match_one_hybrid_with_rerank("Коробка КС-30 model123")

    assert result.status == "NOT_FOUND"
    assert result.query_interpretation["competitor_lookup"]["reason"] == "pre_enrichment_out_of_scope"


def test_web_constraints_stay_soft_while_enriched_query_drives_retrieval():
    pre = interpretation(normalized="Кран шаровой DN40 резьбовой ВР/ВР", dn=40, thread="female_female")
    enriched = interpretation(
        normalized="Кран шаровой DN40 резьбовой ВР/ВР латунный полнопроходной",
        dn=40,
        thread="female_female",
        material="brass",
        bore="full",
    )

    class Interpreter:
        def __init__(self):
            self.calls = []

        def interpret(self, query, competitor_context=None):
            self.calls.append(competitor_context)
            return pre if competitor_context is None else enriched

    class LookupResult:
        def prompt_context(self):
            return {"source": "web", "candidate": {"material": "brass", "bore": "full"}}

        def debug_payload(self):
            return {"attempted": True, "accepted": True, "reason": "web_evidence_found"}

    class Lookup:
        def __init__(self):
            self.calls = 0

        def lookup(self, query):
            self.calls += 1
            return LookupResult()

    class Hybrid:
        def __init__(self):
            self.calls = []

        def search(self, query, limit, canonical_query=None):
            self.calls.append((query, canonical_query))
            return [
                SearchCandidate(
                    ld_id=1,
                    name="Кран шаровой LD DN40 ВР/ВР",
                    article="A1",
                    score=0.02,
                    dn="40",
                    joining_type="Резьбовое",
                    properties=[
                        {"name": "Тип продукта", "values": ["Кран шаровой"]},
                        {"name": "Тип резьбы", "values": ["ВР/ВР"]},
                    ],
                )
            ]

    class Reranker:
        def __init__(self):
            self.constraints = None

        def rerank(self, query, candidates, constraints=None):
            self.constraints = constraints
            return SimpleNamespace(status="NOT_FOUND", selected=[], reason="no exact match")

    lookup = Lookup()
    hybrid = Hybrid()
    reranker = Reranker()
    matcher = NomenclatureMatcher(None, None, settings(), reranker=reranker, hybrid_retriever=hybrid, query_interpreter=Interpreter(), competitor_lookup=lookup)

    result = matcher.match_one_hybrid_with_rerank('Кран VT.214 вн/вн 1 1/2"')

    assert result.status == "NOT_FOUND"
    assert lookup.calls == 1
    assert hybrid.calls[0][1] == enriched.normalized_query
    assert reranker.constraints["dn"] == 40
    assert reranker.constraints["body_material"] is None
    assert reranker.constraints["bore_type"] is None
    assert result.query_interpretation["constraints"]["body_material"] == "brass"
    assert result.query_interpretation["hard_constraints"]["body_material"] is None


def test_rich_explicit_query_skips_web_lookup():
    class Interpreter:
        def interpret(self, query, competitor_context=None):
            return interpretation(
                dn=25,
                joining="flanged",
                thread=None,
                material="stainless_steel",
                control="electric",
                normalized="Кран шаровой DN25 PN40 фланцевый нержавеющий электропривод",
            )

    class Lookup:
        def lookup(self, query):
            raise AssertionError("rich explicit query should skip web")

    class Hybrid:
        def search(self, query, limit, canonical_query=None):
            return []

    matcher = NomenclatureMatcher(None, None, settings(), hybrid_retriever=Hybrid(), query_interpreter=Interpreter(), competitor_lookup=Lookup())
    result = matcher.match_one_hybrid_with_rerank("Кран шаровый из нерж. ст. BV17 DN25 PN40 ф/ф с электроприводом")

    assert result.status == "NOT_FOUND"
    assert result.query_interpretation["competitor_lookup"]["reason"] == "pre_enrichment_enough_explicit_detail"



def test_model_suffix_dn_never_becomes_hard_constraint():
    pre = interpretation(dn=15, joining=None, thread=None)
    enriched = interpretation(
        dn=20,
        joining="threaded",
        thread="female_female",
        material="brass",
        bore="full",
    )

    hard = NomenclatureMatcher._hard_constraints(
        "Кран шаровый VT.217.N.05",
        pre,
        enriched,
    )

    assert hard["product_type"] == "ball_valve"
    assert hard["dn"] is None
    assert hard["pn_min_mpa"] is None
    assert hard["joining_type"] is None
    assert hard["thread_type"] is None
    assert hard["body_material"] is None
    assert hard["bore_type"] is None
    assert hard["valve_type"] is None


def test_explicit_query_dn_pn_and_connection_remain_hard():
    pre = interpretation(dn=20, joining="threaded", thread="female_female")

    hard = NomenclatureMatcher._hard_constraints(
        'Кран шаровый VT.217.N.05 Ду20 Ру20 резьбовой ВР/ВР 3/4"',
        pre,
    )

    assert hard["dn"] == 20
    assert hard["pn_min_mpa"] == 2.0
    assert hard["joining_type"] == "threaded"
    assert hard["thread_type"] == "female_female"
