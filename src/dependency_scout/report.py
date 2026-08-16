"""Stage-1 shortlist assembly and the human-readable table for review.

Deliverables A-D of the target-prioritization stage. Stops before structural
modeling; the checkpoint owners in team/CHECKPOINTS.md sign off first.
"""
from .models import Involvement, RankedCandidate, Shortlist, SourceRecord

# Ranked strictly: a mapped physical contact outranks a better dependency score
# with no contact, because the next stage cannot start without a region.
INVOLVEMENT_RANK = {Involvement.DIRECT: 3, Involvement.INDIRECT: 2,
                    Involvement.PREDICTED: 1, Involvement.UNKNOWN: 0}


def build_shortlist(candidates: list[RankedCandidate], *, disease_scope: str,
                    partner_gene: str = "MED23", top_n: int = 3,
                    sources: list[SourceRecord] | None = None) -> Shortlist:
    """Order by (contact quality, score); shortlist only what may actually proceed."""
    ordered = sorted(
        candidates,
        key=lambda c: (INVOLVEMENT_RANK[c.mediator.involvement], c.final_score or 0.0),
        reverse=True,
    )
    picks = [i for i, c in enumerate(ordered) if c.shortlistable[0]][:top_n]
    return Shortlist(partner_gene=partner_gene, disease_scope=disease_scope,
                     candidates=ordered, shortlist_indices=picks,
                     sources=sources or [])


def _claim_lines(candidate: RankedCandidate) -> list[str]:
    claims = candidate.mediator.claims + candidate.enrichment.claims
    if not claims:
        return ["  - no claims recorded"]
    return [f"  - [{c.support.value}] {c.statement}\n    {' '.join(c.citations)}"
            for c in claims]


def render_markdown(sl: Shortlist) -> str:
    """The table a human reads at the checkpoint. Every row shows its own weakness."""
    out = [f"# Stage 1 — disease-TF candidates vs {sl.partner_gene}", "",
           f"Scope: {sl.disease_scope}. Stops before {sl.stops_before}.",
           f"Awaiting: {'; '.join(sl.awaiting_review)}", "",
           "## A. Ranked candidates", "",
           f"| # | TF | Context | Sel. delta | Dep. gate | {sl.partner_gene} | Region mapped | Score |",
           "|---|---|---|---|---|---|---|---|"]
    for i, c in enumerate(sl.candidates):
        ok, reason = c.shortlistable
        if c.awaiting_dependency_data:
            gate, delta = "awaiting dependency data", "—"
        elif c.gate and not c.gate.eligible:
            gate, delta = f"FAIL: {'; '.join(c.gate.failures)}", f"{c.dependency.selectivity_delta:.2f}"
        else:
            gate, delta = "pass", f"{c.dependency.selectivity_delta:.2f}"
        mark = " **<-- shortlist**" if i in sl.shortlist_indices else ""
        if i in sl.shortlist_indices and c.gate and not c.gate.eligible:
            mark = " **<-- shortlist (Mediator/literature override -- DepMap gate FAILED, see gate column)**"
        if c.mediator.calibration_only:
            mark = " *(control)*"
        out.append(f"| {i} | {c.name}{mark} | {c.disease_context} | "
                   f"{delta} | {gate} | {c.mediator.involvement.value} | "
                   f"{'yes: ' + c.mediator.tf_region if c.mediator.interacting_region_mapped else 'no'} | "
                   f"{'—' if c.final_score is None else f'{c.final_score:.3f}'} |")

    out += ["", "## B. Evidence", ""]
    for i, c in enumerate(sl.candidates):
        out.append(f"**{i}. {c.name}** — {c.disease_context}")
        out += _claim_lines(c)
        out.append("")

    out += ["## C. Mediator involvement", "",
            "| TF | Involvement | Ready for structural modeling | Screening concerns |",
            "|---|---|---|---|"]
    for c in sl.candidates:
        concerns = "; ".join(c.mediator.screening_concerns) or "none recorded"
        out.append(f"| {c.name} | {c.mediator.involvement.value} | "
                   f"{'yes' if c.mediator.ready_for_structural_modeling else 'no'} | {concerns} |")

    out += ["", "## D. Shortlist for human review", ""]
    if not sl.shortlist_indices:
        pending = sum(c.awaiting_dependency_data for c in sl.candidates)
        if pending:
            out.append(f"None yet. {pending} of {len(sl.candidates)} candidates are "
                       "awaiting quantitative dependency data; the dependency stage has "
                       "not run for them. This is an ordering gap, not a rejection.")
        else:
            out.append("None. No candidate passed the dependency gate — this is a "
                       "result, not a failure. Widen the scope or report the gap.")
    for i in sl.shortlist_indices:
        c = sl.candidates[i]
        notes = list(c.mediator.screening_concerns)
        if not c.mediator.ready_for_structural_modeling:
            notes.insert(0, f"{sl.partner_gene} contact is "
                            f"'{c.mediator.involvement.value}', no mapped interacting region")
        if c.gate and not c.gate.eligible:
            notes.insert(0, "no DepMap dependency evidence passed the gate in this run "
                            f"-- included via Mediator/literature evidence instead: "
                            f"{'; '.join(c.gate.failures)}")
        blocker = f" — {'; '.join(notes)}" if notes else ""
        out.append(f"- **{c.name}** ({c.disease_context}){blocker}")

    excluded = [(c, r) for c in sl.candidates
                if not c.shortlistable[0] for r in [c.shortlistable[1]]]
    if excluded:
        out += ["", "### Not shortlisted, and why", ""]
        out += [f"- **{c.name}** — {r}" for c, r in excluded]
    out += ["", "No structural modeling or docking may start from this file. "
            "A human named in team/CHECKPOINTS.md signs first."]
    return "\n".join(out) + "\n"
