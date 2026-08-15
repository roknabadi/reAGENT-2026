# Amir — work order (@roknabadi)

Saturday 2026-08-15. **Final submission Sunday 10:45 AM. Anything not demoable by
Sunday 9:00 AM is not in the demo.**

You own the middle of the pipe and the one artifact the UI reads:
`demo.json` emission, the adapter between the two model packages, Kevin's ranking
weights, the structure stage on a real target, the next-experiment output, and the
BenchFlow trace on the real pipeline.

Vraj owns real data in, compounds/docking out, and all of the UI. **You do not
touch `src/dependency_scout/` except to import from it.** He does not touch
`src/reagent_workflow/`.

Everything in the repo today is synthetic fixture data. Every number you emit
before Vraj lands real DepMap ingest is a test, not evidence, and must say so.

---

## Order of attack

| # | Task | Est | Blocks | Runs parallel with Vraj? |
|---|---|---|---|---|
| A1 | `demo.json` emitter + frozen schema | 2.0 h | **Vraj UI #9–#14 (six tasks)** | yes — do it first, alone |
| A2 | Model adapter `dependency_scout` ↔ `reagent_workflow` | 1.5 h | Vraj's real DepMap reaching the agent | yes — while he pulls DepMap |
| A3 | Kevin's ranking weights behind one table | 0.75 h | nothing; unblocks Kevin signing | yes |
| A4 | Structure stage on the real ELK1–MED23 pair + abstain path | 3.0 h | Vraj UI #12, his docking box | yes |
| A5 | Next experiment from a real run | 0.75 h | Vraj UI #14 | yes |
| A6 | One-command real end-to-end + BenchFlow trace | 1.0 h | the stage demo | no — needs A1–A5 |
| A7 | Freeze: regenerate every artifact, tag, hand Vraj the files | 0.5 h | submission | no |

Hard sequence: **A1 → (A2 ‖ A3 ‖ A4) → A5 → A6 → A7.** A1 is first because six of
Vraj's tasks cannot start without the shape. Ship him a committed example file
within two hours even if the run behind it is still the fixture.

Rules that do not bend: branch per task, PR to `main`, never push to `main`.
Ask Andrey before any paid Modal dispatch. Synthetic data is labelled synthetic.
No claim of binding, safety, efficacy, or validation from anything computed here.

---

## A1 — `demo.json`: the one artifact the UI reads

**Goal.** One self-contained JSON file, written from a finished run directory, that
the UI opens with `fetch('demo.json')` and nothing else.

### WHY

Vraj's tasks #9–#14 are six screens that all read this file. Until the keys are
frozen he is guessing, and every guess is rework at 2 AM. A demo that needs a
network on stage is a demo that fails on stage.

### FILES

- `src/reagent_workflow/demo_export.py` — new, the only new module in A1
- `src/reagent_workflow/cli.py` — add `agent demo-json` subcommand (~12 lines)
- `docs/demo/demo.example.json` — committed output of the fixture run, for Vraj
- `tests/test_agent_workflow.py` — new `DemoExportTests` class

### API

```python
# src/reagent_workflow/demo_export.py
DEMO_SCHEMA_VERSION = "demo-1"

def build_demo_payload(store: RunStore, *, compounds: dict | None = None) -> dict: ...
def write_demo(store: RunStore, path: Path | None = None,
               *, compounds: dict | None = None) -> Path: ...
```

Reads only what is already on disk — `run_state.json`, `input/candidates.json`,
`evidence/evidence.jsonl`, `decisions/scorecards.jsonl`, `decisions/rejections.jsonl`,
`decisions/checkpoints.jsonl`, `structure/comparison.json`, `structure/*/*.json`,
`reports/next_experiment.json`, `reports/final_report.json`. No re-computation, no
network, no import of `dependency_scout`.

Default path `runs/<run_id>/demo.json` so `store.rebuild_manifest()` hashes it.
`--out` copies it wherever the UI lives.

CLI: `agent demo-json <run_id> [--out PATH] [--compounds PATH]`

`--compounds` splices Vraj's docking output straight into `compounds`. Validate only
that it is an object with a `poses` list; do not model it. He owns that shape.

### Frozen schema

Top level, exactly these keys:

```
schema_version, generated_at, run, disease_context, partner_gene,
candidates[], hero, structure, compounds, next_experiment,
human_decisions[], sources[], limitations[], trace_summary
```

Per candidate, exactly these keys:

```
candidate_id, gene, partner_gene, disease_context, rank, status,
score, evidence_completeness, score_components[], dependency,
mediator, claims[], gate
```

`status` ∈ `hero | eligible | rejected | calibration_control | awaiting_dependency_data`.
Every candidate appears — rejected ones included, with their reasons. That is
Vraj's screen #11 and it is the screen that wins.

### EXPECTED OUTPUT

