# Decisions

Append at the top. One entry per decision. Never edit an old entry — supersede
it with a new one that says what it replaces.

Template:

```
## YYYY-MM-DD — <one-line decision>
Decided by: <person>
Why: <evidence, with sources recorded in SOURCES.md>
Alternatives rejected: <what and why>
Reversible: yes/no — <what would reverse it>
```

Keep observed evidence, computed results, predictions, and hypotheses labeled
as such. No claims of binding, safety, efficacy, or experimental validation
from computational results.

---

## 2026-08-15 — `selectivity_delta`: `dependency_scout`'s sign convention wins, `reagent_workflow` flips to match
Decided by: Kevin — proposed, awaiting Andrey's sign-off (this blocks Task #3,
the model adapter, so recording now rather than letting it sit)
Why: `FINDINGS_DEPMAP_ROUND01.md` found the two packages define
`selectivity_delta` as exact negations — `dependency_scout/depmap.py:83`
computes `other.median() - target.median()` (positive = selective) and
`dependency_scout/ranking.py:15` fails candidates below `0.35`, while
`reagent_workflow/ingest.py:136` expects `median_target - median_other`
(negative = selective) and `reagent_workflow/gates.py:80` fails above `-0.3`.
A candidate built in one package is hard-rejected by the other as a data
error, not merely scored differently. Real DepMap output already emits
`dependency_scout`'s convention. I hit the same sign confusion independently
in a separate scratch implementation: percentile-ranking `selectivity_delta`
ascending (the naive read of "delta") put IRF4 — the single strongest hit in
a 38,666-row store — at the bottom of the ranking, because
`in_median - out_median` is very negative for a real dependency. `models.py`
itself lives in `dependency_scout`, and real data already conforms to its
convention, so `reagent_workflow` moving to match is the smaller, lower-risk
change.
Alternatives rejected: flip `dependency_scout` instead — rejected because it
already matches the real data pipeline in production use (Vraj's round-01/02
runs), so flipping it would require re-deriving every existing real result,
not just relabeling a field.
Reversible: yes — it's a formula sign, not a schema shape. Flip `ingest.py`
and `gates.py`'s two lines and re-run existing candidates through the
adapter to confirm nothing else assumed the old convention.

---

## 2026-08-15 — Four roles: Andrey decides, Kevin designs, Vraj and Amir build
Decided by: Andrey
Why: six named roles with overlapping sign-off was slower than the deadline
allows. Andrey now signs every gate and owns workflow direction; Kevin owns the
solution logic (stages, sources, thresholds, ranking); Vraj and Amir split the
code along what each already built — Vraj the two ends of the pipe (real data
in, compounds and demo out), Amir the agent in the middle. Task split in
`team/TASKS.md`. Supersedes the earlier note making Kevin the decision lead.
Working rule: scrappy end-to-end first, polish the weakest link second. A
polished stage connected to nothing scores zero on Sunday.
Alternatives rejected: consensus at each gate (too slow); keeping per-person
directories (CONTRIBUTING forbids, and it fragments review).
Reversible: yes — `ROLES.md`, `TASKS.md`, `CHECKPOINTS.md`, `.github/CODEOWNERS`.

