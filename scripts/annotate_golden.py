from __future__ import annotations

import html
import json
import sys
from pathlib import Path
from typing import Any

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nomenclature_matcher.review_state import (
    default_query_review_state,
    initialize_review_state,
    load_review_state,
    save_review_state,
)


ROOT = Path(__file__).resolve().parents[1]

WORKFLOW_SPECS = {
    "Golden 100": {
        "candidates": ROOT / "data" / "golden_100_review_candidates.json",
        "kind": "review_candidates",
        "labels": ROOT / "data" / "golden_100_labels.json",
        "state": ROOT / "data" / "golden_100_human_review.json",
    },
    "Baseline q01-q11": {
        "candidates": ROOT / "data" / "eval_results.json",
        "kind": "eval_results",
        "labels": ROOT / "data" / "eval_labels.json",
        "state": ROOT / "data" / "eval_human_review.json",
    },
    "Eval V2": {
        "candidates": ROOT / "data" / "eval_v2_review_candidates.json",
        "kind": "review_candidates",
        "labels": ROOT / "data" / "eval_labels_v2.json",
        "state": ROOT / "data" / "eval_v2_human_review.json",
    },
    "Eval V2 expanded": {
        "candidates": ROOT / "data" / "eval_v2_retrieval_miss_candidates.json",
        "kind": "review_candidates",
        "labels": ROOT / "data" / "eval_labels_v2.json",
        "state": ROOT / "data" / "eval_v2_retrieval_miss_human_review.json",
    },
    "Golden generated (legacy)": {
        "candidates": ROOT / "data" / "golden_review_candidates.json",
        "kind": "review_candidates",
        "labels": ROOT / "data" / "golden_labels.json",
        "state": ROOT / "data" / "golden_human_review.json",
    },
}


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _merge_eval_result_candidates(item: dict[str, Any]) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    selected_ids = {
        int(row["ld_id"])
        for row in item.get("deepseek_result", {}).get("selected", [])
        if row.get("ld_id") is not None
    }
    source_specs = (
        ("hybrid_top20", "hybrid", "hybrid_rank", "rrf_score"),
        ("dense_top20", "dense", "dense_rank", "dense_score"),
        ("bm25_top20", "bm25", "bm25_rank", "bm25_score"),
    )
    for key, source, rank_field, score_field in source_specs:
        for position, candidate in enumerate(item.get(key, []), 1):
            ld_id = int(candidate["ld_id"])
            row = merged.setdefault(
                ld_id,
                {
                    "ld_id": ld_id,
                    "article": candidate.get("article"),
                    "name": candidate.get("name", ""),
                    "dn": candidate.get("dn"),
                    "pn": candidate.get("pn"),
                    "joining_type": candidate.get("joining_type"),
                    "dense_rank": None,
                    "bm25_rank": None,
                    "hybrid_rank": None,
                    "dense_score": None,
                    "bm25_score": None,
                    "rrf_score": None,
                    "retrieval_sources": [],
                    "technical_properties": {},
                    "llm_selected": ld_id in selected_ids,
                },
            )
            row["article"] = row.get("article") or candidate.get("article")
            row["name"] = row.get("name") or candidate.get("name", "")
            row[rank_field] = candidate.get(rank_field, position)
            row[score_field] = candidate.get(score_field)
            row["dense_rank"] = candidate.get("dense_rank", row.get("dense_rank"))
            row["dense_score"] = candidate.get("dense_score", row.get("dense_score"))
            row["bm25_rank"] = candidate.get("bm25_rank", row.get("bm25_rank"))
            row["bm25_score"] = candidate.get("bm25_score", row.get("bm25_score"))
            row["rrf_score"] = candidate.get("rrf_score", row.get("rrf_score"))
            if source not in row["retrieval_sources"]:
                row["retrieval_sources"].append(source)

    def sort_key(row: dict[str, Any]) -> tuple[int, int, int, int, int]:
        hybrid = row.get("hybrid_rank") or 999
        dense = row.get("dense_rank") or 999
        bm25 = row.get("bm25_rank") or 999
        return (0 if hybrid != 999 else 1, min(hybrid, dense, bm25), hybrid, dense, bm25)

    return sorted(merged.values(), key=sort_key)


def _load_queries(path: Path, kind: str) -> list[dict[str, Any]]:
    payload = _read_json(path, {})
    if kind == "review_candidates":
        return payload.get("queries", [])
    if kind == "eval_results":
        return [
            {
                "id": item["id"],
                "query": item["query"],
                "metadata": {},
                "candidates": _merge_eval_result_candidates(item),
            }
            for item in payload.get("results", [])
        ]
    raise ValueError(f"Unsupported candidate payload kind: {kind}")


