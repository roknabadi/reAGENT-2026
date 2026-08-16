#!/usr/bin/env python
"""Score the ELK1-MED23 control against the published interface.

    python scripts/score_elk1_control.py

Reads whatever `scripts/elk1_control.py` last wrote into `runs/elk1_control/`
and asks one question: did an unbiased Boltz ensemble put the ELK1 motif in the
MED23 pocket? Runs no GPU and fetches nothing.

The previous version of this analysis was done by hand, which is part of why a
wrong partner protein survived it. Scoring belongs in a file that can be read,
disagreed with, and re-run.

Two measures, and only one of them is informative:

  motif recovery  - how many of ELK1 374-384 are in contact. Nearly free: the
                    92-residue window submitted is mostly disordered, so a model
                    that drapes it across the partner scores well without
                    recognising anything.
  pocket recovery - how many of the seven published MED23 residues are in
                    contact, against how many would be expected if the same
                    number of contacted MED23 residues were drawn at random from
                    the 1368. This is the discriminating one, and it is reported
                    with its chance baseline every time so the raw count is
                    never read alone.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from reagent_workflow.interface import (  # noqa: E402
    ConsensusConfig, build_consensus, contacts_between, parse_mmcif)

OUT = ROOT / "runs" / "elk1_control"

# Monte et al. 2025 (doi:10.1038/s41467-025-59014-8), PDB 9F6Y. MED23 numbering.
PUBLISHED_MED23_POCKET = {339, 343, 379, 382, 383, 533, 537}
PUBLISHED_ELK1_MOTIF = set(range(374, 385))
MED23_LENGTH = 1368

# Chain B is the submitted window, numbered from 1 by the predictor. The control
# submits ELK1 310-401, so chain B residue 1 is ELK1 310.
TAD_START = 310


def main() -> int:
    cifs = sorted(OUT.glob("sample_*.cif"))
    if not cifs:
        raise SystemExit(f"no samples in {OUT}; run scripts/elk1_control.py first")

    cfg = ConsensusConfig()
    samples, rows = [], []
    for path in cifs:
        atoms = parse_mmcif(path)
        chains = sorted({a.chain for a in atoms})
        if len(chains) != 2:
            raise SystemExit(f"{path.name}: expected 2 chains, found {chains}")
        partner_chain, target_chain = chains          # A = MED23, B = ELK1
        iface = contacts_between(atoms, target_chain, partner_chain,
                                 cfg.contact_cutoff_angstrom, sample=path.stem)

        # Renumber the target side into ELK1 coordinates before anything is
        # compared. Scoring predicted residue 65 against published residue 374
        # is the same class of mistake as scoring MED24 against MED23.
        elk1 = {r + TAD_START - 1 for r in iface.target_residues}
        med23 = set(iface.partner_residues)
        chance = len(PUBLISHED_MED23_POCKET) * len(med23) / MED23_LENGTH
        rows.append({
            "sample": path.stem, "contacts": len(iface.contacts),
            "n_elk1": len(elk1), "n_med23": len(med23),
            "motif": len(elk1 & PUBLISHED_ELK1_MOTIF),
            "pocket": sorted(med23 & PUBLISHED_MED23_POCKET),
            "chance": chance,
        })
        samples.append(iface)

    print(f"{len(cifs)} samples from {OUT}\n")
    print(f"{'sample':10s} {'contacts':>9s} {'ELK1 res':>9s} {'in motif':>9s} "
          f"{'MED23 res':>10s} {'in pocket':>10s}  {'by chance':>9s}")
    for r in rows:
        print(f"{r['sample']:10s} {r['contacts']:9d} {r['n_elk1']:9d} "
              f"{r['motif']:6d}/11 {r['n_med23']:10d} "
              f"{len(r['pocket']):7d}/7 {r['chance']:9.2f}"
              + (f"  {r['pocket']}" if r["pocket"] else ""))

    obs = sum(len(r["pocket"]) for r in rows) / len(rows)
    exp = sum(r["chance"] for r in rows) / len(rows)
    print(f"\npocket residues recovered: {obs:.2f} observed, {exp:.2f} expected "
          f"at random  ({obs / exp:.2f}x)" if exp else "")

    consensus = build_consensus(samples, cfg)
    print(f"\nconsensus  : converged={consensus.converged} "
          f"support={consensus.ensemble_support:.2f}")
    if consensus.target_segment:
        seg = consensus.target_segment
        print(f"segment    : predicted {seg.start}-{seg.end} -> ELK1 "
              f"{seg.start + TAD_START - 1}-{seg.end + TAD_START - 1} "
              f"({seg.length} residues)")
    for b in consensus.blockers:
        print(f"  BLOCKER  : {b}")

    record = OUT / "boltz_5x.json"
    if not record.exists():
        record = next(iter(sorted(OUT.glob("boltz_*x.json"))), None)
    if record and record.exists():
        payload = json.loads(record.read_text())
        mets = [s.get("metrics") or {}
                for d in payload.get("dispatches", [])
                for s in (d.get("structures") or [])]
        print()
        for key in ("iptm", "ptm", "complex_plddt", "complex_iplddt",
                    "complex_pde", "complex_ipde"):
            vals = [m[key] for m in mets if isinstance(m.get(key), (int, float))]
            if vals:
                span = (f"   [{min(vals):.3f} - {max(vals):.3f}]"
                        if len(vals) > 1 else "")
                print(f"  {key:16s} {sum(vals) / len(vals):.3f}{span}")

    print("\nA predicted interface is a prediction. Recovering the published "
          "site would show\nthe method reproduces a known answer, not that any "
          "novel prediction is correct.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