## 2026-08-15 — CEBPB–MED23 is `indirect`, not `direct`; it does not go downstream
Decided by: Andrey
Scope: one candidate's evidence classification in the round-01 calibration case.
Not a target selection and not an architectural commitment.
Why: round-01 triage ranked CEBPB second and labelled its MED23 involvement
`DIRECT (region unmapped)`. Under our own contract that label is not available.
`MediatorLink.involvement` derives `indirect` because `interacting_region_mapped`
is false, and `CLAUDE.md` says a whole-protein pull-down with no mapped region is
not contact. The evidence is a single sentence — "Ras induces mediator complex
exchange on C/EBPβ" (Mo et al., *Mol. Cell* 13:241, 2004, PMID 14759370) — which
we have **never read at the source**: it was not retrievable as full text and is
carried on the citing bibliography of Monté et al. 2025
(doi:10.1038/s41467-025-59014-8). Recorded in `SOURCES.md` under "cited but not
retrievable", and typed in `examples/mediator_link_cebpb_med23.json`.
The dependency genetics are genuinely strong — necessary *and* sufficient
mesenchymal-transformation master regulator (doi:10.1038/nature08712) — so this
rejects the *contact*, not the biology.
Alternatives rejected: (a) carrying it as `direct` on round-01's own label — it
is the exact negative control `PROJECT.md` tells us to reject out loud;
(b) dropping CEBPB entirely — the cheap decisive experiment (peptide-tiling the
C/EBPβ TAD against MED23 391–582 in the published FP format, PMC11623927 L80/L125)
would settle it, and that experiment is worth proposing.
Reversible: yes — retrieve Mo 2004, or map the region experimentally. A mapped
region plus a direct-experimental claim flips the derived involvement to `direct`
with no code change.

## 2026-08-15 — Literature triage enters the repo as evidence, never as a ranking
Decided by: Andrey
Scope: how any literature round hands off. **Not** a decision about which biology
the pipeline targets.
Why: round 01 used MED23 as its worked test case and produced its own A–D
dependency grades. Those grades are qualitative calibration, not DepMap Chronos
values, and `DependencyEvidence` requires seven numeric fields (`n_target_models`,
`median_target_effect`, `selectivity_delta`, …) that no literature round can
measure. Emitting `RankedCandidate` objects from letter grades would mean
inventing those numbers, which is what `EnrichmentEvidence.scores_require_claims`
exists to prevent. So a literature round transfers only what is real: verified
source rows in `SOURCES.md` and typed evidence objects in `examples/`. Ranking
stays downstream of the quantitative stage. Full reports stay out of Git as
generated research artifacts under `outputs/`, per `CONTRIBUTING.md`.
Alternatives rejected: (a) synthesising plausible DepMap numbers to make the
pipeline run end-to-end — that is the failure this project exists to catch;
(b) adding a parallel `decisions/` tree from the local research package — `team/`
already serves that purpose, so the open questions became a status blocker and
evidence on checkpoint 2 instead.
Reversible: yes — this constrains ordering, not biology. Once a candidate has
real dependency numbers it can be built as a full `RankedCandidate`.

## 2026-08-15 — Agent traces export as opentraces JSONL, not a custom format
Decided by: Amir (agent workflow)
Why: computed evidence — the installed BenchFlow 0.6.8 in `tools/benchflow`
parses opentraces natively (`benchflow.traces.parsers.parse_opentraces_file`,
and `benchflow.cli.trace_import._detect_format` routes a record to it on
`schema_version` or on carrying both `agent` and `steps`). ATIF was the
alternative, but `benchflow.trajectories.export_atif` emits a single JSON
document (`trainer/atif.json`), while the required artifact is a `.jsonl`.
`runs/<id>/traces/benchflow_trace.jsonl` is now validated by that BenchFlow
interpreter, which reports `detected_format=opentraces`. Each exported step
keeps its `internal_event_id` and artifact paths, so the published trace stays
linked to the internal trace and the run directory.
Alternatives rejected: ATIF (wrong container shape for a JSONL artifact); a
project-specific trace schema (nothing downstream could read it).
Reversible: yes — the exporter is one module, `src/reagent_workflow/benchflow_export.py`.

## 2026-08-15 — The hero artifact uses the frozen task's schema
Decided by: Amir (agent workflow)
Why: `runs/<id>/reports/hero_hypothesis.json` is emitted in the shape defined by
`benchflow/tasks/tf-mediator-hero/task.md` rather than a third workflow-only
format, so the workflow's output is directly readable by the frozen verifier.
Verified by running that verifier's assertions against a fixture run: the five
structural checks pass and the two public-provenance checks fail, because the
fixture's sources are `synthetic://`. That failure is intended and is now a
test — synthetic data must not satisfy a public-evidence check.
Alternatives rejected: inventing a separate report schema (would drift from the
task we are evaluated on).
Reversible: yes — `_hero_hypothesis_payload` in `orchestrator.py`.

