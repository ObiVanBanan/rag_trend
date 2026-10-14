"""Business-friendly DTO and UI around the existing, unchanged tender matcher.

Never interpret retrieval rank, LLM confidence or MATCHED share as accuracy.
"""
from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator

from .observability import get_request_id
from .demo_feedback import DemoFeedbackStore, ReviewConflict, ReviewNotFound
from .product_safety import flange_spec_from_query
from .query_signals import explicit_dn_from_query, explicit_pn_mpa_from_query
from .query_constraints import canonical_product_type
from .models import LDProduct
from .query_constraints import QueryConstraints, candidate_material_text, product_snapshot, technical_compatibility

router = APIRouter(tags=["Бизнес-демо"])

FIELDS = {
    "product_type": "Тип изделия",
    "dn": "Номинальный диаметр",
    "pn_min_mpa": "Номинальное давление",
    "flange_kind": "Тип фланца",
    "stub_dn": "Размер ПНД-втулки",
    "thread_size": "Размер резьбы",
    "joining_type": "Присоединение",
    "thread_type": "Тип резьбы",
    "working_medium": "Рабочая среда",
    "valve_type": "Исполнение",
    "valve_designation": "Обозначение",
    "body_material": "Материал корпуса",
    "body_material_grade": "Марка материала",
    "bore_type": "Тип прохода",
    "control": "Управление",
}
VALUES = {
    "ball_valve": "Шаровой кран", "butterfly_valve": "Дисковый затвор",
    "gate_valve": "Задвижка", "flange": "Фланец",
    "check_valve": "Обратный клапан", "filter": "Фильтр",
    "flanged": "Фланцевое", "welded": "Приварное",
    "threaded": "Резьбовое", "wafer": "Межфланцевое",
    "compression": "Компрессионное",
    "nipple": "Штуцерное", "welded_nipple": "Приварное/штуцерное",
    "male_female": "НР/ВР", "female_female": "ВР/ВР", "male_male": "НР/НР",
    "steel": "Сталь", "brass": "Латунь", "full": "Полнопроходной",
    "pe_stub": "Под ПНД-втулку", "flat_welded": "Плоский приварной",
    "reduced": "Стандартнопроходной", "manual": "Ручное",
    "gearbox": "Редуктор", "electric": "Электропривод",
    "electric_ready": "Под электропривод", "pneumatic": "Пневмопривод",
    "standard": "Стандартное", "underground": "Подземное",
    "regulating": "Регулирующее",
}


class DemoRequest(BaseModel):
    products: list[str] = Field(min_length=1, max_length=100)

    @field_validator("products")
    @classmethod
    def check_products(cls, values: list[str]) -> list[str]:
        if any(not v.strip() or len(v) > 2000 for v in values):
            raise ValueError("Каждая позиция должна содержать 1–2000 символов")
        return values


def parameter_rows(interpretation: dict[str, Any]) -> list[dict[str, Any]]:
    extracted = interpretation.get("constraints") or {}
    hard = interpretation.get("hard_constraints") or {}
    extracted = extracted if isinstance(extracted, dict) else {}
    hard = hard if isinstance(hard, dict) else {}
    result = []
    for field, label in FIELDS.items():
        value = extracted.get(field)
        if value in (None, "", "other", "unknown"):
            continue
        if field == "dn":
            shown = "DN " + str(value)
        elif field == "stub_dn":
            shown = "Ø" + str(value) + " мм"
        elif field == "pn_min_mpa":
            try:
                v = float(value)
                shown = f"{v:g} МПа (PN{v*10:g})"
            except (TypeError, ValueError):
                shown = str(value)
        else:
            shown = VALUES.get(str(value), str(value))
        strict = hard.get(field) not in (None, "", "other", "unknown")
        result.append({
            "key": field, "label": label, "value": shown,
            "check": "Строгое условие" if strict else "Интерпретация",
        })
    return result


def _render_fact(key: str, value: Any) -> str:
    if value is None or value == "" or value == []:
        return "Нет данных"
    if key == "dn":
        return f"DN{value}"
    if key == "stub_dn":
        return f"Ø{value} мм"
    if key == "pn_min_mpa":
        try:
            n = float(value)
            return f"{n:g} МПа (PN{n*10:g})"
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, list):
        return ", ".join(map(str, value[:8]))
    return VALUES.get(str(value), str(value))


