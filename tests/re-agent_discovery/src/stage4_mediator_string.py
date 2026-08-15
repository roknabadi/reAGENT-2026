"""
Stage 4 — Mediator connectivity filter (STRING).

For each candidate TF, queries the STRING PPI database for any recorded
association with the ~33 Mediator complex subunits, requesting every STRING
evidence channel (experimental, database, text-mining, co-expression,
neighborhood, fusion, phylogenetic-profile) rather than just the combined
score. See docs/DISCOVERY_ARCHITECTURE.md §6.

Only edges among the queried node set (TF + Mediator subunits) are returned,
so any hit is a direct TF<->subunit edge -- no bridging through third genes is
included. Bridging is explicitly out of scope for this pass.

IMPORTANT -- what a STRING hit does NOT establish:
The positive-control run below (ELK1-MED23, a cryo-EM-mapped contact) comes
back from STRING with escore=0 and dscore=0 -- its entire combined score is
text-mining (tscore=0.869). Per PROJECT.md, text-mining scores are trivially
inflated by co-citation and are not evidence of physical contact; per
CLAUDE.md and src/dependency_scout/models.py in the main repo (MediatorLink /
SupportType), a "direct" TF-Mediator contact requires a *mapped interacting
region* from direct experimental literature (structure, crosslink, mapped
two-hybrid, ITC). STRING cannot supply that, at any channel. So this stage:
  - drops a TF only when it has ZERO STRING evidence at ANY channel against
    ANY Mediator subunit -- the one genuinely hard filter in this pipeline.
  - never promotes a candidate to `direct` on STRING evidence alone, even
    when escore/dscore are high -- it only flags "worth a literature pass."
  - reports every channel per pair so the reader (or the Stage 5 ranking)
    can see exactly what kind of evidence is behind a hit, rather than
    collapsing it into one opaque combined score.
A STRING hit is a lead for Paperclip literature retrieval, never a
`MediatorLink.involvement == direct` claim by itself.
"""

import argparse
import time

import pandas as pd
import requests

import config

STRING_API = "https://string-db.org/api"
CALLER_IDENTITY = "reAGENT2026_dependency_scout"
SPECIES = 9606

# Known/already-triaged TF-Mediator pairs (see PROJECT.md controls and the
# main repo's round-01, examples/mediator_link_*.json). Run through this
# stage as a sanity check on the STRING method itself -- ELK1/ELF3 are the
# positive controls, RUNX2/CEBPB/ETV1 are already-classified comparisons --
# not as new candidates.
VALIDATION_CONTROLS = ["ELK1", "ELF3", "RUNX2", "CEBPB", "ETV1"]


def load_stage1_candidate_tfs(qvalue_threshold: float = 0.05) -> list[str]:
    """Unique TFs with a significant, dependency-flagged Stage-1 hit
    (see stage1_depmap.py). This is the live candidate set the Mediator
    filter is meant to triage."""
    path = config.RESULTS_DIR / "stage1_dependency_hits.csv"
    df = pd.read_csv(path)
    sig = df[(df["dependency_flag"] == True) & (df["qvalue"] < qvalue_threshold)]  # noqa: E712
    return sorted(sig["tf"].unique().tolist())


