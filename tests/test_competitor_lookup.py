import json
from types import SimpleNamespace

import duckdb

from nomenclature_matcher.competitor_lookup import LocalCompetitorLookup
from nomenclature_matcher.matcher import NomenclatureMatcher
from nomenclature_matcher.models import SearchCandidate
from nomenclature_matcher.query_interpreter import DeepSeekQueryInterpreter, QueryInterpretation


COLUMNS = [
    "family",
    "product_id",
    "name",
    "article",
    "vendor_article",
    "manufacturer_code",
    "brand",
    "model",
    "series",
    "dn_text",
    "pn_text",
    "joining_type",
    "thread_type",
    "connection_size",
    "body_material",
    "seal_material",
    "control",
    "working_medium",
    "url",
    "properties_json",
]


def _settings(path=None):
    return SimpleNamespace(
        competitor_catalog_path=str(path) if path else "missing.parquet",
        competitor_lookup_limit=3,
        competitor_lookup_prelimit=100,
        competitor_lookup_min_confidence=0.84,
        competitor_lookup_min_score_margin=8.0,
        competitor_lookup_enabled=False,
        deepseek_api_key="x",
        deepseek_base_url="https://api.deepseek.com/v1",
        deepseek_model="deepseek-v4-flash",
        deepseek_timeout_seconds=20,
        query_interpreter_enabled=False,
        hybrid_rerank_limit=20,
    )


