# `demo.json` — frozen UI contract

One file. Amir's orchestrator writes it, Vraj's single static HTML reads it.
No server, no build step, no network call at demo time.

- **Written to** `runs/<run_id>/demo.json`, then copied next to the UI file.
- **Loaded by** `fetch("demo.json")` when served, otherwise `<input type="file">`
  + `FileReader` — `file://` blocks `fetch`, so the picker is the fallback, not
  a second artifact.
- **Every number in it already exists** in `src/dependency_scout/models.py` or
  `src/reagent_workflow/models.py`. Nothing here is a new measurement.
- **Nothing resolves outside this file.** No `evidence_id` indirection: reasons
  are sentences, provenance is URLs. The one exception is
  `dependency.source_id` → `sources[]`.

## Version

```
"schema": "reagent-demo/1"
```

Required, exact string match. The UI fails loudly:

```js
if (d.schema !== "reagent-demo/1") throw new Error("demo.json schema mismatch: " + d.schema);
```

Any field added later that the UI must understand bumps this to `reagent-demo/2`.
Adding an optional field the UI can ignore does not.

---

## Top level

| Field | Type | Req | From |
|---|---|---|---|
| `schema` | `"reagent-demo/1"` | yes | constant |
| `generated_at` | ISO8601 str | yes | `store.utc_now()` |
| `run` | object | yes | `RunState` + `HumanCheckpoint` |
| `candidates` | array | yes | ranked, index 0 = top. May be empty |
| `rejections` | array | yes | may be empty; **screen 11** |
| `structure` | object \| `null` | yes | `null` = structure stage has not run |
| `screen` | object \| `null` | yes | `null` = no docking stage at all |
| `summary` | object | yes | `FinalReport` + `NextExperiment` |
| `sources` | array | yes | `SourceRecord` from either package |

### `run`

| Field | Type | Req | From |
|---|---|---|---|
| `run_id` | str | yes | `RunState.run_id` |
| `stage` | str | yes | `RunState.stage` — the stage reached (`INGEST`…`COMPLETE`) |
| `status` | str | yes | `RunState.status` (`initialized`/`running`/`awaiting_human`/`completed`/`failed`/`abstained`) |
| `completed_stages` | [str] | yes | `RunState.completed_stages` |
| `hero_candidate_id` | str \| `null` | yes | `RunState.hero_candidate_id` |
| `fixture_run` | bool | yes | `RunState.fixture_run` |
| `banner` | str \| `null` | yes | non-null whenever any input is `tier: "synthetic"`. **UI renders it verbatim, always visible.** From `candidates.fixture.json.fixture_note` |
| `config_hash` | str | yes | `RunConfig.hash()` |
| `git_commit` | str \| `null` | yes | `RunState.git_commit` |
| `checkpoints` | array | yes | see below |

`run.checkpoints[]` — **which human checkpoints are still open**:

| Field | Type | Req | From |
|---|---|---|---|
| `id` | str | yes | `HumanCheckpoint.checkpoint_id` (`<run_id>-hero`), or `checkpoint-<n>` for project gates |
| `origin` | `"run"` \| `"project"` | yes | `run` = orchestrator checkpoint; `project` = a gate in `team/CHECKPOINTS.md` |
| `name` | str | yes | `HumanCheckpoint.requested_decision`, or the `## <n>. <name>` heading |
| `stage` | str \| `null` | yes | `HumanCheckpoint.stage`; `null` for project gates |
| `status` | `"open"` \| `"approved"` \| `"rejected"` \| `"revised"` | yes | `HumanCheckpoint.status`; project gates map `OPEN`→`open`, `PASSED`→`approved`, `FAILED`→`rejected` |
| `signs_off` | str \| `null` | yes | `HumanCheckpoint.resolved_by`, or the **Signs off:** line |
| `resolved_at` | str \| `null` | yes | `HumanCheckpoint.resolved_at` |

Project gates parse out of `team/CHECKPOINTS.md` with one regex on
`^## (\d+)\. (.+) — (OPEN|PASSED|FAILED)$`. All five are `open` today.

### `candidates[]`

Array order **is** the ranking. Emitter order: eligible-and-scored by score
descending, then `awaiting_dependency`, then `rejected`, then `calibration_only`.
The UI may re-sort; it must not assume any field is non-null.

| Field | Type | Req | From (`dependency_scout` / `reagent_workflow`) |
|---|---|---|---|
| `id` | str | yes | `CandidateHypothesis.candidate_id`; for interface-only rows, `<gene>-<partner>` |
| `gene` | str | yes | `RankedCandidate.name` / `CandidateHypothesis.transcription_factor` |
| `partner_gene` | str \| `null` | yes | `MediatorLink.partner_gene` / `CandidateHypothesis.mediator_subunit` |
| `disease_context` | str | yes | `RankedCandidate.disease_context` (returns `"not yet quantified"` when there is no dependency) / `CandidateHypothesis.disease_context` |
| `status` | enum | yes | derived, see precedence below |
| `score` | float 0–1 \| `null` | yes | `RankedCandidate.final_score` / `CandidateScorecard.total_score`. `null` = not scored |
| `evidence_completeness` | float 0–1 \| `null` | yes | `RankedCandidate.evidence_completeness` / `CandidateScorecard.evidence_completeness` |
| `missing_evidence` | [str] | yes | `CandidateScorecard.missing_components`; `[]` when nothing is missing. Unscored candidates have no scorecard and use the coarse tokens `"dependency"` and `"interacting_region"` |
| `involvement` | `direct`\|`indirect`\|`predicted`\|`unknown` | yes | `MediatorLink.involvement` (derived property) / mapped, see below |
| `region_mapped` | bool | yes | `.interacting_region_mapped` (same name in both) |
| `tf_region` | str \| `null` | yes | `.tf_region` (same name in both) |
| `tractability` | `short_linear_motif`\|`folded_domain`\|`unknown` | yes | `MediatorLink.tractability`; `"unknown"` for `MediatorEvidence`, which has no such field |
| `ready_for_structural_modeling` | bool | yes | `.ready_for_structural_modeling` (derived property in both) |
| `dependency` | object \| `null` | yes | `DependencyEvidence`; **`null` is normal**, see missing data |
| `claims` | array | yes | `MediatorLink.claims` + `EnrichmentEvidence.claims` / built from `EvidenceRecord` |
| `screening_concerns` | [str] | yes | `MediatorLink.screening_concerns` (derived); `[]` for `MediatorEvidence` |
| `gate` | object \| `null` | yes | `GateResult` / `GateOutcome`. `null` = gates have not run |