Real values from the fixture run (`agent init demo1 && agent run demo1`, 2026-08-15):

```json
{
  "schema_version": "demo-1",
  "generated_at": "2026-08-15T21:04:11.882Z",
  "run": {
    "run_id": "demo1",
    "stage": "COMPLETE",
    "status": "completed",
    "fixture_run": true,
    "config_hash": "9c1f…",
    "git_commit": "100138c…",
    "weights": {"dependency_strength": 0.25, "disease_specificity": 0.25,
                "mediator_evidence_quality": 0.20, "structural_tractability": 0.15,
                "dependency_prevalence": 0.10, "normal_cell_completeness": 0.05},
    "gate_thresholds": {"max_median_target_effect": -0.5, "min_target_models": 5,
                        "max_other_dependent_fraction": 0.5,
                        "max_selectivity_delta": -0.3, "min_mediator_support": 0.3,
                        "require_mapped_interacting_region": true}
  },
  "disease_context": "Synthetic epithelial carcinoma (fixture)",
  "partner_gene": "MEDDEMO1",
  "candidates": [
    {
      "candidate_id": "CAND-SELECTIVE",
      "gene": "TFDEMOA",
      "partner_gene": "MEDDEMO1",
      "disease_context": "Synthetic epithelial carcinoma (fixture)",
      "rank": 1,
      "status": "hero",
      "score": 0.80375,
      "evidence_completeness": 1.0,
      "score_components": [
        {"name": "dependency_strength", "raw": -1.05, "unit": "gene_effect_score",
         "normalized": 0.625, "weight": 0.25, "weighted": 0.15625, "missing": false,
         "definition": "Median gene-effect score of the target gene across models…",
         "uncertainty": null, "evidence_ids": ["EV-DEP-A1", "EV-DEP-A2"]}
      ],
      "dependency": {
        "median_target_effect": -1.05, "median_other_effect": -0.12,
        "selectivity_delta": -0.93, "selectivity_convention": "target minus other; more negative is more selective",
        "n_target_models": 18, "n_other_models": 420,
        "target_dependent_fraction": 0.83, "other_dependent_fraction": 0.06,
        "mann_whitney_p": 1e-09, "effect_unit": "gene_effect_score",
        "source_url": "synthetic://fixtures/dependency"
      },
      "mediator": {
        "interacting_region_mapped": true,
        "tf_region": "activation domain, residues 22-33 (synthetic)",
        "involvement": "direct",
        "tractability": "unknown",
        "ready_for_structural_modeling": true,
        "screening_concerns": ["interface tractability not assessed"],
        "calibration_only": false,
        "assay": "synthetic crosslinking mass spectrometry fixture",
        "interaction_support": 0.82
      },
      "claims": [
        {"statement": "…", "support": "direct_experimental",
         "citations": ["synthetic://fixtures/interaction"], "note": null}
      ],
      "gate": {"eligible": true, "passed": ["dependency_strength", "…"], "failures": []}
    },
    {
      "candidate_id": "CAND-PULLDOWN-ONLY",
      "gene": "TFDEMOE",
      "rank": null,
      "status": "rejected",
      "score": null,
      "gate": {
        "eligible": false,
        "passed": ["dependency_strength", "sample_support", "broad_essentiality",
                   "disease_specificity", "mediator_support", "provenance"],
        "failures": [{
          "gate": "mediator_region_mapped",
          "reason": "TF-Mediator contact is not mapped (TFDEMOE-MEDDEMO1): synthetic whole-protein pull-down fixture establishes association but identifies no interacting region, so there is no contact point to model or screen against"
        }]
      }
    }
  ],
  "structure": {"status": "missing", "abstained": false, "abstain_reason": null,
                "candidate_id": null, "method": null, "public_id": null,
                "confidence": {}, "interface_residues": {}, "verdict": null,
                "agreements": [], "disagreements": [], "caveats": []},
  "compounds": {"status": "blocked", "poses": [], "blockers": ["no docking run yet"],
                "disclaimer": "Docking score is not binding affinity. No binding, safety, or efficacy is claimed."},
  "next_experiment": null,
  "human_decisions": [{"checkpoint_id": "demo1-hero", "status": "approved",
                       "resolved_by": "tester", "note": "…"}],
  "sources": [{"source_id": "SRC-FIX-DEP", "name": "…", "url": "synthetic://…",
               "tier": "synthetic", "version": "…"}],
  "limitations": ["FIXTURE RUN: this run used clearly labelled synthetic test data…"],
  "trace_summary": {"events": 71, "by_type": {"stage.started": 6, "candidate.gated": 6}}
}
```

CLI:

```
$ agent demo-json demo1 --out docs/demo/demo.json
{
  "demo_json": "runs/demo1/demo.json",
  "copied_to": "docs/demo/demo.json",
  "candidates": 6,
  "rejected": 4,
  "fixture_run": true,
  "bytes": 41822
}
```

