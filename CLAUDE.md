# Project instructions

- Read `PROJECT.md` and `SOURCE_POLICY.md` before changing the project.
- This is a general target-discovery and drug-prioritization pipeline: disease
  or biological state → candidate targets → ranking → specificity and
  therapeutic window → druggable site → structure → screening → next
  experiment. A candidate is a target and its interaction partner. No disease,
  target class, or mechanism is assumed; TF–Mediator is one worked example and
  ELK1–MED23 the calibration control. See `docs/PIPELINE.md`.
- Build the smallest testable end-to-end workflow.
- Keep shared inputs and outputs typed and independent of any one service.
- Use only public data, public models, sponsor services, and code created here.
- Never use Therna or BioReasonRNA code, data, outputs, prompts, schemas,
  credentials, or unpublished results.
- Label synthetic fixtures as tests, not scientific evidence.
- Record external sources in `SOURCES.md`.
- Separate observed evidence, computed results, predictions, and hypotheses.
- Do not claim binding, safety, efficacy, or experimental validation from
  computational results.
- Validate Tamarind inputs before submitting jobs. Ask before paid,
  long-running, batch, or destructive operations.
- Use the frozen BenchFlow task for agent comparisons; do not present ad hoc
  logs as comparable evaluations. Ask before publishing trace artifacts.
- Never commit secrets. Run relevant tests before handoff.
- Coordinate through `team/` — read `team/README.md`, post what you did and any
  blocker to `team/status/<name>.md`, and log decisions with their evidence in
  `team/DECISIONS.md`. Do not proceed past an `OPEN` gate in
  `team/CHECKPOINTS.md`; abstain and post a blocker instead.
- Work on a branch. Never commit or push to `main` unless explicitly asked in
  that request. `git fetch origin` at the start of a session and before each
  push; merge `origin/main` into the branch as soon as it moves.
- A claimed target–partner contact needs a mapped interacting region.
  Co-expression, correlation, or a whole-protein pull-down is not contact — say
  so and reject it rather than passing it downstream. This holds whatever the
  partner is: a Mediator subunit, a kinase substrate, a scaffold.
- Keep the frozen BenchFlow task's field names (`reagent/tf-mediator-hero`,
  `hero.transcription_factor`, `hero.mediator_subunit`) and `demo.json`'s
  deprecated `transcription_factor` / `mediator_subunit` aliases as they are.
  They are compatibility boundaries we are scored on or building a UI against,
  not the project's vocabulary.
