# Boltz on Modal — handoff to Amir

Everything downstream of Boltz is built, tested and calibrated. The one thing
blocking it is GPU: this workspace (`vrajpatel00222`) refuses H100 without a
payment method on file.

```
╭─ Error ───────────────────────────────────────────────╮
│ Please add a payment method to use H100 GPU functions. │
╰───────────────────────────────────────────────────────╯
```

The Modal **image built fine**. Only the GPU allocation is refused. If your
workspace has credits, you can run this in about ten minutes.

## Run these three

```bash
cd reAGENT-2026 && git pull && source ./activate.sh

# 1. your Modal workspace, then deploy the one app
modal profile current                 # confirm which workspace you are in
proto-tools deploy --apps boltz2 --test

# 2. one sample first, to read the timing before committing to an ensemble
python scripts/elk1_control.py --samples 1

# 3. if the timing is sane, the real control
python scripts/elk1_control.py --samples 5
```

`proto-env` already exists in my workspace; in yours, `proto-tools deploy`
creates it, or `modal environment create proto-env`.

## What the control is

`scripts/elk1_control.py` — spec §6. MED23 (989 aa) and the ELK1 transactivation
domain (310–401, 92 aa) go in as **sequence only**. No contact constraints, no
complex template. The experimentally observed binding residues are deliberately
withheld, so the model has to propose the location itself.

Ground truth (Monté et al. 2025, PDB 9F6Y): ELK1 **374–384** contacts a MED23
cavity lined by **339, 343, 379, 382, 383, 533, 537**.

The published motif sits at offset 64 inside the window we submit. Nothing in
the request points at it.

Sequences are fetched live from UniProt through `proto_tools` rather than
pasted, and that fetch independently confirms the numbering: residues 374–384 of
the retrieved P19419 read `PSIHFWSTLSP`, exactly the published motif.

## What to do with the output

Paste the metrics back, or push `runs/elk1_control/`. What matters is the
**interface** confidence, not the global score — MED23 is a large folded protein
and can make `complex_plddt` look good while the interface is uncertain:

```
pair_chains_iptm   protein_iptm   complex_iplddt   complex_ipde
```

Then the consensus module takes over. It is already calibrated against the
experimental structure — run on the deposited 9F6Y coordinates it recovers all
seven published pocket residues and confines every ELK1 contact to the published
motif, including the pSer383 that makes the interaction phosphorylation-dependent.

```python
from reagent_workflow.interface import build_consensus
# -> ensemble support, smallest confident TF segment, partner contact residues
```

**Pass** = the dominant cluster lands on that neighbourhood. **Fail** does not
invalidate the pipeline, but every novel prediction downstream then carries an
explicit extra limitation, and the artifact records which happened.

## What is already done and does not need redoing

- **Interface consensus** (`interface.py`) — calibrated on 9F6Y, hardened
  against 12 defects an independent tester found, including a register-shifted
  ladder that the first version called "12/12 converged".
- **Search-box definition** (`site.py`) — on the **free** receptor 9F76, not the
  TF-occupied complex. Produces a 23.4 × 24.1 × 26.5 Å box from real coordinates
  that encloses every heavy atom of all seven pocket residues.
- **Chemistry arm** (`chemistry.py`) — RDKit cleanup, descriptors, the
  amphiphilicity proxy, and the three-arm library that keeps the enrichment
  hypothesis refutable.
- **Vina** — compiles natively, CPU, `ready: True`. No GPU needed.
- **Config** (`discovery_config.py`) — every §37 threshold, frozen.
- **Full cancer run** (`run_cancer.py`) — 12 lineages, 402 papers, 0 blocked.
- **241 tests green**, determinism harness passes.

## Two traps that cost me time

**The local Boltz path is dead on Apple silicon.** `run_boltz2` builds a local
env first and needs `cuequivariance-ops-cu12`, which has no macOS-ARM wheels.
Modal is not a convenience here, it is the only route.

**`BENCHFLOW_PYTHON` turns 4 skips into 4 failures** unless you have the real
BenchFlow. This repo has a `benchflow/` directory that Python imports as an
empty namespace package, so a bare `import benchflow` succeeds while every
submodule is missing. The test guard now probes `benchflow.traces.parsers`; if
you have the real interpreter, point `BENCHFLOW_PYTHON` at it and those four
run.

## After the control

If it passes, the next run is the hero candidate rather than a control. My pick
is **FLI1 in Bone** (selectivity 0.71): Ewing sarcoma is driven by EWSR1–FLI1,
and that fusion's activation domain is the EWSR1 prion-like region — a known
coactivator-recruiting surface, so it has the best odds in the whole set of a
documented contact. That gap is what all twelve lineages currently stop at.

```bash
python scripts/run_cancer.py Bone     # see the candidate first
```
