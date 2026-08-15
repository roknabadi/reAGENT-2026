---
name: use-case-discovery
description: >
  Use when choosing WHICH biological problem to point the pipeline at — before
  any target discovery, ranking, or docking. Produces five scored candidate use
  cases and recommends one or two for human review, then stops. Read this before
  assuming a disease, target class, or mechanism.
---

# Target-agnostic use-case discovery

You are selecting the best biological use case for an agentic target-discovery
and drug-prioritization pipeline.

**Do not assume Mediator, transcription factors, cancer, or any predetermined
target class.** Those are prior habits, not evidence. The pipeline is general:

```text
disease / biological state → candidate target discovery → quantitative ranking
→ specificity / therapeutic-window assessment → druggable site or mechanism
→ structural evaluation → small-molecule screening → next validation experiment
```

Your task is to determine what biological problem this pipeline is best suited
to address. Search across plausible diseases, cell states, and target classes,
and identify use cases where **the full pipeline can be supported by public
data**. A use case that breaks at step five is worse than a less exciting one
that survives to step eight.

## Prioritize

- Strong disease-selective genetic or functional dependency evidence
- Quantitative datasets that allow candidates to be **ranked**, not just listed
- Evidence distinguishing disease dependence from normal-cell requirements
- A mechanistically intelligible target or protein interaction
- A known or predictably tractable binding pocket, interface, or regulatory site
- Sufficient structural information: experimental structures, or a credible
  prediction that is constrained rather than speculative
- A realistic path to small-molecule screening
- Independent literature or experimental evidence that can validate the agent's
  reasoning — a case where being wrong would be visible
- A clear falsifiable next experiment
- Feasibility with **currently available** public data and computational tools

## Penalize or reject

Each of these is a way the pipeline produces confident output that means
nothing:

| Reject when | Because |
|---|---|
| the target rests only on expression or correlation | correlation presented as dependency |
| the dependency is broadly essential | real, but not disease-selective, so there is no window |
| normal-tissue liability cannot be assessed | no therapeutic window can be argued |
| there is no defensible druggable site | nothing to screen against |
| structural modelling would be unconstrained speculation | the interface would be invented, not found |
| the downstream screen cannot be meaningfully interpreted | the shortlist would be noise with a ranking |

## Produce

### A. Five candidate use cases

Each defined as:

```text
disease / state → target or target class → therapeutic mechanism
```

### B. Score each on eight dimensions

Every score needs a definition and the evidence behind it. A number with no
source is exactly what this pipeline exists to reject.

| Dimension | What it measures |
|---|---|
| biological evidence | strength and directness of the dependency evidence |
| disease specificity | selective versus broadly required |
| safety / selectivity potential | can a therapeutic window be argued from data |
| mechanistic clarity | is the mechanism intelligible and stated |
| structural tractability | experimental structure, or credibly constrained model |
| small-molecule tractability | is the site something a small molecule could engage |
| public-data availability | can the analysis actually be run from public sources |
| end-to-end feasibility | does the whole chain survive, not just the start |

Record missing evidence as missing. Do not average a gap away, and do not let a
strong score on one dimension carry a use case that fails another outright.

### C. Recommend the top one or two

For running the complete pipeline.

### D. Explain why these beat the alternatives

Specifically as *demonstrations of the pipeline* — which is not the same as
being the most interesting biology. The best demonstration exercises the whole
chain, including the rejections, on data that exists today.

## Stop

**Do not perform detailed target discovery or docking yet.** Stop after
selecting and justifying the best use case or cases, and hand them to human
review. Selecting the problem is itself a decision worth a checkpoint: it
determines everything downstream, and it is far cheaper to change here than
after a week of analysis.

## Working notes

- TF–Mediator is the case the pipeline was first exercised on. It is a
  reasonable candidate, but it starts with no advantage over the alternatives
  and must earn its place on the same eight dimensions.
- Record every use case considered and rejected, with the dimension that killed
  it. The rejected list is evidence that the recommendation was a choice rather
  than a default.
- Cite sources for every claim, in `SOURCES.md`. Public data only.

## Related

- `docs/PIPELINE.md` — the pipeline as a product, independent of target class
- `skills/screening/SKILL.md` — what must be true before a screen is meaningful
- `SOUL.md` — the `USE_CASE_DISCOVERY` stage loads `no-target-class-assumed`
