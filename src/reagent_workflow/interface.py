"""Turn a structural ensemble into a reproducible interface hypothesis.

A single predicted complex is a guess. The question this module answers is
narrower and answerable: **do independent samples put the same short segment of
the target on the same surface of the partner?** Convergence is the signal;
the scalar confidence is a secondary check.

Nothing here decides that an interaction is real. Output interpretation is fixed
at `computational_prediction`, and a consensus is a hypothesis about where to
look, never evidence that two proteins associate.

Pure geometry, no model dependency, so it can be validated against an
experimental complex before any prediction is trusted — see
`tests/test_interface_consensus.py`, which runs it on PDB 9F6Y and requires it
to recover the published ELK1 motif and MED23 pocket residues.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

class ConsensusConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contact_cutoff_angstrom: float = Field(default=4.5, gt=0)
    min_dominant_cluster_fraction: float = Field(default=0.60, ge=0, le=1)
    min_contact_occupancy: float = Field(default=0.60, ge=0, le=1)
    segment_contact_coverage: float = Field(default=0.80, ge=0, le=1)
    cluster_jaccard_threshold: float = Field(default=0.35, ge=0, le=1)
    min_ensemble_samples: int = Field(default=3, ge=2)
    """Fewest independent samples that can demonstrate anything. One structure
    is trivially 1/1 with every residue at occupancy 1.0 — a number that means
    nothing. Duplicates of one prediction collapse to one observation first."""
    min_contacts_per_sample: int = Field(default=3, ge=1)
    """One residue pair repeated across samples is agreement about almost
    nothing. A site needs contact, not a touch."""
    max_segment_length: int = Field(default=40, ge=1)
    """A 'compact segment' that spans most of a domain is not compact. §13."""
    min_partner_occupancy: float = Field(default=0.50, ge=0, le=1)
    """Partner residues seen in fewer than this fraction of the dominant cluster
    are not part of the consensus site and must not widen the docking box."""


@dataclass(frozen=True)
class Atom:
    chain: str
    resi: int
    name: str
    element: str
    x: float
    y: float
    z: float
    bfactor: float = 0.0     # pLDDT for predicted models


@dataclass
class Contact:
    target_resi: int
    partner_resi: int
    min_distance: float
    sample: str
    target_plddt: float = 0.0
    partner_plddt: float = 0.0


@dataclass
class SampleInterface:
    """One predicted (or experimental) complex, reduced to its contacts."""
    sample: str
    contacts: list[Contact] = field(default_factory=list)

    @property
    def target_residues(self) -> set[int]:
        return {c.target_resi for c in self.contacts}

    @property
    def partner_residues(self) -> set[int]:
        return {c.partner_resi for c in self.contacts}

    @property
    def is_empty(self) -> bool:
        return not self.contacts


class TargetSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: int
    end: int
    occupancy: dict[int, float] = Field(default_factory=dict)
    contact_coverage: float = Field(ge=0, le=1)

    @property
    def length(self) -> int:
        return self.end - self.start + 1


class MetricSummary(BaseModel):
    """One confidence number across the samples in a hypothesis."""
    model_config = ConfigDict(extra="forbid")
    mean: float
    min: float
    max: float
    n: int = Field(ge=1)


class InterfaceHypothesis(BaseModel):
    """One cluster of samples, scored on its own terms.

    A cluster that loses the vote is still a hypothesis about where the
    interface is, and discarding it to a list of sample names throws away the
    only thing that made it checkable. Every cluster is scored the same way and
    kept, so a minority placement can be examined, sampled against, or approved
    by a human rather than silently dropped.
    """
    model_config = ConfigDict(extra="forbid")
    hypothesis_id: str
    sample_ids: list[str]
    support_fraction: float = Field(ge=0, le=1)
    target_segment: TargetSegment | None = None
    """Populated only when this hypothesis is localized; otherwise None, so a
    consumer cannot read a segment off a hypothesis that did not earn one."""
    rejected_segment: TargetSegment | None = None
    """What the segment would have been. Diagnosis only."""
    segment_length: int | None = None
    """Compactness. None when no segment reached the occupancy floor."""
    partner_contact_residues: list[int] = Field(default_factory=list)
    confidence: dict[str, MetricSummary] = Field(default_factory=dict)
    """Interface-specific confidence where the samples carry it: mean and range
    across the cluster. Empty when the samples have none."""
    blockers: list[str] = Field(default_factory=list)

    @property
    def localized(self) -> bool:
        """A compact, reproducible segment on a partner surface it agrees on.

        Localization is separate from support. A single sample can be localized
        and still not converged, which is exactly the case this distinction
        exists to represent.
        """
        return (self.target_segment is not None
                and bool(self.partner_contact_residues))

    @property
    def converged(self) -> bool:
        return self.localized and not self.blockers


class InterfaceConsensus(BaseModel):
    """The artifact written to runs/<id>/structure/interface_consensus.json."""
    model_config = ConfigDict(extra="forbid")
    interpretation: str = "computational_prediction"
    status: str = "refused"
    """converged | ambiguous | refused. `ambiguous` means the ensemble holds one
    or more localized hypotheses but none of them carried the vote — a state
    that is neither a result nor nothing, and that previously collapsed into a
    refusal that discarded the alternatives."""
    next_action: str = "abstain"
    """What the status licenses: build_search_site | sample_more | abstain."""
    total_samples: int = Field(ge=0)
    dominant_cluster_samples: int = Field(ge=0)
    ensemble_support: float = Field(ge=0, le=1)
    target_segment: TargetSegment | None = None
    """None whenever `blockers` is non-empty: a refusal must not ship a
    populated segment that a consumer could read as a result."""
    rejected_segment: TargetSegment | None = None
    """What the segment would have been, kept for diagnosis only."""
    partner_contact_residues: list[int] = Field(default_factory=list)
    primary_hypothesis: InterfaceHypothesis | None = None
    """The hypothesis that passed. None unless status is `converged`."""
    alternative_hypotheses: list[InterfaceHypothesis] = Field(default_factory=list)
    """Every other scored hypothesis, in the same order as the clusters. When
    status is `ambiguous` this holds all of them, including the localized
    minority that did not win."""
    alternative_clusters: list[list[str]] = Field(default_factory=list)
    """Sample names only. Superseded by `alternative_hypotheses`; kept because
    existing artifacts and readers refer to it."""
    blockers: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=lambda: [
        "Computational structural hypothesis, not experimental evidence.",
        "Ensemble convergence indicates model self-consistency, not binding.",
    ])

    @property
    def converged(self) -> bool:
        return not self.blockers and self.target_segment is not None


# ── geometry ────────────────────────────────────────────────────────────────

def contacts_between(atoms: list[Atom], target_chain: str, partner_chain: str,
                     cutoff: float, sample: str = "s0") -> SampleInterface:
    """Heavy-atom contacts under `cutoff`, reduced to residue pairs.

    Uniform grid rather than an all-pairs scan: MED23 is ~1200 residues and the
    quadratic version is slow enough to discourage running a real ensemble.
    """
    if target_chain == partner_chain:
        raise ValueError(
            f"target and partner chain are both {target_chain!r}: a chain cannot "
            "form an interface with itself, and self-contacts at 0 A would read "
            "as perfect occupancy everywhere")
    tgt = [a for a in atoms if a.chain == target_chain and a.element != "H"]
    par = [a for a in atoms if a.chain == partner_chain and a.element != "H"]
    if not tgt or not par:
        return SampleInterface(sample=sample)

    cell = cutoff
    grid: dict[tuple[int, int, int], list[Atom]] = defaultdict(list)
    for a in par:
        grid[(int(a.x // cell), int(a.y // cell), int(a.z // cell))].append(a)

    best: dict[tuple[int, int], tuple[float, float, float]] = {}
    for a in tgt:
        gx, gy, gz = int(a.x // cell), int(a.y // cell), int(a.z // cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for b in grid.get((gx + dx, gy + dy, gz + dz), ()):
                        d = math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))
                        if d > cutoff:
                            continue
                        key = (a.resi, b.resi)
                        if key not in best or d < best[key][0]:
                            best[key] = (d, a.bfactor, b.bfactor)
    return SampleInterface(sample=sample, contacts=[
        Contact(target_resi=t, partner_resi=p, min_distance=round(d, 3),
                sample=sample, target_plddt=tb, partner_plddt=pb)
        for (t, p), (d, tb, pb) in sorted(best.items())])


# Modified residues deposited as HETATM but chemically part of the chain. 9F6Y
# carries the ELK1 phosphoserine as SEP; dropping it deletes the residue that
# makes the interaction phosphorylation-dependent in the first place.
POLYMER_HETATM = {
    "SEP", "TPO", "PTR",              # phospho- Ser / Thr / Tyr
    "MSE", "CSO", "CME", "OCS",       # selenomethionine, oxidised cysteines
    "KCX", "MLY", "M3L", "ALY",       # modified lysines
    "HYP", "PCA", "SAC", "CIR", "NEP",
}


def parse_mmcif(path, extra_polymer_residues: set[str] | None = None) -> list[Atom]:
    """Minimal mmCIF atom reader.

    ATOM records plus the modified residues in POLYMER_HETATM, which are part of
    the polymer despite being deposited as HETATM. Waters, ions and true ligands
    stay excluded — they are not part of a protein-protein interface.
    """
    import pathlib
    keep_het = POLYMER_HETATM | (extra_polymer_residues or set())
    lines = pathlib.Path(path).read_text().splitlines()
    i = next(k for k, l in enumerate(lines) if l.startswith("_atom_site."))
    cols = []
    while lines[i].startswith("_atom_site."):
        cols.append(lines[i].strip().split(".")[1])
        i += 1
    ix = {c: n for n, c in enumerate(cols)}
    out: list[Atom] = []
    for l in lines[i:]:
        if l.startswith("#"):
            break
        f = l.split()
        if len(f) < len(cols):
            continue
        if f[ix["group_PDB"]] != "ATOM" and f[ix["label_comp_id"]] not in keep_het:
            continue
        out.append(Atom(
            chain=f[ix["auth_asym_id"]], resi=int(f[ix["auth_seq_id"]]),
            name=f[ix["label_atom_id"]], element=f[ix["type_symbol"]],
            x=float(f[ix["Cartn_x"]]), y=float(f[ix["Cartn_y"]]),
            z=float(f[ix["Cartn_z"]]), bfactor=float(f[ix["B_iso_or_equiv"]])))
    return out


def _jaccard(a: set[int], b: set[int]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b) if (a | b) else 0.0


def cluster_interfaces(samples: list[SampleInterface],
                       cfg: ConsensusConfig) -> list[list[SampleInterface]]:
    """Group samples that put the same residues in contact.

    Similarity is the mean Jaccard overlap of the target and partner contact
    sets, so two samples agreeing on the partner surface but placing a different
    part of the target do not count as agreement.
    """
    # Deterministic input order: sample order is a scheduling artifact and must
    # not reach the verdict. Sorting means a shuffled ensemble clusters the same.
    live = sorted((s for s in samples if not s.is_empty), key=lambda s: s.sample)

    clusters: list[list[SampleInterface]] = []
    for s in live:
        for c in clusters:
            # COMPLETE linkage: a sample joins only if it clears the threshold
            # against EVERY member, not against the running mean. Averaging
            # allows chaining, and chaining is how a peptide sliding one residue
            # at a time along a groove — the signature of a sampler that found
            # nothing — becomes a single "fully converged" cluster whose first
            # and last members barely overlap.
            if all(min(_jaccard(s.target_residues, m.target_residues),
                       _jaccard(s.partner_residues, m.partner_residues))
                   >= cfg.cluster_jaccard_threshold for m in c):
                c.append(s)
                break
        else:
            clusters.append([s])
    # Ties broken by first sample name so the dominant cluster is reproducible.
    clusters.sort(key=lambda c: (-len(c), c[0].sample))
    return clusters


def contact_occupancy(cluster: list[SampleInterface]) -> dict[int, float]:
    """Fraction of cluster samples in which each target residue makes contact."""
    if not cluster:
        return {}
    n = len(cluster)
    tally = Counter(r for s in cluster for r in s.target_residues)
    return {r: c / n for r, c in sorted(tally.items())}


def smallest_confident_segment(cluster: list[SampleInterface],
                               cfg: ConsensusConfig) -> TargetSegment | None:
    """Shortest contiguous target span covering most reproducible contacts.

    Answers "which tightest region of the target touches the partner", which is
    what a screen needs — not "does the target touch the partner".
    """
    occ = contact_occupancy(cluster)
    keep = {r: o for r, o in occ.items() if o >= cfg.min_contact_occupancy}
    if not keep:
        return None
    total = sum(keep.values())
    residues = sorted(keep)
    lo, hi = residues[0], residues[-1]

    best: tuple[int, int, float] | None = None
    for i in range(lo, hi + 1):
        for j in range(i, hi + 1):
            covered = sum(o for r, o in keep.items() if i <= r <= j)
            if covered / total < cfg.segment_contact_coverage:
                continue
            if best is None or (j - i) < (best[1] - best[0]):
                best = (i, j, covered / total)
            break        # shortest j for this i; widening only costs length
    if best is None:
        return None
    i, j, cov = best
    return TargetSegment(start=i, end=j, contact_coverage=round(cov, 4),
                         occupancy={r: round(o, 4) for r, o in occ.items() if i <= r <= j})


def _interface_confidence(cluster: list[SampleInterface],
                          sample_metrics: dict[str, dict[str, float]] | None,
                          ) -> dict[str, MetricSummary]:
    """Confidence for this cluster, restricted to the interface.

    Two sources, both optional. The pLDDT carried on contacting atoms is
    interface-specific by construction — it is scored only over residues that
    are in contact — and is the one number available without the caller passing
    anything. Per-sample scalars supplied by the caller (ipTM and friends) are
    summarised alongside it.

    Global scores are deliberately not synthesised here. On a large well-folded
    partner they report that the partner folded.
    """
    values: dict[str, list[float]] = defaultdict(list)
    for s in cluster:
        if s.contacts:
            plddt = [c.target_plddt for c in s.contacts if c.target_plddt]
            plddt += [c.partner_plddt for c in s.contacts if c.partner_plddt]
            if plddt:
                values["contact_plddt"].append(sum(plddt) / len(plddt))
        for key, val in (sample_metrics or {}).get(s.sample, {}).items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                values[key].append(float(val))
    return {k: MetricSummary(mean=round(sum(v) / len(v), 4), min=round(min(v), 4),
                             max=round(max(v), 4), n=len(v))
            for k, v in sorted(values.items()) if v}


def score_hypothesis(cluster: list[SampleInterface], total_samples: int,
                     cfg: ConsensusConfig, hypothesis_id: str,
                     sample_metrics: dict[str, dict[str, float]] | None = None,
                     ) -> InterfaceHypothesis:
    """Score one cluster the way the dominant cluster has always been scored.

    Identical criteria for every cluster, so a minority hypothesis is held to
    the same standard as the winner rather than to a softer one. The only thing
    that distinguishes them afterwards is support, which is recorded rather than
    applied here.
    """
    support = len(cluster) / total_samples if total_samples else 0.0
    blockers: list[str] = []
    if support < cfg.min_dominant_cluster_fraction:
        blockers.append(
            f"ensemble did not converge: dominant interface in {len(cluster)}/"
            f"{total_samples} samples ({support:.0%}), below the "
            f"{cfg.min_dominant_cluster_fraction:.0%} floor")

    segment = smallest_confident_segment(cluster, cfg)
    if segment is None:
        blockers.append(
            f"no target residue reaches {cfg.min_contact_occupancy:.0%} contact "
            "occupancy within the dominant cluster")
    elif segment.length > cfg.max_segment_length:
        blockers.append(
            f"the reproducible target region spans {segment.length} residues, beyond "
            f"the {cfg.max_segment_length}-residue limit for a compact segment: this "
            "is an extended surface rather than a short motif")

    # Only partner residues this cluster actually agrees on. A residue touched
    # by one outlier sample is not part of the site and must not widen the
    # docking box that is built from this list.
    n = len(cluster)
    tally = Counter(r for s in cluster for r in s.partner_residues)
    partner = sorted(r for r, k in tally.items()
                     if k / n >= cfg.min_partner_occupancy) if n else []

    # A segment that failed its own checks is not shipped as a segment. Whether
    # the *ensemble* converged is a separate question, answered by the caller:
    # a localized minority keeps its segment so it can be examined, and support
    # is what stops it from being docked.
    localized = (segment is not None
                 and segment.length <= cfg.max_segment_length
                 and bool(partner))
    if not partner and segment is not None:
        blockers.append(
            f"no partner residue is contacted in at least "
            f"{cfg.min_partner_occupancy:.0%} of the dominant cluster")
    return InterfaceHypothesis(
        hypothesis_id=hypothesis_id,
        sample_ids=[s.sample for s in cluster],
        support_fraction=round(support, 4),
        target_segment=segment if localized else None,
        rejected_segment=None if localized else segment,
        segment_length=segment.length if segment is not None else None,
        partner_contact_residues=partner,
        confidence=_interface_confidence(cluster, sample_metrics),
        blockers=blockers,
    )


def build_consensus(samples: list[SampleInterface],
                    cfg: ConsensusConfig | None = None,
                    sample_metrics: dict[str, dict[str, float]] | None = None,
                    ) -> InterfaceConsensus:
    """Ensemble → consensus, or an explicit refusal (§35)."""
    cfg = cfg or ConsensusConfig()

    # How many DISTINCT predictions arrived. Identical contact sets are one
    # observation however many times they are handed over: a retry loop or a
    # fan-out that lost its seed would otherwise turn one model into
    # "converged in 8/8". The samples are kept in the denominator so support
    # still reflects what actually ran; only the verdict changes.
    distinct = len({(tuple(sorted(s.target_residues)), tuple(sorted(s.partner_residues)))
                    for s in samples if not s.is_empty})
    total = len(samples)

    if total == 0:
        return InterfaceConsensus(status="refused", next_action="abstain",
                                  total_samples=0, dominant_cluster_samples=0,
                                  ensemble_support=0.0,
                                  blockers=["no structural samples were produced"])

    thin = [s for s in samples
            if not s.is_empty and len(s.contacts) < cfg.min_contacts_per_sample]
    samples = [s for s in samples
               if s.is_empty or len(s.contacts) >= cfg.min_contacts_per_sample]

    clusters = cluster_interfaces(samples, cfg)
    if not clusters:
        return InterfaceConsensus(status="refused", next_action="abstain",
                                  total_samples=total, dominant_cluster_samples=0,
                                  ensemble_support=0.0,
                                  blockers=["no sample placed the target in substantial "
                                            "contact with the partner"
                                            + (f" ({len(thin)} sample(s) had fewer than "
                                               f"{cfg.min_contacts_per_sample} contacts)"
                                               if thin else "")])

    # Every cluster is scored, not only the largest. The clusters are already in
    # a deterministic order, so the ids are stable across a shuffled ensemble.
    hypotheses = [score_hypothesis(c, total, cfg, f"H{i + 1}", sample_metrics)
                  for i, c in enumerate(clusters)]
    dominant = clusters[0]
    support = len(dominant) / total

    # Ensemble-level blockers apply to the whole run, not to any one cluster:
    # they say the ensemble cannot support a verdict at all.
    ensemble_blockers: list[str] = []
    if distinct < cfg.min_ensemble_samples:
        ensemble_blockers.append(
            f"only {distinct} distinct prediction(s) among {total} sample(s); at least "
            f"{cfg.min_ensemble_samples} independent predictions are required before "
            "agreement means anything")

    passing = [h for h in hypotheses if h.converged] if not ensemble_blockers else []
    # At most one cluster can clear a majority floor above 50%, so this is a
    # single hypothesis whenever the floor is set sanely; taking the first keeps
    # it deterministic if a caller lowers it.
    primary = passing[0] if passing else None
    localized = [h for h in hypotheses if h.localized]

    if primary is not None:
        status, next_action = "converged", "build_search_site"
    elif localized:
        # The state this distinction exists for: the ensemble holds a defensible
        # placement that did not carry the vote. Refusing and dropping it loses
        # the only checkable thing in the run; promoting it would dock on a
        # minority. Neither. Sample more.
        status, next_action = "ambiguous", "sample_more"
    else:
        status, next_action = "refused", "abstain"

    blockers = list(ensemble_blockers)
    if primary is None:
        blockers += [b for b in hypotheses[0].blockers if b not in blockers]
    if status == "ambiguous":
        ids = ", ".join(f"{h.hypothesis_id} ({'+'.join(h.sample_ids)}, "
                        f"{h.support_fraction:.0%})" for h in localized)
        blockers.append(
            f"no interface converged, but {len(localized)} localized hypothes"
            f"{'is' if len(localized) == 1 else 'es'} survived scoring: {ids}. "
            "Retained as alternatives; more samples are needed to tell whether "
            "any of them reproduces. Not a docking site.")

    dom_hyp = hypotheses[0]
    return InterfaceConsensus(
        status=status,
        next_action=next_action,
        total_samples=total,
        dominant_cluster_samples=len(dominant),
        ensemble_support=round(support, 4),
        # The top-level fields keep describing the dominant cluster, as they
        # always have, and stay empty unless it is the one that passed.
        target_segment=dom_hyp.target_segment if primary is dom_hyp else None,
        rejected_segment=(dom_hyp.target_segment or dom_hyp.rejected_segment)
                         if primary is not dom_hyp else None,
        partner_contact_residues=dom_hyp.partner_contact_residues,
        primary_hypothesis=primary,
        alternative_hypotheses=[h for h in hypotheses if h is not primary],
        alternative_clusters=[[s.sample for s in c] for c in clusters[1:]],
        blockers=blockers,
    )
