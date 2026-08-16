"""Propose a library for a site, check what it proposed, and dock it.

The screening arm used to be twelve approved drugs written into a script:
identical for every site the pipeline would ever build, which tests the docking
machinery and says nothing about the site. A constant cannot answer "what should
we screen against *this* groove", and the answer to that question is the point.

So the library is proposed from the site's own residues and their chemistry, and
then every structure is put through two checks before it is allowed near a
docking box:

  standardize      RDKit parses it, strips salts, refuses mixtures, biologics
                   and metal complexes. This proves the string is *a* molecule.
  verify_identity  the structure is hashed to an InChIKey and looked up in
                   PubChem. This is the check that matters: a model asked for
                   imatinib can return a SMILES that parses perfectly and is
                   not imatinib, and no amount of local validation notices.
                   A structure PubChem does not know is docked and labelled
                   unverified, never silently passed off as the named compound.
                   A lookup that never completed (PubChem unreachable) is not
                   a verdict either way, so that one is abstained rather than
                   docked. And the InChIKey matching is not the whole check:
                   the proposed name is compared against PubChem's own name
                   for the structure, so a hash match under the wrong name is
                   still caught rather than passed off as "verified".

Docking then proceeds exactly as the smoke screen always did, including the
geometry checks -- a score with no pose geometry is the most convincing wrong
number available here.

Nothing in this file makes a compound a binder. It makes the library relevant to
the site, its members identifiable, and its poses checkable.
"""
from __future__ import annotations

import math
import time

from .chemistry import standardize, verify_identity
from .discovery_config import ChemistryConfig, StructureConfig

def pose_geometry(sdf: str, site, receptor_atoms, pocket: set[int],
                  cfg: StructureConfig | None = None):
    """Where the pose actually landed, relative to the box we asked for.

    Vina is told to search a box; it is not prevented from placing a ligand at
    the box edge, and a score alone cannot distinguish a pose in the pocket
    from one skimming its corner. So the pose is measured, not assumed:

      offset       distance from the pose centroid to the box centre
      inside       centroid within the box at all
      contacts     pocket residues with a heavy atom within cfg.contact_cutoff_angstrom
      min_contact  closest approach to any pocket residue
      clash        any heavy-atom pair closer than 2.0 A

    A pose with a good score, no contacts and a large offset is docking into
    the wrong place, and that combination is exactly what a bare score hides.

    A pose whose geometry cannot be parsed returns only {"parsed": False} —
    callers must check "parsed" before trusting any other key. An unparsed
    pose is not "0 contacts" or "outside the box", it is unknown, and treating
    unknown as clean is how a pose nobody actually looked at becomes `best`.
    """
    cfg = cfg or StructureConfig()
    coords = []
    for line in sdf.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            try:
                x, y, z = float(parts[0]), float(parts[1]), float(parts[2])
            except ValueError:
                continue
            if parts[3].isalpha() and parts[3] != "H":
                coords.append((x, y, z))
    if not coords:
        return {"parsed": False}

    cx = sum(c[0] for c in coords) / len(coords)
    cy = sum(c[1] for c in coords) / len(coords)
    cz = sum(c[2] for c in coords) / len(coords)
    offset = math.dist((cx, cy, cz), site.center)

    near, closest, clash = set(), float("inf"), False
    for a in receptor_atoms:
        if a.element == "H":
            continue
        for c in coords:
            d = math.dist(c, (a.x, a.y, a.z))
            # 2.0 A stays a literal here: it belongs on StructureConfig next
            # to contact_cutoff_angstrom (audited finding, screen.py:75), but
            # discovery_config.py is owned by another workstream and out of
            # scope for this fix.
            if d < 2.0:
                clash = True
            if a.resi in pocket:
                closest = min(closest, d)
                if d <= cfg.contact_cutoff_angstrom:
                    near.add(a.resi)
    return {"parsed": True, "heavy_atoms": len(coords),
            "centroid": [round(cx, 2), round(cy, 2), round(cz, 2)],
            "offset_from_box_centre": round(offset, 2),
            "inside_box": site.contains(cx, cy, cz),
            "pocket_contacts": sorted(near),
            "n_pocket_contacts": len(near),
            "closest_pocket_approach": (round(closest, 2)
                                        if closest != float("inf") else None),
            "clash": clash}


