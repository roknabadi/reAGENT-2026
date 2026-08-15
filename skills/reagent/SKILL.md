---
name: re:AGENT
description: >
  Use when the user is starting a re:AGENT project, or asks what to build, which
  track fits, what the judges reward, or how to plan the weekend. Read this
  FIRST, before any tool skill, to pick a track, scope a project that can be
  demoed by Sunday, and hit the judging bar for the re:AGENT hackathon (End to
  End Agentic Science).
---

# re:AGENT: how to win

re:AGENT is a two-day build weekend for the infrastructure scientific agents
still need: better datasets, sharper tools, and reliable ways to evaluate their
work. The bar is not a faster workflow, it is a result **worth trusting**: every
claim cited or measured, the reasoning easy to inspect, the numbers reproducible.

This workspace ships four tools for exactly that: Paperclip, CELLxGENE Census,
Proto, and Boltz. Read this doc first to choose a track and scope, then open the
matching tool skill and follow its rules.

## 1. Pick one track

Commit to a single track and one demoable claim. A narrow result that holds up
beats a broad one that does not.

### Track A: Build an AI Scientist

An agent that runs a scientific or drug-development workflow end to end: gather
evidence, use the right tools and databases, form and test a hypothesis, produce
a structured output, and make its reasoning inspectable. Examples: a virtual FDA
reviewer, a toxicology agent, a clinical-trial designer, a protein-discovery
agent over embeddings and structure.

- **Tools:** Census for single-cell evidence, Paperclip for literature, trials,
  and regulatory documents, Proto and Boltz for the structure and design steps.
- **Demo:** the agent taking a real input to a structured, inspectable answer.
  Show the reasoning trail, not just the verdict.

### Track B: Build a Dataset or Meta-Analysis

Ask the literature something no single paper answers. Draft queries, run them
across thousands of papers, sharpen and re-run, then find the cross-paper
pattern and demo it.

- **Tools:** Paperclip (papers, trials, patents, regulatory), Census (assemble a
  single-cell dataset or meta-analysis).
- **Demo:** the assembled dataset plus the pattern it reveals, with a citation
  behind every row.

### Track C: Build the Biological Design

Design biology to a spec you set, from one protein to a multi-gene system:
generate candidates and evolve them toward something that could hold up in a
real cell.

- **Tools:** Proto (design, dock, score, inverse-fold), Boltz (fold candidates,
  predict binding), Paperclip and Census to justify the design space.
- **Demo:** the new sequence or system that did not exist before, with the
  scores or structure that argue it is real.

Or bring your own project. The tracks are a starting frame, not a fence.

## 2. Hit the judging bar

Judges reward results worth trusting. On every project:

- **Cite or measure every claim.** Never assert a finding you did not read or
  produce. Paperclip cites by line-pinned URL; Boltz and Proto report the real
  numbers from their JSON; Census numbers carry the pinned `census_version`
  and `value_filter`.
- **Make the reasoning inspectable.** Land the work as reviewable edits in
  `findings.tex` (it compiles to a PDF beside the source), not as tool output
  left in the chat. Show the input, the method, and the caveat, not just the
  answer.
- **Keep it reproducible.** Record versions, filters, seeds, and tool keys next
  to each result so a judge can re-run it.

## 3. Work the clock

- **Saturday morning:** kickoff and tool talks, then form the team and lock ONE
  track and one claim you can actually demo Sunday.
- **Saturday build to the overnight checkpoint:** get one end-to-end path
  working early, even if thin. Queue any long or GPU run (a Boltz affinity pass,
  a big Proto model) before the overnight checkpoint so it finishes by morning.
- **Sunday, 10:45 submission then 12:30 demos:** stop building in time to polish
  the demo. The demo is the assembled dataset, the new design, or the agent's
  inspectable run. Rehearse showing it with the citations visible.

## Then open the tool skill

Once the track is set, read the matching `SKILL.md` before running anything and
follow it exactly: `skills/paperclip/`, `skills/cellxgene-census/`,
`skills/proto/`, `skills/boltz/`.

## Two skills that decide what you build, and what you may claim

Read these before choosing a target or running a screen. They are policy, not
reference — the workflow loads their rules per stage from `SOUL.md`.

- **`skills/use-case-discovery/`** — read FIRST, before assuming a disease,
  target class, or mechanism. Produces five scored candidate use cases and stops
  for human review. Choosing the problem is the decision everything downstream
  inherits, and it is far cheaper to change here.
- **`skills/screening/`** — read BEFORE any docking run. Boltz-2 for the complex,
  ESMFold monomer-only, DrugCentral primary and ChEMBL `max_phase=4` secondary,
  Vina then Boltz-2 rerank, human pose review. Its central rule: without a
  localized, credible interface and a defensible pocket, **abstain rather than
  dock blindly** — a docking program will rank compounds against a pocket that
  does not exist.

`docs/PIPELINE.md` states the pipeline as a product, independent of any one
target class.