def compare_candidate(candidate: Any, interpretation: dict[str, Any]) -> dict[str, Any]:
    """Separate matching, mismatching and unknown catalog facts.

    The check is against interpreted constraints; hard flags identify explicit
    requirements. Do not equate matching these fields with tender equivalence.
    """
    raw = interpretation.get("constraints")
    hard = interpretation.get("hard_constraints")
    empty = {"rows": [], "matched": 0, "mismatched": 0, "unknown": 0}
    if not isinstance(raw, dict):
        return empty
    hard = hard if isinstance(hard, dict) else {}
    try:
        clean = {k: v for k, v in raw.items() if k in QueryConstraints.model_fields}
        constraints = QueryConstraints.model_validate(clean)
        product = LDProduct(
            id=candidate.ld_id, name=candidate.name, article=candidate.article,
            dn=candidate.dn, pn=candidate.pn, joining_type=candidate.joining_type,
            properties=candidate.properties or [],
        )
        actual = product_snapshot(product)
        actual["body_material_grade"] = candidate_material_text(product)
        checks = technical_compatibility(product, constraints)["checks"]
    except (ValueError, TypeError, KeyError):
        return empty
    result = {"rows": [], "matched": 0, "mismatched": 0, "unknown": 0}
    for key, label in FIELDS.items():
        expected = clean.get(key)
        if expected is None or expected == "" or expected == [] or expected in ("other", "unknown"):
            continue
        status = checks.get(key, "unknown")
        if status == "match":
            verdict, field = "MATCH", "matched"
        elif status.startswith("mismatch:"):
            verdict, field = "MISMATCH", "mismatched"
        else:
            verdict, field = "UNKNOWN", "unknown"
        result[field] += 1
        actual_value = actual.get("pn_mpa" if key == "pn_min_mpa" else key)
        if key == "valve_designation":
            actual_value = None  # Not deterministically evaluated by this checker.
        result["rows"].append({
            "key": key, "label": label,
            "required": _render_fact(key, expected),
            "catalog": _render_fact(key, actual_value),
            "status": verdict,
            "strict": hard.get(key) not in (None, "", "other", "unknown"),
        })
    return result


def as_variant(candidate: Any, selected: Any = None, interpretation: dict[str, Any] | None = None) -> dict[str, Any]:
    product = LDProduct(
        id=candidate.ld_id, name=candidate.name, article=candidate.article,
        dn=candidate.dn, pn=candidate.pn, joining_type=candidate.joining_type,
        properties=candidate.properties or [],
    )
    technical = product_snapshot(product)
    return {
        "technical": technical,
        "url": candidate.url if isinstance(candidate.url, str) and candidate.url.startswith("https://") else None,
        "ld_id": candidate.ld_id,
        "name": candidate.name,
        "article": candidate.article,
        "dn": candidate.dn,
        "pn": candidate.pn,
        "joining_type": candidate.joining_type,
        "score": candidate.score,
        "llm_confidence": getattr(selected, "llm_confidence", None),
        "reason": getattr(selected, "reason", None),
        "selection": "RECOMMENDED" if selected is not None else "FOR_REVIEW",
        "comparison": compare_candidate(candidate, interpretation or {}),
    }


