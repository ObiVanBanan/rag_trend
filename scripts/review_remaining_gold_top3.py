from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nomenclature_matcher.remaining_gold_review import build_remaining_review_payload


SOURCE_PATH = ROOT / "data" / "golden_100_review_candidates.json"
BASE_LABELS_PATH = ROOT / "data" / "golden_100_labels.json"
CHATGPT_VERIFIED_PATH = ROOT / "data" / "golden_100_chatgpt_verified_labels.json"
STATE_PATH = ROOT / "data" / "golden_100_remaining_top3_human_review.json"
LABELS_PATH = ROOT / "data" / "golden_100_remaining_top3_labels.json"


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def fmt(value: Any) -> str:
    if value in (None, "", [], {}):
        return "—"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def technical_properties(candidate: dict[str, Any]) -> dict[str, Any]:
    props = candidate.get("technical_properties") or candidate.get("properties") or {}
    return props if isinstance(props, dict) else {}


def candidate_grade(state: dict[str, Any], query_id: str, ld_id: int) -> dict[str, Any] | None:
    return state.get("queries", {}).get(query_id, {}).get("candidate_grades", {}).get(str(ld_id))


def query_state(state: dict[str, Any], query_id: str) -> dict[str, Any]:
    return state.setdefault("queries", {}).setdefault(
        query_id,
        {
            "candidate_grades": {},
            "cursor": 0,
            "final_status": None,
            "final_comment": "",
            "completed": False,
        },
    )


def accepted_ids(qstate: dict[str, Any]) -> list[int]:
    return sorted(
        int(ld_id)
        for ld_id, info in qstate.get("candidate_grades", {}).items()
        if info.get("grade") == "ACCEPT"
    )


def save_grade(
    state: dict[str, Any],
    query_id: str,
    candidate: dict[str, Any],
    grade: str,
    comment: str,
    candidate_count: int,
) -> None:
    qstate = query_state(state, query_id)
    qstate.setdefault("candidate_grades", {})[str(candidate["ld_id"])] = {
        "grade": grade,
        "comment": comment or "",
    }
    qstate["cursor"] = min(int(qstate.get("cursor", 0)) + 1, max(0, candidate_count - 1))
    write_json(STATE_PATH, state)


def save_final(
    state: dict[str, Any],
    labels: dict[str, Any],
    query_id: str,
    status: str,
    comment: str,
) -> None:
    qstate = query_state(state, query_id)
    accepts = accepted_ids(qstate)
    qstate["final_status"] = status
    qstate["final_comment"] = comment or ""
    qstate["completed"] = True

    if status == "MATCHED":
        labels[query_id] = {
            "label_status": "VERIFIED",
            "label_source": "HUMAN_REMAINING_TOP3",
            "expected_status": "MATCHED",
            "acceptable_ld_ids": accepts,
            "human_comment": comment or "",
            "known_positive_ids_exhaustive": False,
        }
    elif status == "NOT_FOUND":
        labels[query_id] = {
            "label_status": "VERIFIED",
            "label_source": "HUMAN_REMAINING_TOP3",
            "expected_status": "NOT_FOUND",
            "acceptable_ld_ids": [],
            "human_comment": comment or "",
        }
    else:
        labels[query_id] = {
            "label_status": "RETRIEVAL_MISS",
            "label_source": "HUMAN_REMAINING_TOP3",
            "expected_status": "MATCHED",
            "acceptable_ld_ids": [],
            "human_comment": comment or "No acceptable item in top-3 retrieval candidates.",
        }

    write_json(STATE_PATH, state)
    write_json(LABELS_PATH, labels)


def next_unfinished_index(queries: list[dict[str, Any]], state: dict[str, Any], current: int) -> int:
    for offset in range(1, len(queries) + 1):
        idx = (current + offset) % len(queries)
        if not query_state(state, queries[idx]["id"]).get("completed"):
            return idx
    return current


