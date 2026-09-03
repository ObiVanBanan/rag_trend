import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nomenclature_matcher.documents import load_products_from_csv
from nomenclature_matcher.eval_label_builder import build_article_index, resolve_articles


HUMAN_LABELS = {
    "q01": {
        "articles": [
            "111N0800163MULD000002100",
            "111N0800163EULD000002100",
            "11150800162MULD000000000",
        ],
        "expected_status": "MATCHED",
        "human_comment": "Human verified from data/моя_разметка.md",
    },
    "q02": {
        "articles": [
            "А1110800162RULD0000001300",
            "21110800162RULD000000000",
            "11110800162MULD000002100",
            "11110809162RULD000000000",
            "А1110800162MULD0000001300",
        ],
        "expected_status": "MATCHED",
        "human_comment": "Human verified from data/моя_разметка.md",
    },
    "q03": {
        "expected_status": None,
        "human_comment": "Тут мало конкретики, я бы этот запрос не брал в работу",
    },
    "q04": {
        "articles": [
            "6-0150.16-1120-000-E0",
            "6-0150.16-1120-000-RR0",
            "6-0150.16-1120-000-RL0",
            "6-0150.16-1120-000-RL0",
            "6-0150.25-1120-000-RR1",
            "6-0150.25-2220-000-RR0",
            "6-0150.25-1120-000-E1",
            "6-0150.16-1130-000-E1",
            "6-0250.25-2220-000-RK0",
        ],
        "expected_status": "MATCHED",
        "human_comment": "Human verified from data/моя_разметка.md",
    },
    "q05": {
        "expected_status": None,
        "human_comment": "",
    },
    "q06": {
        "expected_status": None,
        "human_comment": "",
    },
    "q07": {
        "articles": [
            "AOX-Q-400000000000000000",
            "AOX-Q-100000000000000000",
            "AOX-Q-300000000000000000",
            "AOX-M-600000000000000000",
            "AOX-Q-200000000000000000",
            "AOX-M-700000000000000000",
        ],
        "expected_status": "MATCHED",
        "human_comment": "Human verified from data/моя_разметка.md",
    },
    "q08": {
        "articles": [
            "11110509402MULD00000000C",
            "11110509402MULD000002300",
            "111H0509402MULD000000000",
        ],
        "expected_status": "MATCHED",
        "human_comment": "Human verified from data/моя_разметка.md",
    },
    "q09": {
        "expected_status": None,
        "human_comment": "",
    },
    "q10": {
        "expected_status": None,
        "human_comment": "",
    },
    "q11": {
        "expected_status": None,
        "human_comment": "LD 47.336.15 наиболее подходящий из TOP-20, но у него накидная гайка, а не чистая муфта/муфта",
    },
}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    products = load_products_from_csv(root / "ld_products_full_nomenclature.csv")
    article_index = build_article_index(products)

    dataset = json.loads((root / "data" / "eval_queries.json").read_text(encoding="utf-8"))
    labels: dict[str, dict] = {}
    verified = 0
    unreviewed = 0
    unresolved: list[str] = []
    ambiguous: list[str] = []

    print("Human annotation conversion")
    for item in dataset:
        query_id = item["id"]
        entry = HUMAN_LABELS.get(query_id, {})
        articles = entry.get("articles", [])
        expected_status = entry.get("expected_status")
        human_comment = entry.get("human_comment", "")

        if articles:
            acceptable_ld_ids, unresolved_articles, ambiguous_articles = resolve_articles(article_index, articles)
            unresolved.extend(f"{query_id}: {article}" for article in unresolved_articles)
            ambiguous.extend(f"{query_id}: {article}" for article in ambiguous_articles)
            if acceptable_ld_ids and expected_status == "MATCHED":
                labels[query_id] = {
                    "label_status": "VERIFIED",
                    "acceptable_ld_ids": acceptable_ld_ids,
                    "expected_status": "MATCHED",
                    "human_comment": human_comment,
                }
                verified += 1
                details = [f"VERIFIED, {len(acceptable_ld_ids)} acceptable products"]
                if unresolved_articles:
                    details.append(f"{len(unresolved_articles)} unresolved article(s)")
                if ambiguous_articles:
                    details.append(f"{len(ambiguous_articles)} ambiguous article(s)")
                print(f"{query_id}: " + ", ".join(details))
            else:
                labels[query_id] = {
                    "label_status": "UNREVIEWED",
                    "acceptable_ld_ids": [],
                    "expected_status": None,
                    "human_comment": human_comment,
                }
                unreviewed += 1
                print(f"{query_id}: UNREVIEWED")
        else:
            labels[query_id] = {
                "label_status": "UNREVIEWED",
                "acceptable_ld_ids": [],
                "expected_status": None,
                "human_comment": human_comment,
            }
            unreviewed += 1
            print(f"{query_id}: UNREVIEWED")

    labels_path = root / "data" / "eval_labels.json"
    labels_path.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(f"Verified queries: {verified}")
    print(f"Unreviewed queries: {unreviewed}")
    print(f"Unresolved articles: {len(unresolved)}")
    for item in unresolved:
        print(f"- {item}")
    print(f"Ambiguous articles: {len(ambiguous)}")
    for item in ambiguous:
        print(f"- {item}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
