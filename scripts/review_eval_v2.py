from __future__ import annotations

import json
import sys
from pathlib import Path

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


def load_payloads() -> tuple[dict, dict, dict]:
    candidates = json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))
    labels = json.loads(LABELS_PATH.read_text(encoding="utf-8")) if LABELS_PATH.exists() else {}
    review_state = initialize_review_state(item["id"] for item in candidates["queries"])
    loaded_state = load_review_state(REVIEW_STATE_PATH)
    review_state.update({key: value for key, value in loaded_state.items() if key != "queries"})
    review_state["queries"].update(loaded_state.get("queries", {}))
    return candidates, labels, review_state


def query_summary(query_item: dict, query_state: dict) -> str:
    progress = get_query_progress(query_state, total_candidates=len(query_item["candidates"]))
    return (
        f"{query_item['id']} | "
        f"{progress['reviewed_candidates']}/{progress['total_candidates']} reviewed | "
        f"{progress['accepted']} accept, {progress['rejected']} reject, {progress['unsure']} unsure, {progress['skipped']} skip"
    )


def filter_query_ids(candidates_payload: dict, review_state: dict, mode: str) -> list[str]:
    result: list[str] = []
    for query in candidates_payload["queries"]:
        query_id = query["id"]
        query_state = review_state["queries"].get(query_id, {})
        final_status = query_state.get("final_status", "UNREVIEWED")
        has_unsure = any(item.get("grade") == "UNSURE" for item in query_state.get("candidate_grades", {}).values())
        if mode == "Все":
            result.append(query_id)
        elif mode == "Неразмеченные" and final_status == "UNREVIEWED":
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


def format_candidate(candidate: dict) -> str:
    lines = [
        f"**{candidate['name']}**",
        f"ld_id: `{candidate['ld_id']}` | article: `{candidate.get('article') or ''}`",
        f"DN: `{candidate.get('dn') or '—'}` | PN: `{candidate.get('pn') or '—'}` | type: `{candidate.get('joining_type') or '—'}`",
        f"dense_rank: `{candidate.get('dense_rank')}` | bm25_rank: `{candidate.get('bm25_rank')}` | hybrid_rank: `{candidate.get('hybrid_rank')}`",
        f"dense_score: `{candidate.get('dense_score')}` | bm25_score: `{candidate.get('bm25_score')}` | rrf_score: `{candidate.get('rrf_score')}`",
    ]
    if candidate.get("technical_properties"):
        lines.append("Properties:")
        for prop_name, values in candidate["technical_properties"].items():
            lines.append(f"- {prop_name}: {', '.join(values)}")
    return "\n".join(lines)


def update_labels_file(review_state: dict, labels: dict, query_id: str) -> None:
    updated = json.loads(json.dumps(labels, ensure_ascii=False))
    updated = apply_review_state_to_labels(updated, query_id, review_state)
    LABELS_PATH.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")


def move_cursor(review_state: dict, query_id: str, delta: int, candidate_count: int) -> dict:
    current = review_state["queries"].get(query_id, {}).get("cursor_index", 0)
    next_index = max(0, min(candidate_count - 1, current + delta)) if candidate_count else 0
    return set_query_cursor(review_state, query_id, next_index)


