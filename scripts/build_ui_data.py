#!/usr/bin/env python
"""Build ui/data.json — everything the interface renders, from real sources.

No server, no live calls: the page loads one file. Regenerate after a new run.

Needs downloads/CRISPRGeneEffect.csv, downloads/Model.csv, downloads/lambert_tfs.csv
(all gitignored, fetch commands in team/TASKS.md) and, for the structure panel,
a local mmCIF. Anything missing is skipped with a warning rather than faked.
"""
from __future__ import annotations

import json
import pathlib
import sys
import urllib.request

from dependency_scout.depmap import analyze_gene_effects, load_tf_universe
from dependency_scout.models import MediatorLink, RankedCandidate
from dependency_scout.ranking import gate, rank_all
from dependency_scout.report import build_shortlist

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOWNLOADS, UI = ROOT / "downloads", ROOT / "ui"

LANDSCAPE_CONTEXT = "Lung"          # the worked negative case: 1588 screened, 0 pass
MIN_MODELS = 15                     # Kevin's confidence floor
ROUND02_GENES = {"IRF4", "TP63", "TCF7L2", "EBF1", "PAX8", "ZNF217", "ISL1", "MYCN",
                 "RUNX2", "CEBPB", "ETV1", "ELK1", "ELF3"}
ROUND02_CONTEXTS = ["Lymphoid", "Head and Neck", "Bowel", "Ovary/Fallopian Tube",
                    "Kidney", "Peripheral Nervous System", "Myeloid", "Skin", "Breast"]
INTERFACE = {
    "RUNX2": "examples/mediator_link_runx2_med23.json",
    "CEBPB": "examples/mediator_link_cebpb_med23.json",
    "ETV1": "examples/mediator_link_etv1_med23.json",
    "ELK1": "examples/mediator_link_elk1_med23.json",
    "POU2F3": "examples/coactivator_link_pou2f3_ocat1.json",
    "FOXO4": "examples/coactivator_link_foxo4_kix.json",
}
# ELK1 MED23-binding motif and the MED23 residues lining the pocket (PMC12015215).
STRUCTURE = {
    "pdb_id": "9F6Y", "resolution_a": 3.0, "method": "cryo-EM",
    "citation": "https://doi.org/10.1038/s41467-025-59014-8",
    "chains": {"A": "MED23", "B": "ELK1 MED23-binding motif"},
    "motif_residues": list(range(374, 385)),
    "pocket_residues": [339, 343, 379, 382, 383, 533, 537],
}


def thresholds() -> dict:
    """Read the live gate constants rather than restating them, so the drawn
    boundary can never disagree with the gate that produced the points."""
    import inspect
    src = inspect.getsource(gate)
    return {
        "median_target_effect": -0.5, "target_dependent_fraction": 0.5,
        "other_dependent_fraction": 0.35, "selectivity_delta": 0.35,
        "min_models": 3, "confidence_floor": MIN_MODELS,
        "_source": "dependency_scout.ranking.gate", "_verified": "-0.5" in src,
    }


def ca_trace(cif: pathlib.Path) -> dict:
    lines = cif.read_text().splitlines()
    i = next(k for k, l in enumerate(lines) if l.startswith("_atom_site."))
    cols = []
    while lines[i].startswith("_atom_site."):
        cols.append(lines[i].strip().split(".")[1])
        i += 1
    idx = {c: n for n, c in enumerate(cols)}
    chains: dict[str, list] = {}
    for l in lines[i:]:
        if l.startswith("#"):
            break
        f = l.split()
        if len(f) < len(cols) or f[idx["label_atom_id"]] != "CA":
            continue
        chains.setdefault(f[idx["auth_asym_id"]], []).append([
            int(f[idx["auth_seq_id"]]),
            round(float(f[idx["Cartn_x"]]), 2),
            round(float(f[idx["Cartn_y"]]), 2),
            round(float(f[idx["Cartn_z"]]), 2)])
    return chains