def build_demo_item(match: Any, duration_ms: float) -> dict[str, Any]:
    interpretation = match.query_interpretation or {}
    if not isinstance(interpretation, dict):
        interpretation = {}
    pool = list(match.candidates or [])
    indexed = {c.ld_id: c for c in pool}
    if match.ld_product is not None:
        indexed[match.ld_product.ld_id] = match.ld_product
    recommended = []
    chosen = set()
    if match.status == "MATCHED":
        for s in match.selected or []:
            c = indexed.get(s.ld_id)
            if c is not None and c.ld_id not in chosen:
                recommended.append(as_variant(c, s, interpretation))
                chosen.add(c.ld_id)
        if not recommended and match.ld_product is not None:
            recommended = [as_variant(match.ld_product, interpretation=interpretation)]
            recommended[0]["selection"] = "RECOMMENDED"
            chosen.add(match.ld_product.ld_id)
    # Limit the user review surface to ten catalogue products in total.
    alternatives = [as_variant(c, interpretation=interpretation) for c in pool if c.ld_id not in chosen][:max(0, 10-len(recommended))]
    # This is a real shortlist from retriever, never invented "10 products".
    # Deduplicated selected recommendations appear first, followed by
    # retrieval-ranked candidates. Keep original RAG match status unchanged.
    top_candidates = (recommended + alternatives)[:10]
    status = "ERROR" if match.status == "RERANK_FAILED" else match.status
    reason = str(match.reason or "")
    if status == "ERROR":
        message = "Ошибка этапа обработки. Повторите запрос."
    elif status == "INSUFFICIENT_PARAMETERS":
        message = "Для безопасного подбора недостаточно характеристик. Уточните параметры и повторите поиск."
    elif reason.startswith("HARD_CONSTRAINT_FILTER"):
        message = "Среди найденных кандидатов нет подтверждённого соответствия строгим условиям."
    elif reason.startswith("QUERY_REJECTED"):
        message = "Недостаточно данных либо позиция вне области текущего поиска."
    elif status == "NOT_FOUND":
        message = "Подходящий товар не подтверждён. Это не доказывает отсутствие аналога в каталоге."
    else:
        message = "Рекомендации получены после технической фильтрации и ранжирования."
    return {
        "input": match.query,
        "status": status,
        "message": message,
        "missing_fields": interpretation.get("missing_fields") if status == "INSUFFICIENT_PARAMETERS" else [],
        "normalized_query": interpretation.get("normalized_query") or match.query,
        "editable_constraints": editable_constraints(match.query, interpretation),
        "parameters": parameter_rows(interpretation),
        "recommended": recommended,
        "other_candidates": alternatives,
        "top_candidates": top_candidates,
        "metrics": {
            "latency_ms": round(duration_ms, 1),
            "retrieved_count": len(pool),
            "recommended_count": len(recommended),
            "retrieval_score": match.score,
        },
    }



# A human correction is rendered as a new explicit technical request and sent
# through the SAME safety gate, retrieval and reranker as the original. This is
# not a direct selection bypass and never changes GOLD or Qdrant.
EDIT_FIELDS = {
    "product_type", "dn", "pn_min_mpa", "joining_type", "thread_type",
    "thread_size", "working_medium", "body_material", "body_material_grade",
    "bore_type", "control", "flange_kind", "stub_dn",
}
ENUMS = {
    "product_type": {"ball_valve", "flange", "butterfly_valve", "gate_valve", "check_valve", "filter"},
    "joining_type": {"threaded","flanged","welded","wafer","compression","nipple","welded_nipple"},
    "thread_type": {"female_female","male_male","male_female"},
    "body_material": {"steel","stainless_steel","brass","cast_iron"},
    "bore_type": {"full","reduced"},
    "control": {"manual","gearbox","electric","pneumatic"},
    "flange_kind": {"flat_welded","pe_stub"},
}
PRODUCT_NAMES = {
    "ball_valve":"Кран шаровый", "flange":"Фланец",
    "butterfly_valve":"Затвор дисковый", "gate_valve":"Задвижка",
    "check_valve":"Клапан обратный", "filter":"Фильтр",
}
TEXT_NAMES = {
    "joining_type":{"threaded":"резьбовой","flanged":"фланцевый",
                    "welded":"под приварку","wafer":"межфланцевый","compression":"обжимной",
                    "nipple":"штуцерное присоединение",
                    "welded_nipple":"приварное/штуцерное присоединение"},
    "thread_type":{"female_female":"ВР/ВР","male_male":"НР/НР","male_female":"НР/ВР"},
    "body_material":{"steel":"стальной","stainless_steel":"нержавеющая сталь",
                     "brass":"латунный","cast_iron":"чугунный"},
    "bore_type":{"full":"полнопроходной","reduced":"стандартнопроходной"},
    "control":{"manual":"ручное управление","gearbox":"с редуктором",
               "electric":"электропривод","pneumatic":"пневмопривод"},
}