`status` precedence (first match wins):

1. `calibration_only` — `MediatorLink.calibration_only`. **Never a result.**
2. `rejected` — `gate.eligible == false`
3. `awaiting_dependency` — `RankedCandidate.awaiting_dependency_data` (`dependency is null`)
4. `hero` — `id == run.hero_candidate_id`
5. `eligible`

#### `candidates[].dependency`

Field names are identical in both packages — copy straight across, no mapping.

| Field | Type | Req |
|---|---|---|
| `n_target_models`, `n_other_models` | int | yes |
| `median_target_effect`, `median_other_effect` | float | yes |
| `target_dependent_fraction`, `other_dependent_fraction` | float 0–1 | yes |
| `selectivity_delta` | float | yes |
| `mann_whitney_p` | float \| `null` | yes |
| `effect_unit` | str | yes | `"gene_effect_score"` |
| `source_id` | str | yes | key into `sources[]` |

**Sign convention (frozen here because the two packages disagree):**
`selectivity_delta = median_target_effect - median_other_effect`. More negative =
more selective. `reagent_workflow/gates.py` already uses this;
`dependency_scout/ranking.py:gate()` tests `selectivity_delta < 0.35` as a
positive magnitude and is wrong against its own fixtures. The adapter negates
`dependency_scout` values on the way in. See contradictions.

#### `candidates[].claims[]`

| Field | Type | Req | From |
|---|---|---|---|
| `statement` | str | yes | `Claim.statement` / `EvidenceRecord.claim` |
| `support` | `direct_experimental`\|`genetic_functional`\|`computational_prediction`\|`inference` | yes | `Claim.support` / mapped from `EvidenceRecord` |
| `citations` | [str] | yes, **min 1** | `Claim.citations` / `[SourceRecord.url]` for `EvidenceRecord.source_id` |
| `note` | str \| `null` | yes | `Claim.note` / `"; ".join(EvidenceRecord.limitations)` |

`EvidenceRecord` → `support`:

| Condition | `support` |
|---|---|
| `interpretation == observed` | `direct_experimental` |
| `interpretation == computed` **and** `evidence_type == dependency` | `genetic_functional` (CRISPR knockout is a genetic-functional readout) |
| `interpretation == computed` (other) or `predicted` | `computational_prediction` |
| `interpretation == inference` | `inference` |

A synthetic claim keeps whatever support type it has; the `synthetic` tier on
its source is what marks it as a fixture, not the support type.

`MediatorEvidence.interaction_type` → `involvement`:

| `interaction_type` + region | `involvement` |
|---|---|
| `direct_binding` + `interacting_region_mapped` | `direct` |
| `direct_binding` / `complex_member` / `genetic`, no region | `indirect` |
| `inferred` or `null` | `unknown` |

#### `candidates[].gate`

| Field | Type | Req | From |
|---|---|---|---|
| `eligible` | bool | yes | `GateResult.eligible` / `GateOutcome.eligible` |
| `passed_gates` | [str] | yes | `GateOutcome.passed_gates`; `[]` from `dependency_scout`, which does not name passing gates |
| `failed_gates` | [str] | yes | `GateOutcome.failed_gates`; `[]` from `dependency_scout`, which records reasons only |
| `reasons` | [str] | yes | `GateOutcome.reasons` / `GateResult.failures` |

Gate names are the seven in `gates.py:GATE_NAMES`: `dependency_strength`,
`sample_support`, `broad_essentiality`, `disease_specificity`,
`mediator_support`, `mediator_region_mapped`, `provenance`.

---

## `rejections[]` — screen 11, first-class

This is the screen that wins. It is a top-level array, not something the UI
reconstructs by filtering candidates, because two of its three kinds are not
candidate rejections at all.

| Field | Type | Req | From |
|---|---|---|---|
| `candidate_id` | str | yes | `RejectedCandidate.candidate_id` |
| `gene` | str | yes | the candidate's `gene` |
| `kind` | `"gate"` \| `"advisory"` \| `"pending"` | yes | see below |
| `stage` | str \| `null` | yes | `RejectedCandidate.stage`; `null` for advisory/pending |
| `failed_gates` | [str] | yes | `RejectedCandidate.failed_gates`; `[]` for advisory/pending |
| `reasons` | [str] | yes, **min 1** | `RejectedCandidate.reasons` / `MediatorLink.screening_concerns` / `RankedCandidate.shortlistable[1]` |
| `citations` | [str] | yes | URLs behind the reason where they exist; `[]` for synthetic fixtures |
| `rejected_at` | str \| `null` | yes | `RejectedCandidate.rejected_at` |

`kind`:

| `kind` | Meaning | Source |
|---|---|---|
| `gate` | Failed a hard rule. Not eligible. | `RejectedCandidate` / `GateResult.failures` |
| `advisory` | Passed the gates and is still a poor drug target. **Not a rejection — a stated concern.** | `MediatorLink.screening_concerns` |
| `pending` | Excluded from the shortlist without being wrong: awaiting dependency data, or a calibration control. | `RankedCandidate.shortlistable[1]` |

Every eligible candidate with a non-empty `screening_concerns` produces an
`advisory` row **as well as** its normal candidate row. RUNX2 is the case that
matters: it passes the contact rule mechanically and is still the wrong
molecule to screen against.

---

## `structure`

`null` until the structure stage runs. Otherwise:

