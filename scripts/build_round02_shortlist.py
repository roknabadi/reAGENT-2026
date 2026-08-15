#!/usr/bin/env python
"""Round-02 shortlist: real DepMap dependencies + Andrey's round-01 interface evidence.

Reproduces the quantitative half of Kevin's independent scan through this repo's
own gates, then joins it to the typed Mediator evidence so the two halves of the
pipeline meet in one artifact for the first time.

Needs downloads/CRISPRGeneEffect.csv and downloads/Model.csv (gitignored, see team/TASKS.md).
"""
import json, pathlib, sys
from dependency_scout.depmap import analyze_gene_effects
from dependency_scout.models import MediatorLink, RankedCandidate
from dependency_scout.ranking import rank_all
from dependency_scout.report import build_shortlist, render_markdown

GENES = {"IRF4", "TP63", "TCF7L2", "EBF1", "PAX8", "ZNF217", "ISL1", "MYCN",
         "RUNX2", "CEBPB", "ETV1", "ELK1", "ELF3"}
CONTEXTS = ["Lymphoid", "Head and Neck", "Bowel", "Ovary/Fallopian Tube", "Kidney",
            "Peripheral Nervous System", "Myeloid", "Skin", "Breast"]
MIN_MODELS = 15  # Kevin's confidence floor; below this stays flagged, never shortlisted

INTERFACE = {  # Andrey's round-01 typed evidence, keyed by TF
    "RUNX2": "examples/mediator_link_runx2_med23.json",
    "CEBPB": "examples/mediator_link_cebpb_med23.json",
    "ETV1": "examples/mediator_link_etv1_med23.json",
    "ELK1": "examples/mediator_link_elk1_med23.json",
}


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    ge, models = root / "downloads/CRISPRGeneEffect.csv", root / "downloads/Model.csv"
    if not ge.exists():
        print(f"missing {ge}; see team/TASKS.md for the fetch command", file=sys.stderr)
        return 1

    candidates, seen = [], set()
    for ctx in CONTEXTS:
        for c in rank_all(analyze_gene_effects(ge, models, context=ctx, genes=GENES,
                                               source_version="DepMap Public 24Q2")):
            if c.dependency.n_target_models < MIN_MODELS:
                continue
            if not c.gate.eligible:
                continue
            path = INTERFACE.get(c.dependency.gene)
            if path:
                c.mediator = MediatorLink.model_validate_json(
                    (root / path).read_text(encoding="utf-8"))
            candidates.append(c)
            seen.add(c.dependency.gene)

    # Andrey's interface-only candidates: real contact evidence, no dependency numbers.
    for gene, path in INTERFACE.items():
        if gene in seen:
            continue
        candidates.append(RankedCandidate(
            gene=gene,
            mediator=MediatorLink.model_validate_json((root / path).read_text(encoding="utf-8"))))

    sl = build_shortlist(candidates, disease_scope="pan-cancer, DepMap 24Q2 lineages")
    out = root / "outputs"
    out.mkdir(exist_ok=True)
    (out / "round02_shortlist.json").write_text(
        json.dumps(sl.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
    (out / "round02_shortlist.md").write_text(render_markdown(sl), encoding="utf-8")
    print(render_markdown(sl))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
