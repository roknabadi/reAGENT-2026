# Stress test: name a cancer, run the whole chain

Run 2026-08-15. Twelve DepMap lineages through `scripts/run_cancer.py`, each one
a complete pass — discovery, gates, per-axis literature retrieval, contact,
structure, site, chemistry, screen, next experiment.

**12 runs · 402 papers retrieved live from Paperclip · 0 blocked stages · one
shared input fingerprint (`1c824e11a217decb`).**

Reproduce:

```bash
python scripts/run_cancer.py Lymphoid
python scripts/run_cancer.py "Small Cell Lung Cancer" --column OncotreeSubtype
```

---

## What it found, unprompted

No gene list was supplied. The screen runs all 1,588 Lambert transcription
factors and the gates decide. Every hit below is an independently known
lineage-survival factor for its cancer — this is the validation criterion in
`PROJECT.md`, met across ten lineages at once rather than on a single
hand-picked example.

| Lineage | Top hit | Selectivity Δ | Known for |
|---|---|---|---|
| Skin | **SOX10** | 1.22 | melanoma master regulator |
| Myeloid | **MYB** | 1.08 | AML dependency |
| Lymphoid | **IRF4** | 1.03 | myeloma / lymphoma master TF |
| Kidney | **HNF1B** | 0.93 | kidney lineage factor |
| Bone | **FLI1** | 0.71 | Ewing sarcoma (EWSR1–FLI1) |
| Bowel | **KLF5** | 0.69 | colorectal epithelium |
| Head and Neck | **TP63** | 0.63 | squamous master TF |
| Peripheral Nervous System | **ISL1** | 0.60 | neuroblastoma |
| Ovary/Fallopian Tube | **PAX8** | 0.55 | ovarian/Müllerian lineage |
| Esophagus/Stomach | **KLF5** | 0.40 | squamous/gastric epithelium |

Ten lineages, ten correct answers, none of them told to the pipeline.

## The two that abstained are also correct

**Breast** and **Pancreas** returned 0 of 1,588 passing, and the run abstained
rather than lowering a bar to produce something.

That is the right answer for both. Breast dependency is organised by molecular
subtype — ER, HER2, basal — so a lineage-level pool averages the subtypes
against each other, exactly the dilution described in section 4 of
`FINDINGS_DEPMAP_ROUND01.md`. Pancreatic lines are dominated by KRAS rather than
by a transcription-factor addiction, so there is no selective TF to find at this
granularity.

A pipeline that returned a confident answer for all twelve would be the
suspicious result.

## Literature retrieval

Six evidence axes per candidate, each a separate query, per the stage-1 brief:
dependency, driver, normal tissue, coactivator, activation domain, structure.

Coverage was complete wherever it ran — 18/18 axes for three-candidate lineages,
12/12 for two, 6/6 for one. No axis failed. Retrieval ranged 21–68 papers per
lineage.

Every record is marked *retrieved, not read*. Support type is triaged from
language and anything unrecognised stays `unclassified`; a title is not evidence
and the artifact says so.

## Robustness

| Input | Result |
|---|---|
| 12 real lineages | complete, 0 blocked |
| `Small Cell Lung Cancer` + `--column OncotreeSubtype` | complete |
| `lymphoid`, `LYMPHOID` | complete (case-insensitive) |
| `Notacancer` | blocked at the first stage touching data, names the fix, exit 1 |
| `""` | blocked, exit 1 |
| `Lung; DROP TABLE` | blocked, exit 1 |

Bad input fails on the first stage that touches data, with a reason and a
non-zero exit code. It does not crash, and it does not return an empty result
that could be mistaken for a finding.

## Where every run stops, and why

All twelve stop at the same place: **mapped contact**. The candidates have real,
selective, statistically strong dependencies and no documented coactivator
contact with a mapped interacting region, so site, chemistry and screening
abstain in turn with stated reasons.

That is the honest state of the science, not a defect. It is the same empty
intersection recorded in `FINDINGS_DEPMAP_ROUND01.md` section 5, now confirmed
across ten more lineages: **the TFs with the strongest disease-selective
dependencies are not the TFs with documented Mediator contacts.**

Closing that gap for one candidate is what unblocks everything downstream, and
the run says so in its own next-experiment stage.

## Timing

About 50 seconds per lineage. Most of it is re-reading the 382 MB Chronos matrix
per run; the eighteen Paperclip searches take roughly twenty seconds. Acceptable
for a demo that runs one cancer at a time, and the obvious optimisation — cache
the parsed matrix — is not worth doing before the results matter.