| Field | Type | Req | From |
|---|---|---|---|
| `candidate_id` | str | yes | `StructuralModelResult.candidate_id` |
| `source` | `"experimental"` \| `"predicted"` \| `"none"` | yes | `experimental` when `StructuralTractability.experimental_structure_id` is set |
| `pdb_id` | str \| `null` | yes | `StructuralTractability.experimental_structure_id` / `ProtoScreenSpec.pdb_id` |
| `url` | str \| `null` | yes | RCSB URL for `pdb_id` |
| `method` | str \| `null` | yes | free text, e.g. `"cryo-EM, 3.0 Å"` or `"boltz2 prediction"` |
| `tf_region` | str \| `null` | yes | the hero candidate's `tf_region` |
| `interface_residues` | `{chain_or_gene: [int]}` | yes | `ProtoScreenSpec.interface_residues` |
| `model_status` | `cached`\|`completed`\|`failed`\|`validated_only`\|`skipped`\|`not_run` | yes | `StructuralModelResult.status` |
| `confidence` | `{str: float}` | yes | `StructuralModelResult.confidence` (`plddt`/`ptm`/`iptm`/`avg_pae`). `{}` for an experimental structure — **do not render 0** |
| `comparison` | object \| `null` | yes | `ModelComparison`: `verdict`, `agreements[]`, `disagreements[]`, `caveat` |
| `limitations` | [str] | yes | `StructuralModelResult.limitations` |

## `screen`

`null` when no docking stage exists. Otherwise:

| Field | Type | Req | From |
|---|---|---|---|
| `candidate_id` | str | yes | `ProtoScreenSpec.candidate_gene`'s candidate |
| `status` | `"not_run"` \| `"planned"` \| `"complete"` \| `"blocked"` | yes | emitter |
| `blocked_reason` | str \| `null` | yes | why nothing ran (e.g. an OPEN checkpoint) |
| `receptor` | object | yes | `{pdb_id, receptor_path, search_box}` from `ProtoScreenSpec` |
| `tools` | [str] | yes | `ProtoScreenSpec.tools` |
| `caveat` | str | yes | constant: `"Docking score is not binding affinity."` **UI renders it on the same screen as the scores.** |
| `compounds` | array | yes | may be `[]` |

`screen.compounds[]`:

| Field | Type | Req | From |
|---|---|---|---|
| `id` | str | yes | public compound ID (ChEMBL/PubChem) |
| `smiles` | str | yes | `ProtoScreenSpec.ligand_smiles[i]` |
| `source_url` | str | yes | public record for the ID |
| `docking_score` | float \| `null` | yes | Vina affinity, `null` until docking runs |
| `unit` | str \| `null` | yes | `"kcal/mol"` |
| `pose_path` | str \| `null` | yes | path inside the run dir |
| `notes` | [str] | yes | may be `[]` |

`docking_score` is **this contract's name**, not a field read off
`proto_tools`. Whatever the Vina output calls its affinity, `proto_bridge`
maps it into this key.

## `summary` — screen 14

| Field | Type | Req | From |
|---|---|---|---|
| `headline` | str | yes | `FinalReport.hero_hypothesis` |
| `confidence` | `low`\|`medium`\|`high` | yes | `FinalReport.confidence` |
| `chain` | `[{label, value}]` | yes | disease → target → partner → site → compounds; `value` may be `"—"` |
| `next_experiment` | object \| `null` | yes | `NextExperiment` verbatim: `scientific_question`, `perturbation`, `readout`, `positive_controls[]`, `negative_controls[]`, `possible_outcomes[{outcome, interpretation_change}]`, `limitations[]` |
| `limitations` | [str] | yes | `FinalReport.limitations` |

## `sources[]`

| Field | Type | Req | From |
|---|---|---|---|
| `source_id` | str | yes | `SourceRecord.source_id`; `dependency_scout` records have none, the adapter slugs `name+version` |
| `name` | str | yes | `SourceRecord.name` |
| `url` | str | yes | `SourceRecord.url` (`synthetic://…` for fixtures) |
| `tier` | `synthetic`\|`public_primary`\|`public_derived` | yes | `SourceRecord.tier` |
| `version` | str \| `null` | yes | `SourceRecord.version` |

---

## Missing data — the UI must render all four

| Case | Shape | UI |
|---|---|---|
| Interface evidence, no DepMap numbers | `dependency: null`, `gate: null`, `score: null`, `status: "awaiting_dependency"` | Row renders. Numeric columns show `—`. **Not** a rejection — `disease_context` is literally `"not yet quantified"` |
| Rejected at a gate | `gate.eligible: false`, `score: null` or 0, `status: "rejected"`, plus a `rejections[]` row with `kind: "gate"` | Row is greyed, reasons visible, and it appears on screen 11 |
| Scored but incomplete | `missing_evidence: ["normal_cell_completeness", …]`, `evidence_completeness < 1` | Show which components are missing. Missing scored 0 and its weight was **not** redistributed |
| Calibration control | `status: "calibration_only"`, `rejections[]` row `kind: "pending"` | Labelled a control everywhere it appears. Never counted as a result |

Rules: `null` means unmeasured, never zero. No field is ever dropped — absent
data is an explicit `null`/`[]`, so the UI reads a fixed shape.

---

## Complete example

Round-01 literature candidates (ELK1, RUNX2, CEBPB, ETV1 — real, from
`examples/*.json`) plus the synthetic DepMap fixture candidates from
`src/reagent_workflow/fixtures/candidates.fixture.json`. Scores computed with
the default weights in `config.py`. Claim text is abbreviated here; the
authoritative text is in `examples/`.

