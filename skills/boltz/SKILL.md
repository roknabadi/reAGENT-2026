---
name: Boltz
description: >
  Predicts 3D biomolecular structure and binding affinity from sequence with the
  open-source Boltz-2 model. Use when the user asks to fold a protein, model a
  protein complex or protein-ligand pair, predict a structure from a sequence,
  estimate binding affinity, or read pLDDT/pTM confidence for a predicted
  structure. No API key.
---

# Boltz

Boltz-2 (MIT, no API key) predicts the 3D structure of proteins, nucleic acids, and their complexes from sequence, and predicts protein-ligand binding affinity. Drive it from the sandbox with the `boltz` CLI: describe the molecule in a small YAML file, run `boltz predict`, and read the structure and confidence scores it writes.

## Setup

Python 3.10+. No credentials. Boltz is a sandbox (`Bash`) tool.

1. Put the ~4 GB weight cache on the persistent volume and install once:
  `export BOLTZ_CACHE=/workspace/.boltz`
  `command -v boltz >/dev/null || pip install boltz`
  Install plain `boltz` for CPU; `boltz[cuda]` only on an NVIDIA GPU.
2. Verify it runs: `boltz predict --help >/dev/null && echo ok`.

## Known gotchas (verified 2026-08-14, boltz 2.2.1, CPU)

Grounded in real runs on this stack; they bite on the first command:

- **Pass `--accelerator cpu` when there is no CUDA GPU.** The CLI default is `gpu`. Sandboxes are CPU by default; on Apple Silicon Boltz reports MPS available but runs on CPU, which is expected.
- **The first prediction downloads ~4 GB** to `$BOLTZ_CACHE` (conformer weights ~2.3 GB, affinity weights ~1.8 GB, and the CCD dictionary), printing only "Downloading…". It is a one-time cost **only if `BOLTZ_CACHE` is on the persistent volume**, otherwise it re-downloads every run.
- **Keep the first run tiny or it looks hung.** CPU time scales steeply with length and sampling steps. A ~33-residue chain with `msa: empty --recycling_steps 1 --sampling_steps 25` folds in seconds; the **affinity** pass adds minutes on CPU (~5.5 min in testing). Start small, then scale.
- **`--use_msa_server` calls the public MMseqs2 server** (api.colabfold.com) to build a real MSA (verified: it fetches uniref/bfd `.a3m` alignments). A real MSA meaningfully improves accuracy; use `msa: empty` only for a fast smoke test.
- **Output lands under `boltz_results_<stem>/`, not directly in `--out_dir`.** The ranked structure is `<out_dir>/boltz_results_<stem>/predictions/<stem>/<stem>_model_0.{pdb,cif}`; the scores are `confidence_<stem>_model_0.json` beside it; affinity is `affinity_<stem>.json`. Default format is mmcif; pass `--output_format pdb` for PDB.
- **Re-runs skip finished work** unless you pass `--override`.

## Input YAML

Minimal single protein (`msa: empty` is fast single-sequence mode; omit it and pass `--use_msa_server` for accuracy):

```yaml
version: 1
sequences:
  - protein:
      id: A
      sequence: MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQ
      msa: empty
```

Entity types: `protein`, `dna`, `rna`, `ligand` (`smiles:` or `ccd:`). Repeat an `id` as a list (`id: [A, B]`) to make copies, e.g. a homodimer. For protein-ligand binding affinity, name the ligand as the binder:

```yaml
version: 1
sequences:
  - protein:
      id: A
      sequence: MKTAYIAK...
      msa: empty
  - ligand:
      id: B
      smiles: 'CC(=O)Oc1ccccc1C(=O)O'
properties:
  - affinity:
      binder: B
```

## Running

```bash
export BOLTZ_CACHE=/workspace/.boltz
boltz predict inputs/protein.yaml --accelerator cpu --out_dir predictions \
  --output_format pdb --recycling_steps 1 --sampling_steps 25 --override
```

Drop the reduced steps for the accuracy defaults (3 recycling / 200 sampling) once a small run works. Other flags: `--use_msa_server`, `--diffusion_samples N`, `--seed N`. `boltz predict --help` lists them all.

## Reading the output

Report the real numbers from the JSON; never state a structure or score you did not produce:

- `complex_plddt`: mean confidence 0-1 (per-residue pLDDT is the PDB B-factor column, 0-100). > 0.7 confident, < 0.5 low.
- `ptm`: global fold confidence (0-1). `iptm` / `ligand_iptm`: interface confidence for complexes (0 for a single chain); > 0.8 is a reliable interface.
- Affinity: `affinity_pred_value` = log10 IC50 in µM (lower = stronger binder); `affinity_probability_binary` = 0-1 likelihood it binds.

## Working style in this workspace

Write the input to a YAML file (e.g. `inputs/protein.yaml`) as a normal edit, run Boltz, then write the result into `findings.tex` (or the workspace doc) as a reviewable edit: what was folded, the confidence numbers, and any caveat. Leave the predicted `.pdb`/`.cif` in the predictions folder. If pLDDT is low, say so and suggest what would help (a real MSA via `--use_msa_server`, more sampling steps, a better-defined input) rather than presenting a weak model as settled.