def editable_constraints(query: str, interpretation: dict[str, Any]) -> dict[str, Any]:
    raw = interpretation.get("constraints") or {}
    hard = interpretation.get("hard_constraints") or {}
    parsed = {k:v for k,v in (raw.items() if isinstance(raw,dict) else [])
              if k in EDIT_FIELDS and v is not None}
    if isinstance(hard,dict):
        parsed.update({k:v for k,v in hard.items() if k in EDIT_FIELDS and v is not None})
    parsed.setdefault("product_type",canonical_product_type(query))
    if parsed.get("product_type") not in ENUMS["product_type"]:
        parsed["product_type"] = "other"
    if "dn" not in parsed:
        parsed["dn"] = explicit_dn_from_query(query)
    if "pn_min_mpa" not in parsed:
        parsed["pn_min_mpa"] = explicit_pn_mpa_from_query(query)
    spec = flange_spec_from_query(query)
    if spec is not None:
        parsed.update({
            "product_type":"flange", "flange_kind":spec.kind,
            "dn":spec.dn,"stub_dn":spec.stub_dn,
            "body_material":spec.body_material,
        })
        if spec.body_material_grade: parsed["body_material_grade"]=spec.body_material_grade
        if spec.pn_min_mpa: parsed["pn_min_mpa"]=spec.pn_min_mpa
    return parsed


def validate_edits(values: dict[str, Any]) -> dict[str, Any]:
    if not values or set(values)-EDIT_FIELDS:
        raise ValueError("Используйте только поддерживаемые технические поля.")
    checked = {}
    for name,value in values.items():
        if value in (None,""):
            checked[name] = None
        elif name in {"dn","stub_dn"}:
            if isinstance(value,bool) or not str(value).isdigit() or not 1 <= int(value) <= 2000:
                raise ValueError("DN / размер втулки должен быть числом от 1 до 2000.")
            checked[name] = int(value)
        elif name=="pn_min_mpa":
            try: number=float(value)
            except (TypeError,ValueError): raise ValueError("Неверное давление в МПа.") from None
            if not 0.01 <= number <= 100:
                raise ValueError("Давление должно быть от 0,01 до 100 МПа.")
            checked[name]=number
        elif name in ENUMS:
            if value not in ENUMS[name]:
                raise ValueError(f"Недопустимое значение поля {name}.")
            checked[name]=value
        elif name in {"thread_size","working_medium","body_material_grade"}:
            if (not isinstance(value,str) or len(value)>80 or
                not all(c.isalnum() or c in " .,/+-()\"№" for c in value)):
                raise ValueError(f"Недопустимое значение поля {name}.")
            checked[name]=value.strip()
    return checked


def render_corrected_query(source: str, fields: dict[str, Any]) -> str:
    """Build one noncontradictory specification; rerun the production matcher."""
    kind=fields.get("product_type")
    if kind not in PRODUCT_NAMES:
        raise ValueError("Укажите тип изделия.")
    f=lambda key: fields.get(key)
    words=[PRODUCT_NAMES[kind]]
    if kind=="flange":
        if f("flange_kind")=="pe_stub":
            if not f("dn") or not f("stub_dn"):
                raise ValueError("Для фланца ПНД нужны оба диаметра.")
            words=[f"Фланец стальной Ду{f('dn')} под втулку ПНД Ду{f('stub_dn')}"]
        elif f("flange_kind")=="flat_welded":
            words=["Фланец стальной плоский приварной"]
    elif kind=="ball_valve" and "11б27п" in source.lower():
        words.append("11Б27П")
    if f("body_material") and not (kind=="flange" and f("flange_kind")=="pe_stub"):
        words.append(TEXT_NAMES["body_material"].get(f("body_material"),""))
    if f("body_material_grade"):
        words.append("марка стали "+str(f("body_material_grade")))
    if f("dn") and not (kind=="flange" and f("flange_kind")=="pe_stub"):
        words.append(f"Ду{f('dn')}")
    if f("pn_min_mpa"):
        # Query recognizers accept Russian Ру in bar (1 МПа = 10 бар).
        words.append(f"Ру{float(f('pn_min_mpa'))*10:g}")
    for attr in ("joining_type","thread_type","bore_type","control"):
        if f(attr):
            words.append(TEXT_NAMES[attr].get(f(attr),""))
    if f("thread_size"):
        words.append("резьба G"+str(f("thread_size")).removeprefix("G"))
    if f("working_medium"):
        words.append("для "+str(f("working_medium")))
    return " ".join(w for w in words if w)


