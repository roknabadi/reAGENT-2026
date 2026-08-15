# re:AGENT 2026 clean-room workspace

This directory is intentionally isolated from Therna and BioReasonRNA. Use only
public sources, public models, event-provided services, and code written for the
hackathon.

The project definition and scientific endpoint are in [`PROJECT.md`](PROJECT.md).
Setup, branching, testing, and pull-request instructions are in
[`CONTRIBUTING.md`](CONTRIBUTING.md).

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
