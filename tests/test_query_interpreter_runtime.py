import json
from types import SimpleNamespace

from nomenclature_matcher.matcher import NomenclatureMatcher
from nomenclature_matcher.models import SearchCandidate
from nomenclature_matcher.query_interpreter import DeepSeekQueryInterpreter
from nomenclature_matcher.reranker import DeepSeekReranker


class FakeCompletions:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class FakeClient:
    def __init__(self, content):
        self.completions = FakeCompletions(content)
        self.chat = SimpleNamespace(completions=self.completions)


def settings():
    return SimpleNamespace(
        deepseek_api_key="x",
        deepseek_base_url="https://api.deepseek.com/v1",
        deepseek_model="deepseek-v4-flash",
        deepseek_timeout_seconds=20,
        rerank_result_limit=3,
        hybrid_rerank_limit=20,
        query_interpreter_enabled=False,
    )


def interpretation_payload(*, searchable=True, normalized_query="Кран шаровой DN25 резьбовой ВР/НР"):
    return {
        "searchable": searchable,
        "normalized_query": normalized_query,
        "reason": "specific" if searchable else "too broad",
        "constraints": {
            "product_type": "ball_valve",
            "dn": 25 if searchable else None,
            "pn_min_mpa": None,
            "joining_type": "threaded" if searchable else None,
            "thread_type": "male_female" if searchable else None,
            "working_medium": None,
            "valve_type": "standard",
            "valve_designation": None,
            "body_material": None,
            "body_material_grade": None,
            "bore_type": None,
            "control": None,
            "catalog_scope": "in_scope",
            "ambiguous": not searchable,
            "comment": "",
        },
    }


def compact_native_designation_payload():
    payload = interpretation_payload(
        normalized_query="\u041a\u0440\u0430\u043d \u0448\u0430\u0440\u043e\u0432\u043e\u0439 \u041a\u0428\u0426\u0424 050.016.02 DN50 PN16"
    )
    payload["constraints"].update(
        {
            "dn": 50,
            "pn_min_mpa": 1.6,
            "joining_type": "flanged",
            "thread_type": None,
            "valve_designation": "\u041a\u0428\u0426\u0424 050.016.02",
            "body_material": "steel",
            "body_material_grade": "20",
            "bore_type": "full",
            "control": "electric_ready",
            "comment": "model payload",
        }
    )
    return payload


def threaded_dn25_candidate(ld_id=1):
    return SearchCandidate(
        ld_id=ld_id,
        name="Кран шаровой LD DN25 ВР/НР",
        article=f"A{ld_id}",
        score=0.02,
        dn="25",
        joining_type="Резьбовое",
        properties=[
            {"name": "Тип продукта", "values": ["Кран шаровой"]},
            {"name": "Тип резьбы", "values": ["ВР/НР"]},
        ],
    )


def test_interpreter_parses_gate_normalization_and_constraints():
    client = FakeClient(json.dumps(interpretation_payload(), ensure_ascii=False))
    interpreter = DeepSeekQueryInterpreter(settings(), client=client)

    result = interpreter.interpret('Кран VT.218 вн/нар 1"')

    assert result.searchable is True
    assert result.normalized_query == "Кран шаровой DN25 резьбовой ВР/НР"
    assert result.constraints.dn == 25
    assert result.constraints.thread_type == "male_female"
    assert result.constraints.valve_designation is None