def _store(request: Request, required: bool = True) -> DemoFeedbackStore | None:
    runtime=request.app.state.runtime
    store=getattr(runtime,"demo_feedback",None)
    if store is not None:
        return store
    history=getattr(runtime,"history",None)
    if history is not None and getattr(history,"pool",None) is not None:
        return DemoFeedbackStore(history.pool)
    if required:
        raise HTTPException(status_code=503,detail="Хранение пользовательских решений недоступно.")
    return None


class ReviewTarget(BaseModel):
    request_id: str = Field(min_length=8, max_length=128)
    review_token: str = Field(min_length=30, max_length=128)
    item_index: int = Field(ge=0, le=99)
    revision: int = Field(ge=0)


class CorrectionRequest(ReviewTarget):
    changes: dict[str, Any] = Field(min_length=1, max_length=13)


class AppendItemRequest(BaseModel):
    request_id: str = Field(min_length=8, max_length=128)
    review_token: str = Field(min_length=30, max_length=128)
    expected_count: int = Field(ge=1, le=100)
    query: str = Field(default="", max_length=2000)
    fields: dict[str, Any] = Field(default_factory=dict, max_length=13)

    @field_validator("query")
    @classmethod
    def clean_query(cls, value: str) -> str:
        # Only one item per API call; prevent multi-line batch injection.
        if "\n" in value or "\r" in value:
            raise ValueError("Добавить можно только одну позицию за раз.")
        return value.strip()


class DecisionRequest(ReviewTarget):
    action: Literal["ACCEPTED","REJECTED","SELECTED_OTHER"]
    selected_ld_id: int | None = None
    acknowledge_unknown: bool = False


def _handle_review_error(exc: Exception) -> None:
    if isinstance(exc,ReviewNotFound):
        raise HTTPException(status_code=404,detail=str(exc)) from None
    if isinstance(exc,ReviewConflict):
        raise HTTPException(status_code=409,detail=str(exc)) from None
    if isinstance(exc,ValueError):
        raise HTTPException(status_code=422,detail=str(exc)) from None
    raise exc


@router.post("/v1/demo/correct")
def correct_result(payload: CorrectionRequest, request: Request) -> dict[str, Any]:
    store=_store(request)
    try:
        row=store.get(payload.request_id,payload.item_index,payload.review_token)
        if row["revision"]!=payload.revision:
            raise ReviewConflict("Позиция изменилась. Повторите загрузку результатов.")
        edits=validate_edits(payload.changes)
        current=row["current_result"]
        merged={**(current.get("editable_constraints") or {}),**edits}
        for key in list(merged):
            if merged[key] is None: merged.pop(key)
        corrected_query=render_corrected_query(current["input"],merged)
        started=perf_counter()
        match=request.app.state.runtime.matcher.match_one_hybrid_with_rerank(corrected_query)
        result=build_demo_item(match,(perf_counter()-started)*1000)
        # Preserve the tender source separately from the corrected technical query.
        result["source_input"]=current.get("source_input",current["input"])
        result["editable_constraints"]=merged
        result["corrected_query"]=corrected_query
        result["input"]=result["source_input"]
        decision=store.revise(payload.request_id,payload.item_index,payload.review_token,
                              payload.revision,result,edits)
        return decision
    except (ReviewNotFound,ReviewConflict,ValueError) as exc:
        _handle_review_error(exc)


@router.post("/v1/demo/append")
def append_demo_item(payload: AppendItemRequest, request: Request) -> dict[str, Any]:
    """One incremental search, no recomputation of prior items.

    The raw tender description is preserved for audit. A structured request
    uses the same query synthesis and safety gate as user corrections.
    """
    store = _store(request)
    # Ensure session and expected length match before calling paid providers.
    try:
        store.assert_can_append(payload.request_id, payload.review_token, payload.expected_count)
        fields = validate_edits(payload.fields) if payload.fields else {}
        if not payload.query and not fields:
            raise ValueError("Введите товар или заполните его характеристики.")
        if fields:
            # Editing is a deliberate replacement of technical constraints:
            # all user-entered fields are explicit; never concatenate
            # contradictory unvalidated source fragments to bypass hard gates.
            base = editable_constraints(payload.query,{})
            base.update(fields)
            base = {key:value for key,value in base.items() if value is not None}
            query = render_corrected_query(payload.query,base)
        else:
            query = payload.query
        if not query:
            raise ValueError("Нужно указать товар для подбора.")
        started = perf_counter()
        try:
            match = request.app.state.runtime.matcher.match_one_hybrid_with_rerank(query)
            result = build_demo_item(match,(perf_counter()-started)*1000)
        except Exception:
            result = {
                "input": query, "status": "ERROR",
                "message": "Сервис подбора временно недоступен.",
                "normalized_query": query, "parameters": [],
                "recommended": [], "other_candidates": [], "top_candidates": [],
                "editable_constraints": fields,
            }
        # Keep original input visible, and the normalized technical
        # specification available for further correction.
        if payload.query:
            result["source_input"] = payload.query
            result["input"] = payload.query
        else:
            result["source_input"] = query
        if fields:
            result["corrected_query"] = query
            result["editable_constraints"] = base
        return store.append(payload.request_id,payload.review_token,
                            payload.expected_count,result)
    except (ReviewNotFound,ReviewConflict,ValueError) as exc:
        _handle_review_error(exc)


