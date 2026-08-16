#!/usr/bin/env python
"""Where does this transcription factor touch MED23? Ask an ensemble, not a model.

    python scripts/predict_med23_interface.py POU2F3 --accession Q9UKI9
    python scripts/predict_med23_interface.py POU2F3 --accession Q9UKI9 --dispatch
    python scripts/predict_med23_interface.py POU2F3 --accession Q9UKI9 --score

The interface renders MED23 for every candidate the pipeline proposes, and until
now it had nothing candidate-specific to highlight on it: the only receptor-side
residues on file are the ELK1 cavity from PDB 9F6Y, which is where ELK1 binds and
is not a site for anyone else. Painting those in a candidate's colour would be
the exact substitution this project exists to avoid, so the panel says "not
predicted" instead. This script is how that stops being the answer.

It co-folds the full-length TF with full-length MED23 five times under different
seeds, then asks `interface.build_consensus` whether the samples agree. What
reaches the interface is the consensus, not a single prediction — one Boltz
sample of a 1,700-residue complex will always produce *an* interface, and a
number that cannot disagree with itself is not evidence. Convergence across
independent seeds is a statement about the model's self-consistency and nothing
more; it is not binding, and every artifact written here says so.

Three things are deliberate:

**The accession is required and checked.** A gene symbol does not identify a
protein. MED23 is pinned to Q9ULK4 (O75448 is MED24, an adjacent subunit of the
same module and of plausible length — it stood in for MED23 through an entire
GPU run once), and the TF accession is verified against the gene name UniProt
reports for it before anything is dispatched.

**Real alignments, not single-sequence mode.** `run_boltz2` consumes only MSAs
the caller supplies, so an input without them silently predicts in
single-sequence mode, where Boltz-2 is much weaker. The ELK1 and POU2F3 controls
both showed the difference; this uses the same per-chain ColabFold search they do.

**Dispatch is opt-in.** Each sample is GPU time on Modal, and MED23 is 1,368
residues — the complex here is roughly three times the length of the POU2F3
control. Without `--dispatch` the script prints exactly what it would send and
exits, so the cost is a decision someone makes rather than a side effect of
running the file.
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

from reagent_workflow.interface import (ConsensusConfig, build_consensus,  # noqa: E402
                                        contacts_between, parse_mmcif)
from reagent_workflow.site import CURATED_POCKETS                          # noqa: E402

MED23 = CURATED_POCKETS["MED23"]
MED23_ACC = MED23["uniprot"]          # Q9ULK4, 1368 aa. NOT O75448 (MED24).
SEEDS = (20260815, 20260816, 20260817, 20260818, 20260819)
OUT = ROOT / "runs" / "interfaces"


def workdir(gene: str) -> pathlib.Path:
    return OUT / f"{gene.upper()}_MED23"


def score(gene: str, cfg: ConsensusConfig) -> dict | None:
    """Samples on disk → one consensus, or an explicit refusal.

    Chain A is the transcription factor and chain B is MED23, so the residues
    that reach the interface are `partner_contact_residues` — receptor-side by
    construction. Reading the target side by mistake is how a run ends up
    boxing MED23 around the TF's own numbering.
    """
    paths = sorted(workdir(gene).glob("sample_*.json"))
    if not paths:
        return None

    import tempfile
    samples, metrics = [], []
    for p in paths:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not d.get("structures"):
            continue
        st = d["structures"][0]
        with tempfile.NamedTemporaryFile("w", suffix=".cif", delete=False) as f:
            f.write(st["structure"])
            tmp = f.name
        samples.append(contacts_between(parse_mmcif(tmp), target_chain="A",
                                        partner_chain="B",
                                        cutoff=cfg.contact_cutoff_angstrom,
                                        sample=p.stem))
        metrics.append({"sample": p.name, **{k: v for k, v in st["metrics"].items()
                                             if k in ("iptm", "ptm", "plddt")}})

    consensus = build_consensus(samples, cfg)
    resolved = sorted(set(consensus.partner_contact_residues))
    record = {
        "target": gene.upper(),
        "partner": "MED23",
        "partner_uniprot": MED23_ACC,
        "interpretation": "computational_prediction",
        "samples": metrics,
        "consensus": consensus.model_dump(mode="json"),
        # What the interface highlights. Empty whenever the consensus was
        # rejected: a refusal must not ship residues a viewer can paint.
        "partner_contact_residues": resolved if consensus.converged else [],
        "overlaps_elk1_cavity": sorted(set(resolved) & set(MED23["residues"])),
        "limitations": [
            "Computational structural hypothesis, not experimental evidence.",
            "Ensemble convergence is model self-consistency, not binding.",
            f"Residue numbering is MED23 {MED23_ACC}; residues unresolved in the "
            "free structure 9F76 cannot be drawn.",
        ],
    }
    out = workdir(gene) / "consensus.json"
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")

    print(f"\n{len(samples)} sample(s) -> {out.relative_to(ROOT)}")
    print(f"  support   {consensus.ensemble_support:.0%} "
          f"({consensus.dominant_cluster_samples}/{consensus.total_samples})")
    if consensus.blockers:
        for b in consensus.blockers:
            print(f"  REFUSED   {b}")
        print("  no residues written: a rejected consensus highlights nothing")
    else:
        print(f"  MED23     {resolved}")
        print(f"  vs ELK1   {record['overlaps_elk1_cavity']} shared with the "
              f"cavity in 9F6Y")
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("gene", help="transcription factor gene symbol, e.g. POU2F3")
    ap.add_argument("--accession", help="its UniProt accession; required to dispatch")
    ap.add_argument("--dispatch", action="store_true",
                    help="run the ensemble on Modal GPU (paid, minutes per sample)")
    ap.add_argument("--score", action="store_true",
                    help="build the consensus from samples already on disk")
    ap.add_argument("--samples", type=int, default=len(SEEDS))
    a = ap.parse_args()
    gene = a.gene.upper()
    cfg = ConsensusConfig()

    if a.score and not a.dispatch:
        return 0 if score(gene, cfg) else 2

    from calibrate_structure import fetch_sequence, verify_accession

    if not a.accession:
        print("REFUSED  --accession is required: a gene symbol does not identify "
              "a protein, and the wrong accession models the wrong protein "
              "without anything downstream being able to tell", file=sys.stderr)
        return 2

    # MED23 is checked against 9F76 as well as its gene name — it is supposed to
    # be a chain of the structure this project renders, and that is a second,
    # independent way to catch the adjacent-subunit substitution. The TF gets
    # the gene-name check only: it has no reference structure here, and
    # demanding one would refuse every candidate the pipeline can propose.
    seqs, problems = {}, []
    for acc, name, pdb in ((a.accession, gene, None),
                           (MED23_ACC, "MED23", MED23["free_receptor_pdb"])):
        seq, url, pdbs, genes = fetch_sequence(acc, name)
        seqs[name] = seq
        if pdb:
            problems.extend(verify_accession(acc, name, genes, pdbs, pdb_id=pdb))
        elif name.upper() not in [g.upper() for g in (genes or [])]:
            problems.append(f"{acc} has gene names {genes or '[]'}, which do not "
                            f"include {name}: this is not the protein asked for.")
        print(f"{name:8s} {acc}  {len(seq)} aa  {url}")
        print(f"         gene names {genes}")
    if problems:
        for p in problems:
            print(f"REFUSED  {p}", file=sys.stderr)
        return 2

    tf, med23 = seqs[gene], seqs["MED23"]
    complexes = [{"chains": [
        {"id": "A", "sequence": tf, "entity_type": "protein"},
        {"id": "B", "sequence": med23, "entity_type": "protein"},
    ]}]
    print(f"\ncomplex  chain A {gene} {len(tf)} aa · chain B MED23 {len(med23)} aa "
          f"= {len(tf) + len(med23)} residues")
    print(f"ensemble {a.samples} seeds {SEEDS[:a.samples]}, full length, no template, "
          "no constraint: the model proposes the site")

    if not a.dispatch:
        print("\nplan only — nothing was dispatched and nothing was paid for.")
        print("Add --dispatch to run it, and expect GPU minutes per sample at this "
              "length. Ask before spending: see team/CHECKPOINTS.md.")
        return 0

    from pou2f3_control import build_msas
    from proto_tools.modal.client import dispatch_to_modal
    from proto_tools.tools.structure_prediction.boltz2.boltz2 import (Boltz2Config,
                                                                     Boltz2Input)

    wd = workdir(gene)
    wd.mkdir(parents=True, exist_ok=True)
    msas, depth, _ = build_msas(tf, med23, wd / "msa")
    print(f"MSA      chain A {depth[0]} sequences, chain B {depth[1]} sequences "
          "(ColabFold server, per chain, unpaired)")

    t0, written = time.time(), []
    for i, seed in enumerate(SEEDS[:a.samples], 1):
        # One dispatch per member: `diffusion_samples` returns only the best
        # sample by confidence, which cannot say whether independent samples
        # agree, and agreement is the entire question.
        t1 = time.time()
        try:
            result = dispatch_to_modal(
                "boltz2-prediction",
                Boltz2Input(complexes=complexes, msas=[msas]),
                Boltz2Config(diffusion_samples=1, include_pae_matrix=True,
                             seed=seed, device="modal"))
        except Exception as e:
            print(f"  seed {seed}  FAILED  {type(e).__name__}: {str(e)[:120]}")
            continue
        payload = result.model_dump(mode="json") if hasattr(result, "model_dump") \
            else result
        p = wd / f"sample_{i}_seed{seed}.json"
        p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        written.append(p)
        print(f"  seed {seed}  {time.time() - t1:.0f}s  -> {p.name}")

    print(f"\n{len(written)}/{a.samples} samples in {time.time() - t0:.0f}s")
    if not written:
        return 1
    return 0 if score(gene, cfg) else 1


if __name__ == "__main__":
    raise SystemExit(main())
