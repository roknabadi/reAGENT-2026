#!/usr/bin/env python
"""Orthogonal structural control: POU2F3 - POU2AF2 (OCA-T1), from sequence only.

    python scripts/pou2f3_control.py                  # ground truth only, free
    python scripts/pou2f3_control.py --dispatch       # + 5 Boltz samples on H100

Why a second control, when ELK1-MED23 already exists: that one cannot currently
be scored. Its interaction is phosphorylation-dependent, and pSer383 cannot be
supplied through Proto — `Chain.modifications` is accepted, validated, and then
dropped by `complex_to_yaml` before the request leaves for Modal (see
tests/test_boltz_modifications.py). So ELK1's ipTM 0.31 is confounded: it is
not evidence about Boltz, and tuning anything on the strength of it would be
fitting to an artefact.

This complex has none of that problem, and differs from ELK1-MED23 on every
axis that matters:

    ELK1 - MED23                    POU2F3 - POU2AF2
    cryo-EM, 3.0 A                  X-ray, 1.7 A
    phosphorylation-dependent       no modification involved
    989 aa partner                  140 aa partner
    PDB 9F6Y                        PDB 9PFP

Ground truth comes from 9PFP, which contains two independent copies of the
complex in the asymmetric unit. Running the consensus contact machinery on both
gives Jaccard 1.00 on each side — the interface is not a modelling choice, and
the machinery reproduces it exactly across independent copies. That is
established here before anything is predicted, and it is the number the
prediction is scored against.

The prediction itself is deliberately uninformed: both chains go in full-length
from UniProt, no contact constraints, no template, no hint that residues
186-192 or 234-242 matter. Five independent dispatches with five seeds, because
`diffusion_samples` returns only the best sample by confidence and cannot
answer whether independent samples agree.

Thresholds are whatever `ConsensusConfig` already says. Nothing here is tuned
after seeing a result; if the control fails, it fails.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from reagent_workflow.interface import (ConsensusConfig, contacts_between,  # noqa: E402
                                        parse_mmcif)

STRUCTURE = ROOT / "downloads" / "9PFP.cif"
OUT = ROOT / "runs" / "pou2f3_control"

# UniProt accessions, resolved live rather than vendored.
POU2F3_ACC, POU2AF2_ACC = "Q9UKI9", "A6NFH5"

# 9PFP asymmetric unit: two independent copies of the complex, plus DNA.
#   A / E   POU2F3 POU domains (186-340)      the TF
#   B / F   OCA-T1 / POU2AF2 (11-32)          the coactivator
#   C,D / G,H   duplex DNA
COPIES = (("A", "B"), ("E", "F"))

SEEDS = (20260815, 20260816, 20260817, 20260818, 20260819)


def ground_truth(cfg: ConsensusConfig) -> dict:
    """The experimental interface, from both copies, at the standard cutoff."""
    atoms = parse_mmcif(STRUCTURE)
    per_copy = []
    for tf_chain, co_chain in COPIES:
        s = contacts_between(atoms, target_chain=tf_chain, partner_chain=co_chain,
                             cutoff=cfg.contact_cutoff_angstrom,
                             sample=f"{tf_chain}{co_chain}")
        per_copy.append({"copy": f"{tf_chain}/{co_chain}",
                         "pou2f3": sorted(s.target_residues),
                         "pou2af2": sorted(s.partner_residues),
                         "n_contacts": len(s.contacts)})

    a, b = per_copy
    def jac(x, y):
        x, y = set(x), set(y)
        return round(len(x & y) / len(x | y), 3) if (x | y) else 0.0

    return {
        "pdb": "9PFP", "method": "X-ray diffraction", "resolution_angstrom": 1.7,
        "title": "POU2F3 POU domains bound to coactivator OCA-T1 and DNA",
        "cutoff_angstrom": cfg.contact_cutoff_angstrom,
        "copies": per_copy,
        "reproducibility": {
            "pou2f3_jaccard": jac(a["pou2f3"], b["pou2f3"]),
            "pou2af2_jaccard": jac(a["pou2af2"], b["pou2af2"]),
        },
        # Consensus across copies: residues seen in BOTH.
        "pou2f3_interface": sorted(set(a["pou2f3"]) & set(b["pou2f3"])),
        "pou2af2_interface": sorted(set(a["pou2af2"]) & set(b["pou2af2"])),
        "limitations": [
            "The deposited complex contains duplex DNA, which the "
            "sequence-only prediction omits. If DNA is required to organise "
            "the POU domains, its absence is a candidate explanation for a "
            "failed prediction and must be considered before blaming Boltz.",
            "Only the POU domains (186-340) are resolved in the crystal; the "
            "prediction submits full-length POU2F3.",
            "Two copies in one asymmetric unit are not two independent "
            "experiments. They share a crystal form.",
        ],
    }


def _read_a3m(path: pathlib.Path) -> list[tuple[str, str]]:
    """(id, aligned sequence) pairs. a3m lowercase columns are insertions
    relative to the query and are dropped, leaving equal-length rows."""
    import re
    out, name, buf = [], None, []
    for line in path.read_text().splitlines():
        if line.startswith(">"):
            if name is not None:
                out.append((name, re.sub(r"[a-z]", "", "".join(buf))))
            name, buf = line[1:].split()[0], []
        elif line.strip():
            buf.append(line.strip())
    if name is not None:
        out.append((name, re.sub(r"[a-z]", "", "".join(buf))))
    return out


def build_msas(pou2f3: str, pou2af2: str, workdir: pathlib.Path):
    """Homology alignments for both chains, from the public ColabFold server.

    This is not an optimisation. `run_boltz2` only ever consumes MSAs the caller
    supplies -- `use_msa=True` in the config does NOT make it run a search on
    this path -- so a `Boltz2Input` without `msas` produces `msa: empty` for
    every chain and the prediction runs in single-sequence mode. Boltz-2 is
    substantially weaker there, so a control run that way measures the wrong
    thing while looking like a fair test.

    The server returns both chains' alignments concatenated in one a3m; rows are
    assigned to a chain by their alignment length, which is the query length.
    """
    from proto_tools.entities.msa import MSA
    from proto_tools.tools.sequence_alignment.mmseqs2.msa_server import \
        run_remote_msa_search
    from proto_tools.tools.structure_prediction.shared_data_models import ComplexMSAs

    workdir.mkdir(parents=True, exist_ok=True)
    res = run_remote_msa_search([pou2f3, pou2af2], workdir / "pou2f3",
                                use_pairing=True, timeout=1800)
    a3m = next(pathlib.Path(res).glob("*.a3m"))
    rows = _read_a3m(a3m)

    per_chain, counts = {}, {}
    for idx, query in ((0, pou2f3), (1, pou2af2)):
        hits = [(i, s) for i, s in rows if len(s) == len(query)]
        if not hits:
            raise RuntimeError(f"no alignment rows of length {len(query)} in {a3m}")
        # Query first: `extract_msa_sequences` and Boltz both treat row 0 as the
        # sequence being predicted.
        ordered = sorted(hits, key=lambda kv: kv[1] != query)
        per_chain[idx] = MSA(aligned_sequences=[s for _, s in ordered],
                             sequence_ids=[i for i, _ in ordered])
        counts[idx] = len(ordered)

    # `paired=False`. A paired MSA asserts that row i of chain A and row i of
    # chain B come from the same organism, which is a real signal about whether
    # two proteins co-evolve -- and the two blocks here come back with 1493 and
    # 1492 rows, so that correspondence does not hold. Padding or truncating to
    # equal length would manufacture the pairing rather than find it, feeding
    # the model a co-evolution claim nothing supports. Unpaired alignments give
    # the same depth without the invented signal.
    return ComplexMSAs(per_chain=per_chain, paired=False), counts, str(a3m)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dispatch", action="store_true",
                    help="run 5 Boltz samples on Modal H100 (GPU time, minutes)")
    ap.add_argument("--samples", type=int, default=len(SEEDS))
    ap.add_argument("--no-msa", action="store_true",
                    help="single-sequence mode; records what that costs")
    ap.add_argument("--tag", default="", help="suffix for the output filenames")
    a = ap.parse_args()

    if not STRUCTURE.exists():
        print(f"missing {STRUCTURE}; fetch it with\n"
              f"  curl -sL -o {STRUCTURE} https://files.rcsb.org/download/9PFP.cif",
              file=sys.stderr)
        return 2

    cfg = ConsensusConfig()
    gt = ground_truth(cfg)

    print(f"ground truth   PDB {gt['pdb']}  {gt['method']} {gt['resolution_angstrom']} A")
    print(f"               {gt['title']}")
    for c in gt["copies"]:
        print(f"  copy {c['copy']}   POU2F3 {c['pou2f3']}")
        print(f"              POU2AF2 {c['pou2af2']}   ({c['n_contacts']} contacts)")
    print(f"  reproducibility  POU2F3 Jaccard "
          f"{gt['reproducibility']['pou2f3_jaccard']}, POU2AF2 Jaccard "
          f"{gt['reproducibility']['pou2af2_jaccard']}  "
          f"(cutoff {gt['cutoff_angstrom']} A, config unchanged)")
    print(f"  interface        POU2F3 {gt['pou2f3_interface']}")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ground_truth.json").write_text(json.dumps(gt, indent=2))
    print(f"\nwrote {OUT / 'ground_truth.json'}")

    if not a.dispatch:
        print("\nNo prediction run. Pass --dispatch to spend GPU time on "
              f"{a.samples} Boltz samples.")
        return 0

    # ── prediction: full-length, uninformed, one dispatch per seed ─────────
    from calibrate_structure import fetch_sequence
    from proto_tools.modal.client import dispatch_to_modal
    from proto_tools.tools.structure_prediction.boltz2.boltz2 import (Boltz2Config,
                                                                     Boltz2Input)

    pou2f3, url1, _ = fetch_sequence(POU2F3_ACC, "POU2F3")
    pou2af2, url2, _ = fetch_sequence(POU2AF2_ACC, "POU2AF2")
    print(f"\nPOU2F3   {POU2F3_ACC}  {len(pou2f3)} aa (chain A)  {url1}")
    print(f"POU2AF2  {POU2AF2_ACC}  {len(pou2af2)} aa (chain B)  {url2}")
    print("full length, no constraints, no template: the model proposes the site")

    complexes = [{"chains": [
        {"id": "A", "sequence": pou2f3, "entity_type": "protein"},
        {"id": "B", "sequence": pou2af2, "entity_type": "protein"},
    ]}]

    msas, depth, a3m_path = None, {}, None
    if a.no_msa:
        print("MSA        none — single-sequence mode (a weaker model; recorded)")
    else:
        msas, depth, a3m_path = build_msas(pou2f3, pou2af2, OUT / "msa")
        print(f"MSA        chain A {depth[0]} sequences, chain B {depth[1]} "
              f"sequences (ColabFold server, paired)")

    t0 = time.time()
    written = []
    for i, seed in enumerate(SEEDS[:a.samples], 1):
        # One dispatch per ensemble member. `diffusion_samples` returns only the
        # best sample by confidence, which cannot answer whether independent
        # samples agree -- and agreement is the entire question.
        cfgb = Boltz2Config(diffusion_samples=1, include_pae_matrix=True,
                            seed=seed, device="modal")
        t1 = time.time()
        try:
            result = dispatch_to_modal(
                "boltz2-prediction",
                Boltz2Input(complexes=complexes,
                            msas=[msas] if msas is not None else None),
                cfgb)
        except Exception as e:
            print(f"  seed {seed}  FAILED  {type(e).__name__}: {str(e)[:120]}")
            continue
        payload = result.model_dump(mode="json") if hasattr(result, "model_dump") \
            else result
        p = OUT / f"sample{a.tag}_{i}_seed{seed}.json"
        p.write_text(json.dumps(payload, indent=2))
        written.append(str(p))
        print(f"  seed {seed}  {time.time() - t1:.0f}s  -> {p.name}")

    print(f"\n{len(written)}/{a.samples} samples in {time.time() - t0:.0f}s")
    print("Next: run the consensus module over these and compare against "
          "ground_truth.json. Thresholds stay as configured.")
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
