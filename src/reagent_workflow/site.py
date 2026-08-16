"""Consensus interface → a defensible docking box on the free receptor (§14–16).

Two rules shape this module.

The therapeutic hypothesis is that a compound occupies the partner surface so
the TF can no longer engage it. Compounds are therefore screened against the
**free** receptor, never against a structure where the TF already fills the
site — docking into an occupied pocket asks a different question.

And a box is only defensible if it is small and localised. Centring on the whole
protein finds something everywhere and means nothing, so this module refuses
rather than widening: no consensus residues, none of them present in the free
structure, or a bounding volume too large to be a groove all return blockers.

`receptor_residues()` guards the input side of that. The numbers fed to
`build_search_site` must index the RECEPTOR; the CLI previously produced them
by regexing integers out of a free-text description of the TF's own binding
motif, so a run asked to box MED23 was handed ELK1's residues 374–384 and
built a box around whatever MED23 happens to have at 374–384 — a different
pocket entirely, on the other protein, reported as a defensible site. Numbering
is per-chain and nothing about a valid residue index reveals which chain it
belongs to, so that error is invisible downstream and has to be refused here.
"""
from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field

from .discovery_config import StructureConfig
from .interface import Atom

# Experimentally determined receptor-side pockets. Keyed by receptor gene, and
# usable only for the interaction they were measured on -- these are the
# coordinates of one published complex, not a general property of the protein.
#
# A curated entry is the ONLY alternative to a computed consensus, and it
# carries its citation so a site can always be traced to either an ensemble
# this run produced or a structure someone deposited.
# The accession is pinned alongside the gene symbol deliberately. A calibration
# constant in this project read O75448 for MED23 for a week; O75448 is MED24, an
# adjacent subunit of the same Mediator tail module of plausible length, and an
# entire GPU control run modelled the wrong protein while scoring against MED23
# residue numbering. A symbol on its own does not identify a protein.
CURATED_POCKETS: dict[str, dict] = {
    "MED23": {
        "residues": [339, 343, 379, 382, 383, 533, 537],
        "partner": "ELK1",
        "uniprot": "Q9ULK4",          # MED23, 1368 aa. NOT O75448, which is MED24.
        "source": "Monté et al. 2025, PDB 9F6Y (cryo-EM, 3.0 A)",
        "free_receptor_pdb": "9F76",  # MED23 without the TF bound
        "note": ("MED23 cavity contacting the phosphorylated ELK1 "
                 "transactivation domain. Calibration only: it is where ELK1 "
                 "binds, not a site established for any other TF."),
    },
}


def receptor_residues(receptor_gene: str, *, consensus=None,
                      allow_curated: bool = False,
                      curated_for: str | None = None) -> tuple[list[int], str, list[str]]:
    """The only two legal ways to obtain receptor-side residues.

    Returns (residues, basis, blockers). A non-empty `blockers` means no site
    may be built — the caller must abstain rather than fall back to anything
    else, because every available fallback is a number from the wrong protein.

    `consensus` is an `InterfaceConsensus`; its `partner_contact_residues` are
    receptor-side by construction. A blocked consensus contributes nothing:
    `partner_contact_residues` can be populated on a consensus whose
    `target_segment` was rejected, and reading it anyway would resurrect a
    result the consensus module already refused.
    """
    if consensus is not None:
        if getattr(consensus, "blockers", None):
            return [], "", [f"interface consensus was rejected: "
                            f"{'; '.join(consensus.blockers)[:160]}"]
        residues = list(getattr(consensus, "partner_contact_residues", []) or [])
        if not residues:
            return [], "", ["interface consensus produced no partner-side contact "
                            "residues, so there is no surface to box"]
        return sorted(residues), "ensemble consensus (computed this run)", []

    if allow_curated:
        entry = CURATED_POCKETS.get(receptor_gene)
        if not entry:
            return [], "", [f"no experimentally curated pocket is recorded for "
                            f"{receptor_gene}"]
        if curated_for and curated_for != entry["partner"]:
            return [], "", [
                f"the curated {receptor_gene} pocket was measured against "
                f"{entry['partner']}, not {curated_for}; reusing it would assert a "
                "binding site for a TF no one has placed there"]
        return sorted(entry["residues"]), f"curated experimental: {entry['source']}", []

    return [], "", ["no interface consensus for this run, and curated pockets were "
                    "not enabled; a docking site requires one or the other"]