### TESTS TO WRITE (`class DemoExportTests(TempRunCase)`)

| Test | Assertion |
|---|---|
| `test_top_level_keys_are_frozen` | `set(payload) == DEMO_TOP_LEVEL_KEYS` exactly — no extras, no omissions |
| `test_every_candidate_appears_including_rejections` | `len(payload["candidates"]) == len(bundle.candidates)`; every id in `rejections.jsonl` present with `status == "rejected"` |
| `test_every_rejection_names_a_gate_and_a_reason` | for each rejected candidate, `gate["failures"]` non-empty and every entry has non-empty `gate` and `reason` strings |
| `test_no_network_and_no_absolute_paths` | serialized file contains no `http://localhost`, no `/Users/`, no absolute path outside the run dir |
| `test_fixture_run_is_labelled_first` | `payload["run"]["fixture_run"] is True` and `"FIXTURE" in payload["limitations"][0]` |
| `test_builds_on_a_run_stopped_at_the_checkpoint` | build after `run_until_checkpoint()` only: no exception, `structure["status"] == "missing"`, `next_experiment is None` |
| `test_compounds_merge_is_passed_through` | `build_demo_payload(store, compounds={"status":"screened","poses":[{"ligand_id":"X"}]})` puts the pose through untouched and keeps the disclaimer string |

### DONE WHEN

`agent demo-json demo1 --out docs/demo/demo.json` exits 0, the file loads with
`json.load`, `docs/demo/demo.example.json` is committed, and Vraj has the path in
`team/status/amir.md`. All seven tests pass.

### ESTIMATE / CUT

2.0 h. If short: drop `trace_summary` and `score_components[].definition`, keep
`candidates[].gate.failures[]`. **Never cut the rejection block** — it is screen #11.

---

## A2 — Model adapter between the two packages

**Goal.** A candidate built in `dependency_scout` is consumable by `reagent_workflow`
and back, with no shared types and no silent unit errors.

### WHY

Vraj's real DepMap ingest lands as `dependency_scout.models.DependencyEvidence`.
The agent only eats `reagent_workflow.models.CandidateHypothesis`. Without this,
real numbers never reach the gates, the score, the checkpoint, or `demo.json`, and
the whole weekend stays synthetic.

### FILES

- `src/reagent_workflow/adapters.py` — new
- `tests/test_agent_workflow.py` — new `AdapterTests`

`reagent_workflow` may import `dependency_scout`; **not the reverse**. Import inside
the functions (`# noqa: PLC0415`) so `reagent_workflow` still runs where the other
package is absent, matching the existing pattern in `structure.py`.

### API

```python
def to_dependency_evidence(dep: "dependency_scout.models.DependencyEvidence",
                           *, source_id: str, evidence_ids: list[str]
                           ) -> reagent_workflow.models.DependencyEvidence: ...

def to_mediator_evidence(link: "dependency_scout.models.MediatorLink",
                         *, transcription_factor: str, interaction_support: float,
                         assay: str, source_id: str, evidence_ids: list[str]
                         ) -> reagent_workflow.models.MediatorEvidence: ...

def to_source_record(src: "dependency_scout.models.SourceRecord", *, source_id: str
                     ) -> reagent_workflow.models.SourceRecord: ...

def claims_to_evidence(claims: list["Claim"], *, prefix: str, source_id: str
                       ) -> list[reagent_workflow.models.EvidenceRecord]: ...

def to_candidate(rc: "dependency_scout.models.RankedCandidate", *, candidate_id: str,
                 mediator_subunit: str = "MED23", interaction_support: float,
                 assay: str, sources: dict[str, ...]) -> tuple[CandidateHypothesis,
                                                               list[EvidenceRecord],
                                                               list[SourceRecord]]: ...
```

**Three things the adapter must get right, and one it must refuse:**

1. **`selectivity_delta` flips sign.** `dependency_scout.ranking` computes
   `other.median − target.median` (positive = selective, gate wants `>= 0.35`).
   `reagent_workflow` uses `target − other` (negative = selective, gate wants
   `<= -0.3`), and `ingest._validate_candidate` rejects the candidate outright if
   `|target − other − delta| > 0.02`. **Negate it.** A copied value fails ingest
   with a confusing message and costs an hour.
2. `SupportType` → `interaction_type`, one dict, no cleverness:

   | `Claim.support` | mapped region? | `interaction_type` |
   |---|---|---|
   | `direct_experimental` | yes | `direct_binding` |
   | `direct_experimental` | no | `complex_member` |
   | `genetic_functional` | — | `genetic` |
   | `computational_prediction` / `inference` | — | `inferred` |

3. `Claim.citations[]` become `SourceRecord(url=…, tier=public_primary)` +
   `EvidenceRecord(interpretation=…, claim=statement)`. Public sources need
   `version` or `retrieved_at` or the `SourceRecord` validator raises.
