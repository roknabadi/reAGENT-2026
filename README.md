# re:AGENT 2026 clean-room workspace

This directory is intentionally isolated from Therna and BioReasonRNA. Use only
public sources, public models, event-provided services, and code written for the
hackathon.

The project definition and scientific endpoint are in [`PROJECT.md`](PROJECT.md).
Setup, branching, testing, and pull-request instructions are in
[`CONTRIBUTING.md`](CONTRIBUTING.md). Who is doing what, decisions, blockers,
and the human sign-off gates are in [`team/`](team/README.md).
Reproducible agent comparisons and trace publication are documented in
[`benchflow/README.md`](benchflow/README.md).

## Collaborating

After cloning, run the setup script, configure your own Tamarind key, and start
Claude Code:

```bash
./scripts/setup.sh
# Add your personal TAMARIND_API_KEY to .env
source ./activate.sh
claude
```

Shared instructions are in `CLAUDE.md`; the Tamarind connection is in
`.mcp.json`. No credentials are stored in Git.

## Start a shell

```bash
cd /Users/amir/Documents/reAGENT-2026
source ./activate.sh
./scripts/check_readiness.sh
```

After activation, these commands are available:

- `claude` — Claude Code (already installed on the Mac)
- `paperclip` — biomedical literature and database CLI
- `modal` — cloud execution for Proto tools
- `benchflow` / `bench` — BenchFlow CLI
- `python` — isolated Python 3.12 with `proto-language` and `proto-tools`

## Installed components

- Proto source: `vendor/proto-language` (including the `proto-tools` submodule)
- BenchFlow source: `vendor/benchflow`
- Proto, Proto Tools, Modal, and Paperclip: `.venv`
- BenchFlow CLI: `tools`, exposed through `bin`
- Sundial Desktop: `apps/Sundial.app`
- Paperclip skills: `.claude/skills/paperclip` and `.agents/skills/paperclip`
- Proto authoring skills: `.claude/skills` and `.agents/skills`
- Claude project MCP configuration: `.mcp.json`

## Authentication still requiring Amir

These steps open a browser or require a personal/event credential and therefore
cannot be completed unattended:

1. `paperclip login`
2. `modal setup` after claiming the event credits on Saturday
3. Visit <https://biohub.ai>, create a personal API key, and put it in `.env`
4. Visit <https://hackathon.bnchdev.org> and sign in with Google
5. Start `claude` in this directory, run `/mcp`, and authenticate the Paperclip
   and Benchling servers
6. Open `apps/Sundial.app` once and complete its sign-in if you plan to use it

Never paste keys into source files or commit `.env`.

## Proto / PARADE starting point

The installed Proto checkout contains native PARADE constraints for:

- 5' or 3' UTR activity
- on-target/off-target cell specificity
- 3' UTR stability
- differentiable gradient optimization

