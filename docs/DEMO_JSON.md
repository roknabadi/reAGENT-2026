# `demo.json` — the contract between the agent and the UI

TASKS.md #2. **Status: proposed by Amir, awaiting Vraj's confirmation.**
Blocks UI tasks #9–#14.

One file per run. The UI reads it and renders. No server, no build step, no
network, no live calls.

```bash
reagent-agent export-demo <run_id>            # writes runs/<run_id>/demo.json
reagent-agent demo <run_id> --by "<name>"     # full demo, emits demo.json too
```

A ready-to-build-against file exists now, from a fixture run:
`runs/<run_id>/demo.json`. It is ~46 KB on the fixture run and loads with `JSON.parse`.

## Two guarantees you can build on

1. **Every key is always present.** Missing data is `null` or `[]`, never an
   absent key. You never need `if ("x" in obj)`. This is the done-when for
   screen #9, and it holds even for a run that stopped at the checkpoint with no
   structure, no report, and no experiment — there is a test for exactly that.
2. **It is denormalised.** A candidate carries its own claims, citations, gate
   failures, and selectivity numbers. You never join across arrays to render a
   row. `evidence[]` and `sources[]` are there when you want the full table, not
   because a row needs them.

The shape is frozen at `schema_version: "1.0"`. If a field changes meaning the
version bumps and I tell you. Adding a new optional field will not break you,
because you are reading keys you already know.

## Top level

| Key | Type | For |
|---|---|---|
| `schema_version` | `"1.0"` | guard |
| `generated_at` | ISO8601 | footer |
| `run` | object | provenance strip |
| `summary` | object | screen #14 |
| `candidates[]` | array | screens #9, #10 |
| `rejected[]` | array | screen #11 ⭐ |
| `evidence[]` | array | screen #10 |
| `sources[]` | array | citation links |
| `structure` | object | screen #12 |
| `compounds` | object | screen #13 |
| `checkpoints[]` | array | who approved what |
| `next_experiment` | object | judging criterion |
| `limitations[]` | array | always show |

## `candidates[]` and `rejected[]` — same row shape

Both arrays hold the identical object, so one render function does both.
`candidates[]` is gate-eligible and ranked; `rejected[]` is not.

```jsonc
{
  "candidate_id": "CAND-SELECTIVE",
  "rank": 1,                       // null when rejected
  "status": "hero",                // "hero" | "eligible" | "rejected"
  "transcription_factor": "TFDEMOA",
  "mediator_subunit": "MEDDEMO1",
  "disease_context": "…",
  "hypothesis": "…",

  "score": 0.804,                  // null if unscored
  "evidence_completeness": 1.0,
  "score_components": [
    { "name": "dependency_strength", "definition": "…", "raw": -1.05,
      "unit": "gene_effect_score", "normalized": 0.63, "weight": 0.25,
      "weighted": 0.156, "missing": false, "uncertainty": null }
  ],

  "selectivity": {
    "median_target_effect": -1.05, "median_other_effect": -0.12,
    "selectivity_delta": -0.93, "target_dependent_fraction": 0.83,
    "other_dependent_fraction": 0.06, "n_target_models": 18,
    "n_other_models": 420, "unit": "gene_effect_score",
    "awaiting_dependency_data": false
  },

  "involvement": "direct",         // direct | indirect | predicted | unknown
  "interacting_region_mapped": true,
  "tf_region": "activation domain, residues 22-33",
  "interface_tractability": "folded_domain",  // short_linear_motif | folded_domain | unknown
  "screening_concerns": ["…"],     // advisory, not a gate
  "calibration_only": false,       // ELK1/ELF3 controls — never show as a result

  "gate_eligible": true,
  "gate_failures": [               // [] when eligible
    { "gate": "mediator_region_mapped", "reason": "…full sentence…" }
  ],

  "claims": [
    { "statement": "…", "support": "direct_experimental",
      "citations": ["https://…"], "note": "…limitations…" }
  ],
  "evidence_ids": ["EV-…"],
  "contradicting_evidence_ids": ["EV-…"],   // show these; do not hide them
  "uncertainty": ["…"]
}
```

`support` is one of `direct_experimental`, `genetic_functional`,
`computational_prediction`, `inference` — same vocabulary as
`dependency_scout.SupportType`, so the badge colours match across the app.

