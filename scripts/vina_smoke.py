#!/usr/bin/env python
"""One virtual screen against one defensible site, end to end (§P1-2).

    python scripts/vina_smoke.py                 # dry run: builds and refuses to dock
    python scripts/vina_smoke.py --dock          # actually runs Vina (CPU, minutes)
    python scripts/vina_smoke.py --dock --json runs/vina_smoke.json

This is a smoke test of the docking arm, not a screening campaign. It answers a
narrow question — does a site produced by this pipeline actually accept
compounds, and do the numbers come back attached to their provenance — on the
one site that currently exists.

That site is the **experimental MED23 pocket** from the deposited ELK1 complex,
not a predicted one. It is therefore calibration: it is where ELK1 binds, and
nothing here establishes that any discovered TF binds there. Every record says
so.

The hard rule, enforced rather than documented: a site that is not
`defensible` does not get docked. Docking into an undefined box finds something
everywhere and means nothing, and a score reported without a defensible site
would be the most convincing wrong number this project could produce.

Scores are Vina's empirical scoring function. They rank poses; they are not
free energies, not affinities, and not evidence of binding.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from reagent_workflow.discovery_config import DiscoveryConfig       # noqa: E402
from reagent_workflow.interface import parse_mmcif                  # noqa: E402
from reagent_workflow.site import (CURATED_POCKETS, build_search_site,  # noqa: E402
                                   receptor_residues)

FREE_RECEPTOR = ROOT / "downloads" / "9F76.cif"
SEED = 20260815          # fixed: a screen you cannot repeat is not a result
EXHAUSTIVENESS = 8
NUM_POSES = 9

# The same twelve approved drugs the CLI carries. Chemically diverse, all with
# real provenance, and none of them designed for a protein-protein interface --
# which is the point. They are a negative-leaning control that exercises the
# machinery. A good score here is not a hit, it is a reason to check the pose.
SEED_DRUGS: list[tuple[str, str, str]] = [
    ("aspirin", "CC(=O)Oc1ccccc1C(=O)O", "approved"),
    ("atorvastatin", "CC(C)c1c(C(=O)Nc2ccccc2)c(-c2ccccc2)c(-c2ccc(F)cc2)n1CC[C@@H](O)C[C@@H](O)CC(=O)O", "approved"),
    ("imatinib", "Cc1ccc(NC(=O)c2ccc(CN3CCN(C)CC3)cc2)cc1Nc1nccc(-c2cccnc2)n1", "approved"),
    ("celecoxib", "Cc1ccc(-c2cc(C(F)(F)F)nn2-c2ccc(S(N)(=O)=O)cc2)cc1", "approved"),
    ("fluoxetine", "CNCCC(Oc1ccc(C(F)(F)F)cc1)c1ccccc1", "approved"),
    ("ibuprofen", "CC(C)Cc1ccc(cc1)C(C)C(=O)O", "approved"),
    ("naproxen", "COc1ccc2cc(ccc2c1)C(C)C(=O)O", "approved"),
    ("propranolol", "CC(C)NCC(O)COc1cccc2ccccc12", "approved"),
    ("diphenhydramine", "CN(C)CCOC(c1ccccc1)c1ccccc1", "approved"),
    ("warfarin", "CC(=O)CC(c1ccccc1)c1c(O)c2ccccc2oc1=O", "approved"),
    ("dexamethasone", "CC1CC2C3CCC4=CC(=O)C=CC4(C)C3(F)C(O)CC2(C)C1(O)C(=O)CO", "approved"),
    ("metformin", "CN(C)C(=N)NC(N)=N", "approved"),
]


def pose_geometry(sdf: str, site, receptor_atoms, pocket: set[int]):
    """Where the pose actually landed, relative to the box we asked for.

    Vina is told to search a box; it is not prevented from placing a ligand at
    the box edge, and a score alone cannot distinguish a pose in the pocket
    from one skimming its corner. So the pose is measured, not assumed:

      offset       distance from the pose centroid to the box centre
      inside       centroid within the box at all
      contacts     pocket residues with a heavy atom within 4.5 A
      min_contact  closest approach to any pocket residue
      clash        any heavy-atom pair closer than 2.0 A

    A pose with a good score, no contacts and a large offset is docking into
    the wrong place, and that combination is exactly what a bare score hides.
    """
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
            if d < 2.0:
                clash = True
            if a.resi in pocket:
                closest = min(closest, d)
                if d <= 4.5:
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dock", action="store_true",
                    help="actually run Vina (CPU, several minutes)")
    ap.add_argument("--json", type=pathlib.Path)
    ap.add_argument("--limit", type=int, default=len(SEED_DRUGS))
    ap.add_argument("--gene", help="dock into the interface predicted for this TF "
                                   "(runs/interfaces/<GENE>_MED23/consensus.json) "
                                   "instead of the curated ELK1 cavity")
    args = ap.parse_args()

    cfg = DiscoveryConfig()
    partner = cfg.partner_gene

    # ── 1. the site, from a legal source only ──────────────────────────────
    #
    # Two legal sources, and `--gene` selects the second. The therapeutic
    # hypothesis is that a compound sits where the TF would sit, so the box
    # that matters is the one the co-fold puts *that* TF on — the curated
    # cavity is where ELK1 binds and is calibration for everyone else. A
    # consensus that did not converge writes no residues, so this refuses
    # rather than docking into an interface the ensemble rejected.
    curated = CURATED_POCKETS.get(partner, {})
    if args.gene:
        path = (ROOT / "runs" / "interfaces" / f"{args.gene.upper()}_{partner}"
                / "consensus.json")
        if not path.is_file():
            print(f"REFUSED  no ensemble for {args.gene}-{partner}: {path} is not on "
                  f"disk. Run scripts/predict_med23_interface.py {args.gene.upper()} "
                  "--accession <UNIPROT> --dispatch first.", file=sys.stderr)
            return 2
        rec = json.loads(path.read_text(encoding="utf-8"))
        residues = list(rec.get("partner_contact_residues") or [])
        blockers = ([] if residues else
                    [f"the {args.gene}-{partner} consensus produced no partner-side "
                     f"residues: {'; '.join(rec['consensus'].get('blockers', []))[:160]}"])
        basis = (f"ensemble consensus for {args.gene.upper()}-{partner}, "
                 f"{rec['consensus']['dominant_cluster_samples']}/"
                 f"{rec['consensus']['total_samples']} samples agree "
                 f"({rec['consensus']['ensemble_support']:.0%})")
    else:
        residues, basis, blockers = receptor_residues(
            partner, allow_curated=True, curated_for=curated.get("partner"))
    if blockers:
        print(f"REFUSED  {'; '.join(blockers)}", file=sys.stderr)
        return 2
    if not FREE_RECEPTOR.exists():
        print(f"REFUSED  free receptor {FREE_RECEPTOR} is not on disk", file=sys.stderr)
        return 2

    atoms = parse_mmcif(FREE_RECEPTOR)
    site = build_search_site(atoms, residues, cfg.structure,
                             receptor_path=str(FREE_RECEPTOR))
    site.basis = basis

    print(f"receptor    {FREE_RECEPTOR.name}  ({len(atoms)} atoms, free form)")
    print(f"site        {basis}")
    print(f"            residues {residues}")
    if not site.defensible:
        # The hard rule. No score is produced without a site.
        print(f"REFUSED     {'; '.join(site.blockers)}", file=sys.stderr)
        print("no defensible site, so nothing is docked", file=sys.stderr)
        return 2
    print(f"            centre {tuple(round(c, 2) for c in site.center)}  "
          f"size {tuple(round(s, 2) for s in site.size)} A  "
          f"volume {site.volume:.0f} A^3")
    print(f"            resolved {len(site.residues)}/{len(residues)} residues "
          f"in the free structure")
    print(f"ligands     {min(args.limit, len(SEED_DRUGS))} approved drugs, "
          f"seed {SEED}, exhaustiveness {EXHAUSTIVENESS}")
    print(f"caveat      {curated.get('note', '')}")

    record = {
        "receptor": {"path": str(FREE_RECEPTOR), "gene": partner,
                     "form": "free (no TF bound)", "n_atoms": len(atoms)},
        "site": {"basis": basis,
                 "source": (f"Boltz-2 ensemble, {args.gene.upper()}-{partner}"
                            if args.gene else curated.get("source")),
                 "target": args.gene.upper() if args.gene else curated.get("partner"),
                 "requested_residues": residues,
                 "resolved_residues": site.residues,
                 "center": list(site.center), "size": list(site.size),
                 "volume": round(site.volume, 1), "defensible": True},
        "config": {"seed": SEED, "exhaustiveness": EXHAUSTIVENESS,
                   "num_poses": NUM_POSES, "scoring_function": "vina",
                   "device": "cpu"},
        "interpretation": "computational_prediction",
        "limitations": [
            "Vina scores rank poses. They are not free energies, not "
            "affinities, and not evidence of binding.",
            (f"The site is a Boltz-2 prediction of where {args.gene.upper()} "
             f"contacts {partner}. It is a computational hypothesis about the "
             "interface, not an observed one, and a compound scored against it "
             "inherits that uncertainty." if args.gene else
             f"The site is the experimental {partner} pocket from the deposited "
             "ELK1 complex. It is where ELK1 binds; no TF discovered by this "
             "pipeline has been placed there."),
            "The receptor is rigid and unliganded. No induced fit is modelled.",
            "Approved drugs are a machinery control, not a designed library. A "
            "good score here is a reason to inspect the pose, not a hit.",
        ],
        "results": [],
    }

    if not args.dock:
        print("\ndry run — pass --dock to run Vina. Nothing was docked.")
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(json.dumps(record, indent=2))
            print(f"wrote {args.json}")
        return 0

    # ── 2. dock ────────────────────────────────────────────────────────────
    from proto_tools.entities.ligands.ligands import Fragment
    from proto_tools.entities.structures.structure import Structure
    from proto_tools.tools.molecular_docking.vina import (VinaDockingConfig,
                                                          VinaDockingInput,
                                                          VinaSearchBox,
                                                          run_vina_docking)

    receptor = Structure.from_file(str(FREE_RECEPTOR))
    box = VinaSearchBox(center=tuple(site.center), size=tuple(site.size))
    vcfg = VinaDockingConfig(seed=SEED, exhaustiveness=EXHAUSTIVENESS,
                             num_poses=NUM_POSES, device="cpu", verbose=0)
    pocket = set(site.residues)

    print()
    t0 = time.time()
    for name, smiles, provenance in SEED_DRUGS[:args.limit]:
        t1 = time.time()
        row = {"compound": name, "smiles": smiles, "provenance": provenance,
               "library_arm": "approved-drug control"}
        try:
            # `Fragment` resolves a CCD code from the SMILES on construction,
            # and `VinaDockingInput` then re-validates SMILES against CCD and
            # can reject the pairing Proto itself just made -- metformin
            # auto-assigns MF8 and is then refused as "different molecules",
            # a tautomer disagreement inside the lookup. The SMILES is what we
            # have provenance for, so it is made authoritative by dropping the
            # inferred code. `model_copy` because the field is set during
            # validation, not passed in.
            frag = Fragment(id="L", smiles=smiles).model_copy(
                update={"ccd_code": None})
            out = run_vina_docking(
                VinaDockingInput(receptor=receptor, ligands=[frag],
                                 search_box=box),
                vcfg)
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {str(e)[:140]}"
            print(f"  {name:18s} FAILED  {row['error'][:70]}")
            record["results"].append(row)
            continue

        res = (out.results or [None])[0]
        poses = getattr(res, "poses", None) or []
        if not poses:
            row["error"] = "vina returned no pose"
            print(f"  {name:18s} no pose")
            record["results"].append(row)
            continue

        best = poses[0]
        # `primary_metric` is the NAME of the headline metric ("affinity"), not
        # its value -- reading it as the score silently reports every compound
        # as unscored. `Metrics` is dict-like; the value comes from `.get`.
        met = getattr(best, "metrics", None)
        raw = met.get("affinity") if met is not None else None
        try:
            score = float(raw)
        except (TypeError, ValueError):
            score = None
        rmsd = {k: met.get(k) for k in ("rmsd_lower_bound", "rmsd_upper_bound")} \
            if met is not None else {}
        geom = pose_geometry(best.sdf or "", site, atoms, pocket)
        row.update({
            "vina_score": score,
            "score_unit": "kcal/mol",
            "rmsd_bounds": rmsd,
            "n_poses": len(poses),
            "seconds": round(time.time() - t1, 1),
            "geometry": geom,
            "pose_sdf": best.sdf,
            # The flags a bare score hides.
            "wrong_site": bool(geom.get("parsed") and geom.get("n_pocket_contacts") == 0),
            "clash": bool(geom.get("clash")),
            "outside_box": bool(geom.get("parsed") and not geom.get("inside_box")),
        })
        flags = [f for f in ("wrong_site", "clash", "outside_box") if row[f]]
        print(f"  {name:18s} {'  n/a ' if score is None else f'{score:6.2f}'} kcal/mol  "
              f"{geom.get('n_pocket_contacts', 0)} contacts  "
              f"offset {geom.get('offset_from_box_centre', '?')} A"
              + (f"   [{', '.join(flags)}]" if flags else ""))
        record["results"].append(row)

    scored = [r for r in record["results"] if r.get("vina_score") is not None]
    clean = [r for r in scored if not (r["wrong_site"] or r["clash"] or r["outside_box"])]
    record["summary"] = {
        "docked": len(record["results"]), "scored": len(scored),
        "clean_poses": len(clean),
        "best": min((r["vina_score"] for r in clean), default=None),
        "wall_seconds": round(time.time() - t0, 1),
    }
    print(f"\n{len(scored)}/{len(record['results'])} scored, {len(clean)} without "
          f"flags, best {record['summary']['best']} kcal/mol, "
          f"{record['summary']['wall_seconds']}s")
    print("Ranking only. No claim of binding, affinity, safety or efficacy.")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(record, indent=2))
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