4. **Refuse to invent `interaction_support`.** `MediatorLink` carries no numeric
   support. Make it a required keyword argument with no default. If Andrey has not
   given a number, pass `None`-equivalent by not calling the adapter — the gate then
   rejects the candidate as unsupported, which is the correct outcome.

The adapter carries **numbers, not verdicts**. `dependency_scout.ranking.gate` and
`reagent_workflow.gates.evaluate_gates` disagree on thresholds (min models 3 vs 5,
other-dependent fraction 0.35 vs 0.50). Do not reconcile them this weekend. The
workflow re-gates and the workflow's answer is the one in `demo.json`.

### EXPECTED OUTPUT

```python
>>> rc = RankedCandidate.model_validate(json.load(open("outputs/demo_candidates.json"))[0])
>>> rc.dependency.selectivity_delta
0.875
>>> cand, evidence, sources = to_candidate(rc, candidate_id="CAND-SELECTIVE-TF",
...     interaction_support=0.8, assay="co-IP", sources={...})
>>> cand.dependency.selectivity_delta
-0.875
>>> cand.dependency.median_target_effect - cand.dependency.median_other_effect
-0.875
>>> ingest(InputBundle(sources=sources, evidence=evidence, candidates=[cand]))[0].ok
True
```

### TESTS TO WRITE (`class AdapterTests(unittest.TestCase)`)

| Test | Assertion |
|---|---|
| `test_selectivity_delta_is_negated_not_copied` | scout `+0.875` → workflow `-0.875`, and it equals `median_target - median_other` within 0.02 |
| `test_adapted_candidate_survives_ingest` | `ingest(bundle)` returns `report.ok is True` and `rejected_candidates == []` |
| `test_adapter_refuses_to_invent_interaction_support` | `to_mediator_evidence(link)` without `interaction_support` raises `TypeError` |
| `test_elk1_example_round_trips_as_direct` | load `examples/mediator_link_elk1_med23.json` → `MediatorLink` → `MediatorEvidence`; both `.ready_for_structural_modeling` are `True`; `evaluate_gates` produces no `mediator_region_mapped` failure |
| `test_cebpb_example_stays_rejected` | same path on `mediator_link_cebpb_med23.json`: `interaction_type == "complex_member"`, `interacting_region_mapped is False`, gate failure `mediator_region_mapped` present |
| `test_every_claim_citation_becomes_a_source` | number of emitted `SourceRecord`s ≥ number of distinct citation URLs; every one starts `https://` |

### DONE WHEN

A `RankedCandidate` produced by Vraj's `dependency-scout discover` on real DepMap
loads through `to_candidate`, `agent init <run> --input <adapted bundle>` succeeds,
and `agent run <run>` reaches the hero checkpoint with `fixture_run: false`.

### ESTIMATE / CUT

1.5 h. If short: implement `to_candidate` only in the scout → workflow direction.
The reverse direction has no consumer this weekend.

---

## A3 — Kevin's ranking weights behind one table

**Goal.** The six weights live in one readable table; the factor→component mapping
can be filled in without touching scoring logic.

### WHY

Kevin locked 25/25/20/15/10/5. He has **not** supplied which factor gets which
number. Current defaults in `config.py` are 25/15/25/10/15/10 — a different split.
The mapping must be a one-line-per-row edit at 2 AM, not a code change.

### FILES

- `src/reagent_workflow/config.py` — add `WEIGHT_TABLE`, drive `ScoringWeights`
  defaults from it (six one-line edits; the `weights_sum_to_one` validator stays)
- `src/reagent_workflow/demo_export.py` — surface `run.weights` (already in A1)
- `tests/test_agent_workflow.py` — `WeightTableTests`

### API

```python
# Kevin's locked weights, re:AGENT 2026-08-15. The six NUMBERS are fixed.
# The factor -> component mapping below is PROVISIONAL until Kevin confirms.
# Editing this table is the only supported way to change the ranking.
WEIGHT_TABLE: tuple[tuple[str, str, float], ...] = (
    # scoring component            Kevin's factor            weight
    ("dependency_strength",        "dependency strength",     0.25),
    ("disease_specificity",        "disease relevance",       0.25),
    ("mediator_evidence_quality",  "evidence quality",        0.20),
    ("structural_tractability",    "tractability",            0.15),
    ("dependency_prevalence",      "confidence",              0.10),
    ("normal_cell_completeness",   "specificity",             0.05),
)
_W = {component: weight for component, _factor, weight in WEIGHT_TABLE}
```

`ScoringWeights` keeps its six named fields; each default becomes
`Field(default=_W["<name>"], ge=0, le=1)`. Nothing in `scoring.py` changes.

Changing the table changes `RunConfig.hash()`, so scorecards from an earlier run are
stale. **Re-run, do not resume.**

