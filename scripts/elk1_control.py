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
    # `diffusion_samples` does NOT give an ensemble. proto_tools documents it as
    # "Independent structure samples per complex; the best by confidence is
    # returned" — 5 are generated internally and 1 comes back. The control asks
    # whether *independent samples agree*, which that parameter cannot answer.
    # Each ensemble member is therefore its own dispatch with its own seed, and
    # this builds the config for one of them.
    #
    # device="modal" marks the request remote; dispatch_to_modal swaps it for
    # the container's physical device. Without it the config carries "cuda",
    # which is meaningless on the caller's machine.
    cfg = Boltz2Config(diffusion_samples=1, include_pae_matrix=True,
                       seed=seed, device="modal")
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

    # `run_boltz2` runs LOCALLY: it builds ~/.proto/proto_tool_envs/boltz2_env
    # and needs boltz[cuda], whose cuequivariance-ops-cu12 has no macOS-ARM
    # wheel — the exact trap the handoff names. It printed "dispatching to
    # Modal" and then failed a local resolve. `dispatch_to_modal` is the remote
    # path; the logical device is translated to the container's physical one.
    from proto_tools.modal.client import dispatch_to_modal
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    # One dispatch per ensemble member, each with its own seed. Seeds are
    # derived from the base seed so the ensemble is reproducible.
    payloads = []
    for i in range(a.samples):
        seed_i = a.seed + i
        inp_i, cfg_i, _ = build_input(elk1, med23, 1, seed_i)
        print(f"\ndispatching sample {i + 1}/{a.samples} (seed {seed_i}) ...")
        try:
            result = dispatch_to_modal("boltz2-prediction", inp_i, cfg_i)
        except Exception as e:
            print(f"FAILED after {time.time()-t0:.0f}s: {type(e).__name__}: {e}")
            return 1
        payloads.append(result.model_dump(mode="json"))
    dt = time.time() - t0
    print(f"\nreturned in {dt:.0f}s ({dt/max(a.samples,1):.0f}s per sample)")

    payload = {"structures": [s for p in payloads
                              for s in (p.get("structures") or [])]}

    # Each predicted structure goes out as its own .cif, and the metrics as a
    # small JSON beside them. Writing one combined blob and slicing it to 20MB
    # produced a file truncated mid-token: unparseable, and the metrics — which
    # sit after the mmCIF text — were the part that got cut. `parse_mmcif` in
    # interface.py wants paths anyway, so this is also the shape the consensus
    # module consumes.
    structures = payload.get("structures") or []
    cif_paths = []
    for i, s in enumerate(structures):
        text = s.get("structure") if isinstance(s, dict) else None
        if not text:
            continue
        p = OUT / f"sample_{i}.cif"
        p.write_text(text)
        cif_paths.append(p)
        print(f"wrote {p}  ({len(text):,} chars)")

    def strip_structures(obj):
        """Metrics only — the coordinates already went to the .cif files."""
        if isinstance(obj, dict):
            return {k: strip_structures(v) for k, v in obj.items()
                    if k != "structure"}
        if isinstance(obj, list):
            return [strip_structures(v) for v in obj]
        return obj

    metrics_path = OUT / f"metrics_{a.samples}x.json"
    metrics_path.write_text(json.dumps(strip_structures(payload), indent=2))
    print(f"wrote {metrics_path}")

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
