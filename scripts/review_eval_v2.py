from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nomenclature_matcher.review_state import (
    apply_review_state_to_labels,
    finalize_query,
    get_query_progress,
    initialize_review_state,
    load_review_state,
    reopen_query,
    save_review_state,
    set_candidate_grade,
    set_query_cursor,
)


ROOT = Path(__file__).resolve().parents[1]
CANDIDATES_PATH = ROOT / "data" / "eval_v2_review_candidates.json"
REVIEW_STATE_PATH = ROOT / "data" / "eval_v2_human_review.json"
LABELS_PATH = ROOT / "data" / "eval_labels_v2.json"
FILTER_MODES = ["Неразмеченные", "Все", "Сомнительные", "Пропущенные", "Подходящие", "Отклонённые"]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_payloads() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    candidates = _read_json(CANDIDATES_PATH)
    labels = _read_json(LABELS_PATH) if LABELS_PATH.exists() else {}
    review_state = initialize_review_state(query["id"] for query in candidates["queries"])
    if REVIEW_STATE_PATH.exists():
        loaded_state = load_review_state(REVIEW_STATE_PATH)
        review_state["version"] = loaded_state.get("version", review_state["version"])
        review_state["queries"].update(loaded_state.get("queries", {}))
    else:
        save_review_state(REVIEW_STATE_PATH, review_state)
    return candidates, labels, review_state


def save_labels_for_query(review_state: dict[str, Any], labels: dict[str, Any], query_id: str) -> None:
    updated = apply_review_state_to_labels(labels, query_id, review_state)
    _write_json(LABELS_PATH, updated)


def query_label(query_item: dict[str, Any]) -> str:
    query_text = query_item["query"]
    suffix = "…" if len(query_text) > 96 else ""
    return f"{query_item['id']} — {query_text[:96]}{suffix}"


def _format_value(value: Any) -> str:
    if value in (None, ""):
        return "—"
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(items) if items else "—"
    return str(value)


def _lookup_technical_property(technical_properties: dict[str, Any], names: tuple[str, ...]) -> str:
    for name in names:
        if name not in technical_properties:
            continue
        return _format_value(technical_properties[name])
    return "—"


def render_query_progress(
    query_item: dict[str, Any],
    query_state: dict[str, Any],
    review_state: dict[str, Any],
    total_candidates: int,
) -> None:
    current_progress = get_query_progress(query_state, total_candidates=len(query_item["candidates"]))
    total_reviewed_candidates = sum(len(query_state_item.get("candidate_grades", {})) for query_state_item in review_state["queries"].values())

    st.subheader("Прогресс")
    progress_cols = st.columns(4)
    progress_cols[0].metric("Текущий query", f"{current_progress['reviewed_candidates']} / {current_progress['total_candidates']}")
    progress_cols[1].metric("Все queries", f"{total_reviewed_candidates} / {total_candidates}")
    progress_cols[2].metric("ACCEPT", current_progress["accepted"])
    progress_cols[3].metric("REJECT", current_progress["rejected"])

    detail_cols = st.columns(2)
    detail_cols[0].metric("UNSURE", current_progress["unsure"])
    detail_cols[1].metric("SKIP", current_progress["skipped"])


def filter_query_ids(candidates_payload: dict[str, Any], review_state: dict[str, Any], mode: str) -> list[str]:
    result: list[str] = []
    for query in candidates_payload["queries"]:
        query_id = query["id"]
        query_state = review_state["queries"].get(query_id, {})
        final_status = query_state.get("final_status")
        candidate_grades = query_state.get("candidate_grades", {})
        has_unsure = any(item.get("grade") == "UNSURE" for item in candidate_grades.values())
        is_unreviewed = not query_state.get("completed", False) and final_status is None

        if mode == "Все":
            result.append(query_id)
        elif mode == "Неразмеченные" and is_unreviewed:
            result.append(query_id)
        elif mode == "Сомнительные" and has_unsure:
            result.append(query_id)
        elif mode == "Пропущенные" and final_status == "RETRIEVAL_MISS":
            result.append(query_id)
        elif mode == "Подходящие" and final_status == "MATCHED":
            result.append(query_id)
        elif mode == "Отклонённые" and final_status == "NOT_FOUND":
            result.append(query_id)
    return result


