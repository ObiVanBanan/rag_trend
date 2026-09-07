from __future__ import annotations

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


def _technical_rows(candidate: dict[str, Any]) -> list[tuple[str, str]]:
    props = candidate.get("technical_properties") or {}
    rows = [
        ("Тип", props.get("Тип продукта")),
        ("Материал", props.get("Материал корпуса")),
        ("DN", candidate.get("dn") or props.get("Номинальный диаметр, DN")),
        ("PN", candidate.get("pn") or props.get("Номинальное давление, МПа")),
        ("Присоединение", candidate.get("joining_type") or props.get("Присоединение")),
        ("Резьба", props.get("Тип резьбы")),
        ("Среда", props.get("Рабочая среда")),
        ("Управление", props.get("Управление")),
        ("Серия", props.get("Серия")),
    ]
    return [(label, _format_value(value)) for label, value in rows if value not in (None, "", [])]


def _query_state(review_state: dict[str, Any], query_id: str) -> dict[str, Any]:
    return review_state.setdefault("queries", {}).setdefault(query_id, default_query_review_state())


def _accepted_ids(query_state: dict[str, Any]) -> list[int]:
    return sorted(
        int(candidate_id)
        for candidate_id, grade in query_state.get("candidate_grades", {}).items()
        if grade.get("grade") == "ACCEPT"
    )


def _reviewed_count(query_state: dict[str, Any], candidates: list[dict[str, Any]]) -> int:
    candidate_ids = {str(candidate["ld_id"]) for candidate in candidates}
    return sum(1 for candidate_id in query_state.get("candidate_grades", {}) if candidate_id in candidate_ids)


def _candidate_grade(query_state: dict[str, Any], candidate_id: int) -> str | None:
    return query_state.get("candidate_grades", {}).get(str(candidate_id), {}).get("grade")


def _next_unreviewed_index(
    candidates: list[dict[str, Any]], query_state: dict[str, Any], current_index: int
) -> int | None:
    if not candidates:
        return None
    graded_ids = set(query_state.get("candidate_grades", {}))
    for index in range(current_index + 1, len(candidates)):
        if str(candidates[index]["ld_id"]) not in graded_ids:
            return index
    for index in range(0, current_index + 1):
        if str(candidates[index]["ld_id"]) not in graded_ids:
            return index
    return None


def _save_candidate_grade(
    review_state: dict[str, Any],
    state_path: Path,
    query_id: str,
    candidates: list[dict[str, Any]],
    current_index: int,
    grade: str,
    comment: str,
) -> None:
    query_state = _query_state(review_state, query_id)
    candidate = candidates[current_index]
    query_state.setdefault("candidate_grades", {})[str(candidate["ld_id"])] = {
        "grade": grade,
        "comment": comment or "",
    }
    next_index = _next_unreviewed_index(candidates, query_state, current_index)
    query_state["cursor_index"] = current_index if next_index is None else next_index
    save_review_state(state_path, review_state)


