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
"""
from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field

from .discovery_config import StructureConfig
from .interface import Atom, InterfaceConsensus, InterfaceHypothesis


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


def site_from_consensus(consensus: InterfaceConsensus,
                        free_receptor: list[Atom],
                        cfg: StructureConfig | None = None,
                        receptor_chain: str | None = None,
                        receptor_path: str | None = None,
                        hypothesis: InterfaceHypothesis | None = None,
                        approved_by: str | None = None) -> SearchSite:
    """The only path from a consensus to a docking box.

    Docking a site asserts that the site is where the interface is. Only a
    `converged` consensus carries that claim on its own.

    An `ambiguous` consensus holds one or more localized hypotheses that did not
    carry the ensemble — the ELK1-MED23 control produced exactly that, with the
    hypothesis matching the published pocket sitting in a minority of one. It is
    worth keeping and worth sampling against; it is not worth spending a screen
    on. So it docks only when a person names the hypothesis and signs for it,
    and the site records who did.

    `approved_by` is a person, not a flag: an unconverged site that reaches a
    screen should say on its face whose judgement put it there.
    """
    if consensus.status == "converged" and consensus.primary_hypothesis is not None:
        chosen, basis = consensus.primary_hypothesis, None
    elif consensus.status == "ambiguous":
        if not approved_by:
            return _blocked(
                f"consensus status is `ambiguous`: {len(consensus.alternative_hypotheses)} "
                f"hypothes(es) retained, none converged. Next action is "
                f"`{consensus.next_action}`. An unconverged interface may not "
                "generate a docking site automatically; it needs a named human "
                "approval and a chosen hypothesis.",
                consensus.partner_contact_residues)
        candidates = [h for h in consensus.alternative_hypotheses if h.localized]
        if hypothesis is None:
            if len(candidates) != 1:
                return _blocked(
                    f"approval given but no hypothesis chosen, and {len(candidates)} "
                    "localized hypotheses are retained: the approval has to name "
                    "which one to dock.", consensus.partner_contact_residues)
            hypothesis = candidates[0]
        if not hypothesis.localized:
            return _blocked(
                f"hypothesis {hypothesis.hypothesis_id} is not localized, so it "
                "cannot define a search region whatever the approval says.",
                hypothesis.partner_contact_residues)
        chosen = hypothesis
        basis = (f"UNCONVERGED minority hypothesis {chosen.hypothesis_id} "
                 f"({chosen.support_fraction:.0%} of the ensemble, samples "
                 f"{'+'.join(chosen.sample_ids)}), docked on the explicit "
                 f"approval of {approved_by}")
    else:
        return _blocked(
            f"consensus status is `{consensus.status}`, next action "
            f"`{consensus.next_action}`: no interface hypothesis is defensible "
            "enough to define a search region.",
            consensus.partner_contact_residues)

    site = build_search_site(free_receptor, chosen.partner_contact_residues, cfg,
                             receptor_chain=receptor_chain,
                             receptor_path=receptor_path)
    if basis:
        site.basis = basis + "; " + site.basis
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
