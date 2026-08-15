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
    """Order by (contact quality, score); shortlist only gate-eligible candidates."""
    ordered = sorted(
        candidates,
        key=lambda c: (INVOLVEMENT_RANK[c.mediator.involvement], c.final_score),
        reverse=True,
    )
    picks = [i for i, c in enumerate(ordered) if c.gate.eligible][:top_n]
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
        d = c.dependency
        gate = "pass" if c.gate.eligible else f"FAIL: {'; '.join(c.gate.failures)}"
        mark = " **<-- shortlist**" if i in sl.shortlist_indices else ""
        out.append(f"| {i} | {d.gene}{mark} | {d.disease_context} | "
                   f"{d.selectivity_delta:.2f} | {gate} | "
                   f"{c.mediator.involvement.value} | "
                   f"{'yes: ' + c.mediator.tf_region if c.mediator.interacting_region_mapped else 'no'} | "
                   f"{c.final_score:.3f} |")

    out += ["", "## B. Evidence", ""]
    for i, c in enumerate(sl.candidates):
        out.append(f"**{i}. {c.dependency.gene}** — {c.dependency.disease_context}")
        out += _claim_lines(c)
        out.append("")

    out += ["## C. Mediator involvement", "",
            f"| TF | Involvement | Ready for structural modeling |", "|---|---|---|"]
    for c in sl.candidates:
        out.append(f"| {c.dependency.gene} | {c.mediator.involvement.value} | "
                   f"{'yes' if c.mediator.ready_for_structural_modeling else 'no'} |")

    out += ["", "## D. Shortlist for human review", ""]
    if not sl.shortlist_indices:
        out.append("None. No candidate passed the dependency gate — this is a "
                   "result, not a failure. Widen the scope or report the gap.")
    for i in sl.shortlist_indices:
        c = sl.candidates[i]
        blocker = ("" if c.mediator.ready_for_structural_modeling else
                   f" — blocked: {sl.partner_gene} contact is "
                   f"'{c.mediator.involvement.value}', no mapped interacting region")
        out.append(f"- **{c.dependency.gene}** "
                   f"({c.dependency.disease_context}){blocker}")
    out += ["", "No structural modeling or docking may start from this file. "
            "A human named in team/CHECKPOINTS.md signs first."]
    return "\n".join(out) + "\n"