### EXPECTED OUTPUT

`CAND-SELECTIVE` on the fixture, current weights vs the table above:

| Component | normalized | now | Kevin | weighted (Kevin) |
|---|---|---|---|---|
| dependency_strength | 0.625 | 0.25 | 0.25 | 0.15625 |
| disease_specificity | 1.000 | 0.25 | 0.25 | 0.25000 |
| mediator_evidence_quality | 0.820 | 0.15 | 0.20 | 0.16400 |
| structural_tractability | 0.500 | 0.10 | 0.15 | 0.07500 |
| dependency_prevalence | 0.830 | 0.15 | 0.10 | 0.08300 |
| normal_cell_completeness | 1.000 | 0.10 | 0.05 | 0.05000 |
| **total** | | **0.80375** | | **0.77825** |

### TESTS TO WRITE

| Test | Assertion |
|---|---|
| `test_table_is_kevins_six_numbers` | `sorted(w for _,_,w in WEIGHT_TABLE) == [0.05,0.10,0.15,0.20,0.25,0.25]` |
| `test_table_covers_every_scored_component` | `{c for c,_,_ in WEIGHT_TABLE} == {c.name for c in score_candidate(fixture, RunConfig()).components}` |
| `test_weights_still_sum_to_one` | `ScoringWeights()` constructs; a tampered table raises `ValueError` |
| `test_changing_the_table_changes_the_config_hash` | two `RunConfig`s with different weights have different `.hash()` |

### DONE WHEN

`WEIGHT_TABLE` is the only place any weight appears, all four tests pass, and
`team/status/amir.md` asks Kevin one question: *which factor gets which of
25/25/20/15/10/5?* Fill his answer in by editing six numbers.

### ESTIMATE / CUT

0.75 h. Cannot be cut — the pitch says "rank and prioritize", and a judge will ask
what the weights are. Answer with the table on screen.

---

## A4 — Structure stage on a real target (ELK1–MED23), and the abstain path

**Goal.** Real confidence numbers for a real pair, cached, with a comparison written
— and an explicit, visible abstention when no localized interface exists.

### WHY

Everything structural so far is a synthetic fixture with invented 64-residue
sequences. Vraj's docking box (#6) and UI screen #12 both need a real interface.
Equally important: the pipeline must refuse to dock blindly, and that refusal has to
be a recorded artifact, not a silent `return None`.

### Model policy — not negotiable

| Model | Role | Enforced by |
|---|---|---|
| **Boltz-2 via Proto** (`boltz2-prediction`) | the **heterocomplex**: it explicitly predicts biomolecular complexes and supports joint structure + affinity | `StructuralModelRequest.roles_match_the_model` requires `purpose="complex_interface"` and ≥2 chains |
| **ESMFold2** (`esmfold2-prediction`) | **monomer sanity check only** — is each chain foldable in isolation | same validator: `purpose="monomer_confidence"`, exactly 1 chain |

ESMFold2 is never an interface predictor and its agreement is never validation.
`compare_models()` already says so in `ModelComparison.caveat`; do not weaken it.

**Retrieve before you predict.** ELK1–MED23 has a public experimental structure —
PDB **9F6Y**, cryo-EM 3.0 Å, ELK1 MED23-binding motif residues **374–384**
(`PSIHFWSTLS(p)P`), F378 buried, MED23 pocket **I339/L343 (H19), F379/G382/S383 (H21),
V533/M537 (H28)** on the concave face at the HR2/HR3 interface, Kd 81 nM by SPR,
G382F kills binding (doi:10.1038/s41467-025-59014-8). That is `structure.status =
"experimental"` with real `interface_residues` and it costs zero compute. Run Boltz-2
on the same pair as the **general-case demonstration** — the prediction the pipeline
would make for a target with no structure — and show it beside 9F6Y. Do not present
the agreement as validation.

### Abstain rule

If no localized credible interface emerges, **abstain — never dock blindly.**
Today `build_requests()` returns `[]` for two different reasons and the orchestrator
traces only one of them ("partner sequences are missing"), which is wrong half the
time. Fix at the source:

```python
# structure.py
def abstain_reason(candidate: CandidateHypothesis, config: RunConfig) -> str | None:
    """Why no structural request can be built, or None if one can."""
```

`build_requests` returns `[]` when it is non-None. `Orchestrator.run_structure`
writes `structure/abstained.json` (`{"candidate_id", "reason", "abstained_at"}`),
emits `PROTO_REQUEST_INVALID` with the true reason, and returns `None`.
`demo_export` maps that to `structure.status = "abstained"` with `abstain_reason`.

### FILES

- `src/reagent_workflow/structure.py` — `abstain_reason()`, used by `build_requests`
- `src/reagent_workflow/orchestrator.py` — write `structure/abstained.json`; put real
  `interface_residues` into `_hero_hypothesis_payload` (currently hardcoded `{}`)