def test_interpreter_rewrites_only_matching_native_composite_designation():
    query = (
        "\u041a\u0440\u0430\u043d \u0448\u0430\u0440\u043e\u0432\u043e\u0439 LD \u041a\u0428\u0426\u0424 050.016.02 "
        "DN50 PN16 \u0444\u043b\u0430\u043d\u0446\u0435\u0432\u044b\u0439 \u0441\u0442\u0430\u043b\u044c 20 "
        "\u043f\u043e\u043b\u043d\u043e\u043f\u0440\u043e\u0445\u043e\u0434\u043d\u043e\u0439 \u043f\u043e\u0434 "
        "\u044d\u043b\u0435\u043a\u0442\u0440\u043e\u043f\u0440\u0438\u0432\u043e\u0434"
    )
    client = FakeClient(json.dumps(compact_native_designation_payload(), ensure_ascii=False))
    interpreter = DeepSeekQueryInterpreter(settings(), client=client)

    result = interpreter.interpret(query)

    assert result.constraints.valve_designation == "\u041a\u0428\u0426\u0424"
    assert result.constraints.dn == 50
    assert result.constraints.pn_min_mpa == 1.6
    assert result.constraints.joining_type == "flanged"
    assert result.constraints.body_material == "steel"
    assert result.constraints.body_material_grade == "20"
    assert result.constraints.bore_type == "full"
    assert result.constraints.control == "electric_ready"
    assert "designation_family_provenance:compact_native_config" in result.constraints.comment


def test_interpreter_supports_punctuation_and_case_for_native_composite():
    payload = compact_native_designation_payload()
    payload["constraints"]["valve_designation"] = "\u043a\u0448\u0446\u0444.050.016-02"
    client = FakeClient(json.dumps(payload, ensure_ascii=False))
    interpreter = DeepSeekQueryInterpreter(settings(), client=client)

    result = interpreter.interpret(
        "\u041a\u0428\u0426\u0424.050.016-02 DN50 PN16 \u0444\u043b\u0430\u043d\u0435\u0446"
    )

    assert result.constraints.valve_designation == "\u041a\u0428\u0426\u0424"
    assert "designation_family_provenance:compact_native_config" in result.constraints.comment


def test_interpreter_does_not_rewrite_unsupported_designation_forms():
    cases = [
        (
            "competitor",
            "\u0413\u0440\u0430\u043d\u0432\u044d\u043b 050.016.02 DN50",
            "\u0413\u0440\u0430\u043d\u0432\u044d\u043b 050.016.02",
        ),
        (
            "unsupported native-like family",
            "\u041a\u0428\u0424 050.016.02 DN50",
            "\u041a\u0428\u0424 050.016.02",
        ),
        ("standalone family", "\u041a\u0428\u0426\u0424 DN50", "\u041a\u0428\u0426\u0424"),
        ("malformed suffix", "\u041a\u0428\u0426\u0424 050 DN50", "\u041a\u0428\u0426\u0424 050"),
        (
            "designation mismatch",
            "\u041a\u0428\u0426\u0424 050.016.02 DN50",
            "\u041a\u0428\u0426\u0424 080.016.02",
        ),
    ]

    for label, query, designation in cases:
        payload = compact_native_designation_payload()
        payload["constraints"]["valve_designation"] = designation
        payload["constraints"]["comment"] = label
        client = FakeClient(json.dumps(payload, ensure_ascii=False))
        interpreter = DeepSeekQueryInterpreter(settings(), client=client)

        result = interpreter.interpret(query)

        assert result.constraints.valve_designation == designation
        assert "designation_family_provenance:compact_native_config" not in result.constraints.comment