def main() -> None:
    st.set_page_config(page_title="Eval V2 Human Review", layout="wide")
    candidates_payload, labels, review_state = load_payloads()
    query_ids = [query["id"] for query in candidates_payload["queries"]]

    mode = st.sidebar.selectbox("Фильтр", FILTER_MODES, index=0)
    filtered_ids = filter_query_ids(candidates_payload, review_state, mode)
    if not filtered_ids:
        filtered_ids = query_ids

    query_labels = {
        query["id"]: f"{query['id']} — {query['query'][:96]}{'…' if len(query['query']) > 96 else ''}"
        for query in candidates_payload["queries"]
    }
    current_query_id = st.sidebar.selectbox("Query", filtered_ids, format_func=lambda query_id: query_labels[query_id])
    query_item = next(query for query in candidates_payload["queries"] if query["id"] == current_query_id)
    query_state = review_state["queries"][current_query_id]
    candidates = query_item["candidates"]
    cursor_index = min(query_state.get("cursor_index", 0), max(len(candidates) - 1, 0))
    if query_state.get("cursor_index", 0) != cursor_index:
        review_state = set_query_cursor(review_state, current_query_id, cursor_index)
        save_review_state(REVIEW_STATE_PATH, review_state)
        st.rerun()
        return

    st.title("Eval V2 human review")
    st.caption(query_summary(query_item, query_state))

    global_summary = {
        "MATCHED": 0,
        "NOT_FOUND": 0,
        "RETRIEVAL_MISS": 0,
        "UNREVIEWED": 0,
    }
    for qid in query_ids:
        status = review_state["queries"][qid]["final_status"]
        global_summary[status] = global_summary.get(status, 0) + 1
    stats = st.columns(4)
    stats[0].metric("Matched", global_summary["MATCHED"])
    stats[1].metric("Not found", global_summary["NOT_FOUND"])
    stats[2].metric("Retrieval miss", global_summary["RETRIEVAL_MISS"])
    stats[3].metric("Unreviewed", global_summary["UNREVIEWED"])

    st.subheader("Query")
    st.write(query_item["query"])

    if not candidates:
        st.warning("У query нет кандидатов для ревью")
    else:
        candidate = candidates[cursor_index]
        st.subheader(f"Candidate {cursor_index + 1}/{len(candidates)}")
        st.markdown(format_candidate(candidate))
        current_grade = query_state.get("candidate_grades", {}).get(str(candidate["ld_id"]))
        if current_grade:
            st.info(f"Current grade: {current_grade['grade']}")

        action_cols = st.columns(4)
        if action_cols[0].button("ACCEPT", use_container_width=True):
            review_state = set_candidate_grade(review_state, current_query_id, candidate["ld_id"], "ACCEPT")
            review_state = move_cursor(review_state, current_query_id, 1, len(candidates))
            save_review_state(REVIEW_STATE_PATH, review_state)
            st.rerun()
        if action_cols[1].button("REJECT", use_container_width=True):
            review_state = set_candidate_grade(review_state, current_query_id, candidate["ld_id"], "REJECT")
            review_state = move_cursor(review_state, current_query_id, 1, len(candidates))
            save_review_state(REVIEW_STATE_PATH, review_state)
            st.rerun()
        if action_cols[2].button("UNSURE", use_container_width=True):
            review_state = set_candidate_grade(review_state, current_query_id, candidate["ld_id"], "UNSURE")
            save_review_state(REVIEW_STATE_PATH, review_state)
            st.rerun()
        if action_cols[3].button("SKIP", use_container_width=True):
            review_state = set_candidate_grade(review_state, current_query_id, candidate["ld_id"], "SKIP")
            review_state = move_cursor(review_state, current_query_id, 1, len(candidates))
            save_review_state(REVIEW_STATE_PATH, review_state)
            st.rerun()

        nav_cols = st.columns(2)
        if nav_cols[0].button("Prev candidate", use_container_width=True, disabled=cursor_index <= 0):
            review_state = move_cursor(review_state, current_query_id, -1, len(candidates))
            save_review_state(REVIEW_STATE_PATH, review_state)
            st.rerun()
        if nav_cols[1].button("Next candidate", use_container_width=True, disabled=cursor_index >= len(candidates) - 1):
            review_state = move_cursor(review_state, current_query_id, 1, len(candidates))
            save_review_state(REVIEW_STATE_PATH, review_state)
            st.rerun()

    review_state = load_review_state(REVIEW_STATE_PATH)
    query_state = review_state["queries"][current_query_id]
    accepted_ids = []
    if candidates:
        accepted_ids = [candidate["ld_id"] for candidate in candidates if query_state.get("candidate_grades", {}).get(str(candidate["ld_id"]), {}).get("grade") == "ACCEPT"]

    st.subheader("Query finalization")
    final_comment = st.text_area("Комментарий к query", value=query_state.get("final_comment", ""), key=f"comment::{current_query_id}")
    confirm_not_found = st.checkbox("Подтверждаю, что подходящего кандидата нет", key=f"confirm::{current_query_id}")

    final_cols = st.columns(3)
    matched_disabled = not accepted_ids
    if final_cols[0].button("MATCHED", use_container_width=True, disabled=matched_disabled):
        review_state = finalize_query(review_state, current_query_id, "MATCHED", comment=final_comment)
        save_review_state(REVIEW_STATE_PATH, review_state)
        update_labels_file(review_state, labels, current_query_id)
        st.rerun()
    if final_cols[1].button("NOT_FOUND", use_container_width=True, disabled=not confirm_not_found):
        review_state = finalize_query(review_state, current_query_id, "NOT_FOUND", comment=final_comment, confirmed=True)
        save_review_state(REVIEW_STATE_PATH, review_state)
        update_labels_file(review_state, labels, current_query_id)
        st.rerun()
    if final_cols[2].button("RETRIEVAL_MISS", use_container_width=True):
        review_state = finalize_query(review_state, current_query_id, "RETRIEVAL_MISS", comment=final_comment)
        save_review_state(REVIEW_STATE_PATH, review_state)
        st.rerun()

    if st.button("Переоткрыть query", use_container_width=True):
        review_state = reopen_query(review_state, current_query_id)
        save_review_state(REVIEW_STATE_PATH, review_state)
        update_labels_file(review_state, labels, current_query_id)
        st.rerun()

    current_progress = get_query_progress(query_state, total_candidates=len(candidates))
    st.write(
        f"Status: {current_progress['final_status']} | "
        f"Completed: {current_progress['completed']} | "
        f"Accepted IDs: {accepted_ids}"
    )


if __name__ == "__main__":
    main()
