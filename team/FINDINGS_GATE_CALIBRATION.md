# Gate thresholds against real DepMap data

Run 2026-08-15 by Amir. First time `reagent_workflow`'s gates have been applied
to real numbers rather than synthetic fixtures.

Reproduce (Kevin's pipeline regenerates the data; it is gitignored):

```bash
cd tests/re-agent_discovery/src
python stage0_tf_universe.py     # Lambert TF universe, 1639 TFs
python stage1_depmap.py          # DepMap 24Q4, ~430MB download
```

Then apply `reagent_workflow.config.GateThresholds` to
`data/results/stage1_dependency_hits.csv`.

## The pipeline recovers known biology unprompted

132,677 TF × context tests over 1,178 models × 1,552 TFs. Sorted by selectivity,
the top hits are the canonical lineage dependencies, none of which were supplied
as input:

| TF | Context | n | median effect | q |
|---|---|---|---|---|
| IRF4 | Plasma Cell Myeloma | 19 | −1.83 | 1.6e−09 |
| PAX3 | Alveolar Rhabdomyosarcoma | 8 | −1.57 | 3.9e−04 |
| MYOD1 | Alveolar Rhabdomyosarcoma | 8 | −1.47 | 1.9e−03 |
| HNF1B | Renal Clear Cell Carcinoma | 16 | −1.59 | 4.7e−07 |
| SOX10 | Melanoma | 54 | −1.57 | 2.1e−18 |
| EBF1 | DLBCL, NOS | 16 | −1.21 | 9.6e−08 |
| STAT3 | ALK+ ALCL | 7 | −1.37 | 2.6e−09 |
| PAX8 | Renal Clear Cell Carcinoma | 16 | −1.28 | 2.1e−07 |

IRF4 in myeloma, SOX10 in melanoma, PAX8 in renal — these are the textbook
cases. Recovering them without being told them is the check worth having.

## Our thresholds are selective, and one is loose

Applying `GateThresholds` as configured: **189 of 132,677 rows pass (0.14%),
across 78 TFs and 64 contexts.**

Sensitivity to the individual thresholds:

| Change | Rows | TFs |
|---|---|---|
| current defaults | 189 | 78 |
| `min_target_models` 5 → 15 | 75 | 39 |
| `max_other_dependent_fraction` 0.5 → 0.05 | **57** | 37 |
| add `qvalue <= 0.05` | 112 | 51 |

**`max_other_dependent_fraction = 0.5` is the loosest gate we have**, and it is
the one the adversarial review flagged as admitting common-essential genes.
Kevin's own pipeline uses ≤5% out-of-context, ten times stricter, and it is the
single biggest filter difference between the two implementations. Moving it
alone cuts the pass list by two thirds.

## For Kevin to decide, not me

Three open questions. All three are disease-specificity calls, which is Kevin's
area, so I have changed nothing:

1. **Out-of-context fraction.** 0.5 or 0.05? His pipeline says 0.05. If the
   answer is 0.05, `config.GateThresholds.max_other_dependent_fraction` should
   move and roughly two thirds of currently-passing rows drop out.
2. **Minimum n.** We use 5; the earlier task note said he wants 15. At 15 the
   list halves, and it would drop PAX3/MYOD1 in alveolar rhabdomyosarcoma (n=8)
   and STAT3 in ALK+ ALCL (n=7) — real dependencies with small cohorts. That is
   the trade being made.
3. **q-value.** Kevin's design says q is never a gate, kept as its own column.
   `reagent_workflow` does not gate on it either, which agrees. Worth confirming
   that is deliberate on both sides rather than coincidental.

## A convention mismatch worth knowing about

Three components compute `selectivity_delta` and they do not agree on its sign:

| Component | Formula | Selective means |
|---|---|---|
| `dependency_scout.depmap` | `other − target` | positive |
| `reagent_workflow` | `target − other` | negative |
| `stage1_depmap` (Kevin) | `in − out` | negative |

The adapter used to negate on the way in, which is correct for
`dependency_scout` and silently inverts Kevin's output. It now **recomputes the
delta from the medians**, which are unambiguous, so no source's convention can
flip the science. If you add a fourth producer, do not copy its delta — derive
it.