def test_matcher_skips_retrieval_for_broad_query():
    class Interpreter:
        def interpret(self, query):
            from nomenclature_matcher.query_interpreter import QueryInterpretation

            return QueryInterpretation.model_validate(
                interpretation_payload(searchable=False, normalized_query="Краны шаровые")
            )

    class Hybrid:
        def __init__(self):
            self.calls = []

        def search(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return []

    hybrid = Hybrid()
    matcher = NomenclatureMatcher(
        None,
        None,
        settings(),
        reranker=None,
        hybrid_retriever=hybrid,
        query_interpreter=Interpreter(),
    )

    result = matcher.match_one_hybrid_with_rerank("Краны шаровые")

    assert result.status == "NOT_FOUND"
    assert result.reason.startswith("QUERY_REJECTED:")
    assert result.query_interpretation["searchable"] is False
    assert hybrid.calls == []


def test_matcher_uses_llm_normalized_query_and_passes_constraints_to_reranker():
    class Interpreter:
        def interpret(self, query):
            from nomenclature_matcher.query_interpreter import QueryInterpretation

            return QueryInterpretation.model_validate(interpretation_payload())

    class Hybrid:
        def __init__(self):
            self.calls = []

        def search(self, query, limit, canonical_query=None):
            self.calls.append((query, limit, canonical_query))
            return [threaded_dn25_candidate()]

    class Reranker:
        def __init__(self):
            self.calls = []

        def rerank(self, query, candidates, constraints=None):
            self.calls.append((query, candidates, constraints))
            return SimpleNamespace(
                status="NOT_FOUND",
                selected=[],
                reason="no exact match",
            )

    hybrid = Hybrid()
    reranker = Reranker()
    matcher = NomenclatureMatcher(
        None,
        None,
        settings(),
        reranker=reranker,
        hybrid_retriever=hybrid,
        query_interpreter=Interpreter(),
    )

    result = matcher.match_one_hybrid_with_rerank('Кран VT.218 вн/нар 1"')

    assert result.status == "NOT_FOUND"
    assert hybrid.calls == [
        ('Кран VT.218 вн/нар 1"', 20, "Кран шаровой DN25 резьбовой ВР/НР")
    ]
    assert len(reranker.calls[0][1]) == 1
    assert reranker.calls[0][2]["dn"] == 25
    assert reranker.calls[0][2]["thread_type"] == "male_female"
    assert reranker.calls[0][2]["valve_designation"] is None


def test_matcher_passes_corrected_family_gate_without_rewriting_queries():
    query = "\u041a\u0440\u0430\u043d \u0448\u0430\u0440\u043e\u0432\u043e\u0439 LD \u041a\u0428\u0426\u0424 050.016.02 DN50 PN16"
    payload = compact_native_designation_payload()
    payload["normalized_query"] = "\u041a\u0428\u0426\u0424 050.016.02 DN50 PN16"
    payload["constraints"]["body_material"] = None
    payload["constraints"]["body_material_grade"] = None
    payload["constraints"]["control"] = None
    interpreter = DeepSeekQueryInterpreter(
        settings(),
        client=FakeClient(json.dumps(payload, ensure_ascii=False)),
    )

    class Hybrid:
        def __init__(self):
            self.calls = []

        def search(self, query, limit, canonical_query=None):
            self.calls.append((query, limit, canonical_query))
            return [
                SearchCandidate(
                    ld_id=7,
                    name="\u041a\u0440\u0430\u043d \u0448\u0430\u0440\u043e\u0432\u043e\u0439 LD \u041a\u0428\u0426\u0424 DN50 PN16",
                    article="LD-KSHCF-50",
                    score=0.02,
                    dn="50",
                    pn="1,6",
                    joining_type="\u0424\u043b\u0430\u043d\u0446\u0435\u0432\u043e\u0435",
                    properties=[
                        {
                            "name": "\u0422\u0438\u043f \u043f\u0440\u043e\u0434\u0443\u043a\u0442\u0430",
                            "values": ["\u041a\u0440\u0430\u043d \u0448\u0430\u0440\u043e\u0432\u043e\u0439"],
                        },
                        {
                            "name": "\u0422\u0438\u043f \u043f\u0440\u043e\u0445\u043e\u0434\u0430",
                            "values": ["\u041f\u043e\u043b\u043d\u044b\u0439 \u043f\u0440\u043e\u0445\u043e\u0434"],
                        },
                    ],
                )
            ]

    class Reranker:
        def __init__(self):
            self.calls = []

        def rerank(self, query, candidates, constraints=None):
            self.calls.append((query, candidates, constraints))
            return SimpleNamespace(status="NOT_FOUND", selected=[], reason="checked")

    hybrid = Hybrid()
    reranker = Reranker()
    matcher = NomenclatureMatcher(
        None,
        None,
        settings(),
        reranker=reranker,
        hybrid_retriever=hybrid,
        query_interpreter=interpreter,
    )

    result = matcher.match_one_hybrid_with_rerank(query)

    assert hybrid.calls == [(query, 20, "\u041a\u0428\u0426\u0424 050.016.02 DN50 PN16")]
    assert reranker.calls[0][0] == query
    assert reranker.calls[0][2]["valve_designation"] == "\u041a\u0428\u0426\u0424"
    assert result.query_interpretation["constraints"]["valve_designation"] == "\u041a\u0428\u0426\u0424"
    assert result.query_interpretation["hard_constraints"]["valve_designation"] == "\u041a\u0428\u0426\u0424"


def test_hard_constraint_filter_removes_full_bore_and_preserves_original_candidate_id():
    full = SearchCandidate(
        ld_id=10,
        name="Кран шаровой LD Ду50 Ру1,6МПа полнопроходной",
        article="FULL",
        score=0.03,
        dn="50",
        pn="1,6",
        joining_type="Фланцевое",
        properties=[
            {"name": "Тип продукта", "values": ["Кран шаровой"]},
            {"name": "Тип прохода", "values": ["Полный проход"]},
        ],
    )
    reduced = SearchCandidate(
        ld_id=20,
        name="Кран шаровой LD Ду50 Ру1,6МПа стандартнопроходной с комплектом ответных фланцев",
        article="REDUCED",
        score=0.02,
        dn="50",
        pn="1,6",
        joining_type="Фланцевое",
        properties=[
            {"name": "Тип продукта", "values": ["Кран шаровой"]},
            {"name": "Тип прохода", "values": ["Неполный проход"]},
        ],
    )

    class Reranker:
        def __init__(self):
            self.calls = []

        def rerank(self, query, candidates, constraints=None):
            self.calls.append((query, candidates, constraints))
            return SimpleNamespace(
                status="MATCHED",
                selected=[SimpleNamespace(candidate_id=1, confidence=0.9, reason="compatible")],
                reason="one compatible candidate",
            )

    reranker = Reranker()
    matcher = NomenclatureMatcher(None, None, settings(), reranker=reranker)
    constraints = {
        "product_type": "ball_valve",
        "dn": 50,
        "pn_min_mpa": 1.6,
        "joining_type": "flanged",
        "thread_type": None,
        "working_medium": None,
        "valve_type": "standard",
        "valve_designation": None,
        "body_material": None,
        "body_material_grade": None,
        "bore_type": "reduced",
        "control": None,
        "catalog_scope": "in_scope",
        "ambiguous": False,
        "comment": "",
    }

    result = matcher.rerank_candidates("query", [full, reduced], constraints=constraints)

    assert [candidate.ld_id for candidate in reranker.calls[0][1]] == [20]
    assert result.status == "MATCHED"
    assert result.ld_product.ld_id == 20
    assert result.selected[0].candidate_id == 2
    assert len(result.candidates) == 2


def test_hard_constraint_filter_returns_not_found_before_reranker_when_no_candidate_survives():
    class Reranker:
        def __init__(self):
            self.calls = []

        def rerank(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            raise AssertionError("must not be called")

    reranker = Reranker()
    matcher = NomenclatureMatcher(None, None, settings(), reranker=reranker)
    constraints = interpretation_payload()["constraints"]
    wrong = SearchCandidate(
        ld_id=1,
        name="Кран шаровой LD DN100 фланцевый",
        article="WRONG",
        score=0.02,
        dn="100",
        joining_type="Фланцевое",
    )

    result = matcher.rerank_candidates("query", [wrong], constraints=constraints)

    assert result.status == "NOT_FOUND"
    assert result.reason.startswith("HARD_CONSTRAINT_FILTER:")
    assert reranker.calls == []


def test_reranker_prompt_contains_structured_bore_constraint():
    reranker = DeepSeekReranker(
        settings(),
        client=FakeClient(json.dumps({"status": "NOT_FOUND", "selected": []})),
    )
    prompt = reranker._build_prompt(
        "Кран шаровой стандартнопроходной Ду50",
        [
            SearchCandidate(
                ld_id=1,
                name="Кран полнопроходной",
                article="A1",
                score=0.02,
                search_text="Тип прохода: Полный проход",
            )
        ],
        {"product_type": "ball_valve", "dn": 50, "bore_type": "reduced"},
    )

    assert "QUERY_CONSTRAINTS:" in prompt
    assert '"bore_type": "reduced"' in prompt
    assert "Тип прохода: Полный проход" in prompt
