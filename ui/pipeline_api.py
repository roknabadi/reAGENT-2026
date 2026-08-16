"""The live run behind the interface: the question actually drives the pipeline.

The previous version was a facade. It ran one generic Paperclip search capped at
six results and served a precomputed Lung landscape for every question, so the
plot, the candidate table and the structure were identical whatever you asked.
Only the sources rail responded, which is why the numbers looked instant and
hardcoded — they were.

This runs the real thing per question: resolve a disease context from the text,
screen the TF universe against DepMap for that context, apply the gates, then
retrieve evidence across the six axes for the candidates that survive. Slower,
and the numbers change when the question does, because they are computed.

Where a stage genuinely cannot run it says so rather than serving a stand-in.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable

from dependency_scout.depmap import analyze_gene_effects, load_tf_universe
from dependency_scout.models import MediatorLink
from dependency_scout.ranking import rank_all
from reagent_workflow.literature import gather

# The real DepMap OncotreeLineage vocabulary, plus the words people actually use.
# Order matters: the first match wins, so specific phrases precede bare lineages.
ALIASES: list[tuple[str, str]] = [
    (r"ewing|osteosarcom\w*|\bbone\b", "Bone"),
    (r"melanom\w*|\bskin\b|cutaneous", "Skin"),
    (r"myelom\w*|lymphom\w*|\bdlbcl\b|lymphoid|\bb[- ]cell\b", "Lymphoid"),
    (r"\baml\b|myeloid|leukaemi\w*|leukemi\w*", "Myeloid"),
    (r"neuroblastom\w*|peripheral nervous", "Peripheral Nervous System"),
    (r"gliom\w*|glioblastom\w*|\bcns\b|\bbrain\b", "CNS/Brain"),
    (r"\brenal\b|\bkidney\b|ccrcc", "Kidney"),
    (r"ovarian|\bovary\b|fallopian", "Ovary/Fallopian Tube"),
    (r"colorect\w*|\bcolon\b|\bbowel\b", "Bowel"),
    (r"pancrea\w*", "Pancreas"),
    (r"\bbreast\b", "Breast"),
    (r"\bprostate\b", "Prostate"),
    (r"head and neck|hnscc|\boral\b", "Head and Neck"),
    (r"gastric|\bstomach\b|esophag\w*|oesophag\w*", "Esophagus/Stomach"),
    (r"\bbladder\b|urothelial", "Bladder/Urinary Tract"),
    (r"\bliver\b|hepatocellular|\bhcc\b", "Liver"),
    (r"sarcom\w*|soft tissue", "Soft Tissue"),
    (r"\blung\b|\bnsclc\b|\bsclc\b", "Lung"),
]


def resolve_context(question: str) -> tuple[str | None, str]:
    """Free text -> a DepMap lineage. Returns (context, how it was resolved)."""
    q = (question or "").lower()
    for pattern, lineage in ALIASES:
        if re.search(pattern, q):
            return lineage, f"matched {lineage!r} in the question"
    return None, ("no cancer type recognised in the question; name a tissue or "
                  "disease, e.g. 'melanoma', 'Ewing sarcoma', 'multiple myeloma'")


def run_live(question: str, data_paths, cfg, emit: Callable[[str, dict], None],
             interface_evidence: dict[str, MediatorLink] | None = None,
             top_n: int = 3, min_models: int = 15) -> None:
    """Stream the real pipeline for one question. `emit(event, payload)` per step."""
    ge, models, tfs = data_paths
    interface_evidence = interface_evidence or {}

    context, how = resolve_context(question)
    emit("stage", {"id": "question", "state": "done" if context else "blocked",
                   "detail": question or "no question given",
                   "note": how})
    if not context:
        for sid in ("literature", "discovery", "ranking", "specificity", "site",
                    "structure", "screening", "experiment"):
            emit("stage", {"id": sid, "state": "pending", "detail": "not run",
                           "note": "waiting on a recognised disease context"})
        emit("done", {"ok": False})
        return

    # ── discovery: computed for THIS context, not served from a cache ──────
    emit("stage", {"id": "discovery", "state": "running",
                   "detail": f"screening the TF universe against {context}"})
    if not ge.exists():
        emit("stage", {"id": "discovery", "state": "blocked",
                       "detail": f"{ge.name} is not present",
                       "note": "see team/TASKS.md for the fetch command"})
        emit("done", {"ok": False})
        return
    universe = load_tf_universe(tfs)
    records = analyze_gene_effects(ge, models, context=context, genes=universe,
                                   source_version="DepMap Public 24Q2")
    if not records:
        emit("stage", {"id": "discovery", "state": "blocked",
                       "detail": f"no cell lines matched {context!r}"})
        emit("done", {"ok": False})
        return
    ranked = rank_all(records)
    n_lines = max(c.dependency.n_target_models for c in ranked)
    emit("stage", {"id": "discovery", "state": "done",
                   "detail": f"{len(ranked)} TFs across {n_lines} {context} cell lines",
                   "note": "DepMap 24Q2 Chronos, Lambert TF catalogue. Computed now, "
                           "not cached.",
                   "context": context})

    # the plot the interface draws, for this context
    emit("landscape", {"context": context, "points": [
        {"gene": c.dependency.gene,
         "median": round(c.dependency.median_target_effect, 3),
         "sel": round(c.dependency.selectivity_delta, 3),
         "tfrac": round(c.dependency.target_dependent_fraction, 3),
         "ofrac": round(c.dependency.other_dependent_fraction, 3),
         "n": c.dependency.n_target_models,
         "pass": c.gate.eligible, "why": c.gate.failures,
         "low_n": c.dependency.n_target_models < min_models}
        for c in ranked]})

    # ── gates ──────────────────────────────────────────────────────────────
    eligible = [c for c in ranked
                if c.gate.eligible and c.dependency.n_target_models >= min_models]
    tally: dict[str, int] = {}
    for c in ranked:
        for w in c.gate.failures:
            tally[w] = tally.get(w, 0) + 1
    note = "; ".join(f"{n}x {w}" for w, n in
                     sorted(tally.items(), key=lambda kv: -kv[1])[:2])
    emit("stage", {
        "id": "ranking", "state": "done" if eligible else "abstained",
        "detail": (f"{len(eligible)} of {len(ranked)} clear every gate: "
                   + ", ".join(f"{c.dependency.gene} (sel "
                               f"{c.dependency.selectivity_delta:.2f})"
                               for c in eligible[:5])) if eligible
                  else f"0 of {len(ranked)} clear every gate",
        "note": note})

    emit("candidates", {"rows": [
        {"gene": c.dependency.gene, "context": context,
         "n": c.dependency.n_target_models,
         "median": round(c.dependency.median_target_effect, 3),
         "sel": round(c.dependency.selectivity_delta, 3),
         "tfrac": round(c.dependency.target_dependent_fraction, 3),
         "ofrac": round(c.dependency.other_dependent_fraction, 3),
         "score": round(c.final_score, 3) if c.final_score is not None else None,
         "gate_pass": True, "gate_why": [], "awaiting": False,
         "shortlisted": i < top_n,
         "partner": cfg.partner_gene, "involvement": "unknown",
         "region": None, "region_mapped": False, "tractability": "unknown",
         "control": False, "concerns": [], "ready": False,
         "blocked_because": None, "claims": []}
        for i, c in enumerate(eligible)]})

    # ── literature: six axes per candidate, on-target only ─────────────────
    top = eligible[:top_n]
    if not top:
        emit("stage", {"id": "literature", "state": "abstained",
                       "detail": "no candidate survived the gates to search evidence for",
                       "note": "Retrieval follows the gates; searching all 1588 TFs "
                               "would find something for every one of them."})
    else:
        emit("stage", {"id": "literature", "state": "running",
                       "detail": f"six evidence axes for {len(top)} candidate(s)"})
        total_on, total_axes, hits, leads, errors = 0, 0, 0, [], []
        for c in top:
            gene = c.dependency.gene
            ev, papers, errs = gather(gene, context, cfg.partner_gene, per_axis=4)
            errors.extend(errs)
            for p in papers:
                emit("paper", {"title": p.title, "id": p.accession, "url": p.url,
                               "abstract": p.abstract, "axis": p.axis,
                               "gene": gene, "support": p.suggested_support})
            total_on += len(papers)
            total_axes += len(ev.axes)
            hits += len(ev.axes_with_hits)
            if ev.has_coactivator_lead:
                leads.append(gene)
            emit("evidence", {"gene": gene, "axes": {
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

    # ── everything downstream of a mapped contact ──────────────────────────
    with_contact = [c for c in eligible
                    if (link := interface_evidence.get(c.dependency.gene))
                    and link.interacting_region_mapped and not link.calibration_only]
    emit("stage", {
        "id": "specificity", "state": "done" if eligible else "abstained",
        "detail": f"{len(eligible)} candidate(s) with a selective dependency",
        "note": "Cancer-cell selectivity is not normal-tissue safety."})
    emit("stage", {
        "id": "site", "state": "done" if with_contact else "abstained",
        "detail": (", ".join(f"{c.dependency.gene}-{cfg.partner_gene}"
                             for c in with_contact) if with_contact
                   else "no candidate here has a mapped interacting region"),
        "note": "A whole-protein pull-down is association, not contact."})
    emit("stage", {
        "id": "structure", "state": "abstained",
        "detail": "no ensemble for this context",
        "note": "Structural discovery is a separate costed GPU step, not run inline. "
                "The 3D view shows the ELK1-MED23 reference structure, which is "
                "calibration, not this run."})
    emit("stage", {
        "id": "screening", "state": "pending", "detail": "not run",
        "note": "Needs a signed-off site (checkpoint 4) before compounds are docked."})
    emit("stage", {
        "id": "experiment", "state": "done",
        "detail": (f"test whether {eligible[0].dependency.gene} dependence in {context} "
                   "requires a coactivator contact" if eligible
                   else f"no TF clears the gates in {context} at lineage granularity"),
        "note": ("The dependency is real and the contact is undocumented; closing that "
                 "gap is what unblocks the rest." if eligible
                 else "Either this lineage is not TF-addicted, or it needs "
                      "molecular-subtype resolution. Both are results.")})
    emit("done", {"ok": True})
