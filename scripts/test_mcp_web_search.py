from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nomenclature_matcher.query_interpreter import DeepSeekQueryInterpreter
from nomenclature_matcher.settings import Settings
from nomenclature_matcher.web_search_mcp import MCPWebSearchLookup


DEFAULT_QUERIES = [
    "Затвор дисковый поворотный РИДАН ЗДМ 05.16.100 Ду 100 (082X4424R)",
    'Кран VALTEC VT.214 1"',
    'Кран IVR 60 1" НР-ВР с американкой',
    'Кран IVR 956 3/4" НР-ВР',
    "Кран Ду150 ANSI1500 №2378929",
    "Кран шаровой BV17 Ду25 Ру40 нержавеющий с электроприводом",
    "Краны Danfoss",
]


def _load_queries(path: str | None) -> list[str]:
    if not path:
        return DEFAULT_QUERIES
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise SystemExit("--input must contain a JSON array of strings")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test DuckDuckGo MCP competitor enrichment.")
    parser.add_argument("--input", help="Optional JSON array of queries")
    parser.add_argument("--output", default="mcp_web_search_test.json")
    parser.add_argument(
        "--with-interpreter",
        action="store_true",
        help="Also pass MCP evidence to DeepSeekQueryInterpreter (requires DEEPSEEK_API_KEY).",
    )
    args = parser.parse_args()

    settings = Settings()
    lookup = MCPWebSearchLookup(settings)
    interpreter = DeepSeekQueryInterpreter(settings) if args.with_interpreter else None
    rows: list[dict] = []

    try:
        for query in _load_queries(args.input):
            row: dict = {"query": query}
            try:
                result = lookup.lookup(query)
                row["web_search"] = result.debug_payload()
                context = result.prompt_context()
                if interpreter is not None:
                    interpretation = interpreter.interpret(query, competitor_context=context)
                    row["interpretation"] = interpretation.model_dump()
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(row)

            web = row.get("web_search") or {}
            interp = row.get("interpretation") or {}
            constraints = interp.get("constraints") or {}
            print("\n===", query)
            print(
                json.dumps(
                    {
                        "web_attempted": web.get("attempted"),
                        "web_accepted": web.get("accepted"),
                        "web_reason": web.get("reason"),
                        "pages_fetched": len(web.get("pages") or []),
                        "searchable": interp.get("searchable"),
                        "normalized_query": interp.get("normalized_query"),
                        "constraints": constraints,
                        "error": row.get("error"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
    finally:
        lookup.close()

    output = Path(args.output)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nFull diagnostic written to: {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
