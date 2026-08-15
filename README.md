# re:AGENT 2026 clean-room workspace

This directory is intentionally isolated from Therna and BioReasonRNA. Use only
public sources, public models, event-provided services, and code written for the
hackathon.

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

