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
    client = SequenceClient([json.dumps(data, ensure_ascii=False)])

    result = DeepSeekQueryInterpreter(settings(), client=client).interpret("ФСФ dy 050 PN16")

    assert result.constraints.valve_type is None
    assert result.constraints.control is None


def test_explicit_thread_orientation_overrides_llm_slip():
    data = payload()
    data["constraints"]["thread_type"] = "male_male"
    client = SequenceClient([json.dumps(data, ensure_ascii=False)])

    result = DeepSeekQueryInterpreter(settings(), client=client).interpret(
        'Кран VT.245 с дренажем вн.-вн. 3/4"'
    )

    assert result.constraints.thread_type == "female_female"


def test_invalid_json_is_retried_once():
    valid = json.dumps(payload(), ensure_ascii=False)
    client = SequenceClient(["{broken", valid])

    result = DeepSeekQueryInterpreter(settings(), client=client).interpret("Кран шаровой Ду50 Ру16")

    assert result.searchable is True
    assert len(client.completions.calls) == 2
    assert "Предыдущий ответ не прошёл" in client.completions.calls[1]["messages"][1]["content"]
