import json
from types import SimpleNamespace

from nomenclature_matcher.query_interpreter import DeepSeekQueryInterpreter


def settings():
    return SimpleNamespace(
        deepseek_api_key="x",
        deepseek_base_url="https://api.deepseek.com/v1",
        deepseek_model="deepseek-v4-flash",
        deepseek_timeout_seconds=20,
    )


def payload(*, product_type="ball_valve", searchable=True):
    return {
        "searchable": searchable,
        "normalized_query": "normalized",
        "reason": "test",
        "constraints": {
            "product_type": product_type,
            "dn": None,
            "pn_min_mpa": None,
            "joining_type": None,
            "thread_type": None,
            "working_medium": None,
            "valve_type": "standard",
            "valve_designation": None,
            "body_material": None,
            "body_material_grade": None,
            "bore_type": None,
            "control": "manual",
            "catalog_scope": "in_scope",
            "ambiguous": False,
            "comment": "",
        },
    }


class SequenceCompletions:
    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        index = min(len(self.calls) - 1, len(self.contents) - 1)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.contents[index]))]
        )


class SequenceClient:
    def __init__(self, contents):
        self.completions = SequenceCompletions(contents)
        self.chat = SimpleNamespace(completions=self.completions)


def test_null_product_type_does_not_fail_validation():
    data = payload(product_type=None, searchable=False)
    client = SequenceClient([json.dumps(data, ensure_ascii=False)])

    result = DeepSeekQueryInterpreter(settings(), client=client).interpret("Краны Danfoss")

    assert result.searchable is False
    assert result.constraints.product_type == "other"


def test_non_ball_valve_drops_inapplicable_valve_type_and_implicit_manual_control():
    data = payload(product_type="filter")
    data["constraints"]["dn"] = 50
    data["constraints"]["pn_min_mpa"] = 1.6
    client = SequenceClient([json.dumps(data, ensure_ascii=False)])

    result = DeepSeekQueryInterpreter(settings(), client=client).interpret("ФСФ dy 050 PN16")

    assert result.searchable is True
    assert result.constraints.valve_type is None
    assert result.constraints.control is None


def test_explicit_thread_orientation_overrides_llm_slip():
    data = payload()
    data["constraints"]["dn"] = 20
    data["constraints"]["joining_type"] = "threaded"
    data["constraints"]["thread_type"] = "male_male"
    client = SequenceClient([json.dumps(data, ensure_ascii=False)])

    result = DeepSeekQueryInterpreter(settings(), client=client).interpret(
        'Кран VT.245 с дренажем вн.-вн. 3/4"'
    )

    assert result.constraints.thread_type == "female_female"


def test_type_plus_dn_without_model_is_rejected_even_if_llm_marks_searchable():
    data = payload()
    data["constraints"]["dn"] = 50
    client = SequenceClient([json.dumps(data, ensure_ascii=False)])

    result = DeepSeekQueryInterpreter(settings(), client=client).interpret("Кран шаровыйДу50")

    assert result.searchable is False
    assert "Недостаточно различающих характеристик" in result.reason


def test_llm_overreject_is_rescued_when_in_scope_query_has_anchor_and_second_constraint():
    data = payload(searchable=False)
    data["constraints"]["dn"] = 50
    data["constraints"]["joining_type"] = "flanged"
    client = SequenceClient([json.dumps(data, ensure_ascii=False)])

    result = DeepSeekQueryInterpreter(settings(), client=client).interpret(
        "Кран шаровой фланцевый DN50"
    )

    assert result.searchable is True
    assert "deterministic eligibility gate" in result.reason


def test_broad_category_is_not_rescued_without_concrete_anchor():
    data = payload(searchable=False)
    data["constraints"]["working_medium"] = "газ"
    data["constraints"]["body_material"] = "steel"
    data["constraints"]["valve_type"] = "gas"
    client = SequenceClient([json.dumps(data, ensure_ascii=False)])

    result = DeepSeekQueryInterpreter(settings(), client=client).interpret(
        "Стальные газовые краны"
    )

    assert result.searchable is False


def test_out_of_scope_query_is_not_rescued_even_when_specific():
    data = payload(searchable=False)
    data["constraints"]["dn"] = 50
    data["constraints"]["joining_type"] = "flanged"
    data["constraints"]["catalog_scope"] = "out_of_scope"
    client = SequenceClient([json.dumps(data, ensure_ascii=False)])

    result = DeepSeekQueryInterpreter(settings(), client=client).interpret(
        "Реле потока DN50 фланцевое"
    )

    assert result.searchable is False


def test_exact_source_model_can_keep_sparse_query_searchable():
    data = payload()
    data["constraints"]["dn"] = 20
    client = SequenceClient([json.dumps(data, ensure_ascii=False)])

    result = DeepSeekQueryInterpreter(settings(), client=client).interpret('Кран VT.245 3/4"')

    assert result.searchable is True


def test_service_query_is_rejected_even_if_llm_marks_searchable():
    data = payload()
    data["constraints"]["dn"] = 80
    data["constraints"]["pn_min_mpa"] = 1.6
    client = SequenceClient([json.dumps(data, ensure_ascii=False)])

    result = DeepSeekQueryInterpreter(settings(), client=client).interpret(
        "Смена крана шарового диаметром 80 мм"
    )

    assert result.searchable is False
    assert "работу/услугу" in result.reason


def test_invalid_json_is_retried_once():
    data = payload()
    data["constraints"]["dn"] = 50
    data["constraints"]["pn_min_mpa"] = 1.6
    valid = json.dumps(data, ensure_ascii=False)
    client = SequenceClient(["{broken", valid])

    result = DeepSeekQueryInterpreter(settings(), client=client).interpret("Кран шаровой Ду50 Ру16")

    assert result.searchable is True
    assert len(client.completions.calls) == 2
    assert "Предыдущий ответ не прошёл" in client.completions.calls[1]["messages"][1]["content"]
