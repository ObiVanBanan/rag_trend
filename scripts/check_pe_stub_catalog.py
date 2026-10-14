"""Diagnose LD catalogue support for PE stub flange requests (no LLM/Qdrant).

Read the same LD CSV as production. Select by source-derived dimensions,
not LD ids or benchmark labels. Does not modify matcher, Qdrant or GOLD.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nomenclature_matcher.documents import load_products_from_csv
from nomenclature_matcher.product_safety import flange_spec_from_query
from nomenclature_matcher.query_constraints import (
    QueryConstraints, candidate_flange_bore_mm, candidate_flange_dn,
    candidate_flange_kind, candidate_stub_dn, evaluate_product,
)

DEFAULT_REQUESTS = [
    "Фланец стальной ду80мм. под втулку ПНД ду90мм.",
    "Фланец стальной ду50мм. под втулку ПНД ду63мм.",
    "Фланец стальной ду40мм. под втулку ПНД ду50мм.",
]


def diagnose(csv: Path, requests: list[str]) -> dict:
    products = load_products_from_csv(csv)
    rows = []
    for query in requests:
        spec = flange_spec_from_query(query)
        if spec is None or spec.kind != "pe_stub" or spec.dn is None or spec.stub_dn is None:
            raise ValueError(f"Not a fully specified PE-stub flange query: {query!r}")
        constraints = QueryConstraints(
            product_type="flange",flange_kind="pe_stub",
            dn=spec.dn,stub_dn=spec.stub_dn,
            body_material=spec.body_material,
            body_material_grade=spec.body_material_grade,
            pn_min_mpa=spec.pn_min_mpa,
        )
        # Near misses must be visible even when hard-eligibility is zero.
        near = [p for p in products if candidate_flange_kind(p)=="pe_stub" and
                candidate_flange_dn(p)==spec.dn]
        matched = [p for p in near if evaluate_product(p,constraints).matches]
        def view(p):
            return {
                "ld_id":p.id, "article":p.article, "name":p.name,
                "flange_dn":candidate_flange_dn(p),
                "flange_bore_mm":candidate_flange_bore_mm(p),
                "inferred_pipe_od_mm":candidate_stub_dn(p),
                "hard_checks":evaluate_product(p,constraints).checks,
            }
        rows.append({
            "query":query, "hard_eligible":len(matched),
            "same_dn_pe_flange_candidates":len(near),
            "matched": [view(p) for p in matched[:20]],
            "near_misses": [view(p) for p in near if p not in matched][:15],
        })
    return {
        "catalog_path":str(csv), "catalog_count":len(products),
        "all_requests_have_eligible_catalog_product":all(r["hard_eligible"] for r in rows),
        "results":rows,
    }


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv",type=Path,default=Path("ld_products_full_nomenclature.csv"))
    p.add_argument("--input",type=Path,
                   help="Optional JSON array of PE-stub tender strings, instead of three defaults")
    p.add_argument("--out",type=Path,default=Path(".tmp/pe_stub_catalog_diagnostic.json"))
    a=p.parse_args()
    if not a.csv.is_file():
        p.error(f"Missing catalog CSV: {a.csv}")
    requests=json.loads(a.input.read_text(encoding="utf-8")) if a.input else DEFAULT_REQUESTS
    if not isinstance(requests,list) or not requests or not all(isinstance(q,str) for q in requests):
        p.error("Input must be a nonempty JSON array of tender query strings")
    result=diagnose(a.csv.resolve(),requests)
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    for row in result["results"]:
        print(f'{row["query"]}: hard eligible={row["hard_eligible"]}, '
              f'same-DN PE candidates={row["same_dn_pe_flange_candidates"]}')
    print(f"Full per-candidate report: {a.out}")
    return 0 if result["all_requests_have_eligible_catalog_product"] else 1


if __name__=="__main__":
    raise SystemExit(main())
