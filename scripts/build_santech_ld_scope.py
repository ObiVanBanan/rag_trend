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
                COALESCE(
                    NULLIF(trim(json_extract_string(props, '$."Бренд"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Производитель"')), '')
                ) AS brand,
                COALESCE(
                    NULLIF(trim(json_extract_string(props, '$."Название"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Наименование"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Тип изделия"')), '')
                ) AS product_name,
                COALESCE(
                    NULLIF(trim(json_extract_string(props, '$."Тип"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Тип изделия"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Тип шарового крана"')), '')
                ) AS product_type,
                COALESCE(
                    NULLIF(trim(json_extract_string(props, '$."Вид"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Исполнение"')), '')
                ) AS product_variant,
                COALESCE(
                    NULLIF(trim(json_extract_string(props, '$."Серия"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Серия бренда"')), '')
                ) AS series,
                NULLIF(trim(json_extract_string(props, '$."Модель"')), '') AS model,
                COALESCE(
                    NULLIF(trim(json_extract_string(props, '$."Артикул"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Артикул производителя"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Код производителя"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Код товара"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Номенклатурный номер"')), '')
                ) AS vendor_article,
                NULLIF(trim(json_extract_string(props, '$."Код производителя"')), '') AS manufacturer_code,
                COALESCE(
                    NULLIF(trim(json_extract_string(props, '$."Тип присоединения"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Тип соединения"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Присоединение"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Соединение"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Тип подключения"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Подключение"')), '')
                ) AS joining_type,
                COALESCE(
                    NULLIF(trim(json_extract_string(props, '$."Вид резьбы"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Резьба"')), '')
                ) AS thread_type,
                COALESCE(
                    NULLIF(trim(json_extract_string(props, '$."Присоединение, дюйм"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Стандарт подключения, дюйм"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Подключение, дюйм"')), '')
                ) AS connection_size,
                COALESCE(
                    NULLIF(trim(json_extract_string(props, '$."Материал корпуса"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Материал корпуса крана"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Материал корпуса клапана"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Материал корпуса затвора"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Материал изделия"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Основной материал"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Материал"')), '')
                ) AS body_material,
                NULLIF(trim(json_extract_string(props, '$."Материал уплотнения"')), '') AS seal_material,
                COALESCE(
                    NULLIF(trim(json_extract_string(props, '$."Тип управления"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Управление"')), '')
                ) AS control,
                NULLIF(trim(json_extract_string(props, '$."Вид рукоятки"')), '') AS handle_type,
                COALESCE(
                    NULLIF(trim(json_extract_string(props, '$."Тип рабочей среды"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Рабочая среда"')), '')
                ) AS working_medium,
                COALESCE(
                    NULLIF(trim(json_extract_string(props, '$."Инженерная система"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Область применения"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Назначение"')), '')
                ) AS engineering_system,
                COALESCE(
                    NULLIF(trim(json_extract_string(props, '$."Страна-производитель"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Страна происхождения"')), '')
                ) AS country,
                NULLIF(trim(json_extract_string(props, '$."Описание"')), '') AS description_text,
                COALESCE(
                    NULLIF(trim(dn_source), ''),
                    NULLIF(trim(json_extract_string(props, '$."Условный проход, мм"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Ду, мм"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Ду"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."ДУ"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Номинальный диаметр, DN"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Номинальный диаметр"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Диаметр условного прохода"')), '')
                ) AS dn_text,
                COALESCE(
                    NULLIF(trim(pn_source), ''),
                    NULLIF(trim(json_extract_string(props, '$."Ру"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."РУ"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Номинальное давление, PN"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Номинальное давление"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Макс. рабочее давление, бар"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Рабочее давление, бар"')), ''),
                    NULLIF(trim(json_extract_string(props, '$."Максимальное рабочее давление, бар"')), '')
                ) AS pn_text,
                lower(
                    coalesce(name, '') || ' ' ||
                    coalesce(json_extract_string(props, '$."Название"'), '') || ' ' ||
                    coalesce(json_extract_string(props, '$."Наименование"'), '') || ' ' ||
                    coalesce(json_extract_string(props, '$."Тип изделия"'), '') || ' ' ||
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
            manufacturer_code,
            brand,
            model,
            series,
            product_name,
            product_type,
            product_variant,
            dn_text,
            pn_text,
            joining_type,
            thread_type,
            connection_size,
            body_material,
            seal_material,
            control,
            handle_type,
            working_medium,
            engineering_system,
            country,
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

    completeness_columns = [
        "brand",
        "model",
        "vendor_article",
        "dn_text",
        "pn_text",
        "joining_type",
        "thread_type",
        "connection_size",
        "body_material",
        "control",
        "working_medium",
    ]
    completeness_expr = ", ".join(
        f"sum(CASE WHEN {column} IS NOT NULL THEN 1 ELSE 0 END) AS {column}"
        for column in completeness_columns
    )
    completeness_values = con.execute(
        f"SELECT {completeness_expr} FROM structured WHERE {where_scope}"
    ).fetchone()
    completeness = {
        column: {
            "filled": int(value or 0),
            "ratio": round(int(value or 0) / total_scope, 6) if total_scope else 0.0,
        }
        for column, value in zip(completeness_columns, completeness_values)
    }

    profile = {
        "rows_in_ld_scope": total_scope,
        "family_counts": {str(family): int(count) for family, count in counts},
        "parquet_size_bytes": parquet.stat().st_size,
        "structured_columns": [row[0] for row in con.execute("DESCRIBE SELECT * FROM structured").fetchall()],
        "field_completeness": completeness,
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
    md.extend(["", "## Structured field completeness", "", "| Field | Filled | Coverage |", "|---|---:|---:|"])
    for column, stats in completeness.items():
        md.append(f"| {column} | {stats['filled']:,} | {stats['ratio']:.1%} |")
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