## 2026-08-15 — CEBPB–MED23 is `indirect`, not `direct`; it does not go downstream
Decided by: Andrey
Scope: one candidate's evidence classification in the round-01 calibration case.
Not a target selection and not an architectural commitment.
Why: round-01 triage ranked CEBPB second and labelled its MED23 involvement
`DIRECT (region unmapped)`. Under our own contract that label is not available.
`MediatorLink.involvement` derives `indirect` because `interacting_region_mapped`
is false, and `CLAUDE.md` says a whole-protein pull-down with no mapped region is
not contact. The evidence is a single sentence — "Ras induces mediator complex
exchange on C/EBPβ" (Mo et al., *Mol. Cell* 13:241, 2004, PMID 14759370) — which
we have **never read at the source**: it was not retrievable as full text and is
carried on the citing bibliography of Monté et al. 2025
(doi:10.1038/s41467-025-59014-8). Recorded in `SOURCES.md` under "cited but not
retrievable", and typed in `examples/mediator_link_cebpb_med23.json`.
The dependency genetics are genuinely strong — necessary *and* sufficient
mesenchymal-transformation master regulator (doi:10.1038/nature08712) — so this
rejects the *contact*, not the biology.
Alternatives rejected: (a) carrying it as `direct` on round-01's own label — it
is the exact negative control `PROJECT.md` tells us to reject out loud;
(b) dropping CEBPB entirely — the cheap decisive experiment (peptide-tiling the
C/EBPβ TAD against MED23 391–582 in the published FP format, PMC11623927 L80/L125)
would settle it, and that experiment is worth proposing.
Reversible: yes — retrieve Mo 2004, or map the region experimentally. A mapped
region plus a direct-experimental claim flips the derived involvement to `direct`
with no code change.

## 2026-08-15 — Literature triage enters the repo as evidence, never as a ranking
Decided by: Andrey
Scope: how any literature round hands off. **Not** a decision about which biology
the pipeline targets.
Why: round 01 used MED23 as its worked test case and produced its own A–D
dependency grades. Those grades are qualitative calibration, not DepMap Chronos
values, and `DependencyEvidence` requires seven numeric fields (`n_target_models`,
`median_target_effect`, `selectivity_delta`, …) that no literature round can
measure. Emitting `RankedCandidate` objects from letter grades would mean
inventing those numbers, which is what `EnrichmentEvidence.scores_require_claims`
exists to prevent. So a literature round transfers only what is real: verified
source rows in `SOURCES.md` and typed evidence objects in `examples/`. Ranking
stays downstream of the quantitative stage. Full reports stay out of Git as
generated research artifacts under `outputs/`, per `CONTRIBUTING.md`.
Alternatives rejected: (a) synthesising plausible DepMap numbers to make the
pipeline run end-to-end — that is the failure this project exists to catch;
(b) adding a parallel `decisions/` tree from the local research package — `team/`
already serves that purpose, so the open questions became a status blocker and
evidence on checkpoint 2 instead.
Reversible: yes — this constrains ordering, not biology. Once a candidate has
real dependency numbers it can be built as a full `RankedCandidate`.

## 2026-08-15 — Coordinate through `team/`, not chat
Decided by: Vraj
Why: judging weights inspectability; decisions made in chat leave no trace in
the repo. Per-person status files avoid merge conflicts; shared logs are
append-at-top for the same reason.
Alternatives rejected: an issue tracker (agents can't write to it as cheaply);
one shared status file (conflicts on every push).
Reversible: yes — the files are plain Markdown, delete the directory.