def main() -> int:
    ge, models = DOWNLOADS / "CRISPRGeneEffect.csv", DOWNLOADS / "Model.csv"
    tf_list = DOWNLOADS / "lambert_tfs.csv"
    if not ge.exists():
        print(f"missing {ge}; see team/TASKS.md", file=sys.stderr)
        return 1

    universe = load_tf_universe(tf_list)
    print(f"TF universe: {len(universe)}")

    # 1. The landscape: every TF in one context, with its gate verdict.
    landscape = []
    for c in rank_all(analyze_gene_effects(ge, models, context=LANDSCAPE_CONTEXT,
                                           genes=universe,
                                           source_version="DepMap Public 24Q2")):
        d = c.dependency
        landscape.append({
            "gene": d.gene, "n": d.n_target_models,
            "median": round(d.median_target_effect, 3),
            "sel": round(d.selectivity_delta, 3),
            "tfrac": round(d.target_dependent_fraction, 3),
            "ofrac": round(d.other_dependent_fraction, 3),
            "p": d.mann_whitney_p,
            "pass": c.gate.eligible,
            "why": c.gate.failures,
            "low_n": d.n_target_models < MIN_MODELS,
        })
    print(f"landscape: {len(landscape)} TFs, {sum(x['pass'] for x in landscape)} pass")

    # 2. Real candidates across lineages, joined to typed interface evidence.
    candidates, seen = [], set()
    for ctx in ROUND02_CONTEXTS:
        for c in rank_all(analyze_gene_effects(ge, models, context=ctx,
                                               genes=ROUND02_GENES,
                                               source_version="DepMap Public 24Q2")):
            if c.dependency.n_target_models < MIN_MODELS or not c.gate.eligible:
                continue
            if (p := INTERFACE.get(c.dependency.gene)):
                c.mediator = MediatorLink.model_validate_json(
                    (ROOT / p).read_text(encoding="utf-8"))
            candidates.append(c)
            seen.add(c.dependency.gene)
    for gene, p in INTERFACE.items():
        if gene not in seen:
            candidates.append(RankedCandidate(
                gene=gene,
                mediator=MediatorLink.model_validate_json(
                    (ROOT / p).read_text(encoding="utf-8"))))

    sl = build_shortlist(candidates, disease_scope="pan-cancer, DepMap 24Q2")
    rows = []
    for i, c in enumerate(sl.candidates):
        ok, reason = c.shortlistable
        d = c.dependency
        rows.append({
            "gene": c.name, "context": c.disease_context,
            "n": d.n_target_models if d else None,
            "median": round(d.median_target_effect, 3) if d else None,
            "sel": round(d.selectivity_delta, 3) if d else None,
            "tfrac": round(d.target_dependent_fraction, 3) if d else None,
            "ofrac": round(d.other_dependent_fraction, 3) if d else None,
            "score": round(c.final_score, 3) if c.final_score is not None else None,
            "gate_pass": c.gate.eligible if c.gate else None,
            "gate_why": c.gate.failures if c.gate else [],
            "awaiting": c.awaiting_dependency_data,
            "shortlisted": i in sl.shortlist_indices,
            "blocked_because": reason,
            "partner": c.mediator.partner_gene,
            "involvement": c.mediator.involvement.value,
            "region": c.mediator.tf_region,
            "region_mapped": c.mediator.interacting_region_mapped,
            "tractability": c.mediator.tractability.value,
            "control": c.mediator.calibration_only,
            "concerns": c.mediator.screening_concerns,
            "ready": c.mediator.ready_for_structural_modeling,
            "claims": [{"statement": cl.statement, "support": cl.support.value,
                        "citations": cl.citations, "note": cl.note}
                       for cl in c.mediator.claims],
        })

    # 3. Structure, if a local mmCIF is present.
    structure = None
    cif = DOWNLOADS / f"{STRUCTURE['pdb_id']}.cif"
    if not cif.exists():
        try:
            urllib.request.urlretrieve(
                f"https://files.rcsb.org/download/{STRUCTURE['pdb_id']}.cif", cif)
        except Exception as e:  # offline is fine; the panel just says so
            print(f"structure skipped: {e}", file=sys.stderr)
    if cif.exists():
        structure = {**STRUCTURE, "trace": ca_trace(cif)}
        print("structure:", {k: len(v) for k, v in structure["trace"].items()})

    UI.mkdir(exist_ok=True)
    payload = {
        "generated_from": "DepMap Public 24Q2 Chronos; Lambert et al. TF catalogue v1.01",
        "landscape_context": LANDSCAPE_CONTEXT,
        "thresholds": thresholds(),
        "landscape": landscape,
        "candidates": rows,
        "structure": structure,
    }
    (UI / "data.json").write_text(json.dumps(payload, separators=(",", ":")) + "\n",
                                 encoding="utf-8")
    kb = (UI / "data.json").stat().st_size / 1024
    print(f"wrote ui/data.json ({kb:.0f} KB), {len(rows)} candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
