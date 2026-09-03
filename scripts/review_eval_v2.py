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
    get_accepted_candidate_ids_for_ids,
    get_reviewed_candidate_count_for_ids,
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
EXPANDED_CANDIDATES_PATH = ROOT / "data" / "eval_v2_retrieval_miss_candidates.json"
EXPANDED_REVIEW_STATE_PATH = ROOT / "data" / "eval_v2_retrieval_miss_human_review.json"
LABELS_PATH = ROOT / "data" / "eval_labels_v2.json"
WORKFLOWS = {
    "Базовый review": (CANDIDATES_PATH, REVIEW_STATE_PATH),
    "Expanded retrieval-miss review": (EXPANDED_CANDIDATES_PATH, EXPANDED_REVIEW_STATE_PATH),
}
CANDIDATE_VIEW_MODES = ["Все кандидаты", "Только ACCEPT"]
QUERY_FILTER_MODES = ["Все", "Неразмеченные", "Сомнительные", "Пропущенные", "Подходящие", "Отклонённые", "Queries с ACCEPT"]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_payloads(candidates_path: Path, review_state_path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    candidates = _read_json(candidates_path)
    labels = _read_json(LABELS_PATH) if LABELS_PATH.exists() else {}
    review_state = initialize_review_state(query["id"] for query in candidates["queries"])
    if review_state_path.exists():
        loaded_state = load_review_state(review_state_path)
        review_state["version"] = loaded_state.get("version", review_state["version"])
        review_state["queries"].update(loaded_state.get("queries", {}))
    else:
        save_review_state(review_state_path, review_state)
    return candidates, labels, review_state


def save_labels_for_query(
    review_state: dict[str, Any],
    labels: dict[str, Any],
    query_id: str,
    candidate_ids: list[int] | None = None,
) -> None:
    updated = apply_review_state_to_labels(labels, query_id, review_state, candidate_ids=candidate_ids)
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


def workflow_session_key(workflow_name: str, key_name: str, query_id: str) -> str:
    return f"{workflow_name}::{key_name}::{query_id}"


def _current_pool_grade_summary(query_state: dict[str, Any], candidate_ids: list[int]) -> dict[str, int]:
    allowed_ids = {str(candidate_id) for candidate_id in candidate_ids}
    summary = {"accepted": 0, "rejected": 0, "unsure": 0, "skipped": 0, "reviewed_candidates": 0}
    for candidate_id, grade_info in query_state.get("candidate_grades", {}).items():
        if candidate_id not in allowed_ids:
            continue
        grade = grade_info.get("grade")
        summary["reviewed_candidates"] += 1
        if grade == "ACCEPT":
            summary["accepted"] += 1
        elif grade == "REJECT":
            summary["rejected"] += 1
        elif grade == "UNSURE":
            summary["unsure"] += 1
        elif grade == "SKIP":
            summary["skipped"] += 1
    return summary


def render_query_progress(
    query_item: dict[str, Any],
    query_state: dict[str, Any],
    total_reviewed_candidates: int,
    total_candidates: int,
) -> dict[str, int]:
    current_candidate_ids = [candidate["ld_id"] for candidate in query_item["candidates"]]
    current_progress = _current_pool_grade_summary(query_state, current_candidate_ids)

    st.subheader("Прогресс")
    progress_cols = st.columns(4)
    progress_cols[0].metric("Текущий query", f"{current_progress['reviewed_candidates']} / {len(current_candidate_ids)}")
    progress_cols[1].metric("Все queries", f"{total_reviewed_candidates} / {total_candidates}")
    progress_cols[2].metric("ACCEPT", current_progress["accepted"])
    progress_cols[3].metric("REJECT", current_progress["rejected"])

    detail_cols = st.columns(2)
    detail_cols[0].metric("UNSURE", current_progress["unsure"])
    detail_cols[1].metric("SKIP", current_progress["skipped"])
    return current_progress


def filter_query_ids(candidates_payload: dict[str, Any], review_state: dict[str, Any], mode: str) -> list[str]:
    result: list[str] = []
    for query in candidates_payload["queries"]:
        query_id = query["id"]
        query_state = review_state["queries"].get(query_id, {})
        final_status = query_state.get("final_status")
        candidate_grades = query_state.get("candidate_grades", {})
        has_unsure = any(item.get("grade") == "UNSURE" for item in candidate_grades.values())
        current_candidate_ids = [candidate["ld_id"] for candidate in query.get("candidates", [])]
        has_accept = bool(get_accepted_candidate_ids_for_ids(query_state, current_candidate_ids))
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
        elif mode == "Queries с ACCEPT" and has_accept:
            result.append(query_id)
    return result


def build_visible_candidates(
    candidates: list[dict[str, Any]],
    query_state: dict[str, Any],
    mode: str,
) -> list[tuple[int, dict[str, Any]]]:
    if mode == "Только ACCEPT":
        accepted_ids = {str(candidate_id) for candidate_id in query_state.get("candidate_grades", {}) if query_state.get("candidate_grades", {}).get(str(candidate_id), {}).get("grade") == "ACCEPT"}
        return [(index, candidate) for index, candidate in enumerate(candidates) if str(candidate["ld_id"]) in accepted_ids]
    return list(enumerate(candidates))


def resolve_visible_candidate_index(visible_candidates: list[tuple[int, dict[str, Any]]], cursor_index: int) -> int | None:
    if not visible_candidates:
        return None
    for position, (full_index, _) in enumerate(visible_candidates):
        if full_index >= cursor_index:
            return position
    return 0


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
    if query_state.get("completed", False):
        st.caption("Query завершён, редактирование кандидата заблокировано. Используйте 'Переоткрыть query' для изменения.")


def next_unreviewed_candidate_index(candidates: list[dict[str, Any]], query_state: dict[str, Any], current_index: int) -> int:
    if not candidates:
        return 0
    current_index = max(0, min(current_index, len(candidates) - 1))
    graded_ids = set(query_state.get("candidate_grades", {}))
    for index in range(current_index + 1, len(candidates)):
        if str(candidates[index]["ld_id"]) not in graded_ids:
            return index
    for index in range(0, current_index + 1):
        if str(candidates[index]["ld_id"]) not in graded_ids:
            return index
    return current_index


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
    review_state_path: Path,
) -> dict[str, Any]:
    updated = set_candidate_grade(review_state, query_id, candidate["ld_id"], grade, comment=comment)
    updated = move_cursor_to_next_unreviewed(updated, query_id, candidates, current_index)
    save_review_state(review_state_path, updated)
    return updated


