"""
Assembles data/results/dashboard_data.json -- the single consolidated blob
the dashboard artifact embeds. Aggregates ranked_candidates.json (per-context
rows) up to one entry per gene, attaches every STRING lead considered (not
just the ones that survived to a RankedCandidate row) and every cached
literature search result, so the dashboard can show what was checked and
rejected, not only what survived.
"""

import json
from pathlib import Path

import pandas as pd

RESULTS = Path(__file__).resolve().parent.parent / "data" / "results"


def run():
    ranked = json.loads((RESULTS / "ranked_candidates.json").read_text())
    lit_cache = json.loads((RESULTS / "literature_search_cache.json").read_text())
    string_hits = pd.read_csv(RESULTS / "stage4_mediator_string_hits.csv")
    validation = json.loads((RESULTS / "ranking_validation_controls.json").read_text())
    dep_universe = pd.read_csv(RESULTS / "stage1_dependency_hits.csv", low_memory=False)

    genes: dict[str, dict] = {}
    for row in ranked:
        gene = row["dependency"]["gene"]
        ctx = row["dependency"]["disease_context"]
        g = genes.setdefault(gene, {"gene": gene, "contexts": {}})
        c = g["contexts"].setdefault(
            ctx,
            {
                "disease_context": ctx,
                "n_target_models": row["dependency"]["n_target_models"],
                "n_other_models": row["dependency"]["n_other_models"],
                "median_target_effect": row["dependency"]["median_target_effect"],
                "median_other_effect": row["dependency"]["median_other_effect"],
                "target_dependent_fraction": row["dependency"]["target_dependent_fraction"],
                "selectivity_delta": row["dependency"]["selectivity_delta"],
                "qvalue": row["dependency"]["mann_whitney_p"],
                "gate_eligible": row["gate"]["eligible"],
                "discovery_score": row["discovery_score"],
                "partners": [],
            },
        )
        c["partners"].append(
            {
                "partner_gene": row["mediator"]["partner_gene"],
                "has_claim": bool(row["mediator"]["claims"]),
                "interacting_region_mapped": row["mediator"]["interacting_region_mapped"],
                "enrichment_score": row["enrichment_score"],
                "evidence_completeness": row["evidence_completeness"],
                "final_score": row["final_score"],
                "notes": row["enrichment"]["notes"],
            }
        )

    for gene, g in genes.items():
        g["contexts"] = sorted(g["contexts"].values(), key=lambda c: c["qvalue"])
        g["best_final_score"] = max(p["final_score"] for c in g["contexts"] for p in c["partners"])
        g["best_qvalue"] = min(c["qvalue"] for c in g["contexts"])

        tf_hits = string_hits[(string_hits["tf"] == gene) & (string_hits["is_validation_control"] == False)]  # noqa: E712
        subunit_counts = tf_hits["mediator_subunit"].nunique()
        g["mediator_leads"] = [
            {
                "mediator_subunit": r["mediator_subunit"],
                "combined_score": r["combined_score"],
                "experimental_score": r["experimental_score"],
                "database_score": r["database_score"],
                "textmining_score": r["textmining_score"],
                "coexpression_score": r["coexpression_score"],
            }
            for _, r in tf_hits.sort_values("combined_score", ascending=False).iterrows()
        ]
        g["mediator_artifact_suspect"] = bool(subunit_counts >= 15)

        g["literature"] = [
            entry for key, entry in lit_cache.items() if key.split("::")[0] == gene
        ]

    candidates = sorted(genes.values(), key=lambda g: g["best_final_score"], reverse=True)

    n_leads_total = int((string_hits["is_validation_control"] == False).sum())  # noqa: E712
    n_leads_rejected_artifact = sum(1 for g in candidates if g["mediator_artifact_suspect"])
    n_claims_confirmed = sum(1 for e in lit_cache.values() if e.get("claim"))

    output = {
        "generated_at": "2026-08-15",
        "summary": {
            "tfs_screened": int(dep_universe["tf"].nunique()),
            "context_tests_run": int(len(dep_universe)),
            "significant_candidates": len(candidates),
            "mediator_leads_found": n_leads_total,
            "tfs_with_artifact_pattern": n_leads_rejected_artifact,
            "literature_pairs_checked": len(lit_cache),
            "literature_claims_confirmed": n_claims_confirmed,
        },
        "candidates": candidates,
        "validation_controls": validation,
    }
    out_path = RESULTS / "dashboard_data.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"wrote {out_path} ({out_path.stat().st_size:,} bytes), {len(candidates)} candidates")


if __name__ == "__main__":
    run()
