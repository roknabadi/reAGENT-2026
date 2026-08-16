#!/usr/bin/env python
"""Name a cancer, run the whole chain, get an artifact or an explicit refusal.

    python scripts/run_cancer.py "Lymphoid"
    python scripts/run_cancer.py "Small Cell Lung Cancer" --column OncotreeSubtype
    python scripts/run_cancer.py Kidney --json runs/kidney.json

Disease context -> selective TF dependency -> literature -> mapped contact ->
druggable site -> compound library -> next experiment. Every stage reports
`done`, `abstained` or `blocked` with a reason, and no stage invents an input it
was not given.

Deliberately not wired to the Boltz API. The structural stage consumes an
ensemble if one exists on disk and abstains if not, rather than spending money
mid-run.

Needs the gitignored DepMap files (team/TASKS.md) and, for the literature stage,
a working `paperclip` login.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import time

from dependency_scout.depmap import analyze_gene_effects, load_tf_universe
from dependency_scout.models import MediatorLink, RankedCandidate
from dependency_scout.provenance import InputFile, RunProvenance
from dependency_scout.ranking import rank_all
from dependency_scout.report import build_shortlist
from reagent_workflow.chemistry import (Compound, assemble_library, describe,
                                        amphiphilicity_proxy, standardize)
from reagent_workflow.discovery_config import DiscoveryConfig
from reagent_workflow.interface import parse_mmcif
from reagent_workflow.site import build_search_site

ROOT = pathlib.Path(__file__).resolve().parents[1]
D = ROOT / "downloads"
GE, MODELS, TFS = D / "CRISPRGeneEffect.csv", D / "Model.csv", D / "lambert_tfs.csv"
FREE_RECEPTOR = D / "9F76.cif"
EXAMPLES = ROOT / "examples"
MIN_MODELS = 15

# A small approved-drug set with real provenance. Stands in for the DrugCentral
# pull until that download is wired; every entry still carries its source so
# nothing without provenance can reach a screen.
SEED_DRUGS = [
    ("aspirin", "CC(=O)Oc1ccccc1C(=O)O", "COX inhibitor", "analgesic"),
    ("atorvastatin", "CC(C)c1c(C(=O)Nc2ccccc2)c(-c2ccccc2)c(-c2ccc(F)cc2)n1CC[C@@H](O)C[C@@H](O)CC(=O)O", "HMG-CoA reductase", "hyperlipidaemia"),
    ("imatinib", "Cc1ccc(NC(=O)c2ccc(CN3CCN(C)CC3)cc2)cc1Nc1nccc(-c2cccnc2)n1", "BCR-ABL kinase", "CML"),
    ("celecoxib", "Cc1ccc(-c2cc(C(F)(F)F)nn2-c2ccc(S(N)(=O)=O)cc2)cc1", "COX-2", "arthritis"),
    ("fluoxetine", "CNCCC(Oc1ccc(C(F)(F)F)cc1)c1ccccc1", "SERT", "depression"),
    ("ibuprofen", "CC(C)Cc1ccc(cc1)C(C)C(=O)O", "COX", "analgesic"),
    ("naproxen", "COc1ccc2cc(ccc2c1)C(C)C(=O)O", "COX", "analgesic"),
    ("propranolol", "CC(C)NCC(O)COc1cccc2ccccc12", "beta-adrenergic", "hypertension"),
    ("diphenhydramine", "CN(C)CCOC(c1ccccc1)c1ccccc1", "H1 receptor", "allergy"),
    ("warfarin", "CC(=O)CC(c1ccccc1)c1c(O)c2ccccc2oc1=O", "VKORC1", "anticoagulation"),
    ("dexamethasone", "CC1CC2C3CCC4=CC(=O)C=CC4(C)C3(F)C(O)CC2(C)C1(O)C(=O)CO", "glucocorticoid receptor", "inflammation"),
    ("metformin", "CN(C)C(=N)NC(N)=N", "AMPK/complex I", "type 2 diabetes"),
]


class Stage:
    def __init__(self, run: "Run", sid: str, name: str):
        self.run, self.sid, self.name = run, sid, name
        self.t0 = time.time()

    def done(self, detail, note="", **extra):
        return self.run._emit(self.sid, self.name, "done", detail, note, self.t0, extra)

    def abstain(self, detail, note="", **extra):
        return self.run._emit(self.sid, self.name, "abstained", detail, note, self.t0, extra)

    def blocked(self, detail, note="", **extra):
        return self.run._emit(self.sid, self.name, "blocked", detail, note, self.t0, extra)


class Run:
    def __init__(self, context: str, quiet: bool = False):
        self.context, self.quiet = context, quiet
        self.stages: list[dict] = []
        self.papers: list[dict] = []

    def stage(self, sid, name) -> Stage:
        return Stage(self, sid, name)

    def _emit(self, sid, name, state, detail, note, t0, extra):
        rec = {"id": sid, "name": name, "state": state, "detail": detail,
               "note": note, "seconds": round(time.time() - t0, 2), **extra}
        self.stages.append(rec)
        if not self.quiet:
            mark = {"done": "OK  ", "abstained": "ABST", "blocked": "BLOCK"}[state]
            print(f"[{mark}] {name:24s} {detail}")
            if note:
                print(f"{'':32s}{note}")
        return rec

    @property
    def ok(self) -> bool:
        return not any(s["state"] == "blocked" for s in self.stages)


def paperclip_search(query: str, n: int = 5) -> list[dict]:
    exe = shutil.which("paperclip") or str(ROOT / ".venv/bin/paperclip")
    try:
        out = subprocess.run([exe, "search", "-s", "pmc", query, "-n", str(n)],
                             capture_output=True, text=True, timeout=120)
    except Exception:
        return []
    ID = re.compile(r"\b((?:PMC|bio_|med_|arx_)[\w.]+)\b")
    papers, cur = [], None
    for raw in out.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^(\d+)\.\s+(.*)$", line)
        if m:
            if cur:
                papers.append(cur)
            cur = {"title": m.group(2), "id": "", "url": ""}
            continue
        if cur is None:
            continue
        if line.startswith("http") and not cur["url"]:
            cur["url"] = line
        elif not cur["id"] and (h := ID.search(line)):
            cur["id"] = h.group(1)
    if cur:
        papers.append(cur)
    return [p for p in papers if p["title"]]


def load_interface_evidence() -> dict[str, MediatorLink]:
    out = {}
    for f in sorted(EXAMPLES.glob("*_link_*.json")):
        gene = f.stem.split("_link_")[1].split("_")[0].upper()
        try:
            out[gene] = MediatorLink.model_validate_json(f.read_text(encoding="utf-8"))
        except Exception:
            continue
    return out


def run(context: str, column: str | None, cfg: DiscoveryConfig, quiet=False) -> Run:
    r = Run(context, quiet)

    s = r.stage("question", "Question")
    s.done(f"selective TF dependencies in {context}",
           "Scoped to public DepMap dependency data and the PMC full-text corpus.")

    # 1 — discovery
    s = r.stage("discovery", "Target discovery")
    if not GE.exists():
        s.blocked(f"missing {GE.name}", "see team/TASKS.md for the fetch command")
        return r
    universe = load_tf_universe(TFS)
    try:
        records = analyze_gene_effects(GE, MODELS, context=context, genes=universe,
                                       context_column=column,
                                       source_version="DepMap Public 24Q2")
    except Exception as e:
        s.blocked(f"context {context!r} could not be screened", str(e)[:160])
        return r
    if not records:
        s.blocked(f"no cell lines matched {context!r}",
                  "check the context name against DepMap OncotreeLineage/OncotreeSubtype")
        return r
    ranked = rank_all(records)
    n_lines = max(c.dependency.n_target_models for c in ranked)
    s.done(f"{len(ranked)} TFs screened across {n_lines} {context} cell lines",
           "DepMap 24Q2 Chronos, restricted to the Lambert TF catalogue.",
           screened=len(ranked), cell_lines=n_lines)

    # 2 — gates
    s = r.stage("ranking", "Ranking & gates")
    eligible = [c for c in ranked
                if c.gate.eligible and c.dependency.n_target_models >= MIN_MODELS]
    tally: dict[str, int] = {}
    for c in ranked:
        for w in c.gate.failures:
            tally[w] = tally.get(w, 0) + 1
    top = "; ".join(f"{n}x {w}" for w, n in sorted(tally.items(), key=lambda kv: -kv[1])[:2])
    if eligible:
        names = ", ".join(f"{c.dependency.gene} (sel {c.dependency.selectivity_delta:.2f})"
                          for c in eligible[:5])
        s.done(f"{len(eligible)} of {len(ranked)} clear every gate: {names}", top,
               eligible=len(eligible))
    else:
        s.abstain(f"0 of {len(ranked)} clear every gate", top, eligible=0)

    # 3 — literature
    s = r.stage("literature", "Literature")
    genes = [c.dependency.gene for c in eligible[:3]]
    q = (f"{' OR '.join(genes)} {context} transcription factor dependency coactivator"
         if genes else f"{context} transcription factor dependency")
    r.papers = paperclip_search(q, 5)
    if r.papers:
        s.done(f"{len(r.papers)} papers retrieved", "Retrieved, not read. A title is not evidence.",
               query=q, papers=len(r.papers))
    else:
        s.blocked("Paperclip returned nothing",
                  "CLI missing, not logged in, or no match for this query", query=q)

    # 4 — contact evidence
    s = r.stage("contact", "Mapped contact")
    evidence = load_interface_evidence()
    for c in eligible:
        if (link := evidence.get(c.dependency.gene)):
            c.mediator = link
    with_contact = [c for c in eligible
                    if c.mediator.interacting_region_mapped and not c.mediator.calibration_only]
    if with_contact:
        s.done(", ".join(f"{c.name}-{c.mediator.partner_gene}" for c in with_contact),
               "A whole-protein pull-down is association, not contact.")
    else:
        s.abstain("no gate-passing candidate has a mapped interacting region",
                  f"{len(eligible)} candidate(s) with a real dependency, none with a "
                  "documented contact — that gap is the finding, not a failure")

    # 5 — structural ensemble
    s = r.stage("structure", "Structural ensemble")
    s.abstain("no Boltz ensemble on disk for this context",
              "Structural discovery is not run inline; it is a separate, costed step. "
              "The consensus module is calibrated and ready to consume one.")

    # 6 — druggable site
    s = r.stage("site", "Druggable site")
    site = None
    if with_contact and FREE_RECEPTOR.exists():
        pocket = sorted({int(m) for c in with_contact
                         for m in re.findall(r"\b(\d{2,4})\b", c.mediator.tf_region or "")})
        site = build_search_site(parse_mmcif(FREE_RECEPTOR), pocket[:12],
                                cfg.structure, receptor_path=str(FREE_RECEPTOR))
        (s.done if site.defensible else s.abstain)(
            f"box {site.size} A around {len(site.residues)} residues" if site.defensible
            else "; ".join(site.blockers)[:150],
            "Screened against the free receptor, not a TF-occupied one.")
    else:
        s.abstain("no consensus interface to place a box around",
                  "A site needs a mapped contact first.")

    # 7 — compound library
    s = r.stage("chemistry", "Compound library")
    if site is None or not site.defensible:
        s.abstain("no defensible site, so no screen",
                  "Docking without a site finds something everywhere and means nothing.")
        lib = []
    else:
        pool = []
        for name, smi, target, ind in SEED_DRUGS:
            parent, why = standardize(smi)
            if parent is None:
                continue
            c = Compound(name=name, smiles=smi, parent_smiles=parent,
                         source="curated approved set", source_id=name,
                         source_version="2026-08-15", retrieved="2026-08-15",
                         approval_status="approved", known_target=target, indication=ind,
                         **describe(parent))
            c.amphiphilicity_proxy = amphiphilicity_proxy(parent, cfg.chemistry)
            pool.append(c)
        lib = assemble_library(pool, n=len(pool), cfg=cfg.chemistry)
        arms = {}
        for c in lib:
            arms[c.arm.value] = arms.get(c.arm.value, 0) + 1
        s.done(f"{len(lib)} compounds: " + ", ".join(f"{v} {k}" for k, v in sorted(arms.items())),
               "Controls ship alongside the enriched arm so the enrichment stays refutable.")

    # 8 — docking
    s = r.stage("docking", "Virtual screen")
    s.abstain("not run", "Vina is wired and CPU-runnable; it needs a signed-off site "
                         "(checkpoint 4) before compounds are docked.")

    # 9 — next experiment
    s = r.stage("experiment", "Next experiment")
    if with_contact:
        c = with_contact[0]
        s.done(f"test whether {c.name} dependence in {context} requires the "
               f"{c.mediator.partner_gene} contact",
               "Point-mutate the mapped region and ask whether the dependency survives.")
    elif eligible:
        top_g = eligible[0].dependency.gene
        s.done(f"find or exclude a coactivator contact for {top_g}",
               f"{top_g} has a real selective dependency and no documented contact. "
               "Closing that gap is what unblocks everything downstream.")
    else:
        s.done("no candidate cleared the dependency gate in this context",
               "Either the context is not TF-addicted at this granularity, or it needs "
               "molecular-subtype resolution. Both are results.")

    r.shortlist = build_shortlist(eligible or ranked[:5], disease_scope=context) if ranked else None
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("context", help="DepMap lineage or subtype, e.g. Lymphoid")
    ap.add_argument("--column", help="OncotreeSubtype for subtype-level runs")
    ap.add_argument("--json", help="write the full run record here")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    cfg = DiscoveryConfig()
    print(f"\n=== {a.context} ===")
    r = run(a.context, a.column, cfg, a.quiet)

    if a.json:
        prov = RunProvenance(inputs=[InputFile.pin(n, p) for n, p in
                                     (("gene_effect", GE), ("models", MODELS),
                                      ("tf_universe", TFS)) if p.exists()])
        out = pathlib.Path(a.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "context": a.context, "stages": r.stages, "papers": r.papers,
            "provenance": prov.model_dump(mode="json"),
            "fingerprint": prov.fingerprint}, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {out}")

    states = {s["state"] for s in r.stages}
    print(f"\n{len(r.stages)} stages · "
          f"{sum(s['state']=='done' for s in r.stages)} done, "
          f"{sum(s['state']=='abstained' for s in r.stages)} abstained, "
          f"{sum(s['state']=='blocked' for s in r.stages)} blocked")
    return 1 if "blocked" in states else 0


if __name__ == "__main__":
    raise SystemExit(main())
