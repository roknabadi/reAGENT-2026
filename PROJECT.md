# Agentic discovery of disease-specific TF–Mediator vulnerabilities

## In two lines

Find a transcription factor that a specific disease depends on abnormally,
confirm it has a *documented physical contact point* with one Mediator subunit,
then computationally screen small molecules that could block that exact contact.

## Why this shape

Cells use TFs to switch genes on and off, and TFs relay that signal through the
Mediator complex to RNA polymerase II. In cancers, one TF often gets stuck "on"
and the disease cell becomes dependent on it — transcriptional addiction. TFs
resist direct drugging because their DNA-binding surface is flat and
featureless, so the target is instead the specific spot where the TF physically
touches Mediator.

The load-bearing word is **documented**. The agent's job is to prove from
literature and public databases that a TF–Mediator contact is real and mapped,
not merely correlated with the disease, *before* anyone models it in 3D or
docks against it. Correlation passing itself off as contact is the failure mode
this project exists to catch.

## The shape of a passing argument

Illustrative only — the agent must derive and verify this chain itself, not
assume it. Every link is a claim the pipeline has to source or reject.

| Link | Question the agent must answer with evidence |
|---|---|
| Disease | Is there a disease/cell state with a *selective* dependency? |
| TF | Is the dependency real and specific — not overexpression, not pan-essential? |
| Contact | Is a TF↔Mediator-subunit interaction physically mapped, with the interacting region identified? |
| Interface | Is there a structure, or a credible model, of that region? |
| Pocket | Is there a definable, evidence-bounded site to screen against? |

A worked shape, using a widely discussed case: a childhood cancer whose cells
cannot survive without one amplified TF; that TF is undruggable at its
DNA-binding face; but its activation domain has been reported to engage a
Mediator subunit, and *that* handshake — unlike the TF itself — may present
something a small molecule can wedge into. Whether each of those links actually
holds is exactly what the agent has to establish. **Do not carry this example
into a result. It is a template for the argument, not evidence.**

Positive and negative controls are below; run both before trusting a new
candidate.

## Goal

Build a Track A **AI Scientist** workflow that uses public disease data to find
a selective transcription-factor dependency, connect it to a Mediator
interaction, and advance the strongest hypothesis toward structure-based drug
discovery.

Mediator links enhancer-bound transcription factors to promoter and RNA
polymerase II machinery. Restricting discovery to this system keeps the search
biologically focused and the structural hypotheses testable.

## Controls

**Positive — the agent should find these.**

- **ELK1–MED23:** a structurally characterized TF–Mediator interaction.
- **ELF3–MED23:** a disease-relevant interaction in HER2-driven epithelial
  cancers.

These are validation examples, not predetermined targets.

**Negative — the agent should reject these, out loud, with a reason.**

- A **pan-essential** TF: broadly required across cell lines, so a real
  dependency but not a selective one.
- A TF that is **overexpressed** in a disease with no dependency signal.
- A TF–Mediator pair supported only by **co-expression or a whole-protein
  pull-down**, with no interacting region mapped.

A run that only ever says yes has not demonstrated judgment. The rejection
trace — which gate fired, on what evidence — is the part that shows the
reasoning is real, and it is what `GateResult` in
`src/dependency_scout/models.py` exists to record.

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
