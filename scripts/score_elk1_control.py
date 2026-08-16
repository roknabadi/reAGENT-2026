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

Both are counts of residues seen at *an* interface. The stronger question is
whether the motif and the pocket touch *each other*, so contacts with ELK1
374-384 on one side and a published pocket residue on the other are listed
individually, with distances. That is the claim the paper makes, and it is the
one a residue count can fake.

Across the 15 samples run on 2026-08-15, three recover 7/7 pocket residues and
only two of those put a motif residue against a pocket residue: one sample hits
the whole pocket with the wrong part of ELK1. That is why the pairs are listed
and not just counted. See team/FINDINGS_ELK1_CONTROL.md.
"""
from __future__ import annotations

import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from reagent_workflow.interface import (  # noqa: E402
    ConsensusConfig, build_consensus, cluster_interfaces, contacts_between,
    parse_mmcif)

OUT = ROOT / "runs" / "elk1_control"


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


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

    # Per-sample confidence, in dispatch order, so the table can ask whether
    # any reported number identifies the sample that is right.
    metrics: list[dict] = []
    record = OUT / f"boltz_{len(cifs)}x.json"
    if not record.exists():
        record = next(iter(sorted(OUT.glob("boltz_*x.json"))), record)
    if record.exists():
        payload = json.loads(record.read_text())
        metrics = [s.get("metrics") or {}
                   for d in payload.get("dispatches", [])
                   for s in (d.get("structures") or [])]

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
            "iptm": (metrics[len(rows)].get("iptm")
                     if len(rows) < len(metrics) else None),
            # Interface pLDDT: the pLDDT of the atoms actually in contact,
            # rather than of the whole complex. Reported because across 15
            # samples it ranked the recoveries 1st, 3rd and 4th while ipTM
            # ranked them 1st, 3rd and 8th. Neither is applied to anything.
            "plddt": _mean([c.target_plddt for c in iface.contacts if c.target_plddt]
                           + [c.partner_plddt for c in iface.contacts
                              if c.partner_plddt]),
            # The published claim itself: motif residue against pocket residue.
            "pairs": sorted(
                ((c.target_resi + TAD_START - 1, c.partner_resi, c.min_distance)
                 for c in iface.contacts
                 if c.target_resi + TAD_START - 1 in PUBLISHED_ELK1_MOTIF
                 and c.partner_resi in PUBLISHED_MED23_POCKET),
                key=lambda t: t[2]),
        })
        samples.append(iface)

    print(f"{len(cifs)} samples from {OUT}\n")
    print(f"{'sample':10s} {'ipTM':>6s} {'ifacepLDDT':>11s} {'contacts':>9s} "
          f"{'ELK1 res':>9s} {'in motif':>9s} {'MED23 res':>10s} {'in pocket':>10s} "
          f"{'by chance':>10s}")
    for r in rows:
        iptm = f"{r['iptm']:6.3f}" if isinstance(r["iptm"], (int, float)) else "     -"
        plddt = f"{r['plddt']:11.2f}" if r["plddt"] is not None else "          -"
        print(f"{r['sample']:10s} {iptm} {plddt} {r['contacts']:9d} {r['n_elk1']:9d} "
              f"{r['motif']:6d}/11 {r['n_med23']:10d} "
              f"{len(r['pocket']):7d}/7 {r['chance']:10.2f}"
              + (f"  {r['pocket']}" if r["pocket"] else ""))

    obs = sum(len(r["pocket"]) for r in rows) / len(rows)
    exp = sum(r["chance"] for r in rows) / len(rows)
    if exp:
        print(f"\npocket residues recovered: {obs:.2f} observed, {exp:.2f} "
              f"expected at random  ({obs / exp:.2f}x)")

    # A sample that recovers the whole pocket is worth a separate statement:
    # the mean above averages it away, and the mean is not what happened.
    for r in rows:
        if len(r["pocket"]) == len(PUBLISHED_MED23_POCKET):
            k, n = r["n_med23"], MED23_LENGTH
            p = math.prod((k - i) / (n - i)
                          for i in range(len(PUBLISHED_MED23_POCKET)))
            print(f"\n{r['sample']}: all {len(PUBLISHED_MED23_POCKET)} pocket "
                  f"residues, contacting {k} of {n} MED23 residues.")
            print(f"  probability of that by chance: {p:.1e} "
                  f"(1 in {1 / p:,.0f})")

    pairing = [r for r in rows if r["pairs"]]
    print(f"\nmotif-to-pocket contacts (ELK1 {min(PUBLISHED_ELK1_MOTIF)}-"
          f"{max(PUBLISHED_ELK1_MOTIF)} against the published pocket): "
          f"{len(pairing)}/{len(rows)} samples")
    for r in pairing:
        print(f"  {r['sample']}: {len(r['pairs'])} pairs  " + ", ".join(
            f"E{e}-M{m}:{d:.2f}A" for e, m, d in r["pairs"][:6]))

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

    # Whether the samples that recovered the site are the ones the ensemble
    # agrees on. If they are not, a majority rule reports the wrong answer while
    # holding the right one, which is worth saying out loud rather than leaving
    # to be noticed.
    clusters = cluster_interfaces(samples, cfg)
    hits = {r["sample"] for r in rows if len(r["pocket"]) == len(PUBLISHED_MED23_POCKET)}
    for i, c in enumerate(clusters):
        members = [s.sample for s in c]
        mark = "  <-- dominant" if i == 0 else ""
        mark += "  <-- recovers the pocket" if hits.intersection(members) else ""
        print(f"cluster {i} ({len(c)}/{len(samples)}): {members}{mark}")
    if hits and not hits.intersection(s.sample for s in clusters[0]):
        print("  NOTE     : the sample(s) that found the published site are NOT "
              "in the dominant\n             cluster. Majority vote over this "
              "ensemble discards the correct answer.")

    if metrics:
        print()
        for key in ("iptm", "ptm", "complex_plddt", "complex_iplddt",
                    "complex_pde", "complex_ipde"):
            vals = [m[key] for m in metrics if isinstance(m.get(key), (int, float))]
            if vals:
                span = (f"   [{min(vals):.3f} - {max(vals):.3f}]"
                        if len(vals) > 1 else "")
                print(f"  {key:16s} {sum(vals) / len(vals):.3f}{span}")

    print("\nA predicted interface is a prediction. Recovering the published "
          "site shows\nthe method reproduces a known answer, not that any novel "
          "prediction is correct.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
