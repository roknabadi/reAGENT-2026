# Agentic target discovery and drug prioritization

A general-purpose pipeline that starts from disease biology and moves
systematically toward testable drug hypotheses.

```text
disease state → target discovery → target ranking → specificity
→ druggable site → structural evaluation → small-molecule candidates
→ next experiment
```

The pipeline is designed to be general across diseases and target classes.
**TF–Mediator interactions are one worked example, not the definition of the
system.** They were chosen first because ELK1–MED23 gives a structurally
characterised positive control to calibrate against, and because
"a documented physical contact point" is a demanding case: if the machinery can
refuse an unmapped contact there, it can refuse one anywhere.

## What the system does

**1. Identify candidate therapeutic targets.** Detect genes, proteins, and
regulatory dependencies associated with a specific disease, subtype, or cell
state, from public genetic, functional, molecular, structural, and literature
evidence.

**2. Rank and prioritize.** Score candidates by dependency strength, disease
relevance, evidence quality, tractability, and confidence — each component
scored separately, with its own definition, and never averaged into a single
opaque number.

**3. Evaluate specificity and therapeutic window.** Compare disease dependence
against normal tissues, normal cell types, and other disease contexts, to
prioritise selective vulnerabilities and reduce likely toxicity.

**4. Determine the druggable mechanism.** Identify the protein domain,
interaction interface, binding pocket, or regulatory site that could actually be
perturbed. A target with no located mechanism does not advance.

**5. Evaluate structural tractability.** Retrieve or predict structures, assess
interface or pocket quality, and decide whether the target suits small-molecule
intervention.

**6. Screen small molecules.** Structure-based virtual screening, ranked by
predicted binding, pose quality, and supporting chemical evidence.

**7. Generate the next testable experiment.** An evidence-backed
target–mechanism–compound hypothesis, plus the experiment that would validate or
reject it.

## Why it is built to refuse

Most of the engineering here is not in finding candidates. It is in throwing
them away for stated reasons.

A pipeline that only ever says yes has demonstrated nothing. The rejection trace
— which gate fired, on what evidence, against which threshold — is what makes
the reasoning inspectable, and it is the part that generalises across target
classes. Three failure modes are rejected out loud by default:

| Rejected | Why it is a trap |
|---|---|
| Broadly essential genes | a real dependency, but not a selective one |
| Overexpression with no dependency signal | correlation presented as causation |
| Association with no mapped interaction site | there is nothing to model or screen against |

The third is the one that generalises furthest. Whether the partner is a
Mediator subunit, a kinase substrate, or a scaffold, "these two proteins
co-purify" is not a druggable mechanism, and a whole-protein pull-down is not a
contact point.

## What is agnostic, and what is configured

The workflow code does not know what a transcription factor is. A candidate
carries a `target_gene` and a `partner_gene`, optionally labelled with a free-text
`target_class` and `partner_class` ("transcription factor", "Mediator subunit",
"kinase", "scaffold"). Gates, scoring, structural modelling, and the experiment
generator all operate on target/partner, and use the class labels only to phrase
their output.

Swapping target class means supplying different evidence, not editing the agent.

### Two places the old TF–Mediator names survive

Both are compatibility boundaries, and both are deliberate:

| Boundary | Names kept | Why |
|---|---|---|
| The frozen BenchFlow task `reagent/tf-mediator-hero` | `hero.transcription_factor`, `hero.mediator_subunit` | its verifier reads those keys and we are scored against that task, so renaming them would invalidate the comparison. The task id appears in every `runs/<run_id>/traces/trace_manifest.json` |
| `demo.json` | `transcription_factor`, `mediator_subunit`, `tf_region` | emitted as **deprecated aliases** beside `target_gene` / `partner_gene` / `target_region`, with identical values, while the UI migrates. See `docs/DEMO_JSON.md` |

Neither is the project's vocabulary. Everything else says target and partner.

## What it does not claim

Everything the pipeline computes is a computational result. Predicted structures
are predictions, not observations. Model agreement is not validation. A docking
score is not a binding affinity. No output here is evidence of binding, safety,
efficacy, or experimental validation, and the artifacts are written to keep those
categories separate rather than blurred.

Human approval is required before structural execution, and the gates that
matter are signed by named people, not by the agent.

## Where the pieces live

| Stage | Where |
|---|---|
| Dependency ingest, ranking, screening | `src/dependency_scout/` |
| Agent orchestration, gates, scoring, structure, experiment | `src/reagent_workflow/` |
| The single artifact the UI reads | `docs/DEMO_JSON.md` |
| Agent constitution | `SOUL.md` |
| Evaluation trace | `benchflow/` |
