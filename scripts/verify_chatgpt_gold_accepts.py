from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nomenclature_matcher.chatgpt_gold import (
    build_verified_labels,
    flatten_accepts,
    normalize_review_state,
    read_json,
    review_key,
    review_progress,
    set_review_grade,
    write_json_atomic,
)


ROOT = Path(__file__).resolve().parents[1]
ACCEPTS_PATH = ROOT / "data" / "golden_100_chatgpt_accepts.json"
REVIEW_PATH = ROOT / "data" / "golden_100_chatgpt_accepts_human_review.json"
VERIFIED_LABELS_PATH = ROOT / "data" / "golden_100_chatgpt_verified_labels.json"


def _fmt(value: Any) -> str:
    if value in (None, "", [], {}):
        return "—"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def _persist(accepts: dict[str, Any], state: dict[str, Any]) -> None:
    write_json_atomic(REVIEW_PATH, state)
    write_json_atomic(VERIFIED_LABELS_PATH, build_verified_labels(accepts, state))


def _next_unreviewed(rows: list[dict[str, Any]], state: dict[str, Any], start: int) -> int:
    grades = normalize_review_state(state)["grades"]
    for offset in range(1, len(rows) + 1):
        index = (start + offset) % len(rows)
        row = rows[index]
        if review_key(row["query_id"], row["ld_id"]) not in grades:
            return index
    return min(start, max(0, len(rows) - 1))


def main() -> None:
    st.set_page_config(page_title="Verify ChatGPT GOLD accepts", layout="wide")
    st.title("Проверка только ChatGPT ACCEPT")
    st.caption(
        "Здесь нет AI-отклонений. Ты проверяешь только товары, которые ChatGPT предварительно посчитал подходящими."
    )

    accepts = read_json(ACCEPTS_PATH, {})
    rows = flatten_accepts(accepts or {})
    if not rows:
        st.error(
            "Нет data/golden_100_chatgpt_accepts.json или в нём нет ACCEPT. "
            "Сначала подготовь batch-файлы, запушь их и дай ChatGPT выполнить первичную разметку."
        )
        st.stop()

    state = normalize_review_state(read_json(REVIEW_PATH, {}))
    progress = review_progress(accepts, state)
    cols = st.columns(5)
    cols[0].metric("Всего ACCEPT", progress["total"])
    cols[1].metric("Проверено", progress["reviewed"])
    cols[2].metric("Подтверждено", progress["confirmed"])
    cols[3].metric("Отклонено", progress["rejected"])
    cols[4].metric("Не уверен", progress["unsure"])

    if "chatgpt_accept_cursor" not in st.session_state:
        st.session_state.chatgpt_accept_cursor = _next_unreviewed(rows, state, -1)
    index = min(max(int(st.session_state.chatgpt_accept_cursor), 0), len(rows) - 1)
    row = rows[index]
    key = review_key(row["query_id"], row["ld_id"])
    current = state["grades"].get(key, {})

    st.divider()
    st.subheader(f"{index + 1}/{len(rows)} · {row['query_id']}")
    st.markdown(f"### Запрос\n{row['query']}")

    left, right = st.columns([2, 1])
    with left:
        st.markdown(f"### Кандидат LD {row['ld_id']}")
        st.write(row.get("name") or "—")
        st.write(f"**Артикул:** {_fmt(row.get('article'))}")
        if row.get("reason"):
            st.info(f"Почему ChatGPT выбрал: {row['reason']}")
        if row.get("ai_comment"):
            st.caption(f"Комментарий по запросу: {row['ai_comment']}")

    with right:
        grade = current.get("grade") or "—"
        st.metric("Текущая оценка", grade)

    props = row.get("properties") or {}
    if props:
        st.markdown("#### Характеристики")
        for name, value in props.items():
            st.write(f"**{name}:** {_fmt(value)}")

    comment = st.text_input(
        "Комментарий (необязательно)",
        value=current.get("comment", ""),
        key=f"comment-{key}",
    )

    c1, c2, c3 = st.columns(3)
    if c1.button("✅ Подтвердить", use_container_width=True, type="primary"):
        state = set_review_grade(state, row["query_id"], row["ld_id"], "CONFIRM", comment)
        _persist(accepts, state)
        st.session_state.chatgpt_accept_cursor = _next_unreviewed(rows, state, index)
        st.rerun()
    if c2.button("❌ Отклонить", use_container_width=True):
        state = set_review_grade(state, row["query_id"], row["ld_id"], "REJECT", comment)
        _persist(accepts, state)
        st.session_state.chatgpt_accept_cursor = _next_unreviewed(rows, state, index)
        st.rerun()
    if c3.button("❓ Не уверен", use_container_width=True):
        state = set_review_grade(state, row["query_id"], row["ld_id"], "UNSURE", comment)
        _persist(accepts, state)
        st.session_state.chatgpt_accept_cursor = _next_unreviewed(rows, state, index)
        st.rerun()

    nav1, nav2 = st.columns(2)
    if nav1.button("← Назад", use_container_width=True):
        st.session_state.chatgpt_accept_cursor = max(0, index - 1)
        st.rerun()
    if nav2.button("Вперёд →", use_container_width=True):
        st.session_state.chatgpt_accept_cursor = min(len(rows) - 1, index + 1)
        st.rerun()

    st.divider()
    st.caption(f"Review state: {REVIEW_PATH.relative_to(ROOT)}")
    st.caption(f"Verified labels: {VERIFIED_LABELS_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
