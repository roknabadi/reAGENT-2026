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

# ── configuration (§37) — thresholds live here, never inline ────────────────
DEFAULTS = {
    "contact_cutoff_angstrom": 4.5,
    "min_dominant_cluster_fraction": 0.60,
    "preferred_cluster_fraction": 0.80,
    "min_contact_occupancy": 0.60,
    "segment_contact_coverage": 0.80,
    "cluster_jaccard_threshold": 0.35,
}


class ConsensusConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contact_cutoff_angstrom: float = Field(default=4.5, gt=0)
    min_dominant_cluster_fraction: float = Field(default=0.60, ge=0, le=1)
    preferred_cluster_fraction: float = Field(default=0.80, ge=0, le=1)
    min_contact_occupancy: float = Field(default=0.60, ge=0, le=1)
    segment_contact_coverage: float = Field(default=0.80, ge=0, le=1)
    cluster_jaccard_threshold: float = Field(default=0.35, ge=0, le=1)


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


class InterfaceConsensus(BaseModel):
    """The artifact written to runs/<id>/structure/interface_consensus.json."""
    model_config = ConfigDict(extra="forbid")
    interpretation: str = "computational_prediction"
    total_samples: int = Field(ge=0)
    dominant_cluster_samples: int = Field(ge=0)
    ensemble_support: float = Field(ge=0, le=1)
    target_segment: TargetSegment | None = None
    partner_contact_residues: list[int] = Field(default_factory=list)
    alternative_clusters: list[list[str]] = Field(default_factory=list)
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


def parse_mmcif(path) -> list[Atom]:
    """Minimal mmCIF atom reader. ATOM records only — ligands and waters are not
    part of a protein-protein interface and would inflate the contact set."""
    import pathlib
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
        if len(f) < len(cols) or f[ix["group_PDB"]] != "ATOM":
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
    live = [s for s in samples if not s.is_empty]
    clusters: list[list[SampleInterface]] = []
    for s in live:
        for c in clusters:
            # min, not mean: two samples that agree on the partner surface but
            # dock different regions of the target are describing different
            # hypotheses. Averaging lets partner agreement alone carry them over
            # the threshold and manufactures convergence that is not there.
            sim = sum(
                min(_jaccard(s.target_residues, m.target_residues),
                    _jaccard(s.partner_residues, m.partner_residues))
                for m in c) / len(c)
            if sim >= cfg.cluster_jaccard_threshold:
                c.append(s)
                break
        else:
            clusters.append([s])
    clusters.sort(key=len, reverse=True)
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


def build_consensus(samples: list[SampleInterface],
                    cfg: ConsensusConfig | None = None) -> InterfaceConsensus:
    """Ensemble → consensus, or an explicit refusal (§35)."""
    cfg = cfg or ConsensusConfig()
    total = len(samples)
    if total == 0:
        return InterfaceConsensus(total_samples=0, dominant_cluster_samples=0,
                                  ensemble_support=0.0,
                                  blockers=["no structural samples were produced"])

    clusters = cluster_interfaces(samples, cfg)
    if not clusters:
        return InterfaceConsensus(total_samples=total, dominant_cluster_samples=0,
                                  ensemble_support=0.0,
                                  blockers=["no sample placed the target in contact "
                                            "with the partner"])

    dominant = clusters[0]
    support = len(dominant) / total
    blockers: list[str] = []
    if support < cfg.min_dominant_cluster_fraction:
        blockers.append(
            f"ensemble did not converge: dominant interface in {len(dominant)}/{total} "
            f"samples ({support:.0%}), below the {cfg.min_dominant_cluster_fraction:.0%} floor")

    segment = smallest_confident_segment(dominant, cfg)
    if segment is None:
        blockers.append(
            f"no target residue reaches {cfg.min_contact_occupancy:.0%} contact "
            "occupancy within the dominant cluster")

    partner = sorted({r for s in dominant for r in s.partner_residues})
    return InterfaceConsensus(
        total_samples=total,
        dominant_cluster_samples=len(dominant),
        ensemble_support=round(support, 4),
        target_segment=segment if not blockers else segment,
        partner_contact_residues=partner,
        alternative_clusters=[[s.sample for s in c] for c in clusters[1:]],
        blockers=blockers,
    )