def _save_final_label(
    labels: dict[str, Any],
    labels_path: Path,
    review_state: dict[str, Any],
    state_path: Path,
    query_id: str,
    status: str,
    comment: str,
) -> None:
    query_state = _query_state(review_state, query_id)
    accepted_ids = _accepted_ids(query_state)
    query_state["final_status"] = status
    query_state["final_comment"] = comment or ""
    query_state["completed"] = status in {"MATCHED", "NOT_FOUND", "RETRIEVAL_MISS"}
    save_review_state(state_path, review_state)

    if status == "MATCHED":
        labels[query_id] = {
            "label_status": "VERIFIED",
            "acceptable_ld_ids": accepted_ids,
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


def _seed_state_from_label(
    labels: dict[str, Any], review_state: dict[str, Any], query_id: str
) -> None:
    query_state = _query_state(review_state, query_id)
    if query_state.get("candidate_grades") or query_state.get("final_status"):
        return
    label = labels.get(query_id, {})
    if label.get("label_status") != "VERIFIED":
        return
    if label.get("expected_status") == "MATCHED":
        for candidate_id in label.get("acceptable_ld_ids", []):
            query_state.setdefault("candidate_grades", {})[str(candidate_id)] = {
                "grade": "ACCEPT",
                "comment": "Imported from existing label",
            }
    query_state["final_status"] = label.get("expected_status")
    query_state["final_comment"] = label.get("human_comment", "")
    query_state["completed"] = True


def _render_query_card(item: dict[str, Any], query_state: dict[str, Any]) -> None:
    metadata = item.get("metadata") or {}
    status = query_state.get("final_status") or "UNREVIEWED"
    st.markdown("### Запрос")
    st.info(item["query"], icon="🔎")
    meta_bits = [f"ID: {item['id']}", f"Статус: {status}"]
    if metadata.get("category"):
        meta_bits.append(f"Категория: {metadata['category']}")
    if metadata.get("difficulty"):
        meta_bits.append(f"Сложность: {metadata['difficulty']}")
    st.caption(" · ".join(meta_bits))


def _render_candidate_card(candidate: dict[str, Any], grade: str | None) -> None:
    st.markdown("### Кандидат LD")
    st.markdown(f"## {candidate.get('name') or 'Без названия'}")
    rank_bits = []
    for label, field in (("Hybrid", "hybrid_rank"), ("Dense", "dense_rank"), ("BM25", "bm25_rank")):
        if candidate.get(field) is not None:
            rank_bits.append(f"{label} #{candidate[field]}")
    st.caption(
        f"LD ID {candidate['ld_id']} · Артикул: {candidate.get('article') or '—'}"
        + (" · " + " · ".join(rank_bits) if rank_bits else "")
    )

    rows = _technical_rows(candidate)
    if rows:
        columns = st.columns(3)
        for index, (label, value) in enumerate(rows):
            columns[index % 3].markdown(f"**{label}:** {value}")

    if grade:
        st.info(f"Текущая оценка этого кандидата: **{grade}**")

    with st.expander("Технические детали / retrieval scores"):
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
    st.caption("Один экран = один тендерный запрос + один кандидат LD. Оцени кандидата и сразу переходи к следующему.")

    available = {
        name: spec
        for name, spec in WORKFLOW_SPECS.items()
        if Path(spec["candidates"]).exists()
    }
    if not available:
        st.error(
            "Не найдено файлов кандидатов. Для Golden 100 сначала запусти: "
            "python scripts/prepare_review_candidates.py"
        )
        st.stop()

    workflow = st.sidebar.selectbox("Dataset", list(available))
    spec = available[workflow]
    candidates_path = Path(spec["candidates"])
    labels_path = Path(spec["labels"])
    state_path = Path(spec["state"])

    queries = _load_queries(candidates_path, str(spec["kind"]))
    labels = _read_json(labels_path, {}) or {}
    review_state = initialize_review_state(item["id"] for item in queries)
    if state_path.exists():
        loaded_state = load_review_state(state_path)
        review_state["queries"].update(loaded_state.get("queries", {}))

    for item in queries:
        _seed_state_from_label(labels, review_state, item["id"])

    query_by_id = {item["id"]: item for item in queries}

    def query_status(query_id: str) -> str:
        return _query_state(review_state, query_id).get("final_status") or "UNREVIEWED"

    filter_mode = st.sidebar.selectbox(
        "Запросы",
        ["Неразмеченные", "Все", "MATCHED", "NOT_FOUND", "RETRIEVAL_MISS"],
    )
    if filter_mode == "Все":
        visible_ids = list(query_by_id)
    elif filter_mode == "Неразмеченные":
        visible_ids = [query_id for query_id in query_by_id if query_status(query_id) == "UNREVIEWED"]
    else:
        visible_ids = [query_id for query_id in query_by_id if query_status(query_id) == filter_mode]
    if not visible_ids:
        st.sidebar.success("В выбранном фильтре ничего не осталось 🎉")
        visible_ids = list(query_by_id)

    def query_label(query_id: str) -> str:
        item = query_by_id[query_id]
        return f"[{query_status(query_id)}] {query_id} — {item['query'][:72]}"

    query_id = st.sidebar.selectbox("Query", visible_ids, format_func=query_label)
    item = query_by_id[query_id]
    candidates = item.get("candidates", [])
    query_state = _query_state(review_state, query_id)
    reviewed = _reviewed_count(query_state, candidates)
    accepted_ids = _accepted_ids(query_state)

    verified_queries = sum(
        1
        for candidate_query_id in query_by_id
        if labels.get(candidate_query_id, {}).get("label_status") == "VERIFIED"
    )
    st.sidebar.metric("Verified queries", f"{verified_queries} / {len(queries)}")
    st.sidebar.metric("Кандидаты просмотрены", f"{reviewed} / {len(candidates)}")
    st.sidebar.metric("ACCEPT", len(accepted_ids))

    _render_query_card(item, query_state)

    if query_state.get("completed"):
        st.success(f"Query завершён: {query_state.get('final_status')}")
        if accepted_ids:
            st.write(f"Допустимые LD ID: {accepted_ids}")
        if query_state.get("final_comment"):
            st.caption(query_state["final_comment"])
        if st.button("↩️ Переоткрыть query", use_container_width=True):
            query_state["completed"] = False
            query_state["final_status"] = None
            query_state["final_comment"] = ""
            next_index = _next_unreviewed_index(candidates, query_state, -1)
            query_state["cursor_index"] = 0 if next_index is None else next_index
            save_review_state(state_path, review_state)
            st.rerun()
        st.stop()

    all_reviewed = reviewed >= len(candidates) if candidates else True

    if not all_reviewed:
        current_index = min(max(int(query_state.get("cursor_index", 0)), 0), len(candidates) - 1)
        if _candidate_grade(query_state, int(candidates[current_index]["ld_id"])) is not None:
            next_index = _next_unreviewed_index(candidates, query_state, current_index)
            if next_index is not None:
                current_index = next_index
                query_state["cursor_index"] = current_index
                save_review_state(state_path, review_state)

        candidate = candidates[current_index]
        candidate_id = int(candidate["ld_id"])
        grade = _candidate_grade(query_state, candidate_id)

        progress = reviewed / len(candidates) if candidates else 1.0
        st.progress(progress, text=f"Просмотрено {reviewed} из {len(candidates)} · ACCEPT: {len(accepted_ids)}")
        _render_candidate_card(candidate, grade)

        comment_key = f"candidate-comment::{workflow}::{query_id}::{candidate_id}"
        existing_comment = query_state.get("candidate_grades", {}).get(str(candidate_id), {}).get("comment", "")
        if comment_key not in st.session_state:
            st.session_state[comment_key] = existing_comment
        candidate_comment = st.text_input(
            "Комментарий к кандидату (необязательно)",
            key=comment_key,
            placeholder="Например: DN совпадает, но материал не тот",
        )

        accept_col, reject_col, unsure_col, skip_col = st.columns(4)
        if accept_col.button("✅ Подходит", type="primary", use_container_width=True):
            _save_candidate_grade(
                review_state, state_path, query_id, candidates, current_index, "ACCEPT", candidate_comment
            )
            st.rerun()
        if reject_col.button("❌ Не подходит", use_container_width=True):
            _save_candidate_grade(
                review_state, state_path, query_id, candidates, current_index, "REJECT", candidate_comment
            )
            st.rerun()
        if unsure_col.button("⚠️ Сомневаюсь", use_container_width=True):
            _save_candidate_grade(
                review_state, state_path, query_id, candidates, current_index, "UNSURE", candidate_comment
            )
            st.rerun()
        if skip_col.button("⏭ Пропустить", use_container_width=True):
            _save_candidate_grade(
                review_state, state_path, query_id, candidates, current_index, "SKIP", candidate_comment
            )
            st.rerun()

        nav_left, nav_right = st.columns(2)
        if nav_left.button("← предыдущий кандидат", use_container_width=True, disabled=current_index == 0):
            query_state["cursor_index"] = current_index - 1
            save_review_state(state_path, review_state)
            st.rerun()
        if nav_right.button(
            "следующий кандидат →",
            use_container_width=True,
            disabled=current_index >= len(candidates) - 1,
        ):
            query_state["cursor_index"] = current_index + 1
            save_review_state(state_path, review_state)
            st.rerun()
        st.stop()

    st.success(f"Все кандидаты просмотрены. ACCEPT: {len(accepted_ids)}")
    if accepted_ids:
        st.write(f"Выбранные LD ID: {accepted_ids}")
    else:
        st.warning("Подходящих кандидатов в показанном пуле нет.")

    default_comment = query_state.get("final_comment", "")
    query_comment = st.text_area("Комментарий к query", value=default_comment, height=90)

    matched_col, miss_col, not_found_col = st.columns(3)
    if matched_col.button(
        "✅ Завершить MATCHED",
        type="primary",
        use_container_width=True,
        disabled=not accepted_ids,
    ):
        _save_final_label(
            labels, labels_path, review_state, state_path, query_id, "MATCHED", query_comment
        )
        st.rerun()

    if miss_col.button(
        "⚠️ RETRIEVAL_MISS",
        use_container_width=True,
        disabled=bool(accepted_ids),
    ):
        _save_final_label(
            labels,
            labels_path,
            review_state,
            state_path,
            query_id,
            "RETRIEVAL_MISS",
            query_comment or "No acceptable item in the shown retrieval pool.",
        )
        st.rerun()

    confirm_not_found = st.checkbox(
        "Подтверждаю: подходящего товара нет во всём каталоге LD, а не только среди кандидатов."
    )
    if not_found_col.button(
        "❌ Завершить NOT_FOUND",
        use_container_width=True,
        disabled=bool(accepted_ids) or not confirm_not_found,
    ):
        _save_final_label(
            labels, labels_path, review_state, state_path, query_id, "NOT_FOUND", query_comment
        )
        st.rerun()

    if st.button("🔄 Сбросить оценки кандидатов этого query"):
        query_state["candidate_grades"] = {}
        query_state["cursor_index"] = 0
        query_state["final_status"] = None
        query_state["final_comment"] = ""
        query_state["completed"] = False
        save_review_state(state_path, review_state)
        labels[query_id] = {
            "label_status": "UNREVIEWED",
            "acceptable_ld_ids": [],
            "expected_status": None,
            "human_comment": "",
        }
        _write_json(labels_path, labels)
        st.rerun()


if __name__ == "__main__":
    main()
