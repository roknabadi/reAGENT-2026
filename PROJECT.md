# Agentic discovery of disease-specific TF–Mediator vulnerabilities

## Goal

Build a Track A **AI Scientist** workflow that uses public disease data to find
a selective transcription-factor dependency, connect it to a Mediator
interaction, and advance the strongest hypothesis toward structure-based drug
discovery.

Mediator links enhancer-bound transcription factors to promoter and RNA
polymerase II machinery. Restricting discovery to this system keeps the search
biologically focused and the structural hypotheses testable.

## Positive controls

- **ELK1–MED23:** a structurally characterized TF–Mediator interaction.
- **ELF3–MED23:** a disease-relevant interaction in HER2-driven epithelial
  cancers.

These are validation examples, not predetermined targets.

## Workflow

1. Mine public dependency, cancer/omics, normal-tissue, and literature data for
   disease or cell states selectively dependent on a TF program.
2. Identify known or plausible Mediator interactions for the leading TFs.
3. Select one hero disease–TF–Mediator hypothesis using dependency strength,
   specificity, normal-cell proxies, evidence quality, and tractability.
4. Retrieve or model the interface and reject unsupported structural claims.
5. Define a defensible pocket and run a bounded virtual screen for candidate
   chemical starting points.

Initial setup uses Claude Code and Tamarind. Paperclip, Proto, or Modal can be
used when they materially improve the experiment without becoming architectural
requirements.

## Endpoint

```text
disease/cell state → selective TF dependency → Mediator interface
→ structural hypothesis → candidate compounds
```

The result is a public, evidence-backed, falsifiable hypothesis—not a claim of
binding, safety, efficacy, or experimental validation.
