"""The live run behind the interface: the question drives the pipeline.

Two rewrites are folded into this file, and both were about the same thing —
the interface reporting a conclusion the rest of the project would not have
reached.

The first version was a facade: one generic Paperclip search capped at six
results and a precomputed Lung landscape served for every question, so the
plot, the table and the structure were identical whatever you asked. Only the
sources rail responded, which is why the numbers looked instant and hardcoded.
They were.

The second computed its numbers honestly but applied its own gate — a four-way
AND that no other part of the project used — against a lineage resolved from a
hand-written list of eighteen regexes. That fails in a way a facade does not,
because the output is real and defensible-looking and still wrong: asking
about small cell lung cancer got you an answer about Lung, and pooling SCLC
with every other lung tumour pushes ASCL1, POU2F3, NEUROD1 and INSM1 below
threshold. Four master regulators, invisible, with no error anywhere.

So the decisions are made elsewhere now, by the modules that own them:

  resolve.resolve       free text -> a context the loaded data can answer
                        about, at lineage OR subtype granularity, or an
                        abstention
  verdict.scan_context  the canonical gate from stage1_depmap.py, applied to
                        that context
  verdict.shortlist     the three that clear it
  literature.gather     six evidence axes per candidate, on-target only

Nothing here decides whether a TF is a dependency, and no stage reports `done`
on the strength of a different stage's success.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pandas as pd

from dependency_scout.models import MediatorLink
from reagent_workflow import verdict as V
from reagent_workflow.interface import parse_mmcif
from reagent_workflow.literature import gather
from reagent_workflow.resolve import resolve
from reagent_workflow.site import build_search_site, receptor_residues

DOWNSTREAM = ("literature", "discovery", "ranking", "specificity", "site",
              "structure", "screening", "experiment")


def _model_table(model_path) -> pd.DataFrame:
    return pd.read_csv(model_path, usecols=["ModelID", "OncotreeLineage",
                                            "OncotreeSubtype"])


def run_live(question: str, data_paths, cfg, emit: Callable[[str, dict], None],
             interface_evidence: dict[str, MediatorLink] | None = None,
             top_n: int = 3, free_receptor: Path | None = None) -> None:
    """Stream the real pipeline for one question. `emit(event, payload)` per step."""
    ge_path, model_path, tf_path = data_paths
    interface_evidence = interface_evidence or {}

    if not Path(ge_path).exists():
        emit("stage", {"id": "question", "state": "blocked",
                       "detail": f"{Path(ge_path).name} is not present",
                       "note": "see team/TASKS.md for the fetch command"})
        for sid in DOWNSTREAM:
            emit("stage", {"id": sid, "state": "pending", "detail": "not run"})
        emit("done", {"ok": False})
        return

    # ── question -> context ────────────────────────────────────────────────
    model = _model_table(model_path)
    res = resolve(question, model)
    if not res.ok:
        emit("stage", {"id": "question", "state": "blocked",
                       "detail": question or "no question given",
                       "note": res.note})
        for sid in DOWNSTREAM:
            emit("stage", {"id": sid, "state": "pending", "detail": "not run",
                           "note": "waiting on a disease context"})
        emit("done", {"ok": False})
        return

    m = res.match
    alts = ", ".join(f"{a.context} ({a.score:.2f})" for a in res.alternatives[:3])
    emit("stage", {
        "id": "question", "state": "done", "detail": question,
        "note": (f"resolved to {m.context} — {m.level} level, {m.n_models} models"
                 + (f", within {m.parent_lineage}" if m.parent_lineage else "")
                 + f". {res.note}."
                 + (f" Also considered: {alts}." if alts else "")),
        "context": m.context, "level": m.level})

    # ── discovery: the canonical scan, for THIS context ────────────────────
    emit("stage", {"id": "discovery", "state": "running",
                   "detail": f"screening the TF universe against {m.context}"})
    ge, model_full = V.load_matrix(str(ge_path), str(model_path), str(tf_path))
    verdicts = V.scan_context(ge, model_full, m.context, level=m.level)
    if not verdicts:
        emit("stage", {"id": "discovery", "state": "blocked",
                       "detail": f"{m.context} has too few screened models to test",
                       "note": f"{m.n_models} models are annotated {m.context}, but "
                               "the CRISPR matrix covers fewer than the minimum; "
                               "a broader context would be testable."})
        for sid in DOWNSTREAM[2:]:
            emit("stage", {"id": sid, "state": "pending", "detail": "not run"})
        emit("done", {"ok": False})
        return

    n_lines = verdicts[0].n_target
    emit("stage", {
        "id": "discovery", "state": "done",
        "detail": f"{len(verdicts)} TFs across {n_lines} {m.context} models",
        "note": (f"DepMap {V.DEPMAP_RELEASE} Chronos, Lambert TF catalogue. Gate "
                 f"from {V.CANONICAL_SOURCE} — the same verdict the batch scan "
                 f"reaches. Computed now, not cached."),
        "context": m.context, "level": m.level})

    emit("landscape", {"context": m.context, "level": m.level, "points": [
        {"gene": v.gene,
         "median": round(v.median_target, 3),
         "sel": round(v.median_other - v.median_target, 3),
         "tfrac": round(v.target_dependent_fraction, 3),
         "ofrac": round(v.other_dependent_fraction, 3),
         "n": v.n_target, "q": v.qvalue, "route": v.route,
         "pass": v.significant, "flag": v.dependency_flag,
         "why": V.to_candidate(v).gate.failures,
         "low_n": v.low_n}
        for v in verdicts]})

    # ── gates ──────────────────────────────────────────────────────────────
    top = V.shortlist(verdicts, top_n)
    flagged = [v for v in verdicts if v.dependency_flag]
    routes: dict[str, int] = {}
    for v in flagged:
        routes[v.route] = routes.get(v.route, 0) + 1
    near = [v for v in flagged if not v.significant]
    emit("stage", {
        "id": "ranking", "state": "done" if top else "abstained",
        "detail": (f"{len(top)} shortlisted of {len(flagged)} that clear the gate: "
                   + ", ".join(f"{v.gene} (via {v.route})" for v in top)) if top
                  else f"{len(flagged)} of {len(verdicts)} clear the gate, none at FDR "
                       f"{V.FDR_ALPHA}",
        "note": ("; ".join(f"{n} via {r}" for r, n in sorted(routes.items()))
                 + (f". {len(near)} more pass the gate but miss FDR: "
                    + ", ".join(f"{v.gene} q={v.qvalue:.2f}" for v in near[:4])
                    if near else ""))})

    emit("candidates", {"rows": [
        {"gene": v.gene, "context": v.context, "level": v.context_level,
         "n": v.n_target,
         "median": round(v.median_target, 3),
         "sel": round(v.median_other - v.median_target, 3),
         "tfrac": round(v.target_dependent_fraction, 3),
         "ofrac": round(v.other_dependent_fraction, 3),
         "q": v.qvalue, "route": v.route,
         "gate_pass": True, "gate_why": [], "awaiting": False,
         "shortlisted": True,
         # The partner is what this run SEARCHED against, not something it
         # discovered. Nothing computed here establishes that MED23 is this
         # TF's coactivator.
         "partner": cfg.partner_gene, "partner_is_query": True,
         "involvement": "unknown", "region": None, "region_mapped": False,
         "tractability": "unknown", "control": False, "concerns": [],
         "ready": False, "blocked_because": None, "claims": []}
        for v in top]})

    # ── literature: six axes per candidate, on-target only ─────────────────
    if not top:
        emit("stage", {"id": "literature", "state": "abstained",
                       "detail": "no candidate survived the gate to search evidence for",
                       "note": "Retrieval follows the gate; searching all "
                               f"{len(verdicts)} TFs would find something for "
                               "every one of them."})
    else:
        emit("stage", {"id": "literature", "state": "running",
                       "detail": f"six evidence axes for {len(top)} candidate(s)"})
        total_on, total_axes, hits, leads, errors = 0, 0, 0, [], []
        for v in top:
            ev, papers, errs = gather(v.gene, m.context, cfg.partner_gene, per_axis=4)
            errors.extend(errs)
            for p in papers:
                emit("paper", {"title": p.title, "id": p.accession, "url": p.url,
                               "abstract": p.abstract, "axis": p.axis,
                               "gene": v.gene, "support": p.suggested_support})
            total_on += len(papers)
            total_axes += len(ev.axes)
            hits += len(ev.axes_with_hits)
            if ev.has_coactivator_lead:
                leads.append(v.gene)
            emit("evidence", {"gene": v.gene, "axes": {
                a: {"on_target": r.n_on_target, "returned": r.n_papers,
                    "note": r.note, "query": r.query}
                for a, r in ev.axes.items()}})
        if errors:
            emit("stage", {"id": "literature", "state": "blocked",
                           "detail": f"{len(errors)} axis search(es) failed",
                           "note": "; ".join(errors[:2])})
        else:
            emit("stage", {
                "id": "literature", "state": "done" if total_on else "abstained",
                "detail": f"{total_on} on-target papers across {hits}/{total_axes} axes",
                "note": ("Retrieved, not read. Only papers naming the gene are counted: "
                         "semantic search returns nearest matches, and nearest is not "
                         "relevant. "
                         + (f"Coactivator leads: {', '.join(leads)}." if leads
                            else "No coactivator lead on any candidate."))})

    # ── specificity ────────────────────────────────────────────────────────
    emit("stage", {
        "id": "specificity", "state": "done" if top else "abstained",
        "detail": f"{len(top)} candidate(s) with a selective dependency in {m.context}",
        "note": "Cancer-cell selectivity is not normal-tissue safety."})

    # ── structure ──────────────────────────────────────────────────────────
    # No ensemble is computed inline, so there is no consensus for this run.
    consensus = None
    emit("stage", {
        "id": "structure", "state": "abstained",
        "detail": "no ensemble for this context",
        "note": "Structural discovery is a separate costed GPU step, not run inline. "
                "The 3D view shows the ELK1-MED23 reference structure, which is "
                "calibration, not this run."})

    # ── druggable site ─────────────────────────────────────────────────────
    #
    # This stage used to go `done` whenever some candidate had
    # `interacting_region_mapped`. Those are different claims. A mapped
    # interacting region says a contact is documented somewhere on the TF; a
    # druggable site is a box of receptor coordinates you can dock into. The
    # interface reported the second on evidence for the first, so a run with no
    # structure at all showed a completed site stage.
    #
    # `receptor_residues` allows exactly two origins — an ensemble consensus,
    # or a published receptor-side pocket — and returns blockers otherwise.
    mapped = [v.gene for v in top
              if (link := interface_evidence.get(v.gene))
              and link.interacting_region_mapped and not link.calibration_only]
    residues, basis, blockers = receptor_residues(cfg.partner_gene,
                                                  consensus=consensus)
    site = None
    if not blockers and free_receptor and Path(free_receptor).exists():
        site = build_search_site(parse_mmcif(Path(free_receptor)), residues,
                                 cfg.structure, receptor_path=str(free_receptor))
        site.basis = basis

    if site is not None and site.defensible:
        emit("stage", {
            "id": "site", "state": "done",
            "detail": (f"{site.size[0]:.1f} x {site.size[1]:.1f} x {site.size[2]:.1f} A "
                       f"box on {cfg.partner_gene} around {len(site.residues)} residues"),
            "note": f"From {basis}. Screened against the free receptor, not a "
                    "TF-occupied one.",
            "center": site.center, "size": site.size, "residues": site.residues})
    else:
        why = blockers or (site.blockers if site else
                           [f"the free {cfg.partner_gene} structure is not on disk"])
        emit("stage", {
            "id": "site", "state": "abstained",
            "detail": "; ".join(why)[:180],
            "note": ("A docking box needs receptor-side coordinates: an ensemble "
                     "consensus this run computed, or a published structure. "
                     + (f"{len(mapped)} candidate(s) here have a documented "
                        f"interacting region ({', '.join(mapped)}), which locates a "
                        "contact on the TF and is not a pocket on "
                        f"{cfg.partner_gene}." if mapped
                        else "No candidate here has a documented contact either."))})

    # ── screening ──────────────────────────────────────────────────────────
    emit("stage", {
        "id": "screening",
        "state": "pending" if (site and site.defensible) else "abstained",
        "detail": ("ready to dock against the site above" if site and site.defensible
                   else "no defensible site, so no screen"),
        "note": "Docking without a site finds something everywhere and means nothing."})

    # ── next experiment ────────────────────────────────────────────────────
    emit("stage", {
        "id": "experiment", "state": "done",
        "detail": (f"test whether {top[0].gene} dependence in {m.context} requires a "
                   "coactivator contact" if top
                   else f"no TF clears the gate in {m.context} at {m.level} granularity"),
        "note": ("The dependency is real and the contact is undocumented; closing "
                 "that gap is what unblocks the rest." if top
                 else ("Either this context is not TF-addicted, or it needs finer "
                       "resolution than " + m.level + ". Both are results."))})
    emit("done", {"ok": True})
