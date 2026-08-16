#!/usr/bin/env python
"""The ELK1 positive control: does an unbiased Boltz ensemble find the known site?

    python scripts/elk1_control.py --samples 1     # benchmark one first
    python scripts/elk1_control.py --samples 5     # the real control

Boltz is the only structural predictor in the critical path, so before any novel
interface is believed we ask whether it recovers one we already know. MED23 is
given as sequence, the ELK1 transactivation domain is given as sequence, and
**the experimentally observed binding residues are not disclosed** — no contact
constraints, no complex template. The model proposes the location; the consensus
module then asks whether independent samples agree, and whether what they agree
on is the published site.

Ground truth (Monte et al. 2025, doi:10.1038/s41467-025-59014-8, PDB 9F6Y):
ELK1 374-384 contacts a MED23 cavity lined by 339, 343, 379, 382, 383, 533, 537.

Passing means the dominant cluster lands on that neighbourhood. Failing does not
invalidate the pipeline, but it does mean every novel prediction downstream
carries an explicit extra limitation, and the artifact records which happened.

Costs GPU time on Modal. Run with --samples 1 first and read the timing before
committing to an ensemble.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
SEQS = ROOT / "downloads" / "seqs.json"
OUT = ROOT / "runs" / "elk1_control"

# ELK1 transactivation domain with modest flanking (spec section 4). The
# published motif sits at 374-384, well inside this window, but nothing tells
# the model that.
TAD_START, TAD_END = 310, 401

PUBLISHED_MED23_POCKET = {339, 343, 379, 382, 383, 533, 537}
PUBLISHED_ELK1_MOTIF = set(range(374, 385))


def load_sequences() -> tuple[str, str]:
    if not SEQS.exists():
        raise SystemExit(f"missing {SEQS}; run the sequence fetch first")
    d = json.loads(SEQS.read_text())
    return d["ELK1"], d["MED23"]


def build_input(elk1: str, med23: str, samples: int, seed: int):
    from proto_tools.tools.structure_prediction.boltz2.boltz2 import (
        Boltz2Config, Boltz2Input)
    tad = elk1[TAD_START - 1:TAD_END]
    complexes = [{
        "chains": [
            {"id": "A", "sequence": med23, "entity_type": "protein"},
            {"id": "B", "sequence": tad, "entity_type": "protein"},
        ],
    }]
    cfg = Boltz2Config(diffusion_samples=samples, include_pae_matrix=True, seed=seed)
    return Boltz2Input(complexes=complexes), cfg, tad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--samples", type=int, default=1,
                    help="diffusion samples; benchmark with 1 before running 5")
    ap.add_argument("--seed", type=int, default=20260815)
    ap.add_argument("--dry-run", action="store_true",
                    help="build and describe the request without dispatching")
    a = ap.parse_args()

    elk1, med23 = load_sequences()
    inp, cfg, tad = build_input(elk1, med23, a.samples, a.seed)

    print(f"MED23              {len(med23)} aa (chain A)")
    print(f"ELK1 TAD {TAD_START}-{TAD_END}  {len(tad)} aa (chain B)")
    print(f"  contains the published motif at 374-384: "
          f"{elk1[373:384]!r} -> offset {374 - TAD_START} in the window")
    print(f"diffusion samples  {a.samples}   seed {a.seed}   PAE matrix on")
    print("no contact constraints, no complex template: the model proposes the site")

    if a.dry_run:
        print("\n--dry-run: nothing dispatched")
        return 0

    # dispatch_to_modal, not run_boltz2: the local path builds a CUDA env that
    # has no macOS-ARM wheels, and this is GPU work regardless. The app is
    # deployed by `proto-tools deploy --apps boltz2`.
    from proto_tools.modal import dispatch_to_modal
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    print("\ndispatching to Modal ...")
    try:
        result = dispatch_to_modal("boltz2-prediction", inp, cfg)
    except Exception as e:
        print(f"\nFAILED after {time.time()-t0:.0f}s: {type(e).__name__}: {e}")
        return 1
    dt = time.time() - t0
    print(f"returned in {dt:.0f}s ({dt/max(a.samples,1):.0f}s per sample)")

    payload = result.model_dump(mode="json")

    # Write structures separately. Truncating one giant JSON corrupts it, and a
    # corrupt artifact is worse than a large one.
    structs = payload.get("structures") or []
    for i, st in enumerate(structs):
        for field in ("structure", "cif", "pdb", "content"):
            blob = st.get(field)
            if isinstance(blob, str) and len(blob) > 2000:
                path = OUT / f"sample_{i}.cif"
                path.write_text(blob)
                st[field] = f"<written to {path.name}>"
                break
        pae = st.get("pae")
        if pae is not None:
            (OUT / f"sample_{i}_pae.json").write_text(json.dumps(pae))
            st["pae"] = f"<written to sample_{i}_pae.json>"
    out_path = OUT / f"boltz_{a.samples}x.json"
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"wrote {out_path} plus {len(structs)} structure file(s)")

    # Surface the interface-specific numbers, not the global ones (spec section 8).
    for key in ("iptm", "pair_chains_iptm", "protein_iptm", "complex_plddt",
                "complex_iplddt", "complex_pde", "complex_ipde"):
        for blob in [payload, *(payload.get("results") or [])]:
            if isinstance(blob, dict) and key in blob:
                print(f"  {key:20s} {blob[key]}")
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
