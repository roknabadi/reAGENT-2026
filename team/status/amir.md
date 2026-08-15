# Amir — agent workflow / target prioritization / Proto (@roknabadi)

Newest on top. Format: ../README.md

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