- `src/reagent_workflow/demo_export.py` — the `structure` block
- `inputs/elk1_med23.bundle.json` — real candidate bundle, `fixture: false`
- `SOURCES.md` — 9F6Y, the DOI, both UniProt accessions with retrieval date
- `tests/test_agent_workflow.py` — `AbstainTests`, extend `StructureTests`

### Getting real numbers without spending money

`allow_live_modal` is **off** by default and Modal is paid — **ask Andrey before
dispatching.** The cheap path uses the existing content-addressed cache:

```bash
# 1. build the request, don't run it
agent init elk1 --input inputs/elk1_med23.bundle.json
agent run elk1
agent checkpoint resolve elk1-hero --decision approve --by <andrey>
agent structure validate elk1          # writes structure/request.json, dispatches nothing
python -c "import json;print(json.load(open('runs/elk1/structure/request.json'))['requests'][0]['input_hash'])"

# 2. fold locally with the boltz CLI (skills/boltz/SKILL.md), or on Modal once approved
boltz predict elk1_med23.yaml --out_dir /tmp/b --output_format pdb

# 3. drop the result into the cache under that input_hash, then re-run
python - <<'PY'
from reagent_workflow.structure import StructureCache
StructureCache(Path("runs/elk1/structure/cache")).put(INPUT_HASH, {
    "confidence": {"plddt": 0.0, "ptm": 0.0, "iptm": 0.0},  # from confidence_*.json
    "model_version": "boltz2", "proto_tools_version": "…", "runtime_ms": 0,
})
PY
agent structure run elk1               # cache hit, no dispatch, real numbers
```

Do **not** set `"fixture": true` in that payload — that flag is what makes
`_result_from_cached` stamp the SYNTHETIC TEST FIXTURE limitation on the result.
Real numbers must not carry it, and fixture numbers must.

Sequence sizing: ELK1 can be a construct around residues ~355–399 (covers the MBM).
Full-length MED23 is ~1370 aa and will not fold quickly on CPU — if the Boltz-2 run
is not producing numbers by **Saturday 8 PM, stop**: keep the 9F6Y experimental path,
set `structure.status = "experimental"`, and record the prediction as not attempted.
That is an honest result and it demos fine.

### EXPECTED OUTPUT

```
$ agent structure run elk1
{
  "verdict": "consistent",
  "agreements": ["Boltz2 reports interface confidence ipTM 0.71."],
  "disagreements": [],
  "confidence_delta": {"plddt_boltz2_minus_esmfold2_mean": -0.04},
  "caveat": "Model agreement is not experimental validation. Both models are predictors and can be jointly wrong."
}
```

`demo.json` structure block:

```json
"structure": {
  "status": "experimental",
  "candidate_id": "CAND-ELK1-MED23",
  "public_id": "9F6Y",
  "method": "PDB retrieval; Boltz-2 complex prediction shown alongside; ESMFold2 monomer check only",
  "interface_residues": {"ELK1": [374,375,376,377,378,379,380,381,382,383,384],
                         "MED23": [339,343,379,382,383,533,537]},
  "confidence": {"plddt": 0.82, "ptm": 0.74, "iptm": 0.71},
  "verdict": "consistent",
  "abstained": false, "abstain_reason": null,
  "caveats": ["Boltz2 predicts the complex; ESMFold2 checks monomers only.",
              "Model agreement is not experimental validation.",
              "ELK1 binding is phosphorylation-dependent (pS383); any screen against this site inherits that condition."]
}
```

And on the negative control:

```
$ agent structure run pulldown-only
{"structure": "abstained",
 "reason": "TF-Mediator contact is not mapped (TFDEMOE-MEDDEMO1): association only, no interacting region, so there is no defensible site to model or screen against"}
```

### TESTS TO WRITE

| Test | Assertion |
|---|---|
| `test_abstains_when_the_region_is_not_mapped` | `run_structure()` returns `None`, `structure/abstained.json` exists, its `reason` mentions the mapped region and **not** missing sequences |
| `test_abstain_reason_distinguishes_the_two_causes` | candidate with mapped region but no sequences → reason names sequences; candidate with sequences but no mapped region → reason names the region |
| `test_no_docking_artifact_is_written_when_abstaining` | after an abstain, `structure/comparison.json` absent and `demo.json`'s `compounds.status == "blocked"` |
| `test_boltz2_is_the_only_complex_predictor` | `build_requests` yields exactly one `model=="boltz2"` with ≥2 chains and every `esmfold2` request has exactly 1 chain |
| `test_real_cached_result_is_not_labelled_a_fixture` | cache payload without `"fixture": true` → `source == "cache"` and no limitation contains `"SYNTHETIC TEST FIXTURE"` |
| `test_hero_artifact_carries_interface_residues` | `reports/hero_hypothesis.json` `structure.interface_residues` is non-empty for the real run |