@router.post("/v1/demo/decision")
def decide_result(payload: DecisionRequest, request: Request) -> dict[str, Any]:
    try:
        return _store(request).decide(
            payload.request_id,payload.item_index,payload.review_token,
            payload.revision,payload.action,payload.selected_ld_id,
            payload.acknowledge_unknown)
    except (ReviewNotFound,ReviewConflict,ValueError) as exc:
        _handle_review_error(exc)


@router.get("/v1/demo/stats")
def demo_stats(request: Request, days: int = 30) -> dict[str, Any]:
    # Also guarded at the global middleware level.
    _require_admin(request)
    if not 1 <= days <= 366:
        raise HTTPException(status_code=422,detail="Период 1–366 дней.")
    return _store(request).stats(days)


def _require_admin(request: Request) -> None:
    from .public_auth import valid_basic_auth
    settings=request.app.state.runtime.settings
    username=str(getattr(settings,"public_demo_admin_username","ld-admin") or "ld-admin")
    password=str(getattr(settings,"public_demo_admin_password","") or "")
    if not password or not valid_basic_auth(request.headers.get("authorization"),
                                              username=username,password=password):
        raise HTTPException(status_code=403,detail="Требуется доступ администратора.")


@router.get("/demo/stats", response_class=HTMLResponse, include_in_schema=False)
def demo_stats_page(request: Request) -> HTMLResponse:
    _require_admin(request)
    return HTMLResponse((Path(__file__).parent/"business_demo_stats.html").read_text(encoding="utf-8"))


@router.get("/demo", response_class=HTMLResponse, include_in_schema=False)
def business_page() -> HTMLResponse:
    return HTMLResponse(
        (Path(__file__).parent / "business_demo.html").read_text(encoding="utf-8")
    )


@router.post("/v1/demo/match")
def business_match(payload: DemoRequest, request: Request) -> dict[str, Any]:
    runtime = request.app.state.runtime
    if len(payload.products) > min(runtime.settings.api_max_batch_size, int(getattr(runtime.settings, 'public_demo_max_batch_size', 10))):
        raise HTTPException(status_code=413, detail="Размер пакета превышает лимит")
    started = perf_counter()
    results = []
    for product in payload.products:
        item_started = perf_counter()
        try:
            match = runtime.matcher.match_one_hybrid_with_rerank(product.strip())
            results.append(build_demo_item(match, (perf_counter() - item_started)*1000))
        except Exception:
            # Never disclose provider exceptions, tokens, or stack traces in demo DTO.
            results.append({
                "input": product, "status": "ERROR",
                "message": "Сервис подбора временно недоступен.",
                "normalized_query": product, "parameters": [],
                "recommended": [], "other_candidates": [], "top_candidates": [],
                "metrics": {"latency_ms": round((perf_counter()-item_started)*1000, 1),
                            "retrieved_count": 0, "recommended_count": 0,
                            "retrieval_score": None},
            })
    store=_store(request, required=False)
    session_token=store.start(get_request_id(),results) if store is not None else None
    return {
        "request_id": get_request_id(),
        "review_token": session_token,
        "results": results,
        "latency_ms": round((perf_counter()-started)*1000, 1),
        "accuracy_note": (
            "LLM confidence и retrieval score — не вероятность правильного ответа; "
            "доля MATCHED — не метрика точности."
        ),
    }
