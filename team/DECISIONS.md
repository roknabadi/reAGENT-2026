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

## 2026-08-15 — Coordinate through `team/`, not chat
Decided by: Vraj
Why: judging weights inspectability; decisions made in chat leave no trace in
the repo. Per-person status files avoid merge conflicts; shared logs are
append-at-top for the same reason.
Alternatives rejected: an issue tracker (agents can't write to it as cheaply);
one shared status file (conflicts on every push).
Reversible: yes — the files are plain Markdown, delete the directory.