def update_query_label_from_state(
    review_state: dict[str, Any],
    labels: dict[str, Any],
    query_id: str,
    candidate_ids: list[int] | None = None,
) -> None:
    save_labels_for_query(review_state, labels, query_id, candidate_ids=candidate_ids)


def render_query_finalization(
    review_state: dict[str, Any],
    labels: dict[str, Any],
    workflow_name: str,
    query_item: dict[str, Any],
    query_state: dict[str, Any],
    review_state_path: Path,
) -> None:
    query_id = query_item["id"]
    st.subheader("Завершение query")
    current_candidate_ids = [candidate["ld_id"] for candidate in query_item["candidates"]]
    accepted_ids = get_accepted_candidate_ids_for_ids(query_state, current_candidate_ids)
    reviewed_candidates = get_reviewed_candidate_count_for_ids(query_state, current_candidate_ids)
    total_candidates = len(current_candidate_ids)

    status_text = query_state.get("final_status") or "UNREVIEWED"
    if query_state.get("completed", False):
        st.info(f"Текущий статус query: {status_text}")
        if status_text == "MATCHED":
            st.write(f"acceptable_ld_ids: {accepted_ids}")
        if st.button("Переоткрыть query", use_container_width=True):
            updated = reopen_query(review_state, query_id)
            save_review_state(review_state_path, updated)
            update_query_label_from_state(updated, labels, query_id, current_candidate_ids)
            st.rerun()
        return

    if not query_item["candidates"]:
        st.warning("У этого query нет кандидатов.")
        return

    final_comment_key = workflow_session_key(workflow_name, "query-comment", query_id)
    if final_comment_key not in st.session_state:
        st.session_state[final_comment_key] = query_state.get("final_comment", "")
    final_comment = st.text_area("Комментарий", key=final_comment_key, height=90)

    if reviewed_candidates < total_candidates:
        st.warning(f"Для финализации нужно просмотреть всех кандидатов: {reviewed_candidates} / {total_candidates}.")

    if not accepted_ids:
        st.caption("Для MATCHED нужен хотя бы один ACCEPT.")

    not_found_confirm_key = workflow_session_key(workflow_name, "not-found-confirm", query_id)
    if not_found_confirm_key not in st.session_state:
        st.session_state[not_found_confirm_key] = False
    not_found_confirm = st.checkbox(
        "Подтверждаю, что подходящего товара действительно нет во всём каталоге LD, а не только среди показанных retrieval-кандидатов.",
        key=not_found_confirm_key,
    )
    st.caption("Если товар может существовать в LD, но его нет среди показанных кандидатов — выбирай RETRIEVAL_MISS.")

    action_cols = st.columns(4)
    matched_disabled = reviewed_candidates < total_candidates or not accepted_ids
    not_found_disabled = reviewed_candidates < total_candidates or bool(accepted_ids) or not not_found_confirm
    retrieval_miss_disabled = reviewed_candidates < total_candidates or bool(accepted_ids)

    if action_cols[0].button("✅ Подтвердить MATCHED", use_container_width=True, disabled=matched_disabled):
        updated = finalize_query(
            review_state,
            query_id,
            "MATCHED",
            comment=final_comment,
            candidate_ids=current_candidate_ids,
        )
        save_review_state(review_state_path, updated)
        update_query_label_from_state(updated, labels, query_id, current_candidate_ids)
        st.rerun()
    if action_cols[1].button("❌ NOT_FOUND", use_container_width=True, disabled=not_found_disabled):
        updated = finalize_query(
            review_state,
            query_id,
            "NOT_FOUND",
            comment=final_comment,
            confirmed=True,
            candidate_ids=current_candidate_ids,
        )
        save_review_state(review_state_path, updated)
        update_query_label_from_state(updated, labels, query_id, current_candidate_ids)
        st.rerun()
    if action_cols[2].button("⚠️ RETRIEVAL_MISS", use_container_width=True, disabled=retrieval_miss_disabled):
        updated = finalize_query(
            review_state,
            query_id,
            "RETRIEVAL_MISS",
            comment=final_comment,
            candidate_ids=current_candidate_ids,
        )
        save_review_state(review_state_path, updated)
        update_query_label_from_state(updated, labels, query_id, current_candidate_ids)
        st.rerun()
    if action_cols[3].button("⏸ UNREVIEWED", use_container_width=True):
        updated = finalize_query(review_state, query_id, "UNREVIEWED", comment=final_comment)
        save_review_state(review_state_path, updated)
        update_query_label_from_state(updated, labels, query_id, current_candidate_ids)
        st.rerun()


