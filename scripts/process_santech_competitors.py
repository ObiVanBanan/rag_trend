from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_materialized_lfs(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"Input file does not exist: {path}")
    with path.open("rb") as fh:
        head = fh.read(128)
    if head.startswith(b"version https://git-lfs.github.com/spec/v1"):
        raise SystemExit(
            f"{path} is only a Git LFS pointer. Run `git lfs pull` before processing."
        )


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Profile and compact the Santech competitor product export with DuckDB."
    )
    parser.add_argument("--input", type=Path, default=Path("santech_products_full.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/competitors"))
    parser.add_argument("--sample-rows", type=int, default=200)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()

    source = args.input.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    _ensure_materialized_lfs(source)

    parquet_path = output_dir / "santech_products_compact.parquet"
    sample_path = output_dir / "santech_products_sample.csv"
    profile_path = output_dir / "santech_products_profile.json"
    report_path = output_dir / "README.md"
    manifest_path = output_dir / "manifest.json"
    temp_dir = output_dir / ".duckdb_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(database=":memory:")
    con.execute(f"SET threads TO {max(1, args.threads)}")
    con.execute(f"SET temp_directory='{str(temp_dir).replace("'", "''")}'")
    con.execute("SET preserve_insertion_order=false")

    source_sql = str(source).replace("'", "''")
    try:
        con.execute(
            f"""
            CREATE VIEW raw AS
            SELECT *
            FROM read_csv_auto(
                '{source_sql}',
                header=true,
                all_varchar=true,
                ignore_errors=true,
                null_padding=true,
                sample_size=200000
            )
            """
        )
    except Exception as exc:
        raise SystemExit(f"DuckDB could not read {source}: {exc}") from exc

    description = con.execute("DESCRIBE SELECT * FROM raw").fetchall()
    columns = [row[0] for row in description]
    if not columns:
        raise SystemExit("No columns detected in source CSV")

    total_rows = int(con.execute("SELECT count(*) FROM raw").fetchone()[0])

    profile_exprs: list[str] = []
    for idx, column in enumerate(columns):
        ident = _quote_ident(column)
        normalized = f"NULLIF(trim({ident}), '')"
        profile_exprs.extend(
            [
                f"sum(CASE WHEN {normalized} IS NULL THEN 1 ELSE 0 END) AS nulls_{idx}",
                f"approx_count_distinct({normalized}) AS distinct_{idx}",
                f"max(length({normalized})) AS maxlen_{idx}",
            ]
        )
    profile_values = con.execute("SELECT " + ", ".join(profile_exprs) + " FROM raw").fetchone()

    column_profile: list[dict[str, Any]] = []
    empty_columns: list[str] = []
    pos = 0
    for column in columns:
        null_count = int(profile_values[pos] or 0)
        approx_distinct = int(profile_values[pos + 1] or 0)
        max_length = int(profile_values[pos + 2] or 0)
        pos += 3
        null_ratio = (null_count / total_rows) if total_rows else 1.0
        if null_count == total_rows:
            empty_columns.append(column)
        column_profile.append(
            {
                "name": column,
                "null_or_empty_count": null_count,
                "null_or_empty_ratio": round(null_ratio, 6),
                "approx_distinct": approx_distinct,
                "max_length": max_length,
            }
        )

    kept_columns = [column for column in columns if column not in empty_columns]
    if not kept_columns:
        raise SystemExit("All detected columns are empty")

    normalized_projection = []
    for column in kept_columns:
        ident = _quote_ident(column)
        # Normalize leading/trailing and repeated whitespace, but otherwise preserve source values.
        normalized_projection.append(
            f"NULLIF(regexp_replace(trim({ident}), '\\s+', ' ', 'g'), '') AS {ident}"
        )
    projection_sql = ",\n                ".join(normalized_projection)

    parquet_sql = str(parquet_path).replace("'", "''")
    con.execute(
        f"""
        COPY (
            SELECT DISTINCT
                {projection_sql}
            FROM raw
        )
        TO '{parquet_sql}'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """
    )

    deduplicated_rows = int(
        con.execute(f"SELECT count(*) FROM read_parquet('{parquet_sql}')").fetchone()[0]
    )

    sample_sql = str(sample_path).replace("'", "''")
    con.execute(
        f"""
        COPY (
            SELECT * FROM read_parquet('{parquet_sql}')
            LIMIT {max(1, args.sample_rows)}
        ) TO '{sample_sql}' (HEADER, DELIMITER ',')
        """
    )

    source_size = source.stat().st_size
    parquet_size = parquet_path.stat().st_size
    duplicates_removed = total_rows - deduplicated_rows

    profile = {
        "source": source.name,
        "source_size_bytes": source_size,
        "source_sha256": _sha256(source),
        "rows": total_rows,
        "deduplicated_rows": deduplicated_rows,
        "duplicates_removed": duplicates_removed,
        "duplicate_ratio": round(duplicates_removed / total_rows, 6) if total_rows else 0.0,
        "column_count": len(columns),
        "kept_column_count": len(kept_columns),
        "empty_columns_dropped": empty_columns,
        "columns": column_profile,
        "output_parquet": parquet_path.name,
        "output_parquet_size_bytes": parquet_size,
        "compression_ratio_vs_csv": round(parquet_size / source_size, 6) if source_size else None,
    }
    profile_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    manifest = {
        "source": {
            "path": source.name,
            "size_bytes": source_size,
            "sha256": profile["source_sha256"],
        },
        "output": {
            "path": parquet_path.name,
            "size_bytes": parquet_size,
            "sha256": _sha256(parquet_path),
            "rows": deduplicated_rows,
            "format": "parquet",
            "compression": "zstd",
        },
        "transformations": [
            "drop columns that are 100% null/empty",
            "trim leading/trailing whitespace",
            "collapse repeated whitespace",
            "convert empty strings to null",
            "remove exact duplicate rows after normalization",
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    most_complete = sorted(
        column_profile,
        key=lambda item: (item["null_or_empty_ratio"], -item["approx_distinct"]),
    )[:20]
    report_lines = [
        "# Santech competitor catalog profile",
        "",
        f"- Source: `{source.name}`",
        f"- CSV size: {source_size / (1024**2):.1f} MiB",
        f"- Rows: {total_rows:,}",
        f"- Rows after normalized exact deduplication: {deduplicated_rows:,}",
        f"- Duplicates removed: {duplicates_removed:,} ({profile['duplicate_ratio']:.2%})",
        f"- Columns: {len(columns)}",
        f"- 100% empty columns dropped: {len(empty_columns)}",
        f"- Parquet size: {parquet_size / (1024**2):.1f} MiB",
        f"- Parquet / CSV size ratio: {profile['compression_ratio_vs_csv']:.2%}",
        "",
        "## Most complete columns",
        "",
        "| Column | Empty ratio | Approx. distinct | Max length |",
        "|---|---:|---:|---:|",
    ]
    for item in most_complete:
        safe_name = str(item["name"]).replace("|", "\\|")
        report_lines.append(
            f"| {safe_name} | {item['null_or_empty_ratio']:.2%} | "
            f"{item['approx_distinct']:,} | {item['max_length']:,} |"
        )
    if empty_columns:
        report_lines.extend(
            ["", "## Dropped empty columns", "", ", ".join(f"`{c}`" for c in empty_columns)]
        )
    report_lines.extend(
        [
            "",
            "## Outputs",
            "",
            "- `santech_products_compact.parquet` — normalized, exact-deduplicated full catalog.",
            "- `santech_products_sample.csv` — first rows for quick human inspection.",
            "- `santech_products_profile.json` — per-column completeness/cardinality profile.",
            "- `manifest.json` — source/output hashes and transformation manifest.",
            "",
        ]
    )
    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    try:
        temp_dir.rmdir()
    except OSError:
        pass

    print(json.dumps({k: _json_safe(v) for k, v in profile.items() if k != "columns"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
