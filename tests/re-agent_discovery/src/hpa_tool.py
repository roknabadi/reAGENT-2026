"""
Stage 3 (half) -- Human Protein Atlas normal-tissue expression, as a safety
proxy. Real live REST API, no key, no agent-mediation needed (unlike
Paperclip/literature_search) -- callable directly from Python.

Safety-proxy metric per docs/DISCOVERY_ARCHITECTURE.md SS5: narrow normal-tissue
expression + high expression in the disease context is the favorable
pattern. `tissue_specificity` ("Tissue enhanced"/"Group enriched"/"Low tissue
specificity"/etc.) and the tissue count backing `top_expressed_tissues` are
the two numbers that matter; this is a PROXY, not tolerability data, and is
labeled as such wherever it's surfaced downstream.
"""

import json

import requests

import config

HPA_CACHE_PATH = config.RESULTS_DIR / "hpa_cache.json"
HPA_URL_TMPL = "https://www.proteinatlas.org/{ensembl_id}.json"


def _load_cache() -> dict:
    return json.loads(HPA_CACHE_PATH.read_text()) if HPA_CACHE_PATH.exists() else {}


def query_hpa(gene: str, ensembl_id: str, force: bool = False) -> dict | None:
    """Live HPA lookup, cached by gene symbol. Returns None if HPA has no
    entry for this Ensembl ID (rare but real -- not every Lambert TF has an
    HPA page)."""
    cache = _load_cache()
    if not force and gene in cache:
        return cache[gene]

    resp = requests.get(HPA_URL_TMPL.format(ensembl_id=ensembl_id), timeout=30)
    if resp.status_code != 200:
        cache[gene] = None
        HPA_CACHE_PATH.write_text(json.dumps(cache, indent=2))
        return None

    d = resp.json()
    nTPM = d.get("RNA tissue specific nTPM") or {}
    top_tissues = dict(sorted(nTPM.items(), key=lambda kv: float(kv[1]), reverse=True)[:8])

    result = {
        "ensembl_id": ensembl_id,
        "gene": d.get("Gene"),
        "tissue_specificity": d.get("RNA tissue specificity"),
        "tissue_distribution": d.get("RNA tissue distribution"),
        "tissue_specificity_score": d.get("RNA tissue specificity score"),
        "n_tissues_with_expression": len(nTPM),
        "top_expressed_tissues": top_tissues,
        "protein_class": d.get("Protein class"),
        "disease_involvement": d.get("Disease involvement"),
    }
    cache[gene] = result
    HPA_CACHE_PATH.write_text(json.dumps(cache, indent=2))
    return result


if __name__ == "__main__":
    import pandas as pd

    universe = pd.read_csv(config.TF_UNIVERSE_CACHE).set_index("gene_symbol")["ensembl_id"]
    for gene in ["IRF4", "PAX8", "ISL1", "EBF1", "TCF7L2", "TP63", "ZNF217", "MYCN"]:
        ensembl_id = universe.get(gene)
        if not ensembl_id:
            print(f"{gene}: no Ensembl ID in TF universe cache")
            continue
        result = query_hpa(gene, ensembl_id)
        if result is None:
            print(f"{gene}: no HPA entry")
            continue
        print(f"{gene:8s} {result['tissue_specificity']:22s} {result['tissue_distribution']:18s} "
              f"n_tissues={result['n_tissues_with_expression']:3d}  top={list(result['top_expressed_tissues'].items())[:3]}")
