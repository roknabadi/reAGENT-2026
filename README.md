# re:AGENT 2026 — agentic target discovery and drug prioritization

An agentic pipeline that takes a disease or biological state to a ranked target,
a druggable site, and the next experiment worth running:

```text
disease / biological state → candidate target discovery → quantitative ranking
→ specificity and therapeutic window → druggable site or mechanism
→ structural evaluation → small-molecule screening → next validation experiment
```

A candidate is a **target** and its **interaction partner**. Gates, scoring,
structural modelling, and the experiment generator operate on that pair and use
free-text class labels only to phrase their output, so changing target class
means supplying different evidence, not editing the agent. TF–Mediator is one
worked example — the first case the workflow was exercised on — and ELK1–MED23
is the calibration control, the pair with a published answer that the structural
stage is measured against (`team/FINDINGS_ELK1_CONTROL.md`). Neither is the
scope.

**Proteins, not RNA.** The pipeline reasons about protein–protein interfaces and
small molecules that occupy them.

Where things are:

| | |
|---|---|
| project definition and scientific endpoint | [`PROJECT.md`](PROJECT.md) |
| the pipeline as a product | [`docs/PIPELINE.md`](docs/PIPELINE.md) |
| setup, branching, testing, pull requests | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| who is doing what, decisions, sign-off gates | [`team/`](team/README.md) |
| agent comparisons and trace publication | [`benchflow/README.md`](benchflow/README.md) |
| the agent's constitution | [`SOUL.md`](SOUL.md) |

**Clean room.** This directory is intentionally isolated from Therna and
BioReasonRNA. Use only public sources, public models, event-provided services,
and code written for the hackathon.

## Agent workflow: candidates → hero hypothesis → structure → next experiment

`src/reagent_workflow` is the orchestrated agent. It runs

```text
BIORISK → INGEST → GATE → SCORE → HERO_CHECKPOINT → STRUCTURE
→ NEXT_EXPERIMENT → COMPLETE
```

and stops at the hero checkpoint until a named human approves. The filesystem is
the source of truth: every run lives in `runs/<run_id>/` and resumes from disk
with no conversation history. Each stage loads only the `SOUL.md` rules it
needs.

### Structural evaluation: Boltz2, over replicate seeds

**The pipeline runs Boltz2 only by default.** It is the model Proto prefers for
complexes because it predicts them explicitly, and a second PDB-trained model
agreeing with it is weak evidence — they share training data and fail together
on the same kinds of interface. With one interface model there is no consensus
between models to report, and `compare_models` does not claim one.

```bash
reagent-agent structure run <run_id>      # after approval
# == structure (consistent) ==
# - boltz2 reports interface confidence ipTM 0.66.
```

Two optional models are off by default in `RunConfig`:

- `enable_alphafold2` — a second opinion on the interface. Agreement is recorded
  as agreement, never as confirmation.
- `enable_esmfold2_monomer` — per-chain folding check. It is a single-sequence
  model and **never votes on the interface**, whether it runs or not: counting
  its opinion on something it was never shown would manufacture consensus rather
  than measure it.

What is compared by default is **replicates of one model**
(`structure_replicates`, default 3), judged on **overlapping confidence
intervals** rather than point estimates — two runs of a stochastic model differ,
and the interval is what separates a real disagreement from replicate noise.

### Interface consensus over the ensemble

Replicate structures are reduced to residue contacts and clustered, and **every
cluster is scored and kept** as an `InterfaceHypothesis` — its samples, support,
segment and compactness, agreed partner residues, interface-specific confidence,
and its own blockers. The verdict is one of three:

| status | meaning | next action |
|---|---|---|
| `converged` | one cluster cleared every criterion | `build_search_site` |
| `ambiguous` | nothing converged, but a localized hypothesis survived | `sample_more` |
| `refused` | nothing defensible | `abstain` |

**Only `converged` can generate a docking site.** An `ambiguous` consensus docks
only when a named person picks a hypothesis and signs for it, and the site
records who did.

The middle state exists because of a measurement, not a hunch. On the
ELK1–MED23 control, 3 of 15 samples recovered the published interface and each
time the sample that found it was in a **minority** — the cluster that won the
vote was wrong. A majority rule over an ensemble can report the wrong interface
while holding the right one, so losing clusters keep the residues that make them
checkable instead of being reduced to a list of sample names. Nothing selects on
confidence: it is recorded per hypothesis and acted on nowhere.

### Run report — prompt, research, conclusions

```bash
reagent-agent run-report <run_id> --print
```

