# TF–Mediator Discovery Pipeline

## Input
- A transcription factor (gene symbol) and/or an exact disease-context string (e.g. "Small Cell Lung Cancer")
- Disease-context resolution — mapping a loose request to the exact stored term — is done by an LLM reviewing the known-context list, not automated fuzzy matching

## Workflow

| Stage | Source | Purpose |
|---|---|---|
| 0. TF universe | Lambert et al. 2018 | 1,552 human TFs — defines the search space |
| 1. Dependency mining | DepMap 24Q4 (CRISPR) | Every TF × every disease lineage/subtype (132,677 tests, one-time scan) |
| 2. Mediator connectivity | STRING API | Per-TF connectivity to 33 Mediator subunits, 4 evidence channels kept separate |
| 3. Literature check | Paperclip (PMC/bioRxiv/etc.) | Claims classified by evidence type; negatives cached permanently |
| 4. Protein/structure/drug | UniProt + PDB + ChEMBL (via Paperclip) | Domains, disease annotations, structures, compound testing history |
| 5. Tissue safety proxy | Human Protein Atlas | Normal-tissue expression breadth |
| 6. Regulatory coverage (partial) | ENCODE | TF ChIP-seq experiment existence only |

**Output:** one flat table, one row per (TF, disease context, Mediator subunit lead) — no composite score.

## Logic — gating and filtering

- **One hard gate** (Stage 1): a candidate passes if the group median is dependent, **or** if ≥10% of in-context models are dependent and ≤5% of out-of-context models are — whichever fires. The second path exists so a dependency restricted to a subpopulation within a pooled disease subtype (e.g. one molecular subtype of a cancer) isn't diluted away by the group average.
- Below minimum sample size (n<5 per group): pair is never tested.
- **STRING:** only a whole-complex artifact pattern (≥15 of 33 subunits hit near-uniformly) is rejected; everything else is kept and labeled.
- **Contact rule:** a claimed interaction only counts as confirmed contact if it has a mapped interacting region; otherwise it's downgraded to "indirect," never treated as contact.
- **q-value is never a gate** — kept as its own column, since statistical significance and true dependency direction are separate questions.
- **No composite scoring** — every field is raw evidence with its own status; nothing is averaged into one number.
- Paperclip-sourced data (literature, UniProt/PDB/ChEMBL) is cache-only from code — an agent runs the actual query and writes the result back.

## Status
- Code and docs pushed to branch `status/kevin-2026-08-15-round3` (not merged to `main`)
- Not built: full ENCODE enhancer-linking (blocked on a DepMap↔ENCODE biosample crosswalk), AlphaFold/COSMIC/Open Targets integration