def _catalog(tmp_path):
    path = tmp_path / "competitors.parquet"
    con = duckdb.connect(database=":memory:")
    con.execute(
        "CREATE TABLE products ("
        + ", ".join(f'"{column}" VARCHAR' for column in COLUMNS)
        + ")"
    )
    rows = [
        (
            "butterfly_valve",
            "1",
            "Затвор дисковый РИДАН ЗДМ 05.16.100 Ду100 PN16 082X4424R",
            "082X4424R",
            "082X4424R",
            "082X4424R",
            "РИДАН",
            "ЗДМ 05.16.100",
            "ЗДМ",
            "100",
            "16",
            "межфланцевое",
            None,
            None,
            "чугун",
            "EPDM",
            "рукоятка",
            "вода",
            "https://example/ridan",
            json.dumps(
                {
                    "Бренд": "РИДАН",
                    "Модель": "ЗДМ 05.16.100",
                    "Артикул": "082X4424R",
                    "Условный проход, мм": "100",
                    "Макс. рабочее давление, бар": "16",
                    "Тип соединения": "межфланцевое",
                    "Материал корпуса": "чугун",
                },
                ensure_ascii=False,
            ),
        ),
        (
            "ball_valve",
            "2",
            'Кран шаровой VALTEC VT.214.N.04 1/2" ВР/ВР',
            "VT.214.N.04",
            "VT.214.N.04",
            "VT.214.N.04",
            "VALTEC",
            "VT.214.N.04",
            "VT.214",
            "15",
            "40",
            "резьбовое",
            "ВР/ВР",
            '1/2"',
            "латунь",
            "PTFE",
            "рукоятка",
            "вода",
            "https://example/vt15",
            json.dumps({"Тип шарового крана": "полнопроходной"}, ensure_ascii=False),
        ),
        (
            "ball_valve",
            "3",
            'Кран шаровой VALTEC VT.214.N.06 1" ВР/ВР',
            "VT.214.N.06",
            "VT.214.N.06",
            "VT.214.N.06",
            "VALTEC",
            "VT.214.N.06",
            "VT.214",
            "25",
            "40",
            "резьбовое",
            "ВР/ВР",
            '1"',
            "латунь",
            "PTFE",
            "рукоятка",
            "вода",
            "https://example/vt25",
            json.dumps({"Тип шарового крана": "полнопроходной"}, ensure_ascii=False),
        ),
        (
            "ball_valve",
            "4",
            'Кран шаровой IVR 60 3/4" ВР/НР с американкой',
            "IVR60-20",
            "IVR60-20",
            "IVR60-20",
            "IVR",
            "60",
            "60",
            "20",
            "40",
            "резьбовое",
            "ВР/НР",
            '3/4"',
            "латунь",
            "PTFE",
            "рукоятка",
            "вода",
            "https://example/ivr60-20",
            "{}",
        ),
        (
            "ball_valve",
            "5",
            'Кран шаровой IVR 60 1" ВР/НР с американкой',
            "IVR60-25",
            "IVR60-25",
            "IVR60-25",
            "IVR",
            "60",
            "60",
            "25",
            "40",
            "резьбовое",
            "ВР/НР",
            '1"',
            "латунь",
            "PTFE",
            "рукоятка",
            "вода",
            "https://example/ivr60-25",
            "{}",
        ),
        (
            "ball_valve",
            "6",
            'Кран шаровой IVR 956 3/4" ВР/НР',
            "IVR956-20",
            "IVR956-20",
            "IVR956-20",
            "IVR",
            "956",
            "956",
            "20",
            "40",
            "резьбовое",
            "ВР/НР",
            '3/4"',
            "латунь",
            "PTFE",
            "рукоятка",
            "вода",
            "https://example/ivr956",
            "{}",
        ),
    ]
    placeholders = ",".join("?" for _ in COLUMNS)
    con.executemany(f"INSERT INTO products VALUES ({placeholders})", rows)
    con.execute(f"COPY products TO '{path.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    return path


def test_exact_article_lookup_is_accepted(tmp_path):
    lookup = LocalCompetitorLookup(_settings(_catalog(tmp_path)))

    result = lookup.lookup(
        "Затвор дисковый поворотный РИДАН ЗДМ 05.16.100 Ду 100 (082X4424R)"
    )

    assert result.accepted is True
    assert result.candidates[0].product_id == "1"
    assert result.candidates[0].confidence == 0.99
    assert any(item.startswith("exact_identifier:") for item in result.candidates[0].match_basis)


def test_model_family_plus_inch_disambiguates_variant(tmp_path):
    lookup = LocalCompetitorLookup(_settings(_catalog(tmp_path)))

    result = lookup.lookup('Кран шаровый VALTEC VT.214 1"')

    assert result.accepted is True
    assert result.candidates[0].product_id == "3"
    assert result.candidates[0].dn_text == "25"
    assert "explicit_dn" in result.candidates[0].match_basis


def test_brand_numeric_model_plus_size_disambiguates_ivr(tmp_path):
    lookup = LocalCompetitorLookup(_settings(_catalog(tmp_path)))

    result = lookup.lookup('Кран IVR 60 1" ВР/НР с американкой')

    assert result.accepted is True
    assert result.candidates[0].product_id == "5"
    assert result.identity_terms == ["ivr", "60"]


def test_broad_brand_only_query_does_not_trigger_lookup(tmp_path):
    lookup = LocalCompetitorLookup(_settings(_catalog(tmp_path)))

    result = lookup.lookup("Краны Danfoss")

    assert result.attempted is False
    assert result.accepted is False
    assert result.candidates == []


class _FakeCompletions:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class _FakeClient:
    def __init__(self, content):
        self.completions = _FakeCompletions(content)
        self.chat = SimpleNamespace(completions=self.completions)


def _interpretation_payload():
    return {
        "searchable": True,
        "normalized_query": "кран шаровой DN25 резьбовой ВР/ВР",
        "reason": "identified",
        "constraints": {
            "product_type": "ball_valve",
            "dn": 25,
            "pn_min_mpa": 4.0,
            "joining_type": "threaded",
            "thread_type": "female_female",
            "working_medium": "вода",
            "valve_type": "standard",
            "valve_designation": None,
            "body_material": "brass",
            "body_material_grade": None,
            "bore_type": "full",
            "control": None,
            "catalog_scope": "in_scope",
            "ambiguous": False,
            "comment": "from local catalog",
        },
    }


def test_interpreter_receives_local_catalog_context():
    client = _FakeClient(json.dumps(_interpretation_payload(), ensure_ascii=False))
    interpreter = DeepSeekQueryInterpreter(_settings(), client=client)
    context = {
        "source": "local_santech_competitor_catalog",
        "identity_confidence": 0.95,
        "candidate": {"model": "VT.214.N.06", "dn_text": "25", "pn_text": "40"},
    }

    result = interpreter.interpret("Кран VALTEC VT.214 1 дюйм", competitor_context=context)

    assert result.constraints.dn == 25
    user_message = client.completions.calls[0]["messages"][1]["content"]
    assert "COMPETITOR_CONTEXT:" in user_message
    assert "LOCAL_COMPETITOR_CONTEXT:" not in user_message
    assert "VT.214.N.06" in user_message


def test_matcher_passes_accepted_lookup_context_and_keeps_debug():
    class LookupResult:
        def prompt_context(self):
            return {"source": "local", "identity_confidence": 0.95, "candidate": {"dn_text": "25"}}

        def debug_payload(self):
            return {"attempted": True, "accepted": True, "reason": "test", "candidates": []}

    class Lookup:
        def lookup(self, query):
            return LookupResult()

    class Interpreter:
        def __init__(self):
            self.contexts = []

        def interpret(self, query, competitor_context=None):
            self.contexts.append(competitor_context)
            return QueryInterpretation.model_validate(_interpretation_payload())

    class Hybrid:
        def search(self, query, limit, canonical_query=None):
            return [
                SearchCandidate(
                    ld_id=1,
                    name="Кран шаровой LD DN25 PN40 ВР/ВР",
                    article="LD1",
                    score=0.1,
                    dn="25",
                    pn="4.0",
                    joining_type="Резьбовое",
                    properties=[
                        {"name": "Тип продукта", "values": ["Кран шаровой"]},
                        {"name": "Тип резьбы", "values": ["ВР/ВР"]},
                        {"name": "Материал корпуса", "values": ["Латунь"]},
                        {"name": "Тип прохода", "values": ["Полный проход"]},
                    ],
                )
            ]

    class Reranker:
        def rerank(self, query, candidates, constraints=None):
            return SimpleNamespace(status="NOT_FOUND", selected=[], reason="test")

    interpreter = Interpreter()
    matcher = NomenclatureMatcher(
        None,
        None,
        _settings(),
        reranker=Reranker(),
        hybrid_retriever=Hybrid(),
        query_interpreter=interpreter,
        competitor_lookup=Lookup(),
    )

    result = matcher.match_one_hybrid_with_rerank('Кран VALTEC VT.214 1"')

    assert interpreter.contexts[0] is None
    assert interpreter.contexts[1]["identity_confidence"] == 0.95
    assert result.query_interpretation["competitor_lookup"]["accepted"] is True