def render_query_text(query_item: dict[str, Any]) -> None:
    st.subheader("Вопрос")
    st.write(query_item["query"])


def render_candidate_card(candidate: dict[str, Any], query_state: dict[str, Any]) -> None:
    st.subheader("Кандидат")
    st.markdown(f"### {candidate['name']}")
    st.caption(f"LD ID: {candidate['ld_id']} | Article: {_format_value(candidate.get('article'))}")

    info_cols = st.columns(3)
    info_cols[0].metric("DN", _format_value(candidate.get("dn")))
    info_cols[1].metric("PN", _format_value(candidate.get("pn")))
    info_cols[2].metric("Присоединение", _format_value(candidate.get("joining_type")))

    property_lines = [
        ("Тип продукта", _lookup_technical_property(candidate.get("technical_properties", {}), ("Тип продукта",))),
        ("Материал корпуса", _lookup_technical_property(candidate.get("technical_properties", {}), ("Материал корпуса",))),
        ("DN", _format_value(candidate.get("dn"))),
        ("PN", _format_value(candidate.get("pn"))),
        ("Присоединение", _format_value(candidate.get("joining_type"))),
        ("Тип резьбы", _lookup_technical_property(candidate.get("technical_properties", {}), ("Тип резьбы",))),
        ("Рабочая среда", _lookup_technical_property(candidate.get("technical_properties", {}), ("Рабочая среда",))),
        (
            "Температура рабочей среды",
            _lookup_technical_property(candidate.get("technical_properties", {}), ("Температура рабочей среды, °С", "Температура рабочей среды")),
        ),
        (
            "Температура окружающей среды",
            _lookup_technical_property(candidate.get("technical_properties", {}), ("Температура окружающей среды, °С", "Температура окружающей среды")),
        ),
        ("Управление", _lookup_technical_property(candidate.get("technical_properties", {}), ("Управление",))),
    ]

    left_col, right_col = st.columns(2)
    for index, (label, value) in enumerate(property_lines):
        target = left_col if index < (len(property_lines) / 2) else right_col
        target.write(f"{label}: {value}")

    rank_cols = st.columns(3)
    rank_cols[0].metric("Dense rank", _format_value(candidate.get("dense_rank")))
    rank_cols[1].metric("BM25 rank", _format_value(candidate.get("bm25_rank")))
    rank_cols[2].metric("Hybrid rank", _format_value(candidate.get("hybrid_rank")))

    st.caption(f"Retrieval sources: {_format_value(candidate.get('retrieval_sources'))}")

    candidate_grades = query_state.get("candidate_grades", {})
    current_grade = candidate_grades.get(str(candidate["ld_id"]))
    if current_grade:
        st.info(f"Текущая оценка: {current_grade['grade']}")


def next_unreviewed_candidate_index(candidates: list[dict[str, Any]], query_state: dict[str, Any], current_index: int) -> int:
    graded_ids = set(query_state.get("candidate_grades", {}))
    for index in range(current_index + 1, len(candidates)):
        if str(candidates[index]["ld_id"]) not in graded_ids:
            return index
    return min(current_index, max(len(candidates) - 1, 0))


def move_cursor_to_next_unreviewed(review_state: dict[str, Any], query_id: str, candidates: list[dict[str, Any]], current_index: int) -> dict[str, Any]:
    query_state = review_state["queries"][query_id]
    next_index = next_unreviewed_candidate_index(candidates, query_state, current_index)
    return set_query_cursor(review_state, query_id, next_index)