def _format_value(value: Any) -> str:
    if value in (None, ""):
        return "—"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def _technical_summary(candidate: dict[str, Any]) -> str:
    props = candidate.get("technical_properties") or {}
    preferred = (
        "Тип продукта",
        "Материал корпуса",
        "Тип резьбы",
        "Рабочая среда",
        "Управление",
        "Серия",
    )
    parts = []
    for key in preferred:
        value = props.get(key)
        if value not in (None, "", []):
            parts.append(f"{key}: {_format_value(value)}")
    return " · ".join(parts)


def _candidate_checkbox_key(workflow: str, query_id: str, ld_id: int) -> str:
    return f"golden::{workflow}::{query_id}::{ld_id}::accept"


def _existing_accepted_ids(
    labels: dict[str, Any], review_state: dict[str, Any], query_id: str
) -> set[int]:
    query_state = review_state.get("queries", {}).get(query_id, {})
    accepted = {
        int(candidate_id)
        for candidate_id, grade in query_state.get("candidate_grades", {}).items()
        if grade.get("grade") == "ACCEPT"
    }
    if accepted:
        return accepted
    label = labels.get(query_id, {})
    if label.get("label_status") == "VERIFIED" and label.get("expected_status") == "MATCHED":
        return {int(candidate_id) for candidate_id in label.get("acceptable_ld_ids", [])}
    return set()


def _query_status(labels: dict[str, Any], review_state: dict[str, Any], query_id: str) -> str:
    query_state = review_state.get("queries", {}).get(query_id, {})
    if query_state.get("final_status"):
        return str(query_state["final_status"])
    label = labels.get(query_id, {})
    if label.get("label_status") == "VERIFIED":
        return str(label.get("expected_status") or "VERIFIED")
    return "UNREVIEWED"


def _current_selected_ids(
    workflow: str,
    query_id: str,
    candidates: list[dict[str, Any]],
    existing_accept: set[int],
) -> set[int]:
    return {
        int(candidate["ld_id"])
        for candidate in candidates
        if st.session_state.get(
            _candidate_checkbox_key(workflow, query_id, int(candidate["ld_id"])),
            int(candidate["ld_id"]) in existing_accept,
        )
    }


def _save_query(
    *,
    labels_path: Path,
    state_path: Path,
    labels: dict[str, Any],
    review_state: dict[str, Any],
    query_id: str,
    candidates: list[dict[str, Any]],
    selected_ids: set[int],
    status: str,
    comment: str,
) -> None:
    state = review_state
    state.setdefault("queries", {})
    query_state = state["queries"].setdefault(query_id, default_query_review_state())

    current_ids = {int(candidate["ld_id"]) for candidate in candidates}
    grades = query_state.setdefault("candidate_grades", {})
    for candidate_id in current_ids:
        key = str(candidate_id)
        existing_comment = grades.get(key, {}).get("comment", "")
        grades[key] = {
            "grade": "ACCEPT" if candidate_id in selected_ids else "REJECT",
            "comment": existing_comment,
        }

    query_state["final_status"] = status
    query_state["final_comment"] = comment or ""
    query_state["completed"] = status in {"MATCHED", "NOT_FOUND", "RETRIEVAL_MISS"}
    save_review_state(state_path, state)

    if status == "MATCHED":
        labels[query_id] = {
            "label_status": "VERIFIED",
            "acceptable_ld_ids": sorted(selected_ids),
            "expected_status": "MATCHED",
            "human_comment": comment or "",
        }
    elif status == "NOT_FOUND":
        labels[query_id] = {
            "label_status": "VERIFIED",
            "acceptable_ld_ids": [],
            "expected_status": "NOT_FOUND",
            "human_comment": comment or "",
        }
    else:
        labels[query_id] = {
            "label_status": "UNREVIEWED",
            "acceptable_ld_ids": [],
            "expected_status": None,
            "human_comment": comment or "",
        }
    _write_json(labels_path, labels)