One document answering what the agent was asked, what it examined, and what it
concluded: the objective and the rules in force at each stage, the sources and
contradicting evidence, the candidates scored, the structural consensus with its
intervals, every rejection with the gate that fired, the human decisions, the
proposed experiment, and what the run does not claim. It is reconstructed from
the run directory, so every number in it traces to an artifact on disk rather
than to a narration written alongside the run.

### Biosecurity gateway

Every run is screened before any other stage. Requests that would create
hazardous capability — increasing transmissibility, virulence, host range,
immune escape, or therapeutic resistance; producing select agents; weaponization
— are **refused before `INGEST`**, and no candidate or evidence is written.
Dual-use and non-medical requests stop at a human checkpoint instead.

Countermeasure development is explicitly permitted: antivirals, antibacterials,
and host-directed therapy are the medical use this pipeline serves, and a gate
that blocked them would be broken rather than cautious.

```bash
reagent-agent biorisk check --text "..."   # 0 permitted, 6 review, 7 refused
reagent-agent biorisk show <run_id>        # the recorded assessment
```

A refused run still leaves `biosafety/assessment.json` with the reason and the
matched text — a refusal is an accountability record, not a silence. The policy
is hashed so the agent cannot weaken its own gate. It is a first-pass screen,
not a substitute for institutional biosafety review; see `skills/biorisk/`.

### Demo

```bash
source ./activate.sh
python -m reagent_workflow.cli demo demo-001 --by "your-name"
```

The package also installs `agent` and `reagent-agent` console scripts. Prefer
`reagent-agent` or the `python -m` form: `agent` is a common binary name (Cursor
ships one) and may be shadowed on your `PATH`.

That single command runs the whole loop on synthetic fixtures: gates and
ranking, the hero checkpoint, approval, a cached Boltz2 structure, the next
experiment, the final report, and a BenchFlow JSONL trace it then validates with
the installed BenchFlow.

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
reagent-agent structure run demo-002            # cached Boltz2, no Modal
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

- Boltz2 predicts the target–partner complex, and is the only interface model
  that runs by default. ESMFold2 checks monomers when enabled and is never used
  as an interface predictor.
- Agreement — between replicates, or between models when a second is enabled —
  is recorded as agreement, not as validation.
- An ensemble that does not converge yields `ambiguous` or `refused`, never a
  site. A minority hypothesis is retained for inspection, not promoted.
- Missing evidence scores zero and lowers completeness; its weight is never
  redistributed.
- Broadly essential genes are rejected, and so is any target–partner link with
  no supporting assay (`interaction_support`) or no mapped interacting region
  (`interacting_region_mapped`), with the reason written to
  `decisions/rejections.jsonl`.
- Live Modal dispatch is off by default and needs both an approved checkpoint
  and an explicit `--allow-live-modal`.
- The bundled fixtures are synthetic test data, labelled as such in every
  artifact they touch. They carry no scientific weight.

Traces are written locally and never uploaded; publication needs explicit
approval per [`benchflow/README.md`](benchflow/README.md).

### Why `transcription_factor` still appears in some artifacts

Two boundaries keep the older TF–Mediator field names on purpose:

- the **frozen BenchFlow task** `reagent/tf-mediator-hero` — its verifier reads
  `hero.transcription_factor` and `hero.mediator_subunit`, and we are scored on
  that task, so its schema does not get renamed under it. The task id is
  recorded in every `runs/<run_id>/traces/trace_manifest.json`. See
  [`benchflow/README.md`](benchflow/README.md).
- **`demo.json`**, which dual-emits `transcription_factor` / `mediator_subunit`
  as *deprecated aliases* beside `target_gene` / `partner_gene` while the UI is
  being built against them. See [`docs/DEMO_JSON.md`](docs/DEMO_JSON.md).

Everywhere else the vocabulary is target and partner.

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
cd path/to/reAGENT-2026
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
- Paperclip skills: `.claude/skills/paperclip` and `.agents/skills/paperclip`
- Proto authoring skills: `.claude/skills` and `.agents/skills`
- Claude project MCP configuration: `.mcp.json`

## Authenticate

Each of these opens a browser or needs a personal credential, so each person
does them once on their own machine:

1. `paperclip login`
2. `modal setup` — then `proto-tools deploy --apps boltz2 --test` to confirm GPU
   access
3. Create an API key at <https://biohub.ai> and put it in `.env`
4. Sign in at <https://hackathon.bnchdev.org>
5. Start `claude` here, run `/mcp`, and authenticate the Paperclip server

Never paste keys into source files or commit `.env`. Who has done what is in
[`team/status/`](team/README.md), not here.