Supported public PARADE cell lines are MDA-MB-231, HepG2, Jurkat, SW480,
NALM6, and PA-1 (3' UTR only). Model weights are fetched from a pinned public
PARADE commit and checksum-verified by Proto Tools.

Inspect these files first:

- `vendor/proto-language/proto_language/constraint/rna_expression/`
- `vendor/proto-language/proto-tools/proto_tools/tools/sequence_scoring/parade/README.md`
- `vendor/proto-language/examples/scripts/toy.py`

## Event logistics

- Arrive Saturday by **9:15 AM**; check-in opens at **8:30 AM**.
- Bring laptop, charger, adapters, and photo ID.
- Claim Modal and sponsor credits during the Day 1 lightning talks.

## Hackathon build: Dependency Scout → Proto Screen

The first executable slice lives in `src/dependency_scout`. It ranks public
DepMap disease-selective dependencies, produces a bounded evidence plan, and
compiles structural handoffs against Proto's native Vina input model.

```bash
source ./activate.sh
python -m unittest discover -s tests -v

# Clearly labeled synthetic smoke test (never scientific evidence)
mkdir -p outputs
dependency-scout discover --gene-effect tests/fixtures/gene_effect.csv \
  --models tests/fixtures/models.csv --context Lung --synthetic \
  --output outputs/demo_candidates.json
dependency-scout plan outputs/demo_candidates.json \
  --output outputs/demo_evidence_plan.json

# Compile a public c-Abl/imatinib smoke example to Proto's native Vina contract
dependency-scout validate-proto examples/proto_screen_spec.smoke.json \
  --output outputs/proto_smoke.json
```

See `docs/ARCHITECTURE.md`. Real analysis requires official public DepMap
files; the included fixture only tests behavior.

## Agent workflow: candidates → hero hypothesis → structure → next experiment

`src/reagent_workflow` is the orchestrated agent. It runs

```text
INGEST → GATE → SCORE → HERO_CHECKPOINT → STRUCTURE → NEXT_EXPERIMENT → COMPLETE
```

and stops at the hero checkpoint until a named human approves. The filesystem is
the source of truth: every run lives in `runs/<run_id>/` and resumes from disk
with no conversation history. The agent's constitution is [`SOUL.md`](SOUL.md);
each stage loads only the rules it needs.

### Demo

```bash
source ./activate.sh
python -m reagent_workflow.cli demo demo-001 --by "your-name"
```

The package also installs `agent` and `reagent-agent` console scripts. Prefer
`reagent-agent` or the `python -m` form: `agent` is a common binary name (Cursor
ships one) and may be shadowed on your `PATH`.

That single command runs the whole loop on synthetic fixtures: gates and
ranking, the hero checkpoint, approval, a cached Boltz2/ESMFold2 comparison, the
next experiment, the final report, and a BenchFlow JSONL trace it then validates
with the installed BenchFlow.

### Step by step

Shown with the `reagent-agent` script; `python -m reagent_workflow.cli` takes the
same arguments.

```bash
reagent-agent init demo-002                     # defaults to the fixture bundle
reagent-agent run demo-002                      # runs to the hero checkpoint, stops
reagent-agent status demo-002                   # state, ranking, manifest drift
reagent-agent checkpoint show demo-002          # the case for and against
reagent-agent checkpoint resolve demo-002 demo-002-hero \
    --decision approve --by "your-name"
reagent-agent structure validate demo-002       # compile Proto inputs, run nothing
reagent-agent structure run demo-002            # cached Boltz2 + ESMFold2, no Modal
reagent-agent experiment demo-002               # next experiment + self-improvement
reagent-agent report demo-002                   # final_report.md
reagent-agent trace demo-002 --summary          # internal event counts
reagent-agent trace export-benchflow demo-002   # benchflow_trace.jsonl + manifest
reagent-agent trace validate-benchflow demo-002 # validated by BenchFlow itself
reagent-agent resume demo-002                   # continue from whatever is on disk
reagent-agent evidence show demo-002 EV-CONTRA-A1   # rehydrate compacted detail
```

Use `--input path/to/bundle.json` on `init` to supply your own candidates; the
accepted shape is `InputBundle` in `src/reagent_workflow/ingest.py`.

### What it does and does not claim

- Boltz2 predicts the TF–Mediator complex; ESMFold2 checks monomers only and is
  never used as an interface predictor.
- Model agreement is recorded as agreement, not as validation.
- Missing evidence scores zero and lowers completeness; its weight is never
  redistributed.
- Broadly essential genes and unsupported Mediator links are rejected, with the
  reason written to `decisions/rejections.jsonl`.
- Live Modal dispatch is off by default and needs both an approved checkpoint
  and an explicit `--allow-live-modal`.
- The bundled fixtures are synthetic test data, labelled as such in every
  artifact they touch. They carry no scientific weight.

Traces are written locally and never uploaded; publication needs explicit
approval per [`benchflow/README.md`](benchflow/README.md).