def _render_sticky_query(query_id: str, query_text: str, status: str) -> None:
    safe_id = html.escape(query_id)
    safe_query = html.escape(query_text)
    safe_status = html.escape(status)
    st.markdown(
        """
        <style>
        .golden-sticky-query {
            position: sticky;
            top: 2.85rem;
            z-index: 1000;
            background: rgba(14, 17, 23, 0.96);
            border: 1px solid rgba(250, 250, 250, 0.18);
            border-left: 5px solid #ff4b4b;
            border-radius: 0.65rem;
            padding: 0.8rem 1rem;
            margin: 0.35rem 0 0.85rem 0;
            backdrop-filter: blur(8px);
            box-shadow: 0 4px 18px rgba(0, 0, 0, 0.24);
        }
        .golden-sticky-query .qid {
            opacity: 0.7;
            font-size: 0.78rem;
            margin-bottom: 0.2rem;
        }
        .golden-sticky-query .qtext {
            font-size: 1.05rem;
            font-weight: 700;
            line-height: 1.35;
        }
        .golden-sticky-query .qstatus {
            opacity: 0.75;
            font-size: 0.78rem;
            margin-top: 0.3rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        (
            '<div class="golden-sticky-query">'
            f'<div class="qid">{safe_id}</div>'
            f'<div class="qtext">{safe_query}</div>'
            f'<div class="qstatus">Статус: {safe_status}</div>'
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def _render_candidate(
    workflow: str,
    query_id: str,
    candidate: dict[str, Any],
    default_selected: bool,
) -> None:
    ld_id = int(candidate["ld_id"])
    key = _candidate_checkbox_key(workflow, query_id, ld_id)
    rank_bits = []
    for label, field in (("H", "hybrid_rank"), ("D", "dense_rank"), ("B", "bm25_rank")):
        if candidate.get(field) is not None:
            rank_bits.append(f"{label}#{candidate[field]}")
    rank_text = " · ".join(rank_bits) or "без rank"
    llm_badge = " · 🤖 LLM выбрал" if candidate.get("llm_selected") else ""

    left, right = st.columns([0.07, 0.93])
    with left:
        st.checkbox("OK", value=default_selected, key=key, label_visibility="collapsed")
    with right:
        st.markdown(f"**{candidate.get('name') or 'Без названия'}**")
        st.caption(
            f"LD {ld_id} · {candidate.get('article') or 'без артикула'} · {rank_text}{llm_badge}"
        )
        st.write(
            f"DN: {_format_value(candidate.get('dn'))} · "
            f"PN: {_format_value(candidate.get('pn'))} · "
            f"Присоединение: {_format_value(candidate.get('joining_type'))}"
        )
        technical = _technical_summary(candidate)
        if technical:
            st.caption(technical)
        with st.expander("Retrieval details"):
            st.json(
                {
                    "dense_rank": candidate.get("dense_rank"),
                    "dense_score": candidate.get("dense_score"),
                    "bm25_rank": candidate.get("bm25_rank"),
                    "bm25_score": candidate.get("bm25_score"),
                    "hybrid_rank": candidate.get("hybrid_rank"),
                    "rrf_score": candidate.get("rrf_score"),
                    "retrieval_sources": candidate.get("retrieval_sources", []),
                    "technical_properties": candidate.get("technical_properties", {}),
                }
            )


def main() -> None:
    st.set_page_config(page_title="Golden Dataset Annotator", layout="wide")
    st.title("Golden Dataset Annotator")
    st.caption("Запрос закреплён сверху: при прокрутке кандидатов он остаётся перед глазами.")

    available = {
        name: spec
        for name, spec in WORKFLOW_SPECS.items()
        if Path(spec["candidates"]).exists()
    }
    if not available:
        st.error(
            "Не найдено ни одного файла кандидатов. "
            "Для Golden 100 сначала запусти: python scripts/prepare_review_candidates.py"
        )
        st.stop()

    workflow = st.sidebar.selectbox("Dataset / workflow", list(available))
    spec = available[workflow]
    candidates_path = Path(spec["candidates"])
    labels_path = Path(spec["labels"])
    state_path = Path(spec["state"])

    queries = _load_queries(candidates_path, str(spec["kind"]))
    labels = _read_json(labels_path, {}) or {}
    review_state = initialize_review_state(item["id"] for item in queries)
    if state_path.exists():
        loaded = load_review_state(state_path)
        review_state["queries"].update(loaded.get("queries", {}))

    query_by_id = {item["id"]: item for item in queries}
    statuses = {
        query_id: _query_status(labels, review_state, query_id)
        for query_id in query_by_id
    }
    verified = sum(
        1 for query_id in query_by_id
        if labels.get(query_id, {}).get("label_status") == "VERIFIED"
    )
    st.sidebar.metric("Verified", f"{verified} / {len(queries)}")

    filter_mode = st.sidebar.selectbox(
        "Фильтр",
        ["Неразмеченные", "Все", "MATCHED", "NOT_FOUND", "RETRIEVAL_MISS"],
    )
    if filter_mode == "Все":
        visible_ids = list(query_by_id)
    elif filter_mode == "Неразмеченные":
        visible_ids = [
            query_id for query_id, status in statuses.items()
            if status == "UNREVIEWED"
        ]
    else:
        visible_ids = [
            query_id for query_id, status in statuses.items()
            if status == filter_mode
        ]
    if not visible_ids:
        st.sidebar.success("В этом фильтре всё размечено 🎉")
        visible_ids = list(query_by_id)

    def query_title(query_id: str) -> str:
        text = query_by_id[query_id]["query"]
        return f"[{statuses[query_id]}] {query_id} — {text[:90]}"

    query_id = st.sidebar.selectbox("Query", visible_ids, format_func=query_title)
    item = query_by_id[query_id]
    candidates = item.get("candidates", [])
    existing_accept = _existing_accepted_ids(labels, review_state, query_id)
    status = statuses[query_id]
    label = labels.get(query_id, {})

    _render_sticky_query(query_id, item["query"], status)

    if status != "UNREVIEWED":
        st.info(
            f"Текущий статус: {status}. "
            f"acceptable_ld_ids: {label.get('acceptable_ld_ids', sorted(existing_accept))}"
        )
    if label.get("human_comment"):
        st.caption(f"Предыдущий комментарий: {label['human_comment']}")

    search = st.text_input(
        "Фильтр кандидатов по названию / артикулу / LD ID", ""
    ).strip().lower()
    visible_candidates = []
    for candidate in candidates:
        haystack = " ".join(
            [
                str(candidate.get("ld_id", "")),
                str(candidate.get("article", "")),
                str(candidate.get("name", "")),
                _technical_summary(candidate),
            ]
        ).lower()
        if not search or search in haystack:
            visible_candidates.append(candidate)

    selected_ids = _current_selected_ids(
        workflow, query_id, candidates, existing_accept
    )
    summary_cols = st.columns(3)
    summary_cols[0].metric("Выбрано", len(selected_ids))
    summary_cols[1].metric("Кандидатов", len(candidates))
    summary_cols[2].metric("Статус", status)

    st.subheader(f"Кандидаты: {len(visible_candidates)} / {len(candidates)}")
    st.caption("Поставь галочки у ВСЕХ товаров, которые считаешь допустимым правильным ответом.")

    for candidate in visible_candidates:
        _render_candidate(
            workflow,
            query_id,
            candidate,
            int(candidate["ld_id"]) in existing_accept,
        )
        st.divider()

    selected_ids = _current_selected_ids(
        workflow, query_id, candidates, existing_accept
    )

    st.subheader("Завершить query")
    st.markdown(f"**Запрос:** {item['query']}")
    default_comment = (
        label.get("human_comment", "")
        or review_state.get("queries", {}).get(query_id, {}).get("final_comment", "")
    )
    comment_key = f"golden::{workflow}::{query_id}::comment"
    if comment_key not in st.session_state:
        st.session_state[comment_key] = default_comment
    comment = st.text_area("Комментарий к query", key=comment_key, height=100)
    confirm_key = f"golden::{workflow}::{query_id}::confirm_not_found"
    confirm_not_found = st.checkbox(
        "Для NOT_FOUND подтверждаю: подходящего товара нет во всём каталоге, "
        "а не только среди показанных кандидатов.",
        key=confirm_key,
    )

    matched_col, miss_col, not_found_col, draft_col = st.columns(4)
    if matched_col.button(
        "✅ Сохранить MATCHED",
        use_container_width=True,
        disabled=not selected_ids,
    ):
        _save_query(
            labels_path=labels_path,
            state_path=state_path,
            labels=labels,
            review_state=review_state,
            query_id=query_id,
            candidates=candidates,
            selected_ids=selected_ids,
            status="MATCHED",
            comment=comment,
        )
        st.rerun()

    if miss_col.button(
        "⚠️ RETRIEVAL_MISS",
        use_container_width=True,
        disabled=bool(selected_ids),
    ):
        _save_query(
            labels_path=labels_path,
            state_path=state_path,
            labels=labels,
            review_state=review_state,
            query_id=query_id,
            candidates=candidates,
            selected_ids=set(),
            status="RETRIEVAL_MISS",
            comment=comment or "No acceptable item in the shown retrieval pool; expand candidate search.",
        )
        st.rerun()

    if not_found_col.button(
        "❌ Сохранить NOT_FOUND",
        use_container_width=True,
        disabled=bool(selected_ids) or not confirm_not_found,
    ):
        _save_query(
            labels_path=labels_path,
            state_path=state_path,
            labels=labels,
            review_state=review_state,
            query_id=query_id,
            candidates=candidates,
            selected_ids=set(),
            status="NOT_FOUND",
            comment=comment,
        )
        st.rerun()

    if draft_col.button("💾 Черновик", use_container_width=True):
        _save_query(
            labels_path=labels_path,
            state_path=state_path,
            labels=labels,
            review_state=review_state,
            query_id=query_id,
            candidates=candidates,
            selected_ids=selected_ids,
            status="UNREVIEWED",
            comment=comment,
        )
        st.rerun()

    st.caption(
        f"Файлы: candidates={candidates_path.relative_to(ROOT)} · "
        f"labels={labels_path.relative_to(ROOT)} · state={state_path.relative_to(ROOT)}"
    )


if __name__ == "__main__":
    main()