def main() -> None:
    st.set_page_config(page_title="Golden remaining top-3", layout="wide")
    st.title("Golden remaining top‑3")
    st.caption(
        "Только ранее непроверенные запросы. Для каждого показываются максимум 3 лучших кандидата. "
        "Если среди трёх нет подходящего товара — выбирай RETRIEVAL_MISS, а не NOT_FOUND."
    )

    missing = [path for path in (SOURCE_PATH, BASE_LABELS_PATH, CHATGPT_VERIFIED_PATH) if not path.exists()]
    if missing:
        st.error("Не хватает файлов: " + ", ".join(str(path.relative_to(ROOT)) for path in missing))
        st.stop()

    payload = build_remaining_review_payload(
        read_json(SOURCE_PATH, {}),
        read_json(BASE_LABELS_PATH, {}),
        read_json(CHATGPT_VERIFIED_PATH, {}),
        limit=3,
    )
    queries = payload["queries"]
    if not queries:
        st.success("Непроверенных запросов не осталось 🎉")
        st.stop()

    state = read_json(STATE_PATH, {"version": 1, "queries": {}})
    labels = read_json(LABELS_PATH, {})

    completed = sum(1 for item in queries if query_state(state, item["id"]).get("completed"))
    matched = sum(
        1 for item in queries if query_state(state, item["id"]).get("final_status") == "MATCHED"
    )
    misses = sum(
        1 for item in queries if query_state(state, item["id"]).get("final_status") == "RETRIEVAL_MISS"
    )
    not_found = sum(
        1 for item in queries if query_state(state, item["id"]).get("final_status") == "NOT_FOUND"
    )

    cols = st.columns(4)
    cols[0].metric("Готово", f"{completed}/{len(queries)}")
    cols[1].metric("MATCHED", matched)
    cols[2].metric("RETRIEVAL_MISS", misses)
    cols[3].metric("NOT_FOUND", not_found)

    if "remaining_top3_query_index" not in st.session_state:
        first = next((i for i, item in enumerate(queries) if not query_state(state, item["id"]).get("completed")), 0)
        st.session_state.remaining_top3_query_index = first

    qindex = min(max(int(st.session_state.remaining_top3_query_index), 0), len(queries) - 1)
    item = queries[qindex]
    qid = item["id"]
    qstate = query_state(state, qid)
    candidates = item.get("candidates", [])

    st.divider()
    st.subheader(f"{qindex + 1}/{len(queries)} · {qid}")
    st.markdown(f"### Запрос\n{item['query']}")
    metadata = item.get("metadata") or {}
    meta = []
    if metadata.get("category"):
        meta.append(f"категория: {metadata['category']}")
    if metadata.get("difficulty"):
        meta.append(f"сложность: {metadata['difficulty']}")
    if meta:
        st.caption(" · ".join(meta))

    if qstate.get("completed"):
        st.success(f"Завершено: {qstate.get('final_status')}")
        if accepted_ids(qstate):
            st.write(f"Подтверждённые LD: {accepted_ids(qstate)}")
        if st.button("↩️ Переоткрыть query", use_container_width=True):
            qstate["completed"] = False
            qstate["final_status"] = None
            qstate["final_comment"] = ""
            write_json(STATE_PATH, state)
            st.rerun()
    else:
        graded = qstate.get("candidate_grades", {})
        unreviewed_indices = [i for i, c in enumerate(candidates) if str(c["ld_id"]) not in graded]

        if unreviewed_indices:
            cursor = int(qstate.get("cursor", 0))
            cindex = next((i for i in unreviewed_indices if i >= cursor), unreviewed_indices[0])
            candidate = candidates[cindex]
            props = technical_properties(candidate)

            st.progress(len(graded) / max(1, len(candidates)), text=f"Кандидат {len(graded) + 1} из {len(candidates)}")
            st.markdown(f"### LD {candidate['ld_id']} · {candidate.get('name') or 'Без названия'}")
            st.write(f"**Артикул:** {fmt(candidate.get('article'))}")
            ranks = []
            for label, key in (("Hybrid", "hybrid_rank"), ("Dense", "dense_rank"), ("BM25", "bm25_rank")):
                if candidate.get(key) is not None:
                    ranks.append(f"{label} #{candidate[key]}")
            if ranks:
                st.caption(" · ".join(ranks))

            rows = [
                ("Тип", props.get("Тип продукта")),
                ("Материал", props.get("Материал корпуса")),
                ("DN", candidate.get("dn") or props.get("Номинальный диаметр, DN")),
                ("PN", candidate.get("pn") or props.get("Номинальное давление, МПа")),
                ("Присоединение", candidate.get("joining_type") or props.get("Присоединение")),
                ("Резьба", props.get("Тип резьбы")),
                ("Среда", props.get("Рабочая среда")),
                ("Проход", props.get("Тип прохода")),
                ("Управление", props.get("Управление")),
                ("Серия", props.get("Серия")),
            ]
            visible_rows = [(name, value) for name, value in rows if value not in (None, "", [])]
            if visible_rows:
                ccols = st.columns(3)
                for i, (name, value) in enumerate(visible_rows):
                    ccols[i % 3].markdown(f"**{name}:** {fmt(value)}")

            with st.expander("Все технические свойства"):
                st.json(props)

            comment = st.text_input("Комментарий к кандидату (необязательно)", key=f"comment::{qid}::{candidate['ld_id']}")
            c1, c2, c3 = st.columns(3)
            if c1.button("✅ Подходит", type="primary", use_container_width=True):
                save_grade(state, qid, candidate, "ACCEPT", comment, len(candidates))
                st.rerun()
            if c2.button("❌ Не подходит", use_container_width=True):
                save_grade(state, qid, candidate, "REJECT", comment, len(candidates))
                st.rerun()
            if c3.button("❓ Не уверен", use_container_width=True):
                save_grade(state, qid, candidate, "UNSURE", comment, len(candidates))
                st.rerun()
        else:
            accepts = accepted_ids(qstate)
            st.success(f"Top‑3 просмотрены. ACCEPT: {len(accepts)}")
            if accepts:
                st.write(f"Подходящие LD: {accepts}")
            else:
                st.warning("Среди top‑3 подходящего товара не найдено.")

            final_comment = st.text_area("Комментарий к query (необязательно)", value=qstate.get("final_comment", ""))
            c1, c2, c3 = st.columns(3)
            if c1.button("✅ MATCHED", type="primary", use_container_width=True, disabled=not accepts):
                save_final(state, labels, qid, "MATCHED", final_comment)
                st.session_state.remaining_top3_query_index = next_unfinished_index(queries, state, qindex)
                st.rerun()
            if c2.button("⚠️ RETRIEVAL_MISS", use_container_width=True, disabled=bool(accepts)):
                save_final(state, labels, qid, "RETRIEVAL_MISS", final_comment)
                st.session_state.remaining_top3_query_index = next_unfinished_index(queries, state, qindex)
                st.rerun()

            confirm_not_found = st.checkbox("Подтверждаю, что подходящего товара нет во всём каталоге LD")
            if c3.button("❌ NOT_FOUND", use_container_width=True, disabled=bool(accepts) or not confirm_not_found):
                save_final(state, labels, qid, "NOT_FOUND", final_comment)
                st.session_state.remaining_top3_query_index = next_unfinished_index(queries, state, qindex)
                st.rerun()

    st.divider()
    left, right = st.columns(2)
    if left.button("← Предыдущий query", use_container_width=True, disabled=qindex == 0):
        st.session_state.remaining_top3_query_index = qindex - 1
        st.rerun()
    if right.button("Следующий query →", use_container_width=True, disabled=qindex >= len(queries) - 1):
        st.session_state.remaining_top3_query_index = qindex + 1
        st.rerun()

    st.caption(f"Review state: {STATE_PATH.relative_to(ROOT)}")
    st.caption(f"Labels: {LABELS_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
