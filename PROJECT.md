# Agentic target discovery and drug prioritization pipeline

## In two lines

A general pipeline that starts from disease biology and moves systematically to
a testable drug hypothesis: find what a disease state depends on, prove the
dependency is selective and the contact point is *documented*, then take the
strongest hypothesis toward structure and chemistry — recording every rejection
on the way. Mediator/TF is the first test case, not the definition of the system.

## The seven capabilities, and what actually implements them today

| # | Capability | Code today | Status |
|---|---|---|---|
| 1 | Identify candidate targets | `dependency_scout/depmap.py:analyze_gene_effects` — DepMap-shaped gene-effect matrix → `DependencyEvidence` | PARTIAL: statistic runs, but every input so far is a synthetic fixture and the caller supplies the gene set (no genome-wide sweep) |
| 2 | Rank and prioritize | `dependency_scout/ranking.py` (`gate`/`rank`); `reagent_workflow/gates.py` + `scoring.py` + `orchestrator.run_score` → weighted `CandidateScorecard`, then `run_hero_checkpoint` | BUILT |
| 3 | Specificity / therapeutic window | in-context vs other-context only: `selectivity_delta`, `other_dependent_fraction`. Normal tissue is a scored slot (`normal_cell_completeness`) fed by hand-entered evidence | PARTIAL: no normal-tissue data source is wired |
| 4 | Target location / druggable mechanism | recorded, not derived: `MediatorLink.tf_region`, `interacting_region_mapped`, `InterfaceTractability`; gate `mediator_region_mapped` rejects unmapped contacts | PARTIAL: sites come from literature, no pocket detection |
| 5 | Structural tractability | `reagent_workflow/structure.py` — builds/validates boltz2 + esmfold2 requests, caches results, `compare_models` scores two-model agreement; live Modal only after an approved checkpoint | PARTIAL: no PDB retrieval, no pocket-quality metric |
| 6 | Small-molecule screening | `ProtoScreenSpec` + `proto_bridge.validate_proto_spec` compile and validate a typed Vina input | NOT BUILT: no compound library, nothing docked; the hero payload emits `drug_discovery.status = "blocked"` |
| 7 | Next testable experiment | `experiment.propose_next_experiment` + `improvement.improve_stage` rubric loop → `reports/next_experiment.json` | BUILT |

Cross-cutting: `trace.py` records every stage event, `store.py` makes runs
resumable from disk alone, `benchflow_export.py` exports the trace.

## End to end

```text
disease/cell state → target discovery → target ranking → specificity →
druggable site → structural evaluation → small-molecule candidates → next experiment
```

## Why Mediator/TF is the first test case

Public dependency data is richest in cancer, so a cancer TF program is where the
discovery stage has real numbers to chew on. And the system gives us known
positives to calibrate gates against: ELK1–MED23 and ELF3–MED23 are documented
TF–Mediator contacts, so a gate that rejects them is miscalibrated and a run that
"discovers" them has only shown the plumbing works.

The worked instance, in the original framing: find a transcription factor that a
disease depends on abnormally, confirm it has a *documented physical contact
point* with one Mediator subunit, then screen small molecules that could block
that exact contact. TFs resist direct drugging — their DNA-binding surface is
flat — so the target is the spot where the TF touches Mediator.

The load-bearing word is **documented**, and it generalizes: the pipeline must
prove from public data that a contact is mapped, not merely correlated with the
disease, *before* anyone models it in 3D or docks against it. Correlation passing
itself off as contact is the failure mode this project exists to catch.

**Caveat on generality:** the stages are general; the field names are not yet.
`CandidateHypothesis.transcription_factor` / `.mediator_subunit` and the
`mediator_*` gate names hard-code the test case. Renaming is cheap and can wait.

## The shape of a passing argument

Illustrative only — the agent must derive and verify this chain itself, not
assume it. Every link is a claim the pipeline has to source or reject.

| Link | Question the agent must answer with evidence |
|---|---|
| Disease | Is there a disease/cell state with a *selective* dependency? |
| Target | Is the dependency real and specific — not overexpression, not pan-essential? |
| Contact | Is an interaction with a partner physically mapped, with the interacting region identified? |
| Interface | Is there a structure, or a credible model, of that region? |
| Pocket | Is there a definable, evidence-bounded site to screen against? |

**Do not carry any example chain into a result. It is a template for the
argument, not evidence.**

## Controls

**Positive — the agent should find these.**

- **ELK1–MED23:** a structurally characterized TF–Mediator interaction.
- **ELF3–MED23:** a disease-relevant interaction in HER2-driven epithelial
  cancers.

These are validation examples, not predetermined targets.

**Negative — the agent should reject these, out loud, with a reason.**

- A **pan-essential** target: broadly required across cell lines, so a real
  dependency but not a selective one.
- A target that is **overexpressed** in a disease with no dependency signal.
- A pair supported only by **co-expression or a whole-protein pull-down**, with
  no interacting region mapped.

A run that only ever says yes has not demonstrated judgment. The rejection
trace — which gate fired, on what evidence — is the part that shows the
reasoning is real. `GateResult` in `src/dependency_scout/models.py` and
`decisions/rejections.jsonl` in a `reagent_workflow` run exist to record it.

## Tooling

Claude Code and Tamarind for setup. Paperclip, Proto, or Modal when they
materially improve the experiment without becoming architectural requirements.

## Boundary

The result is a public, evidence-backed, falsifiable hypothesis — not a claim of
binding, safety, efficacy, or experimental validation. Synthetic fixtures are
labelled as tests and carry no scientific weight; a fixture run caps reported
confidence at `low`.
