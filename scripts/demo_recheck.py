"""Replay a frozen, human-verified exact-ID mapping against the business API.

Unverified, SILVER and missing-negative labels are excluded, never guessed.
For constraint-based equivalents, use the project's strict evaluator instead.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def grade(case: dict, response: dict) -> dict:
    expected = case.get("expected_status")
    label_status = case.get("label_status")
    acceptable = {str(x) for x in case.get("acceptable_ld_ids", [])}
    actual = response.get("status", "ERROR")
    first = (response.get("recommended") or [{}])[0]
    ld_id = first.get("ld_id")
    item = {
        "id": case.get("id"), "query": case.get("query"),
        "expected_status": expected, "actual_status": actual,
        "predicted_ld_id": ld_id,
        "latency_ms": (response.get("metrics") or {}).get("latency_ms"),
    }
    if label_status not in ("VERIFIED", "HUMAN_VERIFIED", "GOLD") or expected not in ("MATCHED", "NOT_FOUND"):
        verdict = "EXCLUDED_UNVERIFIED"
    elif expected == "MATCHED" and not acceptable:
        verdict = "EXCLUDED_NO_ACCEPTABLE_IDS"
    elif actual == "ERROR":
        verdict = "ERROR"
    elif expected == "NOT_FOUND":
        verdict = "PASS" if actual == "NOT_FOUND" else "FALSE_MATCH"
    elif actual != "MATCHED":
        verdict = "WRONG_NOT_FOUND"
    else:
        verdict = "PASS" if str(ld_id) in acceptable else "WRONG_PRODUCT"
    item["verdict"] = verdict
    return item


def summarize(rows: list[dict]) -> dict:
    graded = [r for r in rows if not r["verdict"].startswith("EXCLUDED")]
    pos = [r for r in graded if r["expected_status"] == "MATCHED"]
    neg = [r for r in graded if r["expected_status"] == "NOT_FOUND"]
    fraction = lambda n, d: round(n/d, 4) if d else None
    success = lambda group: sum(r["verdict"] == "PASS" for r in group)
    timings = sorted(r["latency_ms"] for r in graded if isinstance(r.get("latency_ms"), (int,float)))
    return {
        "total_cases": len(rows), "graded_cases": len(graded),
        "excluded_cases": len(rows)-len(graded),
        "exact_top1_accuracy": fraction(success(graded), len(graded)),
        "positive_top1_accuracy": fraction(success(pos), len(pos)),
        "wrong_not_found_rate": fraction(sum(r["verdict"]=="WRONG_NOT_FOUND" for r in pos), len(pos)),
        "false_match_rate": fraction(sum(r["verdict"]=="FALSE_MATCH" for r in neg), len(neg)),
        "error_count": sum(r["verdict"]=="ERROR" for r in graded),
        "latency_p50_ms": round(statistics.median(timings), 1) if timings else None,
        "latency_p95_ms": round(timings[min(len(timings)-1,int(len(timings)*.95))],1) if timings else None,
        "verdict_counts": {v:sum(r["verdict"]==v for r in rows) for v in sorted({r["verdict"] for r in rows})},
    }


def replay(cases: list[dict], *, url: str, timeout: float) -> list[dict]:
    results = []
    for case in cases:
        started = time.perf_counter()
        try:
            request = Request(
                url, method="POST",
                data=json.dumps({"products": [case["query"]]}, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=timeout) as response:
                raw = json.load(response)
            match = raw["results"][0]
        except (HTTPError, URLError, ValueError, TimeoutError, KeyError, IndexError, OSError):
            match = {
                "status": "ERROR", "recommended": [],
                "metrics": {"latency_ms": (time.perf_counter()-started)*1000},
            }
        results.append(grade(case, match))
    return results


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cases", type=Path, required=True)
    p.add_argument("--url", default="http://localhost:8000/v1/demo/match")
    p.add_argument("--out", type=Path, default=Path("demo_quality_report.json"))
    p.add_argument("--timeout", type=float, default=120)
    args = p.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not all(isinstance(c, dict) and c.get("query") for c in cases):
        p.error("--cases must be a JSON list with nonempty query fields")
    rows = replay(cases, url=args.url, timeout=args.timeout)
    report = {
        "source": str(args.cases), "endpoint": args.url,
        "quality_protocol": "human-verified exact-top1, alternative LD IDs accepted",
        "summary": summarize(rows), "cases": rows,
    }
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
