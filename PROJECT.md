# Agentic discovery of disease-specific TF–Mediator vulnerabilities

## Goal

Build a Track A **AI Scientist** workflow that starts from public disease data,
identifies a selective transcription-factor dependency, connects it to a
Mediator interaction, and advances the strongest hypothesis toward
structure-based drug discovery.

## Why Mediator

Mediator links enhancer-bound transcription factors to the promoter and RNA
polymerase II machinery. Restricting discovery to this system keeps the search
biologically focused and makes it possible to judge structural hypotheses
rigorously.

## Known examples and positive controls

- **ELK1–MED23:** a structurally characterized TF–Mediator interaction with a
  defined ELK1 interface and associated allosteric changes in MED23.
- **ELF3–MED23:** a disease-relevant interaction in HER2-driven epithelial
  cancers for which disruption of the ELF3–MED23 interaction has shown
  transcriptional and antitumor effects.

These are validation examples of the kind of vulnerability the system should
be capable of recovering. They are not predetermined discovery targets.

## Workflow

### 1. Discovery

Mine public dependency screens, cancer and omics datasets, normal-tissue data,
and primary literature to find diseases or cell states unusually dependent on
a particular transcription factor or narrow transcriptional program.

### 2. Mediator filtering

Determine whether the leading transcription factors have known or plausible
interactions with Mediator. Rank the resulting disease–TF–Mediator hypotheses
by evidence quality rather than by textual plausibility alone.

### 3. Convergence

Nominate one strongest **hero indication–TF–Mediator pair** using explicit
criteria:

- dependency strength and prevalence;
- disease or cell-state specificity;
- normal-cell evidence as a safety proxy, not a safety claim;
- quality and independence of supporting evidence;
- structural confidence and tractability.

The system must also record contradictions, missing evidence, and reasons for
rejecting otherwise attractive candidates.

### 4. Structural hypothesis

Model or retrieve the selected TF–Mediator interface and evaluate its quality.
Use known cases such as ELK1–MED23 as validation controls where appropriate.
Do not invent an interface, binding pocket, or residue mapping when public
evidence is insufficient.

### 5. Drug-discovery hypothesis

Identify a defensible pocket or interface region and run a bounded virtual
screen to generate candidate chemical starting points. Preserve compound,
structure, configuration, model, and scoring provenance. Treat docking and
model scores as computational prioritization, not evidence of binding.

## Initial execution environment

- Claude Code for repository-level reasoning and orchestration.
- Tamarind for accessible structural modeling and screening workflows.
- Paperclip may be used for public literature and biological evidence.
- Proto or Modal may be used when a specific model or additional compute
  materially improves the experiment.

The scientific contracts and repository layout should remain independent of
any one service so components can be changed without rewriting the project.

## Hackathon endpoint

Produce one coherent, evidence-backed story:

```text
disease or cell state
        ↓
selective TF dependency
        ↓
Mediator interaction
        ↓
structural hypothesis
        ↓
candidate chemical starting points
```

The endpoint is a falsifiable target and screening hypothesis with public
provenance—not a claim of experimental validation, therapeutic efficacy,
binding, selectivity, or safety.