class SearchSite(BaseModel):
    """A docking box derived from coordinates, never a hardcoded cube."""
    model_config = ConfigDict(extra="forbid")
    center: tuple[float, float, float]
    size: tuple[float, float, float]
    residues: list[int] = Field(default_factory=list)
    basis: str = "ensemble consensus mapped onto the free experimental receptor"
    receptor_path: str | None = None
    blockers: list[str] = Field(default_factory=list)

    @property
    def defensible(self) -> bool:
        return not self.blockers

    @property
    def volume(self) -> float:
        return self.size[0] * self.size[1] * self.size[2]

    def contains(self, x: float, y: float, z: float) -> bool:
        return all(abs(p - c) <= s / 2 for p, c, s
                   in zip((x, y, z), self.center, self.size, strict=True))


def _blocked(reason: str, residues: list[int]) -> SearchSite:
    return SearchSite(center=(0.0, 0.0, 0.0), size=(0.0, 0.0, 0.0),
                      residues=residues, blockers=[reason])


def build_search_site(free_receptor: list[Atom], consensus_residues: list[int],
                      cfg: StructureConfig | None = None,
                      receptor_chain: str | None = None,
                      receptor_path: str | None = None) -> SearchSite:
    """Map consensus partner residues onto the free receptor and box them.

    `consensus_residues` come from the ensemble; the coordinates come from the
    experimental free structure. Residues the ensemble reports but the free
    structure does not resolve are dropped and named, because silently boxing
    the ones that happen to be present would move the site without saying so.
    """
    cfg = cfg or StructureConfig()
    if not consensus_residues:
        return _blocked("no consensus interface residues were produced, so no "
                        "search region can be defined", [])
    if not free_receptor:
        return _blocked("free receptor structure is unavailable", consensus_residues)

    atoms = [a for a in free_receptor
             if (receptor_chain is None or a.chain == receptor_chain)
             and a.element != "H"]
    wanted = set(consensus_residues)
    hit = [a for a in atoms if a.resi in wanted]
    if not hit:
        return _blocked(
            "none of the consensus interface residues are resolved in the free "
            "receptor, so the predicted site cannot be located on it",
            consensus_residues)

    found = {a.resi for a in hit}
    missing = sorted(wanted - found)

    xs = [a.x for a in hit]; ys = [a.y for a in hit]; zs = [a.z for a in hit]
    pad = cfg.box_padding_angstrom
    size = tuple(round(max(hi - lo, 0.0) + 2 * pad, 2)
                 for lo, hi in ((min(xs), max(xs)), (min(ys), max(ys)), (min(zs), max(zs))))
    center = tuple(round((min(v) + max(v)) / 2, 3) for v in (xs, ys, zs))

    site = SearchSite(center=center, size=size, residues=sorted(found),
                      receptor_path=receptor_path)
    if missing:
        site.basis += f"; {len(missing)} consensus residue(s) unresolved in the free structure: {missing}"

    # §15/§13 — a groove, or an abstention. Not a box around the protein.
    if max(size) > cfg.max_box_dimension_angstrom:
        site.blockers.append(
            f"interface spans {max(size):.1f} A, beyond the "
            f"{cfg.max_box_dimension_angstrom:.0f} A limit for a localised site: this "
            "is a broad surface rather than a groove, and is not plausibly "
            "small-molecule addressable")
    return site


def residue_polarity(free_receptor: list[Atom], residues: list[int],
                     sequence: dict[int, str] | None = None) -> dict[str, list[int]]:
    """Classify site residues for the pose-orientation check (§25).

    Needs residue identities; mmCIF atom records carry them, but when only
    numbering is available this returns empty classes rather than guessing —
    an invented chemical environment would silently drive the pose filter.
    """
    if not sequence:
        return {"hydrophobic": [], "polar": [], "charged": [], "unknown": sorted(residues)}
    HYD, POL, CHG = set("AVLIMFWYPCG"), set("STNQH"), set("DEKR")
    out: dict[str, list[int]] = {"hydrophobic": [], "polar": [], "charged": [], "unknown": []}
    for r in sorted(residues):
        aa = (sequence.get(r) or "").upper()
        key = ("hydrophobic" if aa in HYD else "polar" if aa in POL
               else "charged" if aa in CHG else "unknown")
        out[key].append(r)
    return out


def site_centroid_distance(site: SearchSite, x: float, y: float, z: float) -> float:
    return math.dist((x, y, z), site.center)
