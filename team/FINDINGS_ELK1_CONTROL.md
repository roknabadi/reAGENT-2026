# ELK1–MED23 positive control

**Result, 2026-08-15, against real MED23: one sample in five recovers the
published site exactly. Our consensus rule discards it.**

Jump to [the result](#the-result-med23-q9ulk4-5-seeds). The retraction below
concerns the earlier run against the wrong protein and is kept because the
correction is the reason the result exists.

---

# The earlier run: RETRACTED, and why

**The first run of 2026-08-15 is void. It did not test what it claimed to test,
and the conclusion drawn from it — "the control fails" — is withdrawn.**

The control docked the ELK1 transactivation domain onto **MED24 (O75448, 989
aa)**, not MED23 (Q9ULK4, 1368 aa), and then scored the answer against MED23
residue numbering taken from PDB 9F6Y. Every number in the previous version of
this document is a measurement of the wrong complex judged by the wrong ruler.

Nothing about Boltz2's ability to recover this interface was established by that
run, in either direction. The run that did establish something is below.

## What was wrong

`scripts/calibrate_structure.py` carried `MED23 = "O75448"`. O75448 is
**MED24** — a different subunit of the same Mediator tail module. It is a real,
reviewed human protein of a plausible length that appears in Mediator complex
structures, so nothing downstream looked odd.

The file verified the ELK1 half against the primary paper line by line: the
motif sequence, the interface residues, the resolution, the Kd, each with a
pinned line number. It never checked the partner accession at all. Its own
`verify_motif` docstring says it exists to catch "the wrong region of the wrong
protein"; it checked the region and not the protein.

`downloads/seqs.json` was written once by hand from that constant, and
`scripts/elk1_control.py` then read the file rather than the accession, so
there was no later point at which the mistake could surface.

## What is not wrong

The contact detection is sound, and this was checked directly rather than
assumed. Running our own `parse_mmcif` + `contacts_between` at 4.5 Å over the
deposited 9F6Y coordinates:

| | our detector on 9F6Y | published |
|---|---|---|
| MED23 pocket residues | **339, 343, 379, 382, 383, 533, 537** (+13 more) | 339, 343, 379, 382, 383, 533, 537 |
| ELK1 residues at the interface | 375–383 | motif 374–384 |

**7/7.** Parsing, chain handling, residue numbering and the cutoff all recover
the published answer from the experimental structure. (9F6Y models only ELK1
374–384 — the rest of the TAD is unresolved — so 375–383 is very nearly
everything there is to find.) The ELK1 sequence was also correct throughout:
our P19419 slice matches 9F6Y's 308–401 construct exactly.

So the failure is confined to one input constant. It is not a bug in the
geometry, the consensus module, or the ensemble machinery.

## Why the void result still looked like a real one

This is the part worth carrying forward.

The MED24 run produced ipTM 0.267 [0.245–0.284] against `ptm` 0.783, five
samples agreeing closely, a diffuse 72-residue contact surface, and a consensus
module that refused with a specific and correct-sounding reason. It read as a
clean negative — a model that could not find a known site. Every one of those
numbers is consistent with "this complex does not exist", which is exactly what
was submitted.

A confident-looking negative is not self-evidently a negative about the thing
you meant. The earlier analysis was right that the "9/11 motif residues" figure
was a trap and that ipTM was the metric to gate on; it was reasoning carefully
about numbers that came from the wrong protein.

## Fixed

1. `MED23 = "Q9ULK4"` in `scripts/calibrate_structure.py`.
2. New `verify_accession()`, applied to **both** chains: the accession must
   carry the gene symbol it is named for, and must appear in UniProt's own
   cross-references for 9F6Y. Two checks, because either alone can pass by
   accident — MED24 shares 10 of MED23's 13 PDB cross-references, since both sit
   in the same whole-Mediator depositions. Only 9F6Y and 9F76 are
   MED23-specific.
3. `scripts/elk1_control.py` fetches both sequences by accession and verifies
   them before dispatching, instead of reading a hand-written file.
   `downloads/seqs.json` is now a record of what was used, not the input, and it
   records the accessions and gene symbols alongside the sequences.
4. `tests/test_calibration_constants.py` — 8 offline tests, including the actual
   bug replayed: MED24's real UniProt entry offered as the partner must fail on
   both counts.

---

# The result: MED23 (Q9ULK4), 5 seeds

Five independent dispatches, seeds 20260815–20260819, 1460 residues per complex,
549 s total (110 s per sample). No contact constraints, no complex template.
Scored by `scripts/score_elk1_control.py`.

| sample | ipTM | contacts | ELK1 res | in motif | MED23 res | **in pocket** | by chance |
|---|---|---|---|---|---|---|---|
| s0 | 0.239 | 177 | 66 | 11/11 | 84 | 0/7 | 0.43 |
| s1 | 0.457 | 232 | 79 | 11/11 | 107 | 0/7 | 0.55 |
| **s2** | **0.481** | 250 | 68 | 6/11 | 118 | **7/7** | 0.60 |
| s3 | 0.436 | 194 | 70 | 7/11 | 87 | 0/7 | 0.45 |
| s4 | 0.463 | 203 | 61 | 8/11 | 97 | 0/7 | 0.50 |

**Sample 2 finds the published site, and finds it specifically.** All seven
pocket residues — 339, 343, 379, 382, 383, 533, 537 — and the contact the paper
singles out:

> "F378-Elk-1 is surrounded in MED23 by side chains from residues I339, L343
> (H19), F379, G382, S383 (H21), and V533 and M537 (H28)" (PMC12015215 L23)

Sample 2 places **F378 3.17 Å from M537 and 4.33 Å from V533**, with I339 4.36 Å
from S376. That is the described contact, from sequence alone.

It is not luck. Sample 2 contacts 118 of MED23's 1368 residues, so the chance of
all seven pocket residues falling among them at random is **3.0 × 10⁻⁸**.

## What the pipeline does with it: discards it

`build_consensus` refuses, and would have suppressed the correct answer:

```
consensus  : converged=False support=0.40
  BLOCKER  : ensemble did not converge: dominant interface in 2/5 samples (40%),
             below the 60% floor
  BLOCKER  : the reproducible target region spans 62 residues, beyond the
             40-residue limit for a compact segment
```

The dominant cluster is **samples 0 and 1 — both 0/7**. Sample 2 is a singleton;
its Jaccard overlap with the others is 0.07–0.17. Majority vote over an ensemble
puts the right answer in a minority of one and reports the wrong one as dominant.

This is a finding about our selection rule, not about Boltz2. The ensemble
*contained* the answer. Nothing in the pipeline could tell.

## Can anything pick the winner?

Partially, and not reliably enough to use yet.

- **ipTM ranks it first**: 0.481 for the correct sample, above 0.463, 0.457,
  0.436, 0.239. But the margin over a **0/7** sample is **0.018**, and all five
  sit below the 0.60 floor — a floor gate rejects the correct sample too. One
  case, one pair: this is a hypothesis to test, not a rule to adopt.
- **Motif recovery is worse than useless — it is anti-correlated.** The correct
  sample scores **6/11**, the lowest of the five; the two samples at **11/11**
  are both 0/7. A sample that drapes the disordered window across the surface
  hits the whole motif; the one that inserts F378 into a cavity commits fewer
  residues. Any scorer weighting motif coverage would have ranked sample 2 last.
- `complex_plddt` is flat (0.836–0.846) and separates nothing.

## What this establishes

- **Boltz2 can recover this interface from sequence alone, without constraints
  or a template.** Once in five seeds, at a precision that is not chance.
- **A single prediction is not enough, and neither is a majority.** The result
  exists only because five seeds ran; it survives only if a minority cluster can
  win.
- **The 60% convergence floor is wrong for this problem as stated.** It encodes
  "most samples agree" when the question is "did any sample find something real".
  Loosening it blindly admits noise — four wrong answers here are also singletons
  or pairs — so the fix is a discriminator, not a lower threshold.
- Global confidence still reports the wrong thing: `ptm` 0.860 and
  `complex_plddt` 0.843 across samples that are mostly wrong about the interface.

## What this does not establish

- **1 of 5 is not a recovery rate.** Five seeds on one pair gives a wide
  interval; 20% is the point estimate and little more.
- The submitted ELK1 is unmodified, while the native interaction depends on
  pSer383. The model was asked for an interaction whose real form it cannot
  represent — and still found the site once. That makes the caveat more
  interesting, not less, but it is still a caveat.
- No claim of binding, affinity, or experimental validation follows from this.
  It is a computational prediction that matches a published structure.

## Follow-up: the consensus fix, and what it does not fix

`build_consensus` now scores and keeps **every** cluster as an
`InterfaceHypothesis` rather than reducing the losers to sample names, and
distinguishes `ambiguous` (nothing converged, but something defensible survived
— next action `sample_more`) from `refused`. Only `converged` can generate a
docking site; `ambiguous` needs a named human approval and a chosen hypothesis.
Thresholds unchanged.

Re-running this artifact through it gives **`refused`**, not `ambiguous`, and the
reason is worth recording: **no cluster is localized, including sample 2.**

| hypothesis | samples | support | segment span | partner residues | spatial extent | pocket |
|---|---|---|---|---|---|---|
| H1 | s0+s1 | 40% | 62 aa | 133 | 65 Å | 0/7 |
| **H2** | **s2** | 20% | **75 aa** | 118 | **75 Å** | **7/7** |
| H3 | s3 | 20% | 69 aa | 87 | 76 Å | 0/7 |
| H4 | s4 | 20% | 75 aa | 97 | 64 Å | 0/7 |

Sample 2 does not propose a compact alternative site. It proposes a **75 Å
diffuse surface that contains the correct 17 Å pocket**. The seven published
residues are in there, along with 111 others.

Scoring the compact contact patch separately — weighting each target residue by
its contact mass instead of counting residues, at the same 80% coverage — was
tried and does not change this: sample 2 goes from 75 to 66 residues, still well
beyond the 40-residue limit. The drape is not a thin tail that trimming removes;
it carries real contact mass. That measure is therefore not shipped, rather than
having its thresholds bent until this one case passes.

So the majority-vote defect and the ELK1 refusal are two separate problems. The
first is fixed and regression-tested. The second is that a 92-residue
disordered window laid across a receptor produces no compact segment on either
side, so the pipeline cannot yet distinguish "found the pocket and much else"
from "found nothing".

## Next

1. **Submit a shorter ELK1 window.** The control gives Boltz2 92 residues of
   mostly disordered TAD, and the drape that defeats localization is largely
   that window. The cheapest test of whether the diffuse interface is the
   model's answer or the question's fault.
2. More seeds on this pair — is the hit rate ~20%, and does ipTM keep ranking
   the correct sample first as n grows? That is the only cheap way to find out
   whether the 0.018 margin is signal.
3. A discriminator that is not majority vote. Interface pLDDT restricted to
   contact residues is now summarised per hypothesis (`contact_plddt`) and does
   separate the samples a little — 59.5 for the correct one against 55.2 for the
   dominant pair — but on one case that is an observation, not a rule. Interface
   PAE is in the payload and has not been looked at.
4. Re-run with pSer383 if Boltz2 can take a modified residue.

Reproduce:

```bash
proto-tools deploy --apps boltz2 --test
python scripts/elk1_control.py --samples 5
```

Artifacts land in `runs/elk1_control/` (gitignored): `sample_<i>.cif` per seed,
`sample_<i>_pae.json` beside it, and `boltz_5x.json` — the dispatch records with
those two fields replaced by pointers.

## Bugs fixed along the way

All in `scripts/elk1_control.py`, in the order they had to be cleared:

1. **It never dispatched to Modal.** Printed "dispatching to Modal" then called
   `run_boltz2()`, which runs locally and dies on Apple silicon —
   `cuequivariance-ops-cu12` has no macOS-ARM wheel.
2. **The artifact was unparseable.** `json.dumps(payload)[:20_000_000]` cut the
   file mid-token, and because the payload leads with a multi-megabyte mmCIF
   string, the metrics were exactly what got destroyed. PAE also arrives nested
   under `structure["metrics"]["pae"]`, so a top-level check misses it — that
   left 183 MB inline on the first five-sample run.
3. **The ensemble was not an ensemble.** `diffusion_samples=5` returns "the best
   by confidence" — one structure. `build_consensus` was handed a single sample
   no matter what was requested.
4. **The confidence summary printed nothing.** It looked for `iptm` at the top
   of the payload; those keys live under `structure["metrics"]`.
5. **The partner was the wrong protein.** The four above are why the control
   could not run. This is why running it would not have helped.