```json
{
  "schema": "reagent-demo/1",
  "generated_at": "2026-08-15T18:40:12.004Z",
  "run": {
    "run_id": "demo-01",
    "stage": "STRUCTURE",
    "status": "awaiting_human",
    "completed_stages": ["INGEST", "GATE", "SCORE", "HERO_CHECKPOINT"],
    "hero_candidate_id": "CAND-SELECTIVE",
    "fixture_run": true,
    "banner": "SYNTHETIC TEST FIXTURE. Invented genes, invented diseases, invented sequences and numbers. Exists to exercise the workflow's gates, scoring, checkpoint, and structural comparison. It is not scientific evidence and must never be cited as a finding.",
    "config_hash": "3f1c9a2e7b5d4c8a",
    "git_commit": "100138c",
    "checkpoints": [
      {
        "id": "demo-01-hero",
        "origin": "run",
        "name": "Approve CAND-SELECTIVE as the hero candidate before any structural modelling.",
        "stage": "HERO_CHECKPOINT",
        "status": "approved",
        "signs_off": "demo-operator",
        "resolved_at": "2026-08-15T18:39:58.220Z"
      },
      {"id": "checkpoint-1", "origin": "project", "name": "TF shortlist", "stage": null, "status": "open", "signs_off": "Andrey", "resolved_at": null},
      {"id": "checkpoint-2", "origin": "project", "name": "Mediator connection", "stage": null, "status": "open", "signs_off": "Andrey", "resolved_at": null},
      {"id": "checkpoint-3", "origin": "project", "name": "Hero target selection", "stage": null, "status": "open", "signs_off": "Andrey", "resolved_at": null},
      {"id": "checkpoint-4", "origin": "project", "name": "Structural model review", "stage": null, "status": "open", "signs_off": "Andrey", "resolved_at": null},
      {"id": "checkpoint-5", "origin": "project", "name": "Virtual-screen hit review", "stage": null, "status": "open", "signs_off": "Andrey", "resolved_at": null}
    ]
  },
  "candidates": [
    {
      "id": "CAND-SELECTIVE",
      "gene": "TFDEMOA",
      "partner_gene": "MEDDEMO1",
      "disease_context": "Synthetic epithelial carcinoma (fixture)",
      "status": "hero",
      "score": 0.804,
      "evidence_completeness": 1.0,
      "missing_evidence": [],
      "involvement": "direct",
      "region_mapped": true,
      "tf_region": "activation domain, residues 22-33 (synthetic)",
      "tractability": "unknown",
      "ready_for_structural_modeling": true,
      "dependency": {
        "n_target_models": 18,
        "n_other_models": 420,
        "median_target_effect": -1.05,
        "median_other_effect": -0.12,
        "target_dependent_fraction": 0.83,
        "other_dependent_fraction": 0.06,
        "selectivity_delta": -0.93,
        "mann_whitney_p": 1e-09,
        "effect_unit": "gene_effect_score",
        "source_id": "SRC-FIX-DEP"
      },
      "claims": [
        {
          "statement": "Crosslinking mass spectrometry in a synthetic fixture places TFDEMOA residues 22-33 in direct contact with the MEDDEMO1 core, identifying the interacting region rather than only the association.",
          "support": "direct_experimental",
          "citations": ["synthetic://fixtures/interaction"],
          "note": "Synthetic record. A mapped crosslink is not a solved interface structure."
        },
        {
          "statement": "TFDEMOA shows a strong selective dependency in synthetic epithelial carcinoma models (median gene effect -1.05) relative to all other lineages (-0.12).",
          "support": "genetic_functional",
          "citations": ["synthetic://fixtures/dependency"],
          "note": "Synthetic values; no experimental measurement underlies them."
        }
      ],
      "screening_concerns": [],
      "gate": {
        "eligible": true,
        "passed_gates": ["dependency_strength", "sample_support", "broad_essentiality", "disease_specificity", "mediator_support", "mediator_region_mapped", "provenance"],
        "failed_gates": [],
        "reasons": []
      }
    },
    {
      "id": "CAND-INCOMPLETE-EVIDENCE",
      "gene": "TFDEMOD",
      "partner_gene": "MEDDEMO1",
      "disease_context": "Synthetic sarcoma (fixture)",
      "status": "eligible",
      "score": 0.421,
      "evidence_completeness": 0.8,
      "missing_evidence": ["normal_cell_completeness", "structural_tractability"],
      "involvement": "direct",
      "region_mapped": true,
      "tf_region": "activation domain, residues 41-52 (synthetic)",
      "tractability": "unknown",
      "ready_for_structural_modeling": true,
      "dependency": {
        "n_target_models": 7,
        "n_other_models": 431,
        "median_target_effect": -0.72,
        "median_other_effect": -0.19,
        "target_dependent_fraction": 0.57,
        "other_dependent_fraction": 0.09,
        "selectivity_delta": -0.53,
        "mann_whitney_p": null,
        "effect_unit": "gene_effect_score",
        "source_id": "SRC-FIX-DEP"
      },
      "claims": [
        {
          "statement": "A synthetic peptide array maps TFDEMOD residues 41-52 as the fragment retained by MEDDEMO1.",
          "support": "direct_experimental",
          "citations": ["synthetic://fixtures/interaction"],
          "note": "Synthetic record. A peptide array tests isolated fragments, not the intact proteins."
        }
      ],
      "screening_concerns": [],
      "gate": {
        "eligible": true,
        "passed_gates": ["dependency_strength", "sample_support", "broad_essentiality", "disease_specificity", "mediator_support", "mediator_region_mapped", "provenance"],
        "failed_gates": [],
        "reasons": []
      }
    },
    {
      "id": "RUNX2-MED23",
      "gene": "RUNX2",
      "partner_gene": "MED23",
      "disease_context": "not yet quantified",
      "status": "awaiting_dependency",
      "score": null,
      "evidence_completeness": null,
      "missing_evidence": ["dependency"],
      "involvement": "direct",
      "region_mapped": true,
      "tf_region": "RUNX2 Runt and PST domains (folded domains, not a short linear motif)",
      "tractability": "folded_domain",
      "ready_for_structural_modeling": true,
      "dependency": null,
      "claims": [
        {
          "statement": "MED23 physically associates with RUNX2 by co-immunoprecipitation in transfected 293T cells and by endogenous co-immunoprecipitation in differentiated MC3T3E1 osteoblastic cells.",
          "support": "direct_experimental",
          "citations": ["https://doi.org/10.1038/ncomms11149"],
          "note": "PMC4821994 L23. Endogenous co-IP, not only a co-transfection artifact."
        },
        {
          "statement": "The interaction with MED23 is mediated via the Runt and PST domains of RUNX2. A GST pull-down using GST-RUNX2 against His-Flag-MED23 implied the interaction is likely direct.",
          "support": "direct_experimental",
          "citations": ["https://doi.org/10.1038/ncomms11149"],
          "note": "The source hedges: 'implied' the interaction was 'likely' direct. Recorded at the source's own confidence, not upgraded."
        },
        {
          "statement": "Med23 deletion in mesenchymal stem cells or osteoblast precursors produces bone defects resembling Runx2 heterozygous mice.",
          "support": "genetic_functional",
          "citations": ["https://doi.org/10.1038/ncomms11149"],
          "note": "Establishes the MED23-RUNX2 axis in NORMAL osteogenic development. A safety liability, not a cancer dependency."
        }
      ],
      "screening_concerns": [
        "folded-domain interface: large buried surface, poor small-molecule tractability compared with a short linear motif"
      ],
      "gate": null
    },
    {
      "id": "CEBPB-MED23",
      "gene": "CEBPB",
      "partner_gene": "MED23",
      "disease_context": "not yet quantified",
      "status": "awaiting_dependency",
      "score": null,
      "evidence_completeness": null,
      "missing_evidence": ["dependency", "interacting_region"],
      "involvement": "indirect",
      "region_mapped": false,
      "tf_region": null,
      "tractability": "unknown",
      "ready_for_structural_modeling": false,
      "dependency": null,
      "claims": [
        {
          "statement": "Ras induces Mediator complex exchange on C/EBP-beta, one of the founding demonstrations that MED23 is targeted by a transcription factor. The contact region on C/EBP-beta is not mapped.",
          "support": "direct_experimental",
          "citations": ["https://pubmed.ncbi.nlm.nih.gov/14759370/"],
          "note": "PROVENANCE WARNING: the primary was not retrievable as full text. The claim rests on a citing sentence in doi:10.1038/s41467-025-59014-8. Do not make this load-bearing."
        },
        {
          "statement": "The C/EBP-beta region that contacts MED23 has never been mapped, so it is unknown whether it engages the MED23 HR2/HR3 groove used by ELK1 and ELF3.",
          "support": "inference",
          "citations": ["https://doi.org/10.1038/s41467-025-59014-8"],
          "note": "Absence of a mapped region in the retrieved literature. This is the gap a peptide-tiling experiment against MED23 391-582 would close."
        }
      ],
      "screening_concerns": [],
      "gate": null
    },
    {
      "id": "ETV1-MED23",
      "gene": "ETV1",
      "partner_gene": "MED23",
      "disease_context": "not yet quantified",
      "status": "awaiting_dependency",
      "score": null,
      "evidence_completeness": null,
      "missing_evidence": ["dependency", "interacting_region"],
      "involvement": "predicted",
      "region_mapped": false,
      "tf_region": null,
      "tractability": "unknown",
      "ready_for_structural_modeling": false,
      "dependency": null,
      "claims": [
        {
          "statement": "ETV1 is predicted, not observed, to present an MBM-like short linear motif to the MED23 HR2/HR3 groove, on the basis of ETS family membership plus KIT-MAPK-regulated protein stability analogous to the ELK1 mechanism.",
          "support": "computational_prediction",
          "citations": [
            "https://doi.org/10.1038/nature09409",
            "https://doi.org/10.1038/s41467-025-59014-8"
          ],
          "note": "NO MED23 EXPERIMENT ON ETV1 EXISTS in the retrieved literature. No proteome-wide MBM motif scan was executed either. The motif scan is the first thing to run."
        }
      ],
      "screening_concerns": [],
      "gate": null
    },
    {
      "id": "CAND-PULLDOWN-ONLY",
      "gene": "TFDEMOE",
      "partner_gene": "MEDDEMO1",
      "disease_context": "Synthetic melanoma (fixture)",
      "status": "rejected",
      "score": null,
      "evidence_completeness": null,
      "missing_evidence": ["interacting_region"],
      "involvement": "indirect",
      "region_mapped": false,
      "tf_region": null,
      "tractability": "unknown",
      "ready_for_structural_modeling": false,
      "dependency": {
        "n_target_models": 21,
        "n_other_models": 417,
        "median_target_effect": -0.98,
        "median_other_effect": -0.15,
        "target_dependent_fraction": 0.79,
        "other_dependent_fraction": 0.07,
        "selectivity_delta": -0.83,
        "mann_whitney_p": 3e-08,
        "effect_unit": "gene_effect_score",
        "source_id": "SRC-FIX-DEP"
      },
      "claims": [
        {
          "statement": "TFDEMOE co-precipitates with MEDDEMO1 in a synthetic whole-protein pull-down. The pull-down used full-length protein and identifies no interacting region.",
          "support": "direct_experimental",
          "citations": ["synthetic://fixtures/interaction"],
          "note": "A whole-protein pull-down reports association, not a contact point. No interacting region is mapped, so there is nothing to model or screen."
        }
      ],
      "screening_concerns": [],
      "gate": {
        "eligible": false,
        "passed_gates": ["dependency_strength", "sample_support", "broad_essentiality", "disease_specificity", "mediator_support", "provenance"],
        "failed_gates": ["mediator_region_mapped"],
        "reasons": ["TF-Mediator contact is not mapped (TFDEMOE-MEDDEMO1): synthetic whole-protein pull-down fixture establishes association but identifies no interacting region, so there is no contact point to model or screen against"]
      }
    },
    {
      "id": "CAND-BROAD",
      "gene": "TFDEMOB",
      "partner_gene": "MEDDEMO1",
      "disease_context": "Synthetic epithelial carcinoma (fixture)",
      "status": "rejected",
      "score": null,
      "evidence_completeness": null,
      "missing_evidence": ["interacting_region"],
      "involvement": "indirect",
      "region_mapped": false,
      "tf_region": null,
      "tractability": "unknown",
      "ready_for_structural_modeling": false,
      "dependency": {
        "n_target_models": 18,
        "n_other_models": 420,
        "median_target_effect": -1.35,
        "median_other_effect": -1.22,
        "target_dependent_fraction": 0.94,
        "other_dependent_fraction": 0.91,
        "selectivity_delta": -0.13,
        "mann_whitney_p": 0.21,
        "effect_unit": "gene_effect_score",
        "source_id": "SRC-FIX-DEP"
      },
      "claims": [
        {
          "statement": "TFDEMOB is required in 91% of all models across every lineage tested, consistent with a broadly essential gene rather than a selective dependency.",
          "support": "genetic_functional",
          "citations": ["synthetic://fixtures/dependency"],
          "note": "Synthetic values."
        }
      ],
      "screening_concerns": [],
      "gate": {
        "eligible": false,
        "passed_gates": ["dependency_strength", "sample_support", "mediator_support", "provenance"],
        "failed_gates": ["broad_essentiality", "disease_specificity", "mediator_region_mapped"],
        "reasons": [
          "dependency is too broad: 91% of other models are also dependent, maximum is 50%",
          "weak disease specificity: selectivity delta -0.130, required at or below -0.300",
          "TF-Mediator contact is not mapped (TFDEMOB-MEDDEMO1): synthetic co-immunoprecipitation fixture establishes association but identifies no interacting region, so there is no contact point to model or screen against"
        ]
      }
    },
    {
      "id": "CAND-OVEREXPRESSED",
      "gene": "TFDEMOF",
      "partner_gene": "MEDDEMO1",
      "disease_context": "Synthetic colorectal carcinoma (fixture)",
      "status": "rejected",
      "score": null,
      "evidence_completeness": null,
      "missing_evidence": [],
      "involvement": "direct",
      "region_mapped": true,
      "tf_region": "activation domain, residues 60-71 (synthetic)",
      "tractability": "unknown",
      "ready_for_structural_modeling": true,
      "dependency": {
        "n_target_models": 24,
        "n_other_models": 414,
        "median_target_effect": -0.18,
        "median_other_effect": -0.11,
        "target_dependent_fraction": 0.08,
        "other_dependent_fraction": 0.05,
        "selectivity_delta": -0.07,
        "mann_whitney_p": 0.44,
        "effect_unit": "gene_effect_score",
        "source_id": "SRC-FIX-DEP"
      },
      "claims": [
        {
          "statement": "TFDEMOF is strongly overexpressed in synthetic colorectal models relative to matched normal tissue (log2 fold change 4.2).",
          "support": "direct_experimental",
          "citations": ["synthetic://fixtures/normal-tissue"],
          "note": "Overexpression is not evidence of dependency."
        },
        {
          "statement": "Despite strong overexpression, TFDEMOF shows no dependency signal in synthetic colorectal models (median gene effect -0.18, indistinguishable from other lineages at -0.11).",
          "support": "genetic_functional",
          "citations": ["synthetic://fixtures/dependency"],
          "note": "Contradicts the overexpression claim. Synthetic values."
        }
      ],
      "screening_concerns": [],
      "gate": {
        "eligible": false,
        "passed_gates": ["sample_support", "broad_essentiality", "mediator_support", "mediator_region_mapped", "provenance"],
        "failed_gates": ["dependency_strength", "disease_specificity"],
        "reasons": [
          "weak dependency: median effect -0.180 in Synthetic colorectal carcinoma (fixture) is above the required -0.500",
          "weak disease specificity: selectivity delta -0.070, required at or below -0.300"
        ]
      }
    },
    {
      "id": "ELK1-MED23",
      "gene": "ELK1",
      "partner_gene": "MED23",
      "disease_context": "not yet quantified",
      "status": "calibration_only",
      "score": null,
      "evidence_completeness": null,
      "missing_evidence": ["dependency"],
      "involvement": "direct",
      "region_mapped": true,
      "tf_region": "ELK1 transactivation domain, MED23-binding motif (MBM) residues 374-384, sequence PSIHFWSTLS(p)P",
      "tractability": "short_linear_motif",
      "ready_for_structural_modeling": true,
      "dependency": null,
      "claims": [
        {
          "statement": "Cryo-EM structure at 3.0 A of human MED23 bound to the phosphorylated ELK1 transactivation domain. The ELK1 MED23-binding motif (residues 374-384) binds the concave face of the MED23 core at the HR2/HR3 interface, in a site formed by helices H19, H21, H28 and H30.",
          "support": "direct_experimental",
          "citations": [
            "https://doi.org/10.1038/s41467-025-59014-8",
            "https://www.rcsb.org/structure/9F6Y"
          ],
          "note": "PDB 9F6Y, EMD-50242. Apo MED23 is PDB 9F76 / EMD-50247."
        },
        {
          "statement": "ELK1 I376, F378 and L382 form the hydrophobic contacts with MED23. F378 binds deeply and is buried, and is surrounded in MED23 by I339 and L343 (H19), F379, G382 and S383 (H21), and V533 and M537 (H28).",
          "support": "direct_experimental",
          "citations": ["https://doi.org/10.1038/s41467-025-59014-8"],
          "note": "These MED23 residues define the pocket a small molecule would have to occupy."
        },
        {
          "statement": "ELK1 phosphorylated at S383 binds MED23 with a Kd of 81 nM by SPR, while only minimal interaction is detected by pull-down without phosphorylation.",
          "support": "direct_experimental",
          "citations": ["https://doi.org/10.1038/s41467-025-59014-8"],
          "note": "Binding is phosphorylation-dependent; any screen against this site inherits that condition."
        },
        {
          "statement": "The designed MED23 G382F mutation disrupts ELK1 binding and impairs ELK1-dependent serum-induced activation of target genes in MED23-null MEFs.",
          "support": "genetic_functional",
          "citations": ["https://doi.org/10.1038/s41467-025-59014-8"],
          "note": "Occupancy of this site is coupled to transcriptional output."
        }
      ],
      "screening_concerns": ["calibration control, never a result"],
      "gate": null
    }
  ],
  "rejections": [
    {
      "candidate_id": "CAND-PULLDOWN-ONLY",
      "gene": "TFDEMOE",
      "kind": "gate",
      "stage": "GATE",
      "failed_gates": ["mediator_region_mapped"],
      "reasons": ["TF-Mediator contact is not mapped (TFDEMOE-MEDDEMO1): synthetic whole-protein pull-down fixture establishes association but identifies no interacting region, so there is no contact point to model or screen against"],
      "citations": [],
      "rejected_at": "2026-08-15T18:39:41.118Z"
    },
    {
      "candidate_id": "CAND-OVEREXPRESSED",
      "gene": "TFDEMOF",
      "kind": "gate",
      "stage": "GATE",
      "failed_gates": ["dependency_strength", "disease_specificity"],
      "reasons": [
        "weak dependency: median effect -0.180 in Synthetic colorectal carcinoma (fixture) is above the required -0.500",
        "weak disease specificity: selectivity delta -0.070, required at or below -0.300"
      ],
      "citations": [],
      "rejected_at": "2026-08-15T18:39:41.119Z"
    },
    {
      "candidate_id": "CAND-BROAD",
      "gene": "TFDEMOB",
      "kind": "gate",
      "stage": "GATE",
      "failed_gates": ["broad_essentiality", "disease_specificity", "mediator_region_mapped"],
      "reasons": [
        "dependency is too broad: 91% of other models are also dependent, maximum is 50%",
        "weak disease specificity: selectivity delta -0.130, required at or below -0.300",
        "TF-Mediator contact is not mapped (TFDEMOB-MEDDEMO1): synthetic co-immunoprecipitation fixture establishes association but identifies no interacting region, so there is no contact point to model or screen against"
      ],
      "citations": [],
      "rejected_at": "2026-08-15T18:39:41.120Z"
    },
    {
      "candidate_id": "RUNX2-MED23",
      "gene": "RUNX2",
      "kind": "advisory",
      "stage": null,
      "failed_gates": [],
      "reasons": [
        "folded-domain interface: large buried surface, poor small-molecule tractability compared with a short linear motif",
        "the same MED23-RUNX2 axis is required in normal osteogenic development, so subunit-level loss is a safety liability"
      ],
      "citations": ["https://doi.org/10.1038/ncomms11149"],
      "rejected_at": null
    },
    {
      "candidate_id": "ELK1-MED23",
      "gene": "ELK1",
      "kind": "pending",
      "stage": null,
      "failed_gates": [],
      "reasons": ["calibration control, never a result"],
      "citations": ["https://doi.org/10.1038/s41467-025-59014-8"],
      "rejected_at": null
    },
    {
      "candidate_id": "CEBPB-MED23",
      "gene": "CEBPB",
      "kind": "pending",
      "stage": null,
      "failed_gates": [],
      "reasons": ["awaiting quantitative dependency data"],
      "citations": [],
      "rejected_at": null
    },
    {
      "candidate_id": "ETV1-MED23",
      "gene": "ETV1",
      "kind": "pending",
      "stage": null,
      "failed_gates": [],
      "reasons": ["awaiting quantitative dependency data"],
      "citations": [],
      "rejected_at": null
    }
  ],
  "structure": {
    "candidate_id": "ELK1-MED23",
    "source": "experimental",
    "pdb_id": "9F6Y",
    "url": "https://www.rcsb.org/structure/9F6Y",
    "method": "cryo-EM, 3.0 A",
    "tf_region": "ELK1 transactivation domain, MED23-binding motif (MBM) residues 374-384",
    "interface_residues": {
      "ELK1": [376, 378, 382],
      "MED23": [339, 343, 379, 382, 383, 533, 537]
    },
    "model_status": "not_run",
    "confidence": {},
    "comparison": null,
    "limitations": [
      "Calibration control. This is a published positive control for the gates, not a result of this run.",
      "Binding is phosphorylation-dependent (ELK1 S383); any screen against this site inherits that condition.",
      "No structure prediction has been run on a candidate from this run."
    ]
  },
  "screen": {
    "candidate_id": "ELK1-MED23",
    "status": "not_run",
    "blocked_reason": "checkpoint-5 (Virtual-screen hit review) is OPEN and no public ligand set has been assembled for the MED23 HR2/HR3 groove.",
    "receptor": {
      "pdb_id": "9F6Y",
      "receptor_path": null,
      "search_box": null
    },
    "tools": ["pdb-fetch-entry"],
    "caveat": "Docking score is not binding affinity.",
    "compounds": []
  },
  "summary": {
    "headline": "In synthetic epithelial carcinoma models, the selective dependency on TFDEMOA is carried by its interaction with the Mediator subunit MEDDEMO1, making that interface a candidate point of intervention. FIXTURE HYPOTHESIS: synthetic data, no scientific claim.",
    "confidence": "low",
    "chain": [
      {"label": "Disease state", "value": "Synthetic epithelial carcinoma (fixture)"},
      {"label": "Target", "value": "TFDEMOA — selective dependency, median gene effect -1.05 vs -0.12"},
      {"label": "Partner", "value": "MEDDEMO1 — direct contact, region mapped"},
      {"label": "Druggable site", "value": "activation domain, residues 22-33 (synthetic)"},
      {"label": "Compounds", "value": "—"}
    ],
    "next_experiment": {
      "scientific_question": "In Synthetic epithelial carcinoma (fixture) models, does the selective dependency on TFDEMOA require its interaction with the Mediator subunit MEDDEMO1, rather than TFDEMOA abundance alone?",
      "perturbation": "Two arms in Synthetic epithelial carcinoma (fixture) models and a non-dependent control lineage: (1) degron-tagged TFDEMOA for acute depletion; (2) separation-of-function TFDEMOA point mutants that disrupt the predicted MEDDEMO1 interface while preserving DNA binding, re-expressed in TFDEMOA-depleted cells.",
      "readout": "Viability and proliferation over 7-10 days, paired with nascent transcription (PRO-seq or TT-seq) of the TFDEMOA target program, plus co-immunoprecipitation of TFDEMOA with MEDDEMO1 to confirm the mutants lose the interaction but retain chromatin binding (CUT&RUN).",
      "positive_controls": [
        "Acute TFDEMOA degradation, which should reduce viability in Synthetic epithelial carcinoma (fixture) models (median gene effect -1.05 gene_effect_score).",
        "A pan-essential gene knockdown, to confirm assay sensitivity in every lineage tested."
      ],
      "negative_controls": [
        "Non-dependent lineages, where TFDEMOA loss should not reduce viability (out-of-context median -0.12 gene_effect_score).",
        "TFDEMOA mutants outside the predicted interface, which should retain both MEDDEMO1 binding and rescue activity.",
        "Non-targeting guide and vehicle-only degron arms."
      ],
      "possible_outcomes": [
        {
          "outcome": "Interface mutants fail to rescue viability and the TFDEMOA target program stays off, while DNA binding is intact.",
          "interpretation_change": "Supports the hypothesis that the TFDEMOA-MEDDEMO1 interaction, not TFDEMOA abundance, carries the dependency."
        },
        {
          "outcome": "Interface mutants rescue viability and transcription as well as wild-type TFDEMOA.",
          "interpretation_change": "Refutes the MEDDEMO1-dependence of the phenotype. This candidate should be withdrawn from structure-based follow-up."
        }
      ],
      "limitations": [
        "Cell-line models do not reproduce the tumour microenvironment, and a dependency that holds in culture may not hold in vivo."
      ]
    },
    "limitations": [
      "SYNTHETIC FIXTURE RUN. No claim here is scientific evidence.",
      "Computational results only. No binding, safety, efficacy, or experimental validation is claimed.",
      "DepMap-style cancer-cell-line selectivity is not normal-tissue safety.",
      "Four candidates carry real literature interface evidence but no dependency quantification; they are unranked, not rejected."
    ]
  },
  "sources": [
    {"source_id": "SRC-FIX-DEP", "name": "Synthetic dependency fixture", "url": "synthetic://fixtures/dependency", "tier": "synthetic", "version": "fixture-1.0"},
    {"source_id": "SRC-FIX-INT", "name": "Synthetic interaction fixture", "url": "synthetic://fixtures/interaction", "tier": "synthetic", "version": "fixture-1.0"},
    {"source_id": "SRC-FIX-NORM", "name": "Synthetic normal-tissue fixture", "url": "synthetic://fixtures/normal-tissue", "tier": "synthetic", "version": "fixture-1.0"},
    {"source_id": "SRC-MONTE-2025", "name": "Monte et al., Nat Commun 2025 — MED23-ELK1 cryo-EM", "url": "https://doi.org/10.1038/s41467-025-59014-8", "tier": "public_primary", "version": "2025"},
    {"source_id": "SRC-PDB-9F6Y", "name": "RCSB PDB 9F6Y", "url": "https://www.rcsb.org/structure/9F6Y", "tier": "public_primary", "version": "2025"},
    {"source_id": "SRC-NCOMMS-11149", "name": "MED23-RUNX2, Nat Commun 2016", "url": "https://doi.org/10.1038/ncomms11149", "tier": "public_primary", "version": "2016"}
  ]
}
```