### DONE WHEN

`runs/elk1/structure/comparison.json` exists with numbers that did not come from
`src/reagent_workflow/fixtures/structure_cache/`, `demo.json` shows real
`interface_residues`, the abstain path is exercised by a test, and Vraj has the
MED23 pocket residues for his docking box.

### ESTIMATE / CUT

3.0 h. Cut in this order: (1) skip the Boltz-2 prediction, keep 9F6Y retrieval +
ESMFold2 monomer check; (2) skip ESMFold2 too, `verdict: "insufficient"` is already a
handled state. **Never cut the abstain path** — it is the honesty the judges asked for.

---

## A5 — Next experiment from a real run

**Goal.** One concrete falsifiable experiment, generated from the real hero candidate,
in `demo.json` and the final report.

### WHY

An explicit judging criterion, and Vraj's screen #14 ends on it. `experiment.py`
already generates a falsifiable design with four outcomes including two that sink the
hypothesis; it has never been run on anything but synthetic genes.

### FILES

- none, if the generator holds — this is a wiring and verification task
- `src/reagent_workflow/demo_export.py` — the `next_experiment` block

Resist adding fields to `NextExperiment`. Anything model-specific (naming MED23
G382F as the published control) goes in the checkpoint note — `agent checkpoint
resolve … --note "…"` is already persisted and already surfaces in `demo.json`
`human_decisions[]`. That is one CLI flag versus a contract change that needs a
`DECISIONS.md` entry and Andrey's signature.

### EXPECTED OUTPUT

```
$ agent experiment elk1
{
  "candidate_id": "CAND-ELK1-MED23",
  "scientific_question": "In <context> models, does the selective dependency on ELK1 require its interaction with the Mediator subunit MED23, rather than ELK1 abundance alone?",
  "outcomes": 4,
  "rubric_score": 0.86
}
```

`demo.json`:

```json
"next_experiment": {
  "question": "…", "perturbation": "…", "readout": "…",
  "positive_controls": ["…"], "negative_controls": ["…"],
  "possible_outcomes": [{"outcome": "…", "interpretation_change": "…"}],
  "limitations": ["…"],
  "rubric_score": 0.86
}
```

### TESTS TO WRITE

| Test | Assertion |
|---|---|
| `test_experiment_names_the_real_pair` | the question contains the hero TF, the Mediator subunit, and the disease context from `run_state`, not fixture strings |
| `test_at_least_one_outcome_refutes_the_hypothesis` | some `interpretation_change` contains `"Refutes"` or `"Contradicts"` |
| `test_controls_cite_the_actual_dependency_numbers` | `positive_controls[0]` contains the hero's `median_target_effect` formatted to 2 dp |
| `test_demo_json_carries_the_experiment` | after `agent experiment`, `demo.json`'s `next_experiment["possible_outcomes"]` has ≥2 entries |

### DONE WHEN

`agent experiment <real run>` returns a rubric score and the same text appears in
`demo.json`, `reports/final_report.md`, and the UI's last screen.

### ESTIMATE / CUT

0.75 h. If short, run it and paste the output — the generator is already written.

---

## A6 — One-command real end-to-end + BenchFlow trace

**Goal.** `agent demo <run> --input <real bundle>` runs the whole pipeline, writes
`demo.json`, exports the BenchFlow trace, and exits 0 on a laptop.

### WHY

Sunday morning you get one shot. A demo assembled from seven commands typed live is
a demo that breaks. Band-4 #15 (BenchFlow trace on the real pipeline) is free once
this exists — `export_trace` and `validate_with_benchflow` already work.

### FILES

- `src/reagent_workflow/cli.py` — `demo.add_argument("--input")`; change
  `args.input = str(FIXTURE_BUNDLE)` to honour a supplied path; add a `demo-json`
  call at the end of `cmd_demo`; make BenchFlow validation non-fatal
- `scripts/demo.sh` — the exact commands, in order, committed

**Landmine:** `cmd_demo` currently `return 0 if result.ok else 1`, and
`validate_with_benchflow` sets `ok = False` when the BenchFlow interpreter is
missing. On any machine without `tools/benchflow`, the demo command exits 1 even
though everything worked. Print the warning, do not fail the demo on it.

### EXPECTED OUTPUT

```
$ agent demo elk1 --input inputs/elk1_med23.bundle.json --by "Andrey Ferrer"
== hero checkpoint (elk1-hero) ==
Approve CAND-ELK1-MED23 (… / ELK1 / MED23) as the hero hypothesis …
recommended: CAND-ELK1-MED23
rejected: ['CAND-CEBPB-MED23', 'CAND-ETV1-MED23']

== approved by Andrey Ferrer ==
== structure (consistent) ==
- Boltz2 reports interface confidence ipTM 0.71.
== next experiment ==
In … models, does the selective dependency on ELK1 require its interaction with MED23 …
== report ==
status=completed confidence=low
== benchflow trace ==
valid=True format=opentraces steps=74 benchflow=0.6.8
== demo.json ==
runs/elk1/demo.json (6 candidates, 2 rejected, fixture_run=false)

run directory: runs/elk1
```

