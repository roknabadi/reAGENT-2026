# First real DepMap run — what the data says

Run 2026-08-15 by Vraj. DepMap Public 24Q2 Chronos gene effects (1100 cell lines
× 18443 genes) restricted to the Lambert et al. 1639-TF universe. This is the
first real, non-synthetic data in the repo.

Reproduce:

```bash
dependency-scout discover --gene-effect downloads/CRISPRGeneEffect.csv \
  --models downloads/Model.csv --context Lung --tf-list downloads/lambert_tfs.csv \
  --source-version "DepMap Public 24Q2" --output outputs/lung_tf_candidates.json
```

The CSVs are gitignored. Commands to fetch them are in `team/TASKS.md`.

---

## 1. Zero TFs pass the gates at lineage level

1588 TFs screened in Lung. **0 eligible.** Failure counts:

| Failures | Gate |
|---|---|
| 1587 | selectivity delta below 0.35 |
| 1533 | weak median dependency |
| 1533 | dependency in fewer than half of target models |
| 66 | dependency too broad outside the target context |

This is a result, not a bug. At whole-lineage granularity essentially no TF is
selectively required in Lung versus all other lineages pooled.

**It is also direct evidence for Kevin's two-pass design.** The signal is not at
lineage level; the drill-down is required, not optional.

## 2. The negative controls fire correctly on real data

The strongest dependencies in the list are exactly the ones that should be
rejected:

| TF | median effect | dependent in target | dependent elsewhere | verdict |
|---|---|---|---|---|
| GTF2B | −1.97 | 100% | 100% | pan-essential, rejected |
| CTCF | −1.73 | 100% | 100% | pan-essential, rejected |
| MYC | −1.99 | 91% | 96% | pan-essential, rejected |
| AHCTF1 | −1.16 | 100% | 100% | pan-essential, rejected |

General transcription machinery is profoundly essential and not selective. The
gate catches all of it, unprompted, on real data. This is the rejection demo.

## 3. n ≥ 3 lets noise to the top — Kevin's n ≥ 15 is right

The four candidates closest to passing in Lung all have **n_target = 3**, while
real lineage members have n = 119. Those genes are sparsely measured in the
screen, not sparsely dependent.

| TF | n_target | selectivity |
|---|---|---|
| MYRFL | 3 | +0.51 |
| ZNF487 | 3 | +0.24 |
| FOXI3 | 3 | +0.20 |
| SMAD5 | 3 | +0.18 |

With `min_models = 3` these sort to the top of a 1588-TF screen. **Answers Kevin
Q0.1 with data: keep the low-confidence flag, and do not let n < 15 into a
shortlist.**

## 4. The fraction gates assume a homogeneous context — they should not

Small Cell Lung Cancer, `--context-column OncotreeSubtype`, n = 23 with CRISPR
data (78 models exist; only 23 are screened).

SCLC's known subtype-defining TF dependencies behave like this:

| TF | median effect | dependent in SCLC | dependent elsewhere |
|---|---|---|---|
| ASCL1 | −0.16 | 39% | **1%** |
| INSM1 | −0.08 | 26% | **0%** |
| POU2F3 | −0.00 | 17% | **0%** |
| NEUROD1 | +0.06 | 13% | **1%** |

Look at the last column. ASCL1 is required in ~0 cell lines outside SCLC and in
39% inside it. That is an extremely specific dependency — and it **fails** our
gate, because the gate demands the median line be dependent and that ≥50% of
target lines be dependent.

The reason is biological: SCLC is four mutually exclusive molecular subtypes
(SCLC-A/N/P/Y). ASCL1 is the dependency of SCLC-A only, so pooling all SCLC
dilutes it below every median-based threshold.

**This is the "cell state" half of the pitch, and our current gate cannot see
it.** A median-based test finds dependencies that are uniform within a context.
It structurally cannot find a dependency that defines a subpopulation — which is
exactly what transcriptional addiction usually looks like.

### For Kevin

The fix is a decision, not code. Options:

1. Drill to molecular subtype so the context becomes homogeneous. Needs a
   subtype assignment DepMap does not ship for SCLC-A/N/P/Y.
2. Add a **specificity-first** gate alongside the median gate: very low
   `other_dependent_fraction` with a meaningful `target_dependent_fraction`,
   regardless of median. ASCL1 passes this; GTF2B and CTCF still fail it.
3. Accept lineage-level insensitivity and screen only contexts already known to
   be homogeneous.

**Recommendation: option 2.** It is a few lines in `ranking.gate`, it keeps every
existing rejection intact, and it is the only one that finds subpopulation
dependencies without data we do not have.

Not implemented — this changes what counts as a target, so it is Kevin's call
and Andrey's sign-off.

---

## Two code bugs found, both blocking the adapter

### `selectivity_delta` has opposite sign conventions in the two packages

| Where | Formula / test | Selective means |
|---|---|---|
| `dependency_scout/depmap.py:83` | `other.median() - target.median()` | **positive** |
| `dependency_scout/ranking.py:15` | fails when `< 0.35` | **positive** |
| `reagent_workflow/ingest.py:136` | expects `median_target - median_other` | **negative** |
| `reagent_workflow/gates.py:80` | fails when `> -0.3` | **negative** |

They are exact negations. A candidate produced by `dependency_scout` and handed
to `reagent_workflow` is **hard-rejected by `ingest._validate_candidate` as a
data error**, not merely scored differently. This blocks the adapter (`TASKS.md`
Band 1 #3) until one convention wins.

Real DepMap output uses the `dependency_scout` convention (positive = selective).
Whichever is chosen, both sides must move together and it needs a
`DECISIONS.md` entry.

### Scoring weights do not match Kevin's locked spec

`reagent_workflow/config.py:51-56` defaults are **25/15/25/10/15/10**. Kevin's
§7 locks **25/25/20/15/10/5**. Neither matches `dependency_scout/ranking.py`,
which uses a flat 80/20 discovery-vs-enrichment split. Three scoring schemes,
one pipeline.