def resolve_string_ids(symbols: list[str]) -> dict[str, str]:
    """Batch-resolve gene symbols to unambiguous STRING identifiers."""
    resp = requests.post(
        f"{STRING_API}/json/get_string_ids",
        data={
            "identifiers": "\r".join(symbols),
            "species": SPECIES,
            "limit": 1,
            "caller_identity": CALLER_IDENTITY,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return {row["queryItem"]: row["stringId"] for row in resp.json()}


def query_tf_mediator_network(tf_string_id: str, mediator_string_ids: list[str]) -> list[dict]:
    """Full-evidence STRING network among {TF} u {Mediator subunits}."""
    ids = [tf_string_id] + mediator_string_ids
    resp = requests.post(
        f"{STRING_API}/json/network",
        data={
            "identifiers": "\r".join(ids),
            "species": SPECIES,
            "network_type": "functional",  # all evidence channels, not just physical
            "required_score": 0,
            "caller_identity": CALLER_IDENTITY,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def run(qvalue_threshold: float = 0.05, sleep_s: float = 1.0):
    stage1_tfs = load_stage1_candidate_tfs(qvalue_threshold)
    candidates = sorted(set(stage1_tfs) | set(VALIDATION_CONTROLS))
    print(
        f"[stage4] {len(stage1_tfs)} Stage-1 dependency candidates + "
        f"{len(VALIDATION_CONTROLS)} validation controls = {len(candidates)} TFs to query"
    )

    all_symbols = candidates + config.MEDIATOR_SUBUNITS
    id_map = resolve_string_ids(all_symbols)
    unresolved = [s for s in all_symbols if s not in id_map]
    if unresolved:
        print(f"[stage4] WARNING: STRING could not resolve: {unresolved}")

    mediator_string_ids = [id_map[m] for m in config.MEDIATOR_SUBUNITS if m in id_map]
    mediator_id_to_symbol = {id_map[m]: m for m in config.MEDIATOR_SUBUNITS if m in id_map}

    rows = []
    tfs_with_no_evidence = []
    for i, tf in enumerate(candidates):
        if tf not in id_map:
            print(f"[stage4] skip {tf}: no STRING id resolved")
            tfs_with_no_evidence.append(tf)
            continue
        print(f"[stage4] ({i + 1}/{len(candidates)}) querying {tf} vs {len(mediator_string_ids)} Mediator subunits ...")
        edges = query_tf_mediator_network(id_map[tf], mediator_string_ids)
        tf_edges = [
            e
            for e in edges
            if (e["preferredName_A"] == tf and e["stringId_B"] in mediator_id_to_symbol)
            or (e["preferredName_B"] == tf and e["stringId_A"] in mediator_id_to_symbol)
        ]
        if not tf_edges:
            tfs_with_no_evidence.append(tf)
        for e in tf_edges:
            partner_id = e["stringId_B"] if e["preferredName_A"] == tf else e["stringId_A"]
            partner = mediator_id_to_symbol[partner_id]
            rows.append(
                {
                    "tf": tf,
                    "mediator_subunit": partner,
                    "is_validation_control": tf in VALIDATION_CONTROLS,
                    "combined_score": e.get("score"),
                    "experimental_score": e.get("escore"),
                    "database_score": e.get("dscore"),
                    "textmining_score": e.get("tscore"),
                    "coexpression_score": e.get("ascore"),
                    "neighborhood_score": e.get("nscore"),
                    "fusion_score": e.get("fscore"),
                    "phylogenetic_score": e.get("pscore"),
                    "has_experimental_or_database": (e.get("escore") or 0) > 0 or (e.get("dscore") or 0) > 0,
                }
            )
        time.sleep(sleep_s)  # STRING fair-use: batch, don't hammer

    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values(
            ["tf", "has_experimental_or_database", "combined_score"],
            ascending=[True, False, False],
        )
    out_path = config.RESULTS_DIR / "stage4_mediator_string_hits.csv"
    result.to_csv(out_path, index=False)

    n_with_evidence = len(candidates) - len(tfs_with_no_evidence)
    print(f"[stage4] {len(result)} TF-Mediator edges found across {n_with_evidence} TFs")
    if tfs_with_no_evidence:
        print(f"[stage4] DROPPED (no STRING evidence at any channel, any Mediator subunit): {tfs_with_no_evidence}")
    print(f"[stage4] wrote {out_path}")
    return result, tfs_with_no_evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stage 4: Mediator connectivity filter (STRING)")
    parser.add_argument("--qvalue-threshold", type=float, default=0.05)
    parser.add_argument("--sleep", type=float, default=1.0)
    args = parser.parse_args()
    run(qvalue_threshold=args.qvalue_threshold, sleep_s=args.sleep)