def save_candidate_grade(
    review_state: dict[str, Any],
    query_id: str,
    candidate: dict[str, Any],
    grade: str,
    comment: str,
    candidates: list[dict[str, Any]],
    current_index: int,
) -> dict[str, Any]:
    updated = set_candidate_grade(review_state, query_id, candidate["ld_id"], grade, comment=comment)
    updated = move_cursor_to_next_unreviewed(updated, query_id, candidates, current_index)
    save_review_state(REVIEW_STATE_PATH, updated)
    return updated


def update_query_label_from_state(review_state: dict[str, Any], labels: dict[str, Any], query_id: str) -> None:
    save_labels_for_query(review_state, labels, query_id)


def render_query_finalization(
    review_state: dict[str, Any],
    labels: dict[str, Any],
    query_item: dict[str, Any],
    query_state: dict[str, Any],
) -> None:
    query_id = query_item["id"]
    st.subheader("Завершение query")

    final_comment_key = f"query-comment::{query_id}"
    if final_comment_key not in st.session_state:
        st.session_state[final_comment_key] = query_state.get("final_comment", "")
    final_comment = st.text_area("Комментарий", key=final_comment_key, height=90)

    accepted_ids = [
        candidate["ld_id"]
        for candidate in query_item["candidates"]
        if query_state.get("candidate_grades", {}).get(str(candidate["ld_id"]), {}).get("grade") == "ACCEPT"
    ]

    status_text = query_state.get("final_status") or "UNREVIEWED"
    if query_state.get("completed", False):
        st.info(f"Текущий статус query: {status_text}")
        if status_text == "MATCHED":
            st.write(f"acceptable_ld_ids: {accepted_ids}")

    if not query_item["candidates"]:
        st.warning("У этого query нет кандидатов.")
        return

    if not accepted_ids:
        st.caption("Для MATCHED нужен хотя бы один ACCEPT.")

    action_cols = st.columns(4)
    if action_cols[0].button("✅ Подтвердить MATCHED", use_container_width=True, disabled=not accepted_ids):
        updated = finalize_query(review_state, query_id, "MATCHED", comment=final_comment)
        save_review_state(REVIEW_STATE_PATH, updated)
        update_query_label_from_state(updated, labels, query_id)
        st.rerun()
    if action_cols[1].button("❌ NOT_FOUND", use_container_width=True):
        updated = finalize_query(review_state, query_id, "NOT_FOUND", comment=final_comment)
        save_review_state(REVIEW_STATE_PATH, updated)
        update_query_label_from_state(updated, labels, query_id)
        st.rerun()
    if action_cols[2].button("⚠️ RETRIEVAL_MISS", use_container_width=True):
        updated = finalize_query(review_state, query_id, "RETRIEVAL_MISS", comment=final_comment)
        save_review_state(REVIEW_STATE_PATH, updated)
        update_query_label_from_state(updated, labels, query_id)
        st.rerun()
    if action_cols[3].button("⏸ UNREVIEWED", use_container_width=True):
        updated = finalize_query(review_state, query_id, "UNREVIEWED", comment=final_comment)
        save_review_state(REVIEW_STATE_PATH, updated)
        update_query_label_from_state(updated, labels, query_id)
        st.rerun()

    if query_state.get("completed", False):
        if st.button("Переоткрыть query", use_container_width=True):
            updated = reopen_query(review_state, query_id)
            save_review_state(REVIEW_STATE_PATH, updated)
            st.rerun()


def render_navigation(
    review_state: dict[str, Any],
    query_id: str,
    candidates: list[dict[str, Any]],
    current_index: int,
) -> None:
    nav_cols = st.columns(2)
    if nav_cols[0].button("← предыдущий кандидат", use_container_width=True, disabled=current_index <= 0):
        updated = set_query_cursor(review_state, query_id, current_index - 1)
        save_review_state(REVIEW_STATE_PATH, updated)
        st.rerun()
    if nav_cols[1].button("следующий кандидат →", use_container_width=True, disabled=current_index >= len(candidates) - 1):
        updated = set_query_cursor(review_state, query_id, current_index + 1)
        save_review_state(REVIEW_STATE_PATH, updated)
        st.rerun()


