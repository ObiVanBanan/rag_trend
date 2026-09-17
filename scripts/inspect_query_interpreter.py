import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nomenclature_matcher.query_interpreter import DeepSeekQueryInterpreter
from nomenclature_matcher.settings import Settings


def _read_queries(path: str | None) -> list[str]:
    raw = Path(path).read_text(encoding="utf-8-sig") if path else sys.stdin.read().lstrip("\ufeff")
    payload = json.loads(raw)
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise ValueError("Input must be a JSON array of strings")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", help="Path to JSON array of nomenclature strings. Reads stdin when omitted.")
    args = parser.parse_args()

    interpreter = DeepSeekQueryInterpreter(Settings())
    rows = []
    for query in _read_queries(args.input):
        try:
            interpretation = interpreter.interpret(query)
            rows.append({"query": query, **interpretation.model_dump()})
        except Exception as exc:
            rows.append(
                {
                    "query": query,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
