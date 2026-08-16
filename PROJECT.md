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
| 4 | Target location / druggable mechanism | recorded, not derived: `MediatorLink.tf_region` / `InteractionEvidence.target_region`, `interacting_region_mapped`, `InterfaceTractability`; gate `interface_region_mapped` rejects unmapped contacts whatever the target class | PARTIAL: sites come from literature, no pocket detection |
| 5 | Structural tractability | Two arms, not yet one. Orchestrated: `reagent_workflow/structure.py` — builds/validates boltz2 + esmfold2 requests, caches results, `compare_models` scores two-model agreement; live Modal only after an approved checkpoint; runs inside the `STRUCTURE` stage of the `reagent-agent` state machine. Unorchestrated: `reagent_workflow/interface.py` — turns a structural *ensemble* of independent seeds into a `CONVERGED` / `AMBIGUOUS` / `REFUSED` consensus (validated against PDB 9F6Y — recovers the published ELK1 motif and MED23 pocket residues); `reagent_workflow/site.py` derives a defensible docking box on the free receptor from that consensus; thresholds for both live in `discovery_config.py` | PARTIAL: no PDB retrieval (receptor structures are hand-curated `.cif` files), no pocket-quality metric. The ensemble/consensus/site arm is real and tested (`tests/test_interface_consensus.py`, `tests/test_site_source.py`) but reachable only from `ui/` and `scripts/` — see the orchestrator gap noted below the table |
| 6 | Small-molecule screening | `reagent_workflow/screen.py` proposes a compound library from the site's own residues and their chemistry, standardizes and identity-checks every structure (`chemistry.py`: RDKit `standardize`, PubChem `verify_identity`), then docks with AutoDock Vina (`proto_tools.tools.molecular_docking.vina.run_vina_docking`); `ui/pipeline_api.py` docks live, and `runs/vina_smoke.json` records a completed 12-compound run | PARTIAL: docking itself works end to end and is exercised, but there is no DrugCentral/ChEMBL approved-drug ingest (`scripts/run_cancer.py` stands a small hand-curated compound set in for it, by its own comment), no orchestrator wiring, no CLI entrypoint (`screen.py`/`site.py`/`interface.py`/`chemistry.py` carry no `argparse`/`__main__`; only `ui/` and `scripts/` import them), and no path from this arm into `demo.json` or `hero_hypothesis.json` — the hero payload still hardcodes `drug_discovery.status = "blocked"` (`orchestrator.py`) whatever `ui/pipeline_api.py` has docked |
| 7 | Next testable experiment | `experiment.propose_next_experiment` + `improvement.improve_stage` rubric loop → `reports/next_experiment.json` | BUILT |

**The orchestrator gap, stated plainly:** the consensus/site/screen arm (row 5's unorchestrated half, and all of row 6) is implemented but reachable only from `ui/` and `scripts/`, never from the orchestrated `reagent-agent` state machine. That machine still runs `BIORISK → INGEST → GATE → SCORE → HERO_CHECKPOINT → STRUCTURE → NEXT_EXPERIMENT → COMPLETE` (`Stage` enum, `src/reagent_workflow/models.py`) with no stage for interface consensus, site definition, chemistry, or screening. A run through the orchestrator never touches `interface.py`, `site.py`, `screen.py`, or `chemistry.py`.

Cross-cutting: `trace.py` records every stage event, `store.py` makes runs
resumable from disk alone, `benchflow_export.py` exports the trace,
`adapters.py` carries candidates between `dependency_scout` and
`reagent_workflow` (the two packages share no types by design), and
`demo_export.py` emits the single `demo.json` the UI reads — its contract is
`docs/DEMO_JSON.md`.

The pipeline as a product pitch, independent of any one target class, is
`docs/PIPELINE.md`.

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

**Generality is now in the code, not just the stages.** A candidate carries a
`target_gene` and a `partner_gene`, optionally labelled with a free-text
`target_class` / `partner_class` ("transcription factor", "Mediator subunit",
"kinase", "scaffold"). Gates, scoring, structural modelling, and the experiment
generator operate on target/partner and use the class labels only to phrase
their output — the workflow code does not know what a transcription factor is.
The old `transcription_factor` / `mediator_subunit` names still load and still
read, as aliases, so existing evidence files and the UI keep working.

Swapping target class means supplying different evidence, not editing the agent.

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
