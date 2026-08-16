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

## 2026-08-16 — A follow-up may reuse a finished run's state, and must date every part of it
Decided by: Amir
Why: The interface re-ran the whole pipeline for every question, so the cheapest
possible follow-up — "why was NKX2-1 rejected?", whose answer the previous run
computed and discarded — cost a full DepMap scan, a Paperclip search per axis
per candidate, and the structural tail. Sessions remove that cost. The risk they
introduce is the one this project exists to catch: a stage reporting a
conclusion it did not compute. Resolved by provenance rather than by
recomputation — every replayed event carries the epoch at which it was computed,
that epoch survives further replays, and every retained stage, the answer's
caveat and the stage badge all say what was retained and from when. Computed
result, not evidence: nothing about the science changes, and no gate is
re-decided on a follow-up (a gate's verdict travels with the state it was
decided on, and a `require_interface_site` condition from the first request
stays in force for the session). Routing is biased toward re-running: a question
naming a disease or tissue outside the retained context, a symbol the scan never
measured, or anything unrecognised gets a full run and is told so.
`tests/test_session_followup.py` (37 tests) holds all of that in place; suite
green at 382 passed / 2 xfailed.
Alternatives rejected: (a) caching keyed on the question string — identical
questions are rare and near-identical ones are the dangerous case; (b) serving
retained state unlabelled, which is fast and indistinguishable on screen from a
fabricated result; (c) letting the model decide whether a follow-up is cheap,
which puts an unbounded classifier in front of "is this the same cohort?".
Reversible: yes — the session id is optional on every endpoint, and `?q=` with
no session is the single-shot path unchanged.

## 2026-08-16 — Committing straight to `main`, per explicit per-request ask, recorded so the rule and the practice stop silently disagreeing
Decided by: Vraj
Why: `CLAUDE.md` says "Work on a branch. Never commit or push to `main` unless
explicitly asked in that request." The eight most recent commits on `main`
(`1f6cf7a` through `a20ddf5`, 2026-08-15 to 2026-08-16, all authored by vraj
<vrajpatel00222@gmail.com>, no merge commits interleaved — confirmed via
`git log --format`) went straight to `main`. That is the rule's own stated
exception, invoked eight times running, not a violation of it — but nothing
recorded that the exception had fired repeatedly, so reading `git log` against
`CLAUDE.md` alone looks like a broken rule rather than a rule correctly
applied. Three pressures made per-request direct commits the right call
instead of branch-and-PR discipline for this stretch of work: a demo
deadline, one operator working solo on this half of the pipeline (no second
reviewer a branch would protect against), and the user re-confirming the
exception per request rather than it being a standing grant.
Alternatives rejected: (a) branch-and-PR for every commit regardless of who
asked — the team's own workflow already uses PRs for shared/reviewed work
(see `Merge pull request #16/#17/#18` immediately before this run in
`git log`); adding that ceremony to one operator's own request-by-request
work in the demo's final stretch would not have caught anything a review
would, since there was no second reviewer in the loop, and would have slowed
the one thing the deadline was pressuring; (b) leaving the rule as stated and
the practice unrecorded — that is exactly the silent disagreement this entry
exists to close.
Reversible: yes — go back to branch + PR for solo work too, or stop asking
per-commit and grant a standing exception instead. Either only changes
practice going forward; it does not change what already happened on `main`.

## 2026-08-15 — An accession must be verified to name the right protein, not just to resolve
Decided by: Amir
Scope: every place the pipeline turns a gene name into a sequence. Supersedes
nothing; adds a check that did not exist.
Why: `scripts/calibrate_structure.py` carried `MED23 = "O75448"` — MED24, an
adjacent subunit of the same Mediator tail module, 989 aa against MED23's 1368.
The ELK1–MED23 control docked the ELK1 motif onto MED24 and scored the result
against MED23 numbering from 9F6Y. The file verified the ELK1 half against the
primary paper with pinned line numbers and never checked the partner accession
at all: it verified transcribed *values* and assumed the accession named the
*protein*. Nothing downstream could catch it — MED24 is real, reviewed, a
plausible length, and appears in Mediator structures. Evidence: UniProt gives
O75448 = MED24_HUMAN 989 aa and Q9ULK4 = MED23_HUMAN 1368 aa; 9F6Y chain A is
Q9ULK4 (`_struct_ref`, auth numbering 1:1 with UniProt); our own contact
detector recovers 7/7 published pocket residues from the deposited 9F6Y
coordinates, so the geometry was never the problem. Recorded in
`team/FINDINGS_ELK1_CONTROL.md` and `SOURCES.md`.
The rule: an accession used as a model input must be checked against two
independent facts — the gene symbol UniProt reports for it, and its presence in
the cross-references of the structure the ground truth is numbered against.
Either alone can pass by accident: MED24 shares 10 of MED23's 13 PDB
cross-references, because both sit in the same whole-Mediator depositions.
Alternatives rejected: (a) checking sequence length against a transcribed
constant — brittle across isoform updates and it would not have caught a
same-length paralogue; (b) trusting `downloads/seqs.json` and reviewing it by
eye — a file written once by hand is exactly what carried the error through a
GPU run and a day of analysis.
Reversible: yes — it is a refusal in one function, `verify_accession`. But a
result produced without it is not reversible, which is the point.

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