### Populated `screen`, once Vina has run

Illustrative shape only — the numbers below are placeholders, not a run.

```json
{
  "candidate_id": "ELK1-MED23",
  "status": "complete",
  "blocked_reason": null,
  "receptor": {
    "pdb_id": "9F6Y",
    "receptor_path": "runs/demo-01/structure/receptor_9f6y_med23.pdb",
    "search_box": {"mode": "coordinates", "center": [12.4, -3.1, 44.8], "size": [20.0, 20.0, 20.0]}
  },
  "tools": ["pdb-fetch-entry", "vina-docking"],
  "caveat": "Docking score is not binding affinity.",
  "compounds": [
    {
      "id": "CHEMBL000000",
      "smiles": "PLACEHOLDER",
      "source_url": "https://www.ebi.ac.uk/chembl/compound_report_card/CHEMBL000000/",
      "docking_score": -8.4,
      "unit": "kcal/mol",
      "pose_path": "runs/demo-01/screen/poses/CHEMBL000000.pdbqt",
      "notes": ["Pose occupies the F378 subpocket (MED23 I339/L343/F379)."]
    }
  ]
}
```

---

## Screen coverage

| # | Screen | Reads |
|---|---|---|
| 9 | Candidate table | `candidates[]`: `gene`, `disease_context`, `dependency.selectivity_delta`, `involvement`, `score`, `status` |
| 10 | Evidence panel | `candidates[].claims[]` (+ `sources[]` for the tier badge) |
| 11 | Rejection view | `rejections[]` |
| 12 | Structure + pocket | `structure` |
| 13 | Compound shortlist | `screen` (`caveat` is mandatory on this screen) |
| 14 | One-screen summary | `summary` + `run.banner` + `run.checkpoints[]` where `status == "open"` |

Nothing else is in the file. If a screen needs a field that is not here, that is
a `reagent-demo/2` conversation with Amir, not a UI workaround.

## Contradictions with the code, as of 2026-08-15

1. **`selectivity_delta` sign.** `reagent_workflow/gates.py` and
   `scoring.py` treat it as target-minus-other (negative = selective).
   `dependency_scout/ranking.py:gate()` fails a candidate when
   `selectivity_delta < 0.35`, i.e. it expects a positive magnitude — against
   the fixture values in this repo every candidate would fail that line. This
   contract freezes the negative convention; the adapter negates
   `dependency_scout` values, or `ranking.py` gets fixed. Either way, one of the
   two files is wrong and it is not this one.
2. **No `tractability` in `reagent_workflow`.** `MediatorEvidence` has no
   equivalent of `MediatorLink.tractability`, so every `reagent_workflow`
   candidate emits `"unknown"` and no `screening_concerns`. This is the schema
   gap Andrey recorded at checkpoint 2: "mapped but not tractable" (RUNX2)
   exists only on the `dependency_scout` side.
3. **`docking_score` has no upstream field name.** `proto_bridge.py` compiles
   `VinaDockingInput` and never reads an output. The key is defined here; the
   mapping from the Vina output is Vraj's.
