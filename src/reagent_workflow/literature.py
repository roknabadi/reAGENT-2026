"""Per-candidate evidence retrieval across the axes Andrey's stage-1 brief names.

One generic search per run cannot answer "is this TF driving the phenotype or
just overexpressed" and "is there a documented coactivator contact" — those are
different questions and they need different queries. This module asks each one
separately, per candidate, and keeps them separate in the answer.

The axes, from the brief:
  dependency        strength of the TF dependency in cancer cells
  driver            drives the phenotype, rather than merely being overexpressed
  normal_tissue     normal-cell expression or dependency, as a safety proxy
  coactivator       direct or functional interaction with a Mediator subunit
  activation_domain activation-domain regions relevant to binding
  structure         existing structural or biochemical evidence

Nothing here reads a paper. Retrieval finds candidates for evidence; a title and
an abstract are not evidence, and the returned records say so. Support type is
inferred from language and is always marked as such, so an inference can never
be mistaken for something a human verified at source.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

ACCESSION = re.compile(r"\b((?:PMC|bio_|med_|arx_|tri_|fda_)[\w.]+)\b")

# Query templates per axis. `{gene}` and `{context}` are filled per candidate;
# `{partner}` is the coactivator being asked about.
AXES: dict[str, str] = {
    "dependency": "{gene} dependency {context} cancer cell line essential survival",
    "driver": "{gene} knockdown knockout phenotype {context} required for growth "
              "not merely overexpressed",
    "normal_tissue": "{gene} normal tissue expression physiological function knockout mouse",
    "coactivator": "{gene} {partner} Mediator complex coactivator physical interaction "
                   "co-immunoprecipitation",
    "activation_domain": "{gene} transactivation domain activation domain residues "
                         "mapping deletion",
    "structure": "{gene} structure crystal cryo-EM complex interface residues",
}

# Phrases that suggest what kind of support a record might carry. Used only to
# triage retrieval; never to assert that support exists.
_DIRECT = ("crystal structure", "cryo-em", "co-immunoprecipitation", "co-ip",
           "pull-down", "pulldown", "itc", "surface plasmon", "nmr structure",
           "x-ray", "crosslink")
_GENETIC = ("knockdown", "knockout", "crispr", "sirna", "shrna", "rescue",
            "loss of function", "depletion", "degron")
_COMPUTATIONAL = ("alphafold", "docking", "predicted", "in silico", "molecular dynamics",
                  "modelling", "modeling", "boltz")


@dataclass
class Paper:
    title: str
    accession: str = ""
    url: str = ""
    abstract: str = ""
    axis: str = ""
    query: str = ""

    @property
    def suggested_support(self) -> str:
        """Language triage only. Marked as a suggestion everywhere it surfaces."""
        blob = f"{self.title} {self.abstract}".lower()
        if any(k in blob for k in _DIRECT):
            return "direct_experimental"
        if any(k in blob for k in _GENETIC):
            return "genetic_functional"
        if any(k in blob for k in _COMPUTATIONAL):
            return "computational_prediction"
        return "unclassified"


class AxisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    axis: str
    query: str
    n_papers: int
    n_on_target: int = 0
    """Retrieved papers that actually name the gene. Paperclip's search is
    semantic and always returns the nearest matches, so a query for a gene that
    does not exist still comes back full. Without this the interface reports
    "6/6 axes hit" for GENEDOESNOTEXIST, which is precisely the false confidence
    the project exists to prevent."""
    accessions: list[str] = Field(default_factory=list)
    titles: list[str] = Field(default_factory=list)
    suggested_support: dict[str, int] = Field(default_factory=dict)
    searched: bool = True
    note: str = ""

    @property
    def empty(self) -> bool:
        """Empty means nothing on target, not nothing returned."""
        return self.n_on_target == 0


class CandidateEvidence(BaseModel):
    """What retrieval found for one candidate, kept per axis."""
    model_config = ConfigDict(extra="forbid")
    gene: str
    context: str
    partner: str
    axes: dict[str, AxisResult] = Field(default_factory=dict)
    caveat: str = ("Retrieved, not read. Titles and abstracts locate candidate evidence; "
                   "a claim is only evidence once a human has verified it at source.")

    @property
    def axes_with_hits(self) -> list[str]:
        return [k for k, v in self.axes.items() if not v.empty]

    @property
    def axes_empty(self) -> list[str]:
        return [k for k, v in self.axes.items() if v.empty]

    @property
    def has_coactivator_lead(self) -> bool:
        a = self.axes.get("coactivator")
        return bool(a and not a.empty)


def mentions(paper: "Paper", gene: str) -> bool:
    """Does this record actually name the gene, as a word rather than a substring?

    Substring matching would let TP53 match TP53BP1 and ELK1 match ELK1L, so the
    match is bounded by non-alphanumerics on both sides.
    """
    g = (gene or "").strip()
    if len(g) < 2:
        return False
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(g)}(?![A-Za-z0-9])",
                     f"{paper.title} {paper.abstract}", re.I) is not None


def _parse(stdout: str, axis: str, query: str) -> list[Paper]:
    papers, cur = [], None
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^(\d+)\.\s+(.*)$", line)
        if m:
            if cur:
                papers.append(cur)
            cur = Paper(title=m.group(2), axis=axis, query=query)
            continue
        if cur is None:
            continue
        if line.startswith("http"):
            if not cur.url:
                cur.url = line
        elif line.startswith('"'):
            cur.abstract = line.strip('"')
        elif not cur.accession and (h := ACCESSION.search(line)):
            cur.accession = h.group(1)
    if cur:
        papers.append(cur)
    return [p for p in papers if p.title]


def search(query: str, axis: str = "", n: int = 4, timeout: int = 120,
           exe: str | None = None) -> tuple[list[Paper], str]:
    """One Paperclip search. Returns (papers, error) — an error is never silent.

    An empty result and a failed call are different things and must not look the
    same: one is a finding about the literature, the other is a broken tool.
    """
    exe = exe or shutil.which("paperclip")
    if not exe:
        return [], "paperclip CLI not found on PATH"
    try:
        out = subprocess.run([exe, "search", "-s", "pmc", query, "-n", str(n)],
                             capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return [], f"search timed out after {timeout}s"
    except Exception as e:
        return [], f"search failed: {type(e).__name__}"
    if out.returncode != 0:
        return [], f"paperclip exited {out.returncode}: {out.stderr.strip()[:120]}"
    papers = _parse(out.stdout, axis, query)
    if not papers and "No papers found" not in out.stdout and not out.stdout.strip():
        return [], "paperclip produced no output at all (auth or key problem)"
    return papers, ""


def gather(gene: str, context: str, partner: str = "MED23", *,
           axes: dict[str, str] | None = None, per_axis: int = 4,
           on_axis=None) -> tuple[CandidateEvidence, list[Paper], list[str]]:
    """Run every axis for one candidate. Returns (evidence, all papers, errors)."""
    axes = axes or AXES
    ev = CandidateEvidence(gene=gene, context=context, partner=partner)
    seen: set[str] = set()
    collected: list[Paper] = []
    errors: list[str] = []

    for axis, template in axes.items():
        q = template.format(gene=gene, context=context, partner=partner)
        papers, err = search(q, axis, per_axis)
        if err:
            errors.append(f"{axis}: {err}")
            ev.axes[axis] = AxisResult(axis=axis, query=q, n_papers=0, searched=False,
                                       note=err)
            if on_axis:
                on_axis(axis, 0, err)
            continue
        on_target = [p for p in papers if mentions(p, gene)]
        tally: dict[str, int] = {}
        for p in on_target:
            tally[p.suggested_support] = tally.get(p.suggested_support, 0) + 1
            key = p.accession or p.title
            if key not in seen:
                seen.add(key)
                collected.append(p)
        if on_target:
            note = ""
        elif papers:
            note = (f"{len(papers)} paper(s) returned, none naming {gene}: semantic "
                    "retrieval returns nearest matches, and nearest is not relevant")
        else:
            note = "no match in the indexed corpus for this axis"
        ev.axes[axis] = AxisResult(
            axis=axis, query=q, n_papers=len(papers), n_on_target=len(on_target),
            accessions=[p.accession for p in on_target if p.accession],
            titles=[p.title for p in on_target],
            suggested_support=tally, note=note)
        if on_axis:
            on_axis(axis, len(on_target), "")
    return ev, collected, errors