def render_navigation(
    review_state: dict[str, Any],
    query_id: str,
    visible_candidates: list[tuple[int, dict[str, Any]]],
    current_position: int,
    review_state_path: Path,
) -> None:
    if not visible_candidates:
        return
    nav_cols = st.columns(2)
    previous_position = (current_position - 1) % len(visible_candidates)
    next_position = (current_position + 1) % len(visible_candidates)
    if nav_cols[0].button("← предыдущий кандидат", use_container_width=True, disabled=len(visible_candidates) <= 1):
        updated = set_query_cursor(review_state, query_id, visible_candidates[previous_position][0])
        save_review_state(review_state_path, updated)
        st.rerun()
    if nav_cols[1].button("следующий кандидат →", use_container_width=True, disabled=len(visible_candidates) <= 1):
        updated = set_query_cursor(review_state, query_id, visible_candidates[next_position][0])
        save_review_state(review_state_path, updated)
        st.rerun()


def main() -> None:
    st.set_page_config(page_title="Eval V2 Human Review", layout="wide")
    workflow_name = st.sidebar.selectbox("Workflow", list(WORKFLOWS))
    candidates_path, review_state_path = WORKFLOWS[workflow_name]
    if not candidates_path.exists():
        st.error(f"Не найден файл кандидатов: {candidates_path}")
        st.stop()

    candidates_payload, labels, review_state = load_payloads(candidates_path, review_state_path)

    query_ids = [query["id"] for query in candidates_payload["queries"]]
    query_by_id = {query["id"]: query for query in candidates_payload["queries"]}

    st.title("Eval V2 Human Review")
    st.caption(f"Workflow: {workflow_name}")

    query_filter_mode = st.sidebar.selectbox("Фильтр запросов", QUERY_FILTER_MODES, index=0)
    candidate_view_mode = st.sidebar.selectbox("Просмотр кандидатов", CANDIDATE_VIEW_MODES, index=0)
    filtered_ids = filter_query_ids(candidates_payload, review_state, query_filter_mode)
    if not filtered_ids:
        filtered_ids = query_ids

    current_query_id = st.sidebar.selectbox("Query", filtered_ids, format_func=lambda query_id: query_label(query_by_id[query_id]))
    query_item = query_by_id[current_query_id]
    query_state = review_state["queries"][current_query_id]
    current_candidate_ids = [candidate["ld_id"] for candidate in query_item["candidates"]]

    candidates = query_item["candidates"]
    current_index = min(query_state.get("cursor_index", 0), max(len(candidates) - 1, 0)) if candidates else 0
    if query_state.get("cursor_index", 0) != current_index:
        review_state = set_query_cursor(review_state, current_query_id, current_index)
        save_review_state(review_state_path, review_state)
        st.rerun()
        return

    total_candidates = sum(len(query["candidates"]) for query in candidates_payload["queries"])
    total_reviewed_candidates = sum(
        get_reviewed_candidate_count_for_ids(
            review_state["queries"].get(query_data["id"], {}),
            [candidate["ld_id"] for candidate in query_data["candidates"]],
        )
        for query_data in candidates_payload["queries"]
    )
    current_progress = render_query_progress(query_item, query_state, total_reviewed_candidates, total_candidates)
    render_query_text(query_item)

    visible_candidates = build_visible_candidates(candidates, query_state, candidate_view_mode)
    visible_candidate_position = resolve_visible_candidate_index(visible_candidates, current_index)

    if not candidates:
        st.warning("У query нет кандидатов для ревью.")
    elif visible_candidate_position is None:
        st.warning("В выбранном режиме нет кандидатов для показа.")
    else:
        candidate_full_index, candidate = visible_candidates[visible_candidate_position]
        st.caption(f"Кандидат {candidate_full_index + 1} / {len(candidates)}")
        render_candidate_card(candidate, query_state)

        comment_key = f"{workflow_name}::candidate-comment::{current_query_id}::{candidate['ld_id']}"
        existing_comment = query_state.get("candidate_grades", {}).get(str(candidate["ld_id"]), {}).get("comment", "")
        if comment_key not in st.session_state:
            st.session_state[comment_key] = existing_comment
        candidate_comment = st.text_area("Комментарий", key=comment_key, height=70, disabled=query_state.get("completed", False))

        action_cols = st.columns(4)
        candidate_buttons_disabled = query_state.get("completed", False)
        if action_cols[0].button("✅ Подходит", use_container_width=True, disabled=candidate_buttons_disabled):
            review_state = save_candidate_grade(
                review_state,
                current_query_id,
                candidate,
                "ACCEPT",
                candidate_comment,
                candidates,
                candidate_full_index,
                review_state_path,
            )
            st.rerun()
        if action_cols[1].button("❌ Не подходит", use_container_width=True, disabled=candidate_buttons_disabled):
            review_state = save_candidate_grade(
                review_state,
                current_query_id,
                candidate,
                "REJECT",
                candidate_comment,
                candidates,
                candidate_full_index,
                review_state_path,
            )
            st.rerun()
        if action_cols[2].button("⚠️ Сомневаюсь", use_container_width=True, disabled=candidate_buttons_disabled):
            review_state = save_candidate_grade(
                review_state,
                current_query_id,
                candidate,
                "UNSURE",
                candidate_comment,
                candidates,
                candidate_full_index,
                review_state_path,
            )
            st.rerun()
        if action_cols[3].button("⏭ Пропустить", use_container_width=True, disabled=candidate_buttons_disabled):
            review_state = save_candidate_grade(
                review_state,
                current_query_id,
                candidate,
                "SKIP",
                candidate_comment,
                candidates,
                candidate_full_index,
                review_state_path,
            )
            st.rerun()

        render_navigation(review_state, current_query_id, visible_candidates, visible_candidate_position, review_state_path)

    updated_review_state = load_review_state(review_state_path)
    render_query_finalization(
        updated_review_state,
        labels,
        workflow_name,
        query_item,
        updated_review_state["queries"][current_query_id],
        review_state_path,
    )

    current_accept_ids = get_accepted_candidate_ids_for_ids(
        updated_review_state["queries"][current_query_id],
        [candidate["ld_id"] for candidate in candidates],
    )
    st.write(
        f"Status: {updated_review_state['queries'][current_query_id].get('final_status') or 'UNREVIEWED'} | "
        f"Completed: {updated_review_state['queries'][current_query_id].get('completed', False)} | "
        f"Accepted IDs: {current_accept_ids}"
    )


if __name__ == "__main__":
    main()
