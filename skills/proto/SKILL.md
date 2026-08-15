---
name: Proto
description: >
  Runs 140+ computational-biology tools (structure prediction, protein/RNA/DNA
  design, docking, inverse folding, sequence & structure alignment, genomic
  scoring, database retrieval) through the Proto CLI and typed Python SDK by Evo
  Design. Use when the user asks to predict or design a protein, RNA, or DNA
  sequence or structure, fold a sequence, dock a ligand, score variants, run a
  bioinformatics tool, or build a generative-biology pipeline. No API key for
  local, open-weight tools.
---

# Proto

One interface to 140+ computational-biology tools (Evo Design), each running in its own auto-built environment. Drive it from the sandbox shell: the `proto-tools` CLI to discover tools, its typed Python API to run one. No account and no API key for local, open-weight tools.

## Setup

Python 3.10+. No credentials for local, open-weight tools.

1. Install if missing (idempotent; it is a git install, no PyPI yet):
  `python3 -c "import proto_tools" 2>/dev/null || pip install "proto-tools[mcp] @ git+https://github.com/evo-design/proto-tools.git"`
2. For constraint-based sequence design (the propose-score-refine layer), also:
  `python3 -c "import proto_language" 2>/dev/null || pip install "git+https://github.com/evo-design/proto-language.git"`
3. The first call to any tool builds an isolated micromamba env for it under `~/.proto/` (cached after; roughly 30-60s cold, sub-second warm). This is normal, not a hang.

## Before you start

Discover offline with the CLI; do not guess tool keys or symbol names.

- `proto-tools agent-context` prints the primer: the `Input -> Config -> run_*() -> Output` pattern plus every discovery verb.
- `proto-tools catalog` lists tools grouped by category; `proto-tools list --cpu` shows the ones that run without a GPU.
- `proto-tools signature <tool>` gives the exact imports, run-function, and required fields; `proto-tools example-input <tool>` gives a minimal valid input; `proto-tools access <tool>` reports whether the weights are open, hf-gated, or request-only.

## Known gotchas (verified 2026-08-14)

These bite on the first command, so read them before writing any:

- **First run of a tool builds its env (roughly 30-60s), then it is cached.** A one-time micromamba setup runs on first use of each tool; warm re-runs are sub-second. Do not kill it as a hang.
- **Tool keys are `<model>-<action>`** (`esmfold-prediction`, `viennarna-prediction`), not `esmfold`. A rejected key prints near matches; `proto-tools list` resolves one you only half know.
- **Symbol names are not guessable from the key.** `mafft-align` exports `MafftInput`, not `MafftAlignInput`. Always run `proto-tools signature <tool>` before importing, rather than inventing the class name.
- **CPU by default; heavy models need a GPU.** ViennaRNA, sequence and structure alignment, ORF prediction, mutagenesis, gene annotation, and database retrieval run on CPU in the sandbox. Large models (Evo2, AlphaFold2/3, ESMFold, ESM3, Boltz2, RFdiffusion) need a GPU and only run when the user has Modal set up (`device="modal"`), so prefer a CPU tool unless the user asked for one of these and has Modal.
- **Gated weights need `HF_TOKEN`.** A few tools (ESM3, AlphaFold3, AlphaGenome) require accepting a license on HuggingFace and `export HF_TOKEN=...` first; `proto-tools access <tool>` flags these as `hf-gated`.
- **If you drive Proto's own MCP server instead of Python:** `run_tool` takes `tool_key` and `inputs` (not `tool_id`/`input`), and it DEFAULTS to `run_on="modal"`; pass `run_on="local"` for CPU tools or it errors on a missing Modal environment. Valid devices are `local`, `modal`, `proto`.

## Working style in a workspace

- Discover with the CLI, then run the smallest CPU tool that answers the question through the Python API. For example, fold an RNA sequence:

  ```python
  from proto_tools.tools.structure_prediction.viennarna.viennarna import (
  ViennaRNAInput,
  run_viennarna,
  )
  out = run_viennarna(ViennaRNAInput(sequences=["GGGAAACCC"]))
  print(out.results[0].structure, out.results[0].mfe)  # (((...))) -1.2
  ```
- Write the synthesis into the workspace files (e.g. `findings.tex`) as normal, reviewable edits, not left in tool output.
- Record the tool key, model, and inputs beside every result so it is reproducible, and cite the method by the DOI from `proto-tools citation <tool>`.

---

*Reference below adapted from the official Proto docs (`proto-tools agent-context`, proto.evodesign.org). Run `proto-tools agent-context` for the current version.*

## The one pattern every tool follows

```
Input -> Config -> run_*() -> Output
```

`Config` is optional (the defaults are supplied). Every `Output` carries `tool_id`, `execution_time`, `success`, and `errors`, plus tool-specific `results`. Biological coordinates are 1-indexed and inclusive.

## Discovery CLI

| Verb | What it gives you |
| --- | --- |
| `proto-tools list [--cpu/--gpu] [--category C]` | Registered tools, one per line |
| `proto-tools catalog` | Tools grouped by category |
| `proto-tools signature <tool>` | Imports, run-function, and required fields |
| `proto-tools example-input <tool>` | A minimal valid Input |
| `proto-tools schema/input/config/output <tool>` | Field-level model docs and JSON Schema |
| `proto-tools access <tool>` | Weights access: open, hf-gated, or request |
| `proto-tools citation <tool>` | BibTeX and DOI for the method |
| `proto-tools doctor` | Check the environment can build tools and reach Modal |

## Categories (140+ tools)

`structure_prediction`, `structure_design`, `structure_alignment`, `structure_scoring`, `structure_dynamics`, `causal_models`, `masked_models`, `inverse_folding`, `binder_design`, `molecular_docking`, `sequence_alignment`, `sequence_scoring`, `gene_annotation`, `orf_prediction`, `rna_splicing`, `mutagenesis`, `database_retrieval`.

## Remote compute (optional)

Heavy or GPU-only tools can run in the user's own Modal workspace instead of locally: pass `device="modal"` to a run call (or `program.run(device="modal")` in proto-language). Deployment happens on first use and costs GPU time, so only reach for it when the user has Modal configured and has asked for a GPU tool. `proto-tools doctor` reports whether Modal is reachable.