def prepare_compounds(proposed: list[dict], log=print) -> tuple[list[dict], list[dict]]:
    """Standardize and identify each proposal. Returns (usable, rejected)."""
    usable, rejected = [], []
    for c in proposed:
        name = str(c.get("name") or "unnamed").strip()
        parent, why = standardize(str(c.get("smiles") or ""))
        if parent is None:
            rejected.append({"compound": name, "smiles": c.get("smiles"),
                             "reason": why or "unusable structure"})
            log(f"  {name:22s} rejected — {why}")
            continue
        # Only pass a real proposed name into the identity check, not the
        # "unnamed" placeholder used for logging — comparing a placeholder
        # against PubChem's Title would manufacture a false name_mismatch.
        proposed_name = str(c["name"]).strip() if c.get("name") else None
        ident = verify_identity(parent, name=proposed_name)
        if ident["status"] == "lookup_failed":
            # PubChem was never actually consulted, so "docked and labelled
            # unverified" would misrepresent a check that could not run.
            # "unverified because PubChem said no" and "unverified because
            # PubChem was never asked" are different facts; only the first
            # one is safe to dock and label.
            rejected.append({"compound": name, "smiles": parent,
                             "reason": ident.get("note") or "identity lookup failed"})
            log(f"  {name:22s} rejected — {ident.get('note')}")
            continue
        usable.append({"compound": name, "smiles": parent,
                       "proposed_smiles": c.get("smiles"),
                       "provenance": c.get("kind") or "proposed",
                       "rationale": c.get("rationale") or "",
                       "identity": ident})
        log(f"  {name:22s} {ident['status']}"
            + (f" — PubChem {ident['name']}" if ident.get("name") else ""))
    return usable, rejected


