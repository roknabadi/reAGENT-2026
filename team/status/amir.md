# Amir — agent workflow / target prioritization / Proto (@roknabadi)

Newest on top. Format: ../README.md

## 2026-08-15 (2) — Amir
Did: TASKS.md **#2 `demo.json`** on `feature/demo-json-contract`. One
self-contained file per run, `reagent-agent export-demo <run_id>`, also emitted by
`reagent-agent demo`. Contract written up in `docs/DEMO_JSON.md` with the shape
for all six band-3 screens. Two guarantees I have tested: every top-level key is
always present (missing data is `null`/`[]`, so screen #9 never needs a
existence check — verified against a run stopped at the checkpoint with no
structure, report, or experiment), and rows are denormalised so no screen joins
arrays. `candidates[]` and `rejected[]` are the *same* row shape, so one render
function does both. `compounds` is emitted with `poses: []` so screen #13 can be
built before the docking run exists. 92 tests pass; fixture file is ~46 KB.
Next: #3 model adapter is assigned to me in TASKS.md but Vraj's status says he is
taking it next — Vraj, say which of us drops it and I will move to #7. #4 ranking
weights is waiting on Kevin.
Blocked: **Vraj to confirm or correct the `demo.json` shape** — it blocks UI
#9–#14, so it is the thing to look at first. Four open questions at the bottom of
`docs/DEMO_JSON.md`; the pose fields are a guess at Vina's output and yours wins.
**Kevin for #4** (the 25/25/20/15/10/5 mapping) and for the DepMap contexts that
unblock #1 → #7, #8, #15. Kevin has not posted yet, so everything downstream of
real numbers is still on fixtures.

## 2026-08-15 — Amir
Did: built the agent workflow in `src/reagent_workflow` (branch
`feature/agent-workflow`): INGEST → GATE → SCORE → HERO_CHECKPOINT → STRUCTURE →
NEXT_EXPERIMENT → COMPLETE, filesystem-resumable under `runs/<run_id>/`, typed
end to end, with `SOUL.md` as the agent constitution. Gates reject broadly
essential genes and unsupported Mediator links with written reasons; missing
evidence scores 0 and lowers completeness without redistributing its weight.
Proto requests compile against the installed contracts (`Boltz2Input`,
`ESMFold2Input`, `Complex`, `TOOL_MAP`) — Boltz2 for the complex, ESMFold2 for
monomers only. Traces export as **opentraces** JSONL and are validated by the
installed BenchFlow 0.6.8 itself. 65 tests pass. Nothing pushed, no Modal spend,
no trace uploaded.
Next: swap the synthetic fixture for a real public candidate bundle (needs
Kevin's DepMap selectivity output and Andrey's Mediator interaction records in
the `InputBundle` shape in `src/reagent_workflow/ingest.py`).
Blocked: none for the software. **Gates 1–4 in `team/CHECKPOINTS.md` are still
OPEN, so no real hero target has been selected or modelled.** Everything
demonstrated so far runs on clearly labelled synthetic fixtures and carries no
scientific weight. Need Kevin + Andrey on gate 1 (TF shortlist) and Andrey on
gate 2 (Mediator connection) before this workflow is pointed at real candidates.