### TESTS TO WRITE

| Test | Assertion |
|---|---|
| `test_demo_accepts_a_non_fixture_bundle` | `main(["demo", "r", "--input", <bundle>, "--by", "t"])` returns 0 and `run_state.fixture_run is False` |
| `test_demo_exit_code_survives_missing_benchflow` | with `BENCHFLOW_PYTHON` pointed at nothing, exit code is still 0 and stdout warns |
| `test_demo_writes_demo_json` | `runs/<id>/demo.json` exists after `agent demo` and parses |
| `test_real_run_trace_has_no_fixture_tag` | exported trace `task.tags` excludes `"fixture"` and `outcome.fixture_run is False` |

### DONE WHEN

`bash scripts/demo.sh` on a clean checkout produces `demo.json`, the report, and a
BenchFlow-valid trace, exit 0, no network beyond what Vraj's ingest already fetched.

### ESTIMATE / CUT

1.0 h. If short: skip `scripts/demo.sh`, keep the `--input` flag and the exit-code fix.

---

## A7 — Freeze

**Goal.** Every artifact regenerated from one commit and handed to Vraj.

### WHY

The UI reads a file. If that file is from a run three commits old, the numbers on
stage do not match the numbers in the repo and someone notices.

### STEPS

1. `python -m pytest tests/ -q` — everything green (7 skips are expected when
   `proto_tools` / BenchFlow are absent).
2. `bash scripts/demo.sh` on the real bundle.
3. Copy `runs/<real>/demo.json` to wherever Vraj's HTML loads it; commit both.
4. Update `SOURCES.md` (9F6Y, the DOI, UniProt accessions, DepMap release) and
   `team/status/amir.md` with the run id, config hash, and git commit.
5. PR to `main`. Andrey signs gates 1 and 2 in `CHECKPOINTS.md` or the demo says
   "calibration only" out loud.

### DONE WHEN

`demo.json` in the repo, the run directory, and the browser are byte-identical, and
`agent status <run>` reports `manifest_drift: []`.

### ESTIMATE

0.5 h. Start it no later than **Sunday 8:00 AM**.

---

## Landmines already in the code

Found while reading, before you trip on them:

| Where | What |
|---|---|
| `dependency_scout/ranking.py` vs `reagent_workflow/models.py` | `selectivity_delta` has **opposite signs** in the two packages. A2 negates it. |
| `store.write_json` → `redact()` | any key whose name contains `auth`, `token`, `secret`, `key` is replaced with `[REDACTED]`. Do not name a `demo.json` field `author`, `authorization`, or `api_key`. |
| `orchestrator._hero_hypothesis_payload` | hardcodes `drug_discovery.status = "blocked"` and the blocker *"Virtual screening is out of scope for this workflow."* — false once Vraj lands docking. Thread the `--compounds` payload into it in A1. |
| same function | `structure.interface_residues` is hardcoded `{}`. Fill it in A4. |
| `cli.cmd_demo` | exits 1 when the BenchFlow interpreter is missing, even on a fully successful run. A6 fixes it. |
| `structure.build_requests` | returns `[]` for two different reasons; the orchestrator traces only "sequences are missing". A4 fixes it. |
| `config.ScoringWeights` | current defaults are 25/15/25/10/15/10 — **not** Kevin's 25/25/20/15/10/5. A3 fixes it. |

---

## Not this weekend — Amir specifically

- Merging `reagent_workflow.models` and `dependency_scout.models`. The adapter is
  the answer. A shared type is a Sunday-morning refactor that eats the demo.
- Any new field on `CandidateHypothesis`, `MediatorEvidence`, `NextExperiment`, or
  `RankedCandidate`. A contract change needs a `DECISIONS.md` entry and Andrey's
  signature, and every one costs more than it returns before Sunday.
- Live Modal dispatch without Andrey saying yes out loud. `allow_live_modal` stays
  off; the cache path in A4 gets real numbers for free.
- Retries, backoff, or resilience beyond the two attempts `_dispatch_live` already
  makes.
- A third scoring dimension, a second checkpoint, or a self-improvement iteration
  past the two `ImprovementIteration` already caps.
- Uploading the BenchFlow trace anywhere. Export and validate locally; publication
  is a separate decision.
- Test coverage for its own sake. The named tests above, nothing more.
- Making `dependency_scout` nicer. It is Vraj's, and it works.
- A UI, a server, a build step, or a schema registry for `demo.json`. It is one
  JSON file read by one HTML file.