def dock(site, receptor_atoms, compounds: list[dict],
         cfg: ChemistryConfig | None = None, *, seed: int,
         exhaustiveness: int | None = None, num_poses: int | None = None,
         log=print) -> dict:
    """Dock a prepared library into a defensible site, with the geometry checks.

    Refuses rather than docks when the site is not defensible: a box that was
    not defensible is a box around the whole protein, and docking into one finds
    something everywhere.
    """
    if site is None or not site.defensible:
        raise ValueError("no defensible site: " + "; ".join(
            getattr(site, "blockers", ["site was not built"])))

    # `cfg` used to be accepted and never read — the only live caller passed
    # None, so ChemistryConfig could never actually reach Vina. Falling back
    # to the default here, and only overriding exhaustiveness/num_poses from
    # cfg when the caller did not explicitly pin them, is what makes cfg
    # load-bearing instead of decorative.
    cfg = cfg or ChemistryConfig()
    if exhaustiveness is None:
        exhaustiveness = cfg.fast_vina_exhaustiveness
    if num_poses is None:
        num_poses = cfg.fast_vina_poses

    from proto_tools.entities.ligands.ligands import Fragment
    from proto_tools.entities.structures.structure import Structure
    from proto_tools.tools.molecular_docking.vina import (VinaDockingConfig,
                                                          VinaDockingInput,
                                                          VinaSearchBox,
                                                          run_vina_docking)

    receptor = Structure.from_file(str(site.receptor_path))
    box = VinaSearchBox(center=tuple(site.center), size=tuple(site.size))
    vcfg = VinaDockingConfig(seed=seed, exhaustiveness=exhaustiveness,
                             num_poses=num_poses, device="cpu", verbose=0)
    pocket = set(site.residues)

    results, t0 = [], time.time()
    for c in compounds:
        row = dict(c)
        t1 = time.time()
        try:
            # `Fragment` infers a CCD code from the SMILES and `VinaDockingInput`
            # can then reject the pairing Proto itself just made -- a tautomer
            # disagreement inside the lookup. The SMILES is what we have
            # provenance for, so it is made authoritative.
            frag = Fragment(id="L", smiles=c["smiles"]).model_copy(
                update={"ccd_code": None})
            out = run_vina_docking(
                VinaDockingInput(receptor=receptor, ligands=[frag], search_box=box),
                vcfg)
        except Exception as e:                               # noqa: BLE001
            row["error"] = f"{type(e).__name__}: {str(e)[:140]}"
            log(f"  {c['compound']:22s} FAILED {row['error'][:60]}")
            results.append(row)
            continue

        res = (out.results or [None])[0]
        poses = getattr(res, "poses", None) or []
        if not poses:
            row["error"] = "vina returned no pose"
            results.append(row)
            continue

        best = poses[0]
        met = getattr(best, "metrics", None)
        raw = met.get("affinity") if met is not None else None
        try:
            score = float(raw)
        except (TypeError, ValueError):
            score = None
        geom = pose_geometry(best.sdf or "", site, receptor_atoms, pocket)
        row.update({
            "vina_score": score, "score_unit": "kcal/mol",
            "n_poses": len(poses), "seconds": round(time.time() - t1, 1),
            "geometry": geom, "pose_sdf": best.sdf,
            "wrong_site": bool(geom.get("parsed") and geom.get("n_pocket_contacts") == 0),
            "clash": bool(geom.get("clash")),
            "outside_box": bool(geom.get("parsed") and not geom.get("inside_box")),
            # wrong_site and outside_box both require geom["parsed"] to be
            # true, so an unparsed pose evaluates False for both and looks
            # clean. This flag is what actually excludes it.
            "geometry_unparsed": not geom.get("parsed"),
        })
        flags = [f for f in ("wrong_site", "clash", "outside_box", "geometry_unparsed")
                if row[f]]
        log(f"  {c['compound']:22s} "
            f"{' n/a ' if score is None else f'{score:6.2f}'} kcal/mol · "
            f"{geom.get('n_pocket_contacts', 0)} contacts"
            + (f" · {', '.join(flags)}" if flags else ""))
        results.append(row)

    scored = [r for r in results if r.get("vina_score") is not None]
    clean = [r for r in scored
             if not (r["wrong_site"] or r["clash"] or r["outside_box"]
                    or r["geometry_unparsed"])]
    # Best among identity-verified compounds only. `best` below can be led by
    # a not_in_pubchem or name_mismatch structure — a starting point, not a
    # claim about a named drug — so a caller that wants a defensible number
    # needs the verified-only one instead.
    verified_clean = [r for r in clean
                      if (r.get("identity") or {}).get("status") == "verified"]
    status_counts = {"verified": 0, "not_in_pubchem": 0, "lookup_failed": 0}
    for r in results:
        s = (r.get("identity") or {}).get("status")
        if s in status_counts:
            status_counts[s] += 1
    return {
        "receptor": {"path": str(site.receptor_path), "gene": "MED23",
                     "form": "free (no TF bound)"},
        "site": {"basis": site.basis, "requested_residues": site.residues,
                 "resolved_residues": site.residues,
                 "center": list(site.center), "size": list(site.size),
                 "defensible": True},
        "config": {"seed": seed, "exhaustiveness": exhaustiveness,
                   "num_poses": num_poses, "scoring_function": "vina",
                   "device": "cpu"},
        "library": "proposed for this site, standardized and identity-checked",
        "interpretation": "computational_prediction",
        "results": results,
        "summary": {"docked": len(results), "scored": len(scored),
                    "clean_poses": len(clean),
                    "best": min((r["vina_score"] for r in clean), default=None),
                    "best_verified": min((r["vina_score"] for r in verified_clean),
                                         default=None),
                    **status_counts,
                    "wall_seconds": round(time.time() - t0, 1)},
        "limitations": [
            "Vina scores rank poses. They are not free energies, not "
            "affinities, and not evidence of binding.",
            "The library was proposed for this site, not measured against it. "
            "A compound appearing here is a starting point, not a hit.",
            "Identity is checked against PubChem by InChIKey. A compound marked "
            "unverified is a structure no public record matches.",
            "The receptor is rigid and unliganded. No induced fit is modelled.",
        ],
    }
