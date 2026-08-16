# ELK1–MED23 positive control: result

Run 2026-08-15 by Amir on Modal (workspace `amir-roknabadi`, `proto-env`), per
`HANDOFF_BOLTZ_AMIR.md`. Boltz2 deployed cleanly; smoke tests 2/2
(prediction 47.7s, affinity 114.0s).

**The control fails.** Boltz2, given MED23 and the ELK1 transactivation domain
as sequence with no contact constraints and no template, does not recover the
published interface. It produces a diffuse, extended contact surface rather than
the compact 374–384 motif inserting into the MED23 cavity.

The consensus module refused, correctly, with its own reason:

> the reproducible target region spans 72 residues, beyond the 40-residue limit
> for a compact segment: this is an extended surface rather than a short motif

## The ensemble

Five independent dispatches, seeds 20260815–20260819, one structure each.
Ground truth: ELK1 **374–384** contacts a MED23 cavity lined by
**339, 343, 379, 382, 383, 533, 537** (Monté et al. 2025, PDB 9F6Y).

| sample | contacts | ELK1 residues | "in motif" | MED23 residues | in pocket |
|---|---|---|---|---|---|
| s0 | 212 | 73 | 9/11 | 100 | 1/7 (537) |
| s1 | 163 | 51 | 9/11 | 75 | 1/7 (537) |
| s2 | 159 | 59 | 10/11 | 77 | 1/7 (537) |
| s3 | 206 | 70 | 9/11 | 97 | 2/7 (343, 537) |
| s4 | 223 | 72 | 10/11 | 101 | 0/7 |

`ensemble_support` 0.60 (dominant cluster 3/5), `target_segment` None, refused.

## The 9/11 number is a trap

Motif recovery of 9–10 out of 11 looks like a pass and is not. Each sample puts
**51–73 of the 92** submitted ELK1 residues in contact with MED23 — most of the
domain is draped across the surface. When nearly everything touches something,
hitting 9 of any 11-residue window is arithmetic, not recognition.

The discriminating measure is the partner side, and it is at chance:

| | observed | expected if contacts were placed at random |
|---|---|---|
| pocket residues recovered (mean of 5) | **1.0** | **0.64** |

Each sample contacts 75–101 of MED23's 989 residues, so 7 × (≈90/989) ≈ 0.64
pocket hits are expected from nothing at all. The single recurring hit, 537, is
what chance predicts.

## Boltz2 says so itself

Mean across the five samples, with the range:

| global — looks fine | | interface — does not | |
|---|---|---|---|
| `ptm` | 0.783 [0.781–0.787] | **`iptm`** | **0.267 [0.245–0.284]** |
| `complex_plddt` | 0.738 [0.735–0.742] | `complex_iplddt` | 0.604 [0.582–0.625] |
| `complex_pde` | 1.23 [1.22–1.24] | `complex_ipde` | 14.45 [14.15–14.90] |

Exactly the gap the handoff predicted: MED23 is a large well-folded HEAT-repeat
solenoid, so the global scores reflect MED23 folding correctly, not the
interface being right. **No sample reaches half the 0.60 ipTM floor**, and the
spread is narrow — this is not one unlucky seed. **The model was not confident
and was not correct, and it reported the former.**

## What this changes

Per the handoff, a fail does not invalidate the pipeline, but it is now on the
record that:

- Every novel interface Boltz2 proposes downstream carries an explicit extra
  limitation: on the one case where the answer is known, the method did not
  recover it.
- `iptm` is the metric to gate on, not `complex_plddt` or `ptm`. A candidate
  passing on global confidence alone should be treated as unsupported.
- The consensus module works. It refused a result that a naive scorer reading
  only "9/11 motif residues" would have called a success — the same failure mode
  as the register-shifted ladder its adversarial tests already cover.

## What this does not establish

- **One pair, one tool, one condition.** This is ELK1–MED23 with MSA on, 5
  samples, at 4.5 Å heavy-atom cutoff. It does not generalise to all interfaces.
- A phosphorylated serine (pSer383) makes the real interaction
  phosphorylation-dependent. The submitted sequence is unmodified, so the model
  was asked for an interaction whose native form it cannot represent. That is a
  fair criticism of the control's design, not an excuse for the result — but it
  is the first thing to vary before concluding Boltz2 cannot do this class of
  interface.
- Larger ensembles, `step_scale` tuning for diversity, or submitting a shorter
  ELK1 window were not tried.

## Four bugs fixed to get here

All in `scripts/elk1_control.py`. The first two were found independently by Vraj
and by me and are already on `main`; the rest follow in PR #13.

1. **It never dispatched to Modal.** Printed "dispatching to Modal" then called
   `run_boltz2()`, which runs locally and dies on Apple silicon —
   `cuequivariance-ops-cu12` has no macOS-ARM wheel. Could not have reached
   Modal from any Mac.
2. **The artifact was unparseable.** `json.dumps(payload)[:20_000_000]` cut the
   file mid-token, and because the payload leads with a multi-megabyte mmCIF
   string, the metrics were exactly what got destroyed. Structures and PAE now
   write beside the record. PAE arrives nested under
   `structure["metrics"]["pae"]`, not at the top of the structure, so a
   top-level check misses it — that nesting left 183 MB of PAE inline on the
   first five-sample run.
3. **The ensemble was not an ensemble.** `diffusion_samples=5` returns "the best
   by confidence" — one structure. `build_consensus` was being handed a single
   sample no matter what was requested.
4. **The confidence summary printed nothing.** It looked for `iptm` and friends
   at the top of the payload; they live under `structure["metrics"]`. The run
   that produced this document printed no metrics at all — they were recovered
   from the artifact afterwards. It now reports the mean and range across the
   ensemble, which is what a control asking "do independent samples agree?"
   should show on its face.

Reproduce:

```bash
proto-tools deploy --apps boltz2 --test
python scripts/elk1_control.py --samples 5
```

Artifacts land in `runs/elk1_control/` (gitignored): `sample_<i>.cif` for each
seed, `sample_<i>_pae.json` beside it, and `boltz_5x.json` — the five dispatch
records with those two fields replaced by pointers, 7 KB and parseable.

`downloads/seqs.json` regenerates from UniProt via `proto_tools`; that fetch
reconfirms P19419 residues 374–384 read `PSIHFWSTLSP`.
