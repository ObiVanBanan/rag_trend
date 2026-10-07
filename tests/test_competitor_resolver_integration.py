import json
from types import SimpleNamespace

from nomenclature_matcher.competitor_resolver import CompetitorResolver
from nomenclature_matcher.matcher import NomenclatureMatcher
from nomenclature_matcher.query_interpreter import DeepSeekQueryInterpreter


def settings(tmp_path):
    return SimpleNamespace(
        competitor_resolver_enabled=True,
        competitor_kb_path=str(tmp_path / "competitor.sqlite3"),
        competitor_catalog_registry_path="data/competitor_catalogs/sources.json",
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


def llm_payload():
    return {
        "searchable": False,
        "normalized_query": "wrong llm interpretation",
        "reason": "ambiguous",
        "constraints": {
            "product_type": "ball_valve",
            "dn": 15,
            "pn_min_mpa": 1.6,
            "joining_type": "flanged",
            "thread_type": "male_female",
            "working_medium": None,
            "valve_type": "standard",
            "valve_designation": None,
            "body_material": "brass",
            "body_material_grade": None,
            "bore_type": "full",
            "control": "electric",
            "catalog_scope": "uncertain",
            "ambiguous": True,
            "comment": "llm guess",
        },
    }


def test_resolver_profile_overrides_llm_and_clears_unproven_fields(tmp_path):
    cfg = settings(tmp_path)
    resolver = CompetitorResolver(cfg)
    resolution = resolver.resolve("Кран шаровой TEMPER 29420125")
    interpreter = DeepSeekQueryInterpreter(
        cfg,
        client=FakeClient(llm_payload()),
    )

    result = interpreter.interpret(
        "Кран шаровой TEMPER 29420125",
        competitor_context=resolution.prompt_context(),
    )

    assert result.searchable is True
    assert result.constraints.catalog_scope == "in_scope"
    assert result.constraints.ambiguous is False
    assert result.constraints.dn == 125
    assert result.constraints.pn_min_mpa == 2.5
    assert result.constraints.joining_type == "flanged"
    assert result.constraints.working_medium is None
    assert result.constraints.body_material == "steel"
    assert result.constraints.thread_type is None
    assert result.constraints.control is None
    assert "TEMPER" not in result.normalized_query.upper()
    assert "Ду125" in result.normalized_query


def test_matcher_uses_resolver_before_web_and_searches_by_technical_profile(tmp_path):
    cfg = settings(tmp_path)
    resolver = CompetitorResolver(cfg)
    interpreter = DeepSeekQueryInterpreter(
        cfg,
        client=FakeClient(llm_payload()),
    )

    class Lookup:
        def __init__(self):
            self.calls = 0

        def lookup(self, query):
            self.calls += 1
            raise AssertionError("web lookup must not run on resolver hit")

    class Hybrid:
        def __init__(self):
            self.calls = []

        def search(self, query, limit, canonical_query=None):
            self.calls.append((query, limit, canonical_query))
            return []

    lookup = Lookup()
    hybrid = Hybrid()
    matcher = NomenclatureMatcher(
        None,
        None,
        cfg,
        hybrid_retriever=hybrid,
        query_interpreter=interpreter,
        competitor_lookup=lookup,
        competitor_resolver=resolver,
    )

    result = matcher.match_one_hybrid_with_rerank(
        "Кран шаровой TEMPER 29420125"
    )

    assert result.status == "NOT_FOUND"
    assert lookup.calls == 0
    assert hybrid.calls
    retrieval_query, limit, _ = hybrid.calls[0]
    assert limit == 20
    assert "TEMPER" not in retrieval_query.upper()
    assert "Ду125" in retrieval_query
    assert result.query_interpretation["competitor_resolution"]["status"] in {
        "CATALOG_SCHEMA_RESOLVED",
        "KB_HIT",
    }
    assert (
        result.query_interpretation["retrieval_trace"]["strategy"]
        == "competitor_resolved_technical_plus_catalog"
    )




def test_matcher_uses_catalog_resolver_for_marshal_without_web(tmp_path):
    cfg = settings(tmp_path)
    resolver = CompetitorResolver(cfg)
    query = "Кран шаровой MARSHAL 11с67п GAS PRO 2ЦП.01.1.016.050/040"
    resolution = resolver.resolve(query)

    assert resolution.status == "CATALOG_DESIGNATION_RESOLVED"
    assert resolution.attributes()["dn"] == 50
    assert resolution.attributes()["joining_type"] == "welded"

    interpreter = DeepSeekQueryInterpreter(
        cfg,
        client=FakeClient(llm_payload()),
    )

    class Lookup:
        def lookup(self, query):
            raise AssertionError("runtime web must not run on MARSHAL catalog hit")

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
        cfg,
        hybrid_retriever=hybrid,
        query_interpreter=interpreter,
        competitor_lookup=Lookup(),
        competitor_resolver=resolver,
    )

    result = matcher.match_one_hybrid_with_rerank(query)

    assert result.status == "NOT_FOUND"
    assert hybrid.calls
    retrieval_query, _, _ = hybrid.calls[0]
    assert "MARSHAL" not in retrieval_query.upper()
    assert "Ду50" in retrieval_query
    assert result.query_interpretation["competitor_resolution"]["status"] in {
        "CATALOG_DESIGNATION_RESOLVED",
        "KB_HIT",
    }
    assert "source_decode" not in result.query_interpretation
    assert (
        result.query_interpretation["retrieval_trace"]["strategy"]
        == "competitor_resolved_technical_plus_catalog"
    )


def test_matcher_uses_catalog_resolver_for_also_without_web(tmp_path):
    cfg = settings(tmp_path)
    resolver = CompetitorResolver(cfg)
    query = "Кран шаровой ALSO КШ.Ф.080.16-01"
    resolution = resolver.resolve(query)

    assert resolution.status == "CATALOG_DESIGNATION_RESOLVED"
    assert resolution.attributes()["dn"] == 80
    assert resolution.attributes()["joining_type"] == "flanged"

    interpreter = DeepSeekQueryInterpreter(
        cfg,
        client=FakeClient(llm_payload()),
    )

    class Lookup:
        def lookup(self, query):
            raise AssertionError("runtime web must not run on ALSO catalog hit")

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
        cfg,
        hybrid_retriever=hybrid,
        query_interpreter=interpreter,
        competitor_lookup=Lookup(),
        competitor_resolver=resolver,
    )

    result = matcher.match_one_hybrid_with_rerank(query)

    assert result.status == "NOT_FOUND"
    retrieval_query, _, _ = hybrid.calls[0]
    assert "ALSO" not in retrieval_query.upper()
    assert "Ду80" in retrieval_query
    assert (
        result.query_interpretation["retrieval_trace"]["strategy"]
        == "competitor_resolved_technical_plus_catalog"
    )
