# ELK1–MED23 positive control: RETRACTED, and why

**The run of 2026-08-15 is void. It did not test what it claimed to test, and
the conclusion drawn from it — "the control fails" — is withdrawn.**

The control docked the ELK1 transactivation domain onto **MED24 (O75448, 989
aa)**, not MED23 (Q9ULK4, 1368 aa), and then scored the answer against MED23
residue numbering taken from PDB 9F6Y. Every number in the previous version of
this document is a measurement of the wrong complex judged by the wrong ruler.

Nothing about Boltz2's ability to recover this interface has been established,
in either direction.

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

## Still open

The control itself. It has not been run against MED23. Re-running it is the
next step; until it has, **no claim about Boltz2's performance on this interface
is supported**, including the cautious ones.

When it does run, the caveat that was true before is still true: the submitted
ELK1 is unmodified, while the native interaction depends on pSer383, so the
model is being asked for an interaction whose real form it cannot represent.

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
