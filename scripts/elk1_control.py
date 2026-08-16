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

The first five-sample run of this control is void: the partner accession named
MED24 (O75448), not MED23 (Q9ULK4), so it docked the ELK1 motif onto the wrong
subunit and scored the answer with MED23 residue numbering. Sequences are now
fetched by accession and verified before anything is dispatched. See
team/FINDINGS_ELK1_CONTROL.md.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
SEQS = ROOT / "downloads" / "seqs.json"
OUT = ROOT / "runs" / "elk1_control"

# Accessions and their checks come from the calibration script rather than being
# repeated here. Two copies of an accession is how one of them goes wrong.
from calibrate_structure import (  # noqa: E402
    ELK1, ELK1_GENE, MED23, MED23_GENE, STRUCTURE_PDB,
    fetch_sequence, verify_accession, verify_motif,
)

# ELK1 transactivation domain with modest flanking (spec section 4). The
# published motif sits at 374-384, well inside this window, but nothing tells
# the model that.
TAD_START, TAD_END = 310, 401

PUBLISHED_MED23_POCKET = {339, 343, 379, 382, 383, 533, 537}
PUBLISHED_ELK1_MOTIF = set(range(374, 385))


def load_sequences() -> tuple[str, str]:
    """Fetch both chains from UniProt and refuse to continue if either is wrong.

    This used to read a hand-made `downloads/seqs.json`, and that file carried
    MED24 under the key "MED23" through an entire GPU run: a file written once
    by hand cannot be checked against anything later. Fetching by accession and
    verifying gene symbol and structure cross-reference before dispatch makes
    the wrong protein a refusal rather than a result. The file is still written,
    now as a record of what was used rather than as the input.
    """
    elk1_seq, elk1_url, elk1_pdbs, elk1_genes = fetch_sequence(ELK1, "ELK1")
    med23_seq, med23_url, med23_pdbs, med23_genes = fetch_sequence(MED23, "MED23")

    problems = (verify_accession(ELK1, ELK1_GENE, elk1_genes, elk1_pdbs)
                + verify_accession(MED23, MED23_GENE, med23_genes, med23_pdbs)
                + verify_motif(elk1_seq))
    if problems:
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        raise SystemExit(
            "REFUSING to dispatch: the sequences are not the proteins this "
            "control is about.")

    SEQS.parent.mkdir(parents=True, exist_ok=True)
    SEQS.write_text(json.dumps({
        "ELK1": elk1_seq, "MED23": med23_seq,
        "_accessions": {"ELK1": ELK1, "MED23": MED23},
        "_genes": {"ELK1": elk1_genes, "MED23": med23_genes},
        "_verified_against": STRUCTURE_PDB,
        "_source_urls": {"ELK1": elk1_url, "MED23": med23_url},
    }, indent=2) + "\n")
    print(f"ELK1  {ELK1} {elk1_genes}  {len(elk1_seq)} aa")
    print(f"MED23 {MED23} {med23_genes}  {len(med23_seq)} aa   "
          f"(both are chains of {STRUCTURE_PDB})")
    return elk1_seq, med23_seq


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
    seeds = [a.seed + i for i in range(a.samples)]
    records = []
    for i, seed_i in enumerate(seeds):
        inp_i, cfg_i, _ = build_input(elk1, med23, 1, seed_i)
        print(f"\ndispatching sample {i + 1}/{a.samples} (seed {seed_i}) ...")
        try:
            result = dispatch_to_modal("boltz2-prediction", inp_i, cfg_i)
        except Exception as e:
            print(f"FAILED after {time.time()-t0:.0f}s: {type(e).__name__}: {e}")
            return 1
        records.append(result.model_dump(mode="json"))
    dt = time.time() - t0
    print(f"\nreturned in {dt:.0f}s ({dt/max(a.samples,1):.0f}s per sample)")

    # Every dispatch's record is kept whole — execution_time, warnings and
    # errors are per-dispatch — and the structures are indexed across them, so
    # sample i is what seed[i] produced.
    payload = {"seeds": seeds, "dispatches": records}
    structures = [s for r in records for s in (r.get("structures") or [])]

    # Write structures and PAE beside the record. Truncating one giant JSON
    # corrupts it, and a corrupt artifact is worse than a large one. Note that
    # PAE arrives nested under structure["metrics"], not at the top of the
    # structure, and it is what actually makes the file large: the first
    # 5-sample run left 183 MB of PAE inline because only the top level was
    # checked. `parse_mmcif` in interface.py takes paths, so the .cif files are
    # also the shape the consensus module consumes.
    for i, st in enumerate(structures):
        for field in ("structure", "cif", "pdb", "content"):
            blob = st.get(field)
            if isinstance(blob, str) and len(blob) > 2000:
                (OUT / f"sample_{i}.cif").write_text(blob)
                st[field] = f"<written to sample_{i}.cif>"
                print(f"wrote {OUT / f'sample_{i}.cif'}  ({len(blob):,} chars)")
                break
        for holder in (st, st.get("metrics")):
            if isinstance(holder, dict) and holder.get("pae") is not None:
                (OUT / f"sample_{i}_pae.json").write_text(
                    json.dumps(holder["pae"]))
                holder["pae"] = f"<written to sample_{i}_pae.json>"
    (OUT / f"boltz_{a.samples}x.json").write_text(json.dumps(payload, indent=2))
    print(f"wrote {OUT / f'boltz_{a.samples}x.json'}")

    # Surface the interface-specific numbers, not the global ones (spec section
    # 8). They live under structure["metrics"]; a top-level lookup matches
    # nothing and prints nothing. Report the spread too — a control that asks
    # whether independent samples agree should show whether they did.
    rows = [st.get("metrics") or {} for st in structures]
    for key in ("iptm", "protein_iptm", "ptm", "complex_plddt",
                "complex_iplddt", "complex_pde", "complex_ipde"):
        vals = [m[key] for m in rows if isinstance(m.get(key), (int, float))]
        if not vals:
            continue
        spread = f"   [{min(vals):.3f} - {max(vals):.3f}]" if len(vals) > 1 else ""
        print(f"  {key:20s} {sum(vals) / len(vals):.3f}{spread}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
