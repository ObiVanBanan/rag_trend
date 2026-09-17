from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb


FAMILIES = (
    "ball_valve",
    "butterfly_valve",
    "gate_valve",
    "check_valve",
    "filter",
    "flange",
    "actuator",
    "gearbox",
)


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract an LD-relevant structured subset from the Santech competitor catalog."
    )
    parser.add_argument("--input", type=Path, default=Path("santech_products_full.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/competitors"))
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--sample-rows", type=int, default=300)
    args = parser.parse_args()

    source = args.input.resolve()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)

    with source.open("rb") as fh:
        if fh.read(128).startswith(b"version https://git-lfs.github.com/spec/v1"):
            raise SystemExit("Input is a Git LFS pointer; run `git lfs pull` first")

    con = duckdb.connect(database=":memory:")
    con.execute(f"SET threads TO {max(1, args.threads)}")
    con.execute("SET preserve_insertion_order=false")
    source_sql = _sql_path(source)
    con.execute(
        f"""
        CREATE VIEW source AS
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

    con.execute(
        """
        CREATE VIEW structured AS
        WITH base AS (
            SELECT
                product_id,
                NULLIF(trim(name), '') AS name,
                NULLIF(trim(article), '') AS article,
                NULLIF(trim(description), '') AS properties_json,
                NULLIF(trim(pn), '') AS pn_source,
                NULLIF(trim(dn), '') AS dn_source,
                NULLIF(trim(category_id), '') AS category_id,
                NULLIF(trim(brand_id), '') AS brand_id,
                NULLIF(trim(unit_id), '') AS unit_id,
                NULLIF(trim(url), '') AS url,
                try_cast(description AS JSON) AS props
            FROM source
        ), enriched AS (
            SELECT
                *,
                NULLIF(trim(json_extract_string(props, '$."Бренд"')), '') AS brand,
                NULLIF(trim(json_extract_string(props, '$."Название"')), '') AS product_name,
                NULLIF(trim(json_extract_string(props, '$."Тип"')), '') AS product_type,
                NULLIF(trim(json_extract_string(props, '$."Вид"')), '') AS product_variant,
                NULLIF(trim(json_extract_string(props, '$."Серия"')), '') AS series,
                NULLIF(trim(json_extract_string(props, '$."Модель"')), '') AS model,
                NULLIF(trim(json_extract_string(props, '$."Артикул"')), '') AS vendor_article,
                NULLIF(trim(json_extract_string(props, '$."Тип присоединения"')), '') AS joining_type,
                COALESCE(
                    NULLIF(trim(json_extract_string(props, '$."Материал корпуса"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Материал корпуса крана"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Материал корпуса клапана"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Материал корпуса затвора"')), '')
                ) AS body_material,
                NULLIF(trim(json_extract_string(props, '$."Управление"')), '') AS control,
                NULLIF(trim(json_extract_string(props, '$."Инженерная система"')), '') AS engineering_system,
                NULLIF(trim(json_extract_string(props, '$."Описание"')), '') AS description_text,
                COALESCE(
                    NULLIF(trim(dn_source), ''),
                    NULLIF(trim(json_extract_string(props, '$."Номинальный диаметр, DN"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Номинальный диаметр"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Присоединение к трубопроводу"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Диаметр условного прохода"')), '')
                ) AS dn_text,
                COALESCE(
                    NULLIF(trim(pn_source), ''),
                    NULLIF(trim(json_extract_string(props, '$."Номинальное давление, PN"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Номинальное давление"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Максимальное рабочее давление, бар"')), '')
                ) AS pn_text,
                lower(
                    coalesce(name, '') || ' ' ||
                    coalesce(json_extract_string(props, '$."Название"'), '') || ' ' ||
                    coalesce(json_extract_string(props, '$."Тип"'), '') || ' ' ||
                    coalesce(json_extract_string(props, '$."Вид"'), '')
                ) AS scope_text
            FROM base
        )
        SELECT
            CASE
                WHEN regexp_matches(scope_text, 'кран.{0,80}шар|шар.{0,80}кран') THEN 'ball_valve'
                WHEN scope_text LIKE '%затвор%' AND (scope_text LIKE '%диск%' OR scope_text LIKE '%поворот%') THEN 'butterfly_valve'
                WHEN scope_text LIKE '%задвиж%' THEN 'gate_valve'
                WHEN scope_text LIKE '%клапан%' AND scope_text LIKE '%обрат%' THEN 'check_valve'
                WHEN scope_text LIKE '%фильтр%' THEN 'filter'
                WHEN scope_text LIKE '%фланец%' OR scope_text LIKE '%фланцы%' THEN 'flange'
                WHEN scope_text LIKE '%электропривод%' OR scope_text LIKE '%пневмопривод%' THEN 'actuator'
                WHEN scope_text LIKE '%редуктор%' THEN 'gearbox'
                ELSE 'other'
            END AS family,
            product_id,
            name,
            article,
            vendor_article,
            brand,
            model,
            series,
            product_name,
            product_type,
            product_variant,
            dn_text,
            pn_text,
            joining_type,
            body_material,
            control,
            engineering_system,
            description_text,
            category_id,
            brand_id,
            unit_id,
            url,
            properties_json
        FROM enriched
        """
    )

    where_scope = "family <> 'other'"
    counts = con.execute(
        f"SELECT family, count(*) AS n FROM structured WHERE {where_scope} GROUP BY family ORDER BY n DESC"
    ).fetchall()
    total_scope = sum(int(row[1]) for row in counts)

    parquet = out / "santech_ld_scope.parquet"
    parquet_sql = _sql_path(parquet)
    con.execute(
        f"""
        COPY (
            SELECT * FROM structured
            WHERE {where_scope}
        ) TO '{parquet_sql}'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """
    )

    sample = out / "santech_ld_scope_sample.csv"
    sample_sql = _sql_path(sample)
    per_family = max(1, args.sample_rows // len(FAMILIES))
    con.execute(
        f"""
        COPY (
            SELECT * EXCLUDE (rn)
            FROM (
                SELECT *, row_number() OVER (PARTITION BY family ORDER BY product_id) AS rn
                FROM structured
                WHERE {where_scope}
            )
            WHERE rn <= {per_family}
            ORDER BY family, product_id
        ) TO '{sample_sql}' (HEADER, DELIMITER ',')
        """
    )

    # Key frequency shows which source properties are worth promoting into typed columns later.
    key_rows = con.execute(
        """
        SELECT key, count(*) AS occurrences
        FROM source,
             UNNEST(json_keys(try_cast(description AS JSON))) AS keys(key)
        GROUP BY key
        ORDER BY occurrences DESC, key
        LIMIT 300
        """
    ).fetchall()

    profile = {
        "rows_in_ld_scope": total_scope,
        "family_counts": {str(family): int(count) for family, count in counts},
        "parquet_size_bytes": parquet.stat().st_size,
        "structured_columns": [row[0] for row in con.execute("DESCRIBE SELECT * FROM structured").fetchall()],
        "top_json_keys": [
            {"key": str(key), "occurrences": int(occurrences)}
            for key, occurrences in key_rows
        ],
    }
    profile_path = out / "santech_ld_scope_profile.json"
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    md = [
        "# LD-scope competitor subset",
        "",
        f"Rows selected from Santech catalog: **{total_scope:,}**.",
        "",
        "## Family counts",
        "",
        "| Family | Rows |",
        "|---|---:|",
    ]
    for family, count in counts:
        md.append(f"| {family} | {int(count):,} |")
    md.extend(
        [
            "",
            f"Parquet size: **{parquet.stat().st_size / (1024**2):.1f} MiB**.",
            "",
            "`properties_json` is retained losslessly; commonly useful properties are also promoted into dedicated columns.",
            "",
        ]
    )
    (out / "LD_SCOPE.md").write_text("\n".join(md), encoding="utf-8")

    print(json.dumps(profile | {"top_json_keys": profile["top_json_keys"][:20]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
