"""
Stage 2 (simplified) -- ENCODE TF ChIP-seq coverage check. Real live REST
API, no key.

This is a deliberately lighter version of what docs/DISCOVERY_ARCHITECTURE.md
SS4 originally specified (full rE2G enhancer-to-gene linking + biosample-matched
overlap). That pipeline needs biosample-to-disease-context mapping this repo
doesn't have yet. What this checks instead, and is honest about only
checking: does ENCODE have ANY TF ChIP-seq experiment for this gene at all,
and if so, in which biosample -- a much cheaper "is this TF's genome-wide
binding pattern characterized anywhere" signal, enrichment only, never a gate.
A biosample name matching the TF's own disease/lineage context (e.g. GM12878,
a lymphoblastoid line, for a Lymphoid-context TF) is a coincidental bonus,
not something this code verifies -- read it yourself before treating it as a
match.
"""

import json

import requests

import config

ENCODE_CACHE_PATH = config.RESULTS_DIR / "encode_cache.json"
ENCODE_URL = "https://www.encodeproject.org/search/"


def _load_cache() -> dict:
    return json.loads(ENCODE_CACHE_PATH.read_text()) if ENCODE_CACHE_PATH.exists() else {}


def query_encode_chipseq(gene: str, force: bool = False) -> dict:
    cache = _load_cache()
    if not force and gene in cache:
        return cache[gene]

    resp = requests.get(
        ENCODE_URL,
        params={
            "type": "Experiment", "target.label": gene, "assay_title": "TF ChIP-seq",
            "status": "released", "format": "json", "limit": 25,
        },
        headers={"Accept": "application/json"}, timeout=30,
    )
    if resp.status_code == 404:
        # ENCODE returns 404 (not an empty 200) when a search matches nothing
        result = {"gene": gene, "n_experiments": 0, "biosamples": []}
        cache[gene] = result
        ENCODE_CACHE_PATH.write_text(json.dumps(cache, indent=2))
        return result
    resp.raise_for_status()
    d = resp.json()
    biosamples = sorted({
        r.get("biosample_ontology", {}).get("term_name")
        for r in d.get("@graph", [])
        if r.get("biosample_ontology", {}).get("term_name")
    })
    result = {"gene": gene, "n_experiments": d.get("total", 0), "biosamples": biosamples}
    cache[gene] = result
    ENCODE_CACHE_PATH.write_text(json.dumps(cache, indent=2))
    return result


if __name__ == "__main__":
    for gene in ["IRF4", "PAX8", "ISL1", "EBF1", "TCF7L2", "TP63", "ZNF217", "MYCN"]:
        r = query_encode_chipseq(gene)
        print(f"{gene:8s} n_experiments={r['n_experiments']:3d}  biosamples={r['biosamples']}")
