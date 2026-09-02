import os

import pandas as pd
import psycopg2
from dotenv import load_dotenv

load_dotenv()


conn = psycopg2.connect(
    host=os.getenv("LD_DB_HOST", "sql.lysov.pw"),
    port=os.getenv("LD_DB_PORT", "5432"),
    database=os.getenv("LD_DB_NAME", "postgres"),
    user=os.getenv("LD_DB_USER", "chatr"),
    password=os.getenv("LD_DB_PASSWORD", "xUvrbSkk9X"),
)

sql_query = """
SELECT
    p.id,
    p.name,
    ps.price,
    p.offer_code AS article,
    ps.dn,
    ps.pn,
    ps.joining_type,
    ps.url,
    pp.properties_json
FROM ld_products.products p
LEFT JOIN ld_products.products_site ps ON p.id = ps.id
LEFT JOIN ld_products.products_properties_site pp ON p.id = pp.product_id
WHERE p.offer_code IS NOT NULL
AND p.offer_code != ''
ORDER BY p.name, p.id
"""

output_path = os.getenv("LD_OUTPUT_CSV", "ld_products_full_nomenclature.csv")

print("Выполняю запрос к LD базе по всей номенклатуре...")
df = pd.read_sql_query(sql_query, conn)
conn.close()

print(f"Получено строк: {len(df)}")
print(df.info())

df.to_csv(output_path, index=False, encoding="utf-8-sig")
print(f"CSV сохранен: {output_path}")