### Screen #11 note

`gate_failures[]` is populated on rejected rows and each entry has both a
`gate` and a full-sentence `reason` meant to be shown verbatim. The fixture run
currently yields four rejections covering all three negative controls named in
PROJECT.md:

| Candidate | Gate | Why it is here |
|---|---|---|
| `CAND-BROAD` | `broad_essentiality` | pan-essential TF |
| `CAND-OVEREXPRESSED` | `dependency_strength` | overexpressed, no dependency |
| `CAND-PULLDOWN-ONLY` | `mediator_region_mapped` | association, no mapped contact |
| `CAND-UNSUPPORTED-MEDIATOR` | `mediator_support` | no assay, no source |

## `structure` — screen #12

```jsonc
{
  "status": "predicted",           // "none" | "predicted" | "experimental"
  "candidate_id": "…",
  "tf_region": "…",                // the mapped region, for the pocket callout
  "interface_residues": {},        // {} until a real target is modelled
  "results": [
    { "request_id": "…", "model": "boltz2", "purpose": "complex_interface",
      "status": "cached", "source": "fixture",
      "confidence": { "plddt": 0.81, "ptm": 0.74, "iptm": 0.66, "avg_pae": 8.4 },
      "chain_map": { "A": "transcription_factor", "B": "mediator_subunit" },
      "limitations": ["…"], "unresolved_questions": ["…"] }
  ],
  "comparison_verdict": "inconsistent",   // consistent | inconsistent | insufficient
  "agreements": ["…"], "disagreements": ["…"],
  "confidence_delta": { "plddt_boltz2_minus_esmfold2_mean": 0.06 },
  "caveats": ["Model agreement is not experimental validation…"]
}
```

Boltz2 rows are the complex; ESMFold2 rows are monomer checks only and cannot
speak to the interface. Please render the `caveats` — the "predicted, not
observed" line is a judging point, not boilerplate.

## `compounds` — screen #13, **your slot**

Emitted with the shape present and `poses: []` so you can build the screen
before the docking run exists. The workflow never writes poses; the docking
stage (TASKS.md #5, #6) fills this in.

```jsonc
{
  "status": "not_run",             // "not_run" | "blocked" | "screened"
  "search_region_basis": null,     // the sentence justifying the box
  "poses": [
    { "ligand_id": "CHEMBL…", "smiles": "…", "score": -8.4,
      "score_unit": "vina_kcal_per_mol", "rank": 1, "kept": true,
      "rationale": "…", "artifact_path": "…", "source_id": "SRC-…" }
  ],
  "blockers": ["…"],
  "caveat": "A docking score is not a binding affinity…"
}
```

If that pose shape does not match what Vina gives you, **change it and tell
me** — I will follow your field names. This is a guess at your output, and
yours is the real one.

## `summary.chain` — screen #14

Five hops, in order, each with a status so you can colour the arrow:

```jsonc
[ { "step": "disease",              "value": "…", "status": "established", "detail": "…" },
  { "step": "transcription_factor", "value": "TFDEMOA", "status": "established" },
  { "step": "mediator_contact",     "value": "TFDEMOA-MEDDEMO1", "status": "established" },
  { "step": "interface",            "value": "inconsistent", "status": "predicted" },
  { "step": "compounds",            "value": "not_run", "status": "blocked" } ]
```

`status` is one of `established`, `predicted`, `missing`, `blocked`. The steps
and their order are fixed, so the layout can be static.

## What is not in here, deliberately

- No live URLs to fetch. Citations are links for the user to click, not for the
  page to load.
- No credentials. There is a test asserting none appear.
- Nothing is read back into the pipeline from this file. It is a projection; if
  you need a field, ask and I will add it rather than you deriving it.

## Open questions for Vraj

1. **Pose shape** — does `DemoPose` match your Vina output? Field names are a
   guess.
2. **`interface_residues`** — `{chain: [resnum]}` today, empty until a real
   target. Is that what screen #12 wants, or do you want them pre-formatted?
3. **Size** — one file, currently ~46 KB on fixtures. With real evidence it will
   grow. Say if you want `evidence[]` trimmed to only what candidates reference.
4. **Anything screens #9–#14 need that is not here.** Cheaper to add now than
   after you have built against it.
