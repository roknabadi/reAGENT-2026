#!/usr/bin/env python
"""The MED23 the interface always renders, plus whatever a run has to highlight.

    python scripts/build_med23_view.py            # -> ui/med23.json

The structure tab used to show nothing until a run produced a complex, so the
common case — no prediction yet — was a blank panel. MED23 is the receptor for
every candidate this pipeline proposes, and it exists as a deposited apo
structure, so it can always be on screen. What changes per run is what is
highlighted on it, not whether there is anything to look at.

The coordinates are PDB **9F76**: MED23 with no TF bound, cryo-EM, residues
54-1334 of 1368 — 92% of the protein. Apo rather than the ELK1 complex on
purpose: the therapeutic hypothesis is that a compound occupies this surface so
a TF can no longer engage it, so the surface has to be shown unoccupied.

Four layers, kept separate because they are different kinds of claim:

  atoms       every resolved heavy atom, chain A. Observed. This is the body
              the rest of the view sits on: a Cα trace alone reads as a wire
              sketch, and a highlight drawn on a wire has nothing to be *on*.
              ~10k atoms is a few hundred KB of JSON and one instanced draw
              call, which is a cheap price for a surface that looks like a
              protein.
  backbone    one point per residue, at Cα. Observed. Carries the fold and the
              chain breaks; drawn as a tube through the atom cloud.
  pocket      the ELK1-binding cavity, from the deposited complex 9F6Y.
              Experimental, but it is where ELK1 binds — not a site shown for
              any other TF.
  predicted   per-candidate interface residues from a Boltz ensemble, when one
              exists. Computational hypothesis, and empty until a run produces
              one rather than filled with a guess.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from reagent_workflow.interface import parse_mmcif          # noqa: E402
from reagent_workflow.site import CURATED_POCKETS           # noqa: E402

RECEPTOR = ROOT / "downloads" / "9F76.cif"
OUT = ROOT / "ui" / "med23.json"
UNIPROT_LENGTH = 1368        # MED23 Q9ULK4


def build() -> dict:
    atoms = parse_mmcif(RECEPTOR)
    # One point per residue, at CA where there is one. This is the fold: the
    # atom cloud below gives the view a body, but a tube through the Cα points
    # is what makes the chain and its breaks readable.
    ca = {}
    for a in atoms:
        if a.chain != "A":
            continue
        if a.name == "CA" or a.resi not in ca:
            ca.setdefault(a.resi, (a.x, a.y, a.z))
            if a.name == "CA":
                ca[a.resi] = (a.x, a.y, a.z)
    trace = [[r, round(x, 2), round(y, 2), round(z, 2)]
             for r, (x, y, z) in sorted(ca.items())]

    # The full body. Hydrogens are absent from the deposition anyway; the guard
    # is here so this stays right if a structure that has them is ever swapped
    # in. Residue index rides along with each atom so the viewer can colour a
    # whole residue without a second lookup table.
    heavy = [[a.resi, round(a.x, 2), round(a.y, 2), round(a.z, 2)]
             for a in atoms if a.chain == "A" and a.element != "H"]

    entry = CURATED_POCKETS["MED23"]
    pocket = sorted(set(entry["residues"]) & set(ca))
    return {
        "gene": "MED23",
        "uniprot": entry["uniprot"],
        "pdb_id": "9F76",
        "method": "cryo-EM",
        "form": "apo — no transcription factor bound",
        "licence": "PDB coordinates, public domain (CC0)",
        "url": "https://www.rcsb.org/structure/9F76",
        "chain": "A",
        "residue_range": [trace[0][0], trace[-1][0]],
        "residues_resolved": len(trace),
        "residues_total": UNIPROT_LENGTH,
        "coverage": round(len(trace) / UNIPROT_LENGTH, 3),
        "trace": trace,
        "atoms": heavy,
        "n_atoms": len(heavy),
        "pocket": {
            "residues": pocket,
            "label": "ELK1-binding cavity",
            "source": entry["source"],
            "interpretation": "observed",
            "note": entry["note"],
        },
        # Filled by a run that produces an ensemble. Empty is the honest state,
        # not a placeholder to be populated with something plausible.
        "predicted": [],
        "ligands": [],
        "limitations": [
            "Apo coordinates. Nothing here shows a TF or a compound bound.",
            "The pocket is where ELK1 binds. It is not a site established for "
            "any other transcription factor.",
            "Residues 1-53 and 1335-1368 are unresolved in this structure.",
        ],
    }


def main() -> int:
    if not RECEPTOR.exists():
        print(f"missing {RECEPTOR}\n"
              f"  curl -sL -o {RECEPTOR} https://files.rcsb.org/download/9F76.cif",
              file=sys.stderr)
        return 2
    view = build()
    OUT.write_text(json.dumps(view))
    print(f"MED23 {view['pdb_id']} ({view['method']}, {view['form']})")
    print(f"  {view['residues_resolved']}/{view['residues_total']} residues "
          f"({view['coverage']:.0%}), {view['residue_range'][0]}-"
          f"{view['residue_range'][1]}")
    print(f"  {view['n_atoms']} heavy atoms, chain {view['chain']}")
    print(f"  pocket {view['pocket']['residues']}  ({view['pocket']['label']})")
    print(f"  wrote {OUT}  ({OUT.stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
