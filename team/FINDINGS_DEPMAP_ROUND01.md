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

## 1. Zero TFs pass the gates **in Lung** — corrected

> **Correction, same day.** The heading originally read "at lineage level". That
> overgeneralised from a single lineage. Kevin's independent all-lineage scan
> found real selective dependencies elsewhere, and re-running this repo's own
> gates confirms eight of them (§5). **Lung is the negative case, not the rule.**

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

---

## 5. Round 02 — the two halves of the pipeline meet

Kevin ran an independent all-lineage scan (DepMap 24Q4, separate scratch repo,
different code). Re-running his named TFs through **this** repo's gates on 24Q2
reproduces his result — eight gate-eligible candidates at n ≥ 15:

| TF | Context | n | median | selectivity | dep. in context | dep. elsewhere | score |
|---|---|---|---|---|---|---|---|
| IRF4 | Lymphoid | 81 | −1.15 | +1.03 | 62% | **3%** | 0.593 |
| PAX8 | Kidney | 37 | −0.90 | +0.77 | 57% | 6% | 0.470 |
| ISL1 | Peripheral Nervous System | 41 | −0.67 | +0.60 | 61% | **0%** | 0.397 |
| TP63 | Head and Neck | 72 | −0.64 | +0.63 | 60% | 6% | 0.387 |
| EBF1 | Lymphoid | 81 | −0.69 | +0.56 | 51% | 1% | 0.374 |
| PAX8 | Ovary/Fallopian Tube | 59 | −0.67 | +0.55 | 58% | 5% | 0.374 |
| MYCN | Peripheral Nervous System | 41 | −0.65 | +0.56 | 56% | 2% | 0.374 |
| ZNF217 | Lymphoid | 81 | −0.68 | +0.38 | 68% | 26% | 0.341 |

Two independent implementations, two DepMap releases, same answers. These are
also independently known biology — IRF4 in myeloma, TP63 in oral SCC, ISL1 and
MYCN in neuroblastoma. The pipeline rediscovered them without being told.

Reproduce: `python scripts/build_round02_shortlist.py` →
`outputs/round02_shortlist.{json,md}`.

### The finding that should drive the next literature pass

Joining the two halves shows the project's central gap in one table:

- **Candidates with real dependencies have no Mediator evidence.** IRF4, PAX8,
  ISL1, TP63, EBF1, MYCN, ZNF217 all sit at `involvement: unknown`.
- **Candidates with Mediator evidence have no dependencies.** RUNX2, CEBPB and
  ETV1 show no selective DepMap dependency in any lineage — independently
  confirmed by Kevin. ELK1 and ELF3 likewise, as expected: their value is
  structural, not fitness-based.

The intersection is currently **empty**. That is the honest state of the
hypothesis, and it is a result worth showing.

It also says where to look next: run the Mediator-contact literature pass
against **IRF4 and PAX8**, which already clear the hardest bar. Kevin reached
the same conclusion independently. Continuing to push RUNX2/CEBPB/ETV1 spends
the remaining time on candidates that fail the dependency gate on real data.

## 6. Open schema-mapping task from Kevin

Kevin's scan emits `n_in`, `n_out`, `in_median`, `out_median`, `cohens_d`,
`pvalue`, `qvalue`. `DependencyEvidence` wants `n_target_models`,
`n_other_models`, `median_target_effect`, `median_other_effect`,
`target_dependent_fraction`, `other_dependent_fraction`, `selectivity_delta`,
`mann_whitney_p`. Mapping is mechanical except two points:

1. `target_dependent_fraction` / `other_dependent_fraction` are not in his
   output. `depmap.py` already computes both from the raw matrix, so the
   simplest resolution is to run the mapping through `analyze_gene_effects`
   rather than converting his table — which is what §5 does.
2. `selectivity_delta` as median gap or Cohen's d. Currently the median gap
   (`other.median() − target.median()`). Changing it is a `DECISIONS.md` entry.
   Cohen's d would be variance-aware and arguably better, but the gate threshold
   0.35 is calibrated to the median gap and would have to move with it.