def main() -> None:
    st.set_page_config(page_title="Eval V2 Human Review", layout="wide")
    candidates_payload, labels, review_state = load_payloads()

    query_ids = [query["id"] for query in candidates_payload["queries"]]
    query_by_id = {query["id"]: query for query in candidates_payload["queries"]}

    st.title("Eval V2 Human Review")

    mode = st.sidebar.selectbox("Фильтр", FILTER_MODES, index=0)
    filtered_ids = filter_query_ids(candidates_payload, review_state, mode)
    if not filtered_ids:
        filtered_ids = query_ids

    current_query_id = st.sidebar.selectbox("Query", filtered_ids, format_func=lambda query_id: query_label(query_by_id[query_id]))
    query_item = query_by_id[current_query_id]
    query_state = review_state["queries"][current_query_id]

    candidates = query_item["candidates"]
    current_index = min(query_state.get("cursor_index", 0), max(len(candidates) - 1, 0)) if candidates else 0
    if query_state.get("cursor_index", 0) != current_index:
        review_state = set_query_cursor(review_state, current_query_id, current_index)
        save_review_state(REVIEW_STATE_PATH, review_state)
        st.rerun()
        return

    total_candidates = sum(len(query["candidates"]) for query in candidates_payload["queries"])
    render_query_progress(query_item, query_state, review_state, total_candidates)
    render_query_text(query_item)

    if not candidates:
        st.warning("У query нет кандидатов для ревью.")
    else:
        candidate = candidates[current_index]
        st.caption(f"Кандидат {current_index + 1} / {len(candidates)}")
        render_candidate_card(candidate, query_state)

        comment_key = f"candidate-comment::{current_query_id}::{candidate['ld_id']}"
        existing_comment = query_state.get("candidate_grades", {}).get(str(candidate["ld_id"]), {}).get("comment", "")
        if comment_key not in st.session_state:
            st.session_state[comment_key] = existing_comment
        candidate_comment = st.text_area("Комментарий", key=comment_key, height=70)

        action_cols = st.columns(4)
        if action_cols[0].button("✅ Подходит", use_container_width=True):
            review_state = save_candidate_grade(
                review_state,
                current_query_id,
                candidate,
                "ACCEPT",
                candidate_comment,
                candidates,
                current_index,
            )
            st.rerun()
        if action_cols[1].button("❌ Не подходит", use_container_width=True):
            review_state = save_candidate_grade(
                review_state,
                current_query_id,
                candidate,
                "REJECT",
                candidate_comment,
                candidates,
                current_index,
            )
            st.rerun()
        if action_cols[2].button("⚠️ Сомневаюсь", use_container_width=True):
            review_state = save_candidate_grade(
                review_state,
                current_query_id,
                candidate,
                "UNSURE",
                candidate_comment,
                candidates,
                current_index,
            )
            st.rerun()
        if action_cols[3].button("⏭ Пропустить", use_container_width=True):
            review_state = save_candidate_grade(
                review_state,
                current_query_id,
                candidate,
                "SKIP",
                candidate_comment,
                candidates,
                current_index,
            )
            st.rerun()

        render_navigation(review_state, current_query_id, candidates, current_index)

    updated_review_state = load_review_state(REVIEW_STATE_PATH)
    render_query_finalization(updated_review_state, labels, query_item, updated_review_state["queries"][current_query_id])

    current_progress = get_query_progress(updated_review_state["queries"][current_query_id], total_candidates=len(candidates))
    st.write(
        f"Status: {current_progress['final_status']} | "
        f"Completed: {current_progress['completed']} | "
        f"Accepted IDs: {[candidate_id for candidate_id, item in updated_review_state['queries'][current_query_id].get('candidate_grades', {}).items() if item.get('grade') == 'ACCEPT']}"
    )


if __name__ == "__main__":
    main()
