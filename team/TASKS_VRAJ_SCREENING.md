# Vraj — virtual screening work order

Owner: Vraj. Approver: Andrey (checkpoints 4 and 5). Logic owner: Kevin.
Covers `team/TASKS.md` #5 (compound set) and #6 (docking run), and produces the
input Amir needs for the `drug_discovery` block of the demo artifact.

Deadline reality: screening must be **done by Sat 22:00** so the Band-3 UI has
real compounds Sunday morning. Anything not demoable Sunday 09:00 is not in the
demo. Budget below is ~5 h of work plus wall-clock docking time.

## Andrey's constraints → where each one lives

| # | Constraint | Task |
|---|---|---|
| 1 | Model the complex (Boltz-2 for the heterocomplex, ESMFold monomer sanity only) | V2 (and V2 is *skipped* when an experimental complex exists — see below) |
| 2 | Defensible localized site, or **abstain** | V1, V3 |
| 3 | Approved-drug library: DrugCentral preferred, ChEMBL `max_phase=4` secondary | V4 |
| 4 | Vina primary screen, rank by score **plus** pose/interface plausibility, keep multiple poses | V5, V6 |
| 5 | Boltz-2 orthogonal rerank on the top subset | V8 |
| 6 | Top ~10 with name/approval/score/pose/contacts/Boltz/known target/why | V6, V7 |

Pipeline: complex → pocket → library → Vina → Boltz rerank → human pose review
→ shortlist. The abstain branch (V3) is a first-class output, not a failure.

## Read this before you start

- `ProtoScreenSpec` (`src/dependency_scout/models.py:253`) already refuses
  `vina-docking` without `receptor_path`, a `search_box`, and ≥1 ligand SMILES.
  Do not add a second gate; fill the fields.
- `validate_proto_spec` (`proto_bridge.py`) compiles a spec into the installed
  `proto_tools...vina.VinaDockingInput`. It checks the receptor and reference
  ligand exist on disk. `ligands` is a list of **SMILES strings** — Vina's Proto
  wrapper does its own 3D embedding, so you need no local RDKit.
- `ProtoScreenSpec.interface_residues: dict[str, list[int]]` exists and **nothing
  reads or writes it anywhere in the repo**. It is the right home for the pocket
  definition and the input to the search box. Use it.
- **The lazy path for the hero pair**: if the selected TF is ELK1 (or the ELF3
  arm of the same groove), the complex is already solved — **PDB 9F6Y**, cryo-EM
  3.0 Å, MED23 + phospho-ELK1 TAD. Downloading a real structure beats predicting
  one, costs nothing, and needs no GPU. Boltz-2 (V2) is only for a TF with no
  experimental complex. ELK1 is `calibration_only` — a screen against it is a
  method control, never a result. Andrey picks the real target at checkpoint 3.

---

## V1 — Receptor + pocket from the public structure

**Goal.** A prepared receptor PDB on disk and a numeric search box, both derived
from public coordinates.
**Why.** V5 cannot start without them, and `ProtoScreenSpec` will not let you
fake either. Everything downstream inherits this box.

**Files:** `src/dependency_scout/screening.py` (new), `downloads/9f6y.pdb`
(gitignored), `outputs/screen/receptor_med23.pdb` (gitignored), `SOURCES.md`.

**Do.** Fetch `https://files.rcsb.org/download/9F6Y.pdb`. Keep the MED23 chain
only (ATOM records, drop waters/HETATM/other chains) with plain text filtering —
no new dependency. Compute the box centre as the centroid of the side-chain
atoms of the seven MED23 pocket residues from the paper: **I339, L343 (H19),
F379, G382, S383 (H21), V533, M537 (H28)**. Box size 22 Å cube.

```python
# screening.py, ~25 lines total
def pocket_center(pdb_path: Path, chain: str, residues: list[int]) -> tuple[float, float, float]
def prepare_receptor(pdb_path: Path, chain: str, out: Path) -> Path
```

**Expected output** (goes straight into `interface_residues` + `search_box`):

```json
{"interface_residues": {"MED23": [339, 343, 379, 382, 383, 533, 537],
                        "ELK1": [376, 378, 382]},
 "search_box": {"mode": "coordinates", "center": [x, y, z], "size": [22.0, 22.0, 22.0]}}
```

**Tests** (`tests/test_pipeline.py`, 3-atom fixture PDB under `tests/fixtures/`):
- `test_pocket_center_is_the_centroid_of_the_named_residues` — asserts the centre
  of a hand-computed 3-atom fixture to 3 dp.
- `test_prepared_receptor_keeps_one_chain_and_drops_solvent` — asserts no
  `HETATM`, no `HOH`, one chain id in the output.
- `test_elk1_anchor_residue_falls_inside_the_box` — F378 CZ from 9F6Y is inside
  `center ± size/2`. If this fails, the box is centred on the wrong groove.

**Done when.** `outputs/screen/receptor_med23.pdb` exists, the box JSON is
written, and 9F6Y is a row in `SOURCES.md` with the retrieval date.
**Hours:** 0.75.
**Cut:** skip the centroid maths, use `ReferenceLigandBox` with the extracted
ELK1 peptide chain as `reference_ligand_path` (padding 4.0). Works, but the box
is ~2× larger and the screen is correspondingly less focused — say so in the
report if you take it.

## V2 — Boltz-2 complex (only if there is no experimental structure) 💸⏱

**Goal.** A MED23+TF heterocomplex model with interface confidence, when V1 has
nothing to download.
**Why.** Andrey's constraint 1. Boltz-2 predicts complexes explicitly; ESMFold2
is a monomer sanity check and is never an interface predictor (already enforced
by `StructuralModelRequest.purpose` in `reagent_workflow/structure.py`).

**Files:** none of yours — this is Amir's `structure.py` path (`agent structure`).
Your job is to hand him the chain sequences and consume the result.

**Cost warning.** Full-length MED23 is ~1368 aa. Boltz-2 on CPU is minutes for a
33-mer; a 1368-aa complex is not a laptop job. Two options, **both need approval
before you run them**:
1. Modal GPU via `dispatch_to_modal` — **paid**. Ask Andrey first.
2. Truncated MED23 construct. The ELF3 work used MED23 **391–582** in FP and
   split-luciferase (doi:10.7554/eLife.97051.3.sa4); the ELK1 pocket residues
   533/537 sit outside 391–582, so a truncation for the ELK1 site must be
   justified separately and stated as a limitation.

**Expected output.** A `StructuralModelResult` with `confidence` containing
iptm/plddt and `interpretation = predicted`.
**Test:** `test_screen_refuses_a_predicted_interface_without_confidence` — a spec
whose `structure_source == "boltz2"` and whose model confidence is missing must
route to the abstain path in V3, not to docking.
**Done when.** Either an experimental structure is in hand (V2 skipped, say so in
the report) or a Boltz-2 complex with recorded confidence exists.
**Hours:** 0.25 if skipped; 1.5 + queue time if run.
**Cut:** skip entirely. Pick a target with a solved complex. This is the single
biggest time sink in the whole plan.

## V3 — Site defensibility gate, and the abstain output

**Goal.** One function that says *proceed* or *abstain with reasons*, before a
single ligand is docked.
**Why.** Andrey: "if no defensible site exists, ABSTAIN rather than dock
blindly." The benchflow verifier already enforces the shape —
`drug_discovery.status == "blocked"` must carry non-empty `blockers`
(`benchflow/tasks/tf-mediator-hero/verifier/test_output.py:72`).

**Files:** `src/dependency_scout/screening.py`.

**Do.** Abstain when any of: `mediator.involvement != direct`; no mapped
`tf_region`; `tractability == folded_domain` (RUNX2's exact problem — the
concern is already produced by `MediatorLink.screening_concerns`); no receptor;
predicted interface with no confidence numbers. Reuse `screening_concerns`
verbatim as blocker strings rather than re-deriving them.

**Expected output** (`CompoundShortlist` with `status="blocked"`):

```json
{"status": "blocked", "compounds": [],
 "search_region_basis": "no defensible search region: interface is predicted only",
 "blockers": ["folded-domain interface: large buried surface, poor small-molecule tractability compared with a short linear motif"]}
```

**Tests:**
- `test_folded_domain_interface_abstains_instead_of_docking` — RUNX2 example JSON
  in, `status == "blocked"`, blockers mention "folded-domain".
- `test_blocked_screen_must_state_blockers` — `status="blocked"` with empty
  `blockers` raises `ValidationError`.

**Done when.** The abstain JSON renders in the UI's compound panel with reasons.
**This is demo-safe on its own** — if V5 never finishes, V3 is still a shippable,
honest screening output.
**Hours:** 0.5. **Cut:** nothing. This one stays.

## V4 — Approved-drug library ⏱

**Goal.** `downloads/drugcentral_approved.tsv` → a deduplicated list of
`(name, library_id, smiles, approval_status)` plus one `SourceRecord`.
**Why.** Andrey's constraint 3: DrugCentral preferred for repurposing (explicit
FDA/EMA/PMDA sets, downloadable SMILES), ChEMBL `max_phase=4` secondary and kept
for its target/bioactivity annotations at the reporting step (V7).

**Files:** `src/dependency_scout/screening.py`, `SOURCES.md`.

**Do.** Pull the DrugCentral structures SMILES dump from
<https://drugcentral.org/download> — **verify the exact filename and version at
the source; do not trust a URL I guessed.** Record version + retrieval date +
sha256 in `SOURCES.md` and in the `SourceRecord` (`tier = public_primary`).
Standardize with stdlib only:
- salt/mixture strip: keep the longest `.`-separated fragment;
- drop anything containing `[Na`, `[K`, `[Ca`, `[Pt`, `[Gd`, `[Tc` after the
  strip, and anything with no carbon;
- drop biologics: length > 200 chars is a good enough proxy this weekend;
- dedupe on the stripped SMILES string (exact match — no canonicalization
  without RDKit; state that as a limitation);
- keep `approval_status = "approved"` rows only for the primary screen.

**Expected output:**

```
DrugCentral:1234  Imatinib  approved  Cc1ccc(NC(=O)c2ccc(CN3CCN(C)CC3)cc2)cc1Nc1nccc(-c2cccnc2)n1
```
≈4–5 k approved parent structures after cleaning.

**Tests:**
- `test_library_strips_salts_and_keeps_the_parent` — `"CC(=O)O.[Na+]"` → `"CC(=O)O"`.
- `test_library_drops_mixtures_biologics_and_duplicates` — count in == count out
  minus the known bad rows, named explicitly.
- `test_library_rows_carry_a_public_source_record` — every row's `SourceRecord`
  has `tier == public_primary` and a real URL.

**Done when.** The TSV loads into `ProtoScreenSpec.ligand_smiles` without a
validation error and `SOURCES.md` has the DrugCentral row.
**Hours:** 0.75.
**Cut:** ship 300 approved drugs sampled with a fixed seed and record the sample
size as a limitation. A 300-compound screen you can explain beats a 5000-compound
screen you never finish.

## V5 — Primary Vina screen ⏱ (batch — ask before launching)

**Goal.** Poses + scores for the library against the V1 box.
**Why.** `TASKS.md` #6. This is the run everything else reports on.

**Files:** `examples/proto_screen_spec.med23.json` (committed, small ligand set),
`outputs/screen/` (gitignored), `src/dependency_scout/cli.py` (one `screen`
subcommand alongside `validate-proto`).

**Blockers to clear first:**
- `proto_tools` **is not installed in `.venv`** (`setup.sh` installs only this
  package and BenchFlow). Install per `skills/proto/SKILL.md`; the first call to
  `vina-docking` builds a micromamba env (30–60 s, network). ⏱
- Run `proto-tools signature vina-docking` and read the **output** class before
  writing any parser. Vina's own output field is literally named
  `affinity (kcal/mol)`. That name is the trap this whole document exists to
  avoid — rename it `docking_score` at the boundary.

**Do.** Validate first: `dependency-scout validate-proto <spec>` must return
`ready: true` before you dispatch anything. Then dock in batches with a **fixed
seed and recorded exhaustiveness**, keeping **≥5 poses per compound** for the top
subset (Andrey: do not trust one score). Run it with `run_in_background` and
check on it; do not block the terminal for an hour.

**Cost.** ~30–60 s per ligand per core. 5 000 ligands is 40–80 core-hours —
**not a laptop job**. Either dock the V4 sample or ask before parallelising onto
anything paid. 💸

**Expected output:** `outputs/screen/poses/DrugCentral_1234_pose{0..4}.pdbqt`
plus a scores TSV.

**Tests:**
- `test_screen_spec_for_the_real_pocket_validates` — the committed spec compiles
  through `validate_proto_spec` (skip when `proto_tools` is absent, same pattern
  as the existing smoke test).
- `test_screen_records_seed_and_exhaustiveness` — the shortlist's `spec` round
  trips with both recorded; a run without them fails.

**Done when.** Scores + ≥5 poses for the top 25 compounds are on disk, config and
seed recorded.
**Hours:** 1.0 hands-on + wall clock.
**Cut:** dock 300 compounds, keep 3 poses. Same code path, one tenth the wait.

## V6 — Typed compound output

**Goal.** `ScreenedCompound` / `CompoundShortlist` in
`src/dependency_scout/models.py`, and a `to_drug_discovery()` that emits Amir's
block verbatim.
**Why.** The scores are worthless to the UI as a TSV, and "docking score is not
affinity" must be structural, not a footnote someone can delete.

```python
class ScreenedCompound(BaseModel):
    """One docked, ranked compound. `docking_score` is a scoring-function
    estimate, NOT a measured or predicted binding affinity."""
    model_config = ConfigDict(extra="forbid")
    name: str                                   # DrugCentral preferred name
    library_id: str                             # "DrugCentral:1234" / "CHEMBL25"
    smiles: str                                 # standardized parent, salt free
    approval_status: Literal["approved", "investigational", "unknown"]
    docking_score: float | None = None          # Vina, kcal/mol, lower = better
    score_units: Literal["vina_kcal_per_mol"] = "vina_kcal_per_mol"
    not_affinity: Literal[
        "Vina docking score is a scoring-function estimate, not a measured or "
        "predicted binding affinity."] = (
        "Vina docking score is a scoring-function estimate, not a measured or "
        "predicted binding affinity.")
    poses_kept: int = Field(default=0, ge=0)
    pose_paths: list[str] = Field(default_factory=list)
    contacts: dict[str, list[int]] = Field(default_factory=dict)  # {"MED23": [382, 383]}
    boltz_metrics: dict[str, float] = Field(default_factory=dict)  # iptm/plddt/affinity_pred
    known_target: str | None = None             # ChEMBL/DrugCentral annotation
    mechanism: str | None = None
    decision: Literal["prioritize", "hold", "reject"]
    rationale: str                              # why, in one sentence
    claims: list[Claim] = Field(default_factory=list)   # for any literature basis
    source: SourceRecord                        # library provenance

    @model_validator(mode="after")
    def a_score_needs_a_pose_and_a_choice_needs_a_reason(self) -> "ScreenedCompound":
        if self.docking_score is not None and not self.pose_paths:
            raise ValueError("a docking score with no stored pose is not reviewable")
        if self.decision == "prioritize" and not self.contacts:
            raise ValueError("prioritizing a compound requires named interface contacts")
        if not self.rationale:
            raise ValueError("every keep/reject decision states its reason")
        return self


class CompoundShortlist(BaseModel):
    """Screening handoff. Mirrors the frozen `drug_discovery` block."""
    model_config = ConfigDict(extra="forbid")
    candidate_gene: str
    partner_gene: str = "MED23"
    site: str                                   # "MED23 concave face, HR2/HR3, H19/H21/H28"
    status: Literal["screened", "blocked"]
    search_region_basis: str
    spec: ProtoScreenSpec | None = None         # exactly what was run
    receptor_source: SourceRecord | None = None # PDB 9F6Y
    library_source: SourceRecord | None = None  # DrugCentral release
    n_library: int = Field(default=0, ge=0)
    n_docked: int = Field(default=0, ge=0)
    compounds: list[ScreenedCompound] = Field(default_factory=list)  # ranked, ~10
    blockers: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    hypothesis_only: bool = True

    @model_validator(mode="after")
    def status_matches_the_evidence(self) -> "CompoundShortlist":
        if self.status == "screened":
            if not self.compounds or not self.search_region_basis or not self.spec:
                raise ValueError("a screened result needs a spec, a region basis, and compounds")
        elif not self.blockers:
            raise ValueError("a blocked screen must state why")
        return self

    def to_drug_discovery(self) -> dict:
        """The block Amir drops into `_hero_hypothesis_payload`."""
        return {"status": self.status,
                "search_region_basis": self.search_region_basis,
                "compounds": [c.model_dump(mode="json") for c in self.compounds],
                "blockers": self.blockers}
```

Reuses `SourceRecord`, `Claim`/`SupportType`, `ProtoScreenSpec` unchanged. No new
enum: `decision` is a three-value `Literal` and nothing else needs it.

**Tests:**
- `test_docking_score_is_never_reported_as_affinity` — `not_affinity` survives
  `model_dump(mode="json")`, and the rendered markdown contains the sentence.
  Assert the compound dict has **no** key named `affinity`.
- `test_a_score_without_a_pose_is_rejected` — raises `ValidationError`.
- `test_prioritized_compound_requires_named_contacts` — raises without `contacts`.
- `test_shortlist_serializes_into_the_benchflow_drug_discovery_block` — the four
  keys and nothing else; `status` ∈ {screened, blocked}.

**Done when.** `CompoundShortlist(...).to_drug_discovery()` passes
`benchflow/tasks/tf-mediator-hero/verifier/test_output.py::test_structural_abstention_is_valid`.
**Hours:** 1.0. **Cut:** drop `claims` and `boltz_metrics`; both default empty.

## V7 — Top-10 report + human pose review packet

**Goal.** A markdown table Andrey can sign checkpoint 5 against, and the JSON the
UI reads.
**Why.** Constraint 6, and checkpoint 5 says "top poses and chemistry inspected
by hand. Docking score is not binding affinity."

**Files:** `src/dependency_scout/report.py` (add `render_compound_shortlist`,
same shape as `render_markdown`), `outputs/screen/shortlist.md`.

**Expected output:**

| # | Drug | Approval | Score (kcal/mol, *not affinity*) | Poses | Key contacts | Boltz | Known target | Call |
|---|---|---|---|---|---|---|---|---|
| 1 | example-drug | approved (FDA) | −9.4 | 5 | MED23 F379, G382, S383 | iptm 0.71 | PDE5 | prioritize — occupies the F378 subpocket in 4/5 poses |
| 2 | example-drug-2 | approved (EMA) | −9.1 | 5 | MED23 I339, L343 | — | HDAC | hold — one pose only, rest exit the groove |

Footer, verbatim, non-negotiable: *docking scores are scoring-function estimates,
not binding affinities; no binding, safety, or efficacy is claimed; poses are
computed predictions awaiting human review at checkpoint 5.*

**Tests:**
- `test_compound_report_states_the_not_affinity_caveat` — substring assert.
- `test_compound_report_lists_rejections_with_reasons` — a `reject` row appears
  with its `rationale`, mirroring the rejection view that wins the demo.

**Done when.** Andrey can read the table and mark checkpoint 5 without opening a
JSON file.
**Hours:** 0.75. **Cut:** JSON only, let the UI render it.

## V8 — Boltz-2 orthogonal rerank 💸⏱

**Goal.** Independent signal on the top ~10, so ranking does not rest on one
scoring function.
**Why.** Andrey's constraint 5.

**Do.** Top 10 compounds + the receptor construct through `boltz2-affinity`
(already in the `ProtoScreenSpec.tools` literal) or the `boltz` CLI with
`properties: affinity`. Write into `ScreenedCompound.boltz_metrics`. **Report
the rank change, not a Kd.** Disagreement between Vina and Boltz is a finding
worth showing, not a bug to hide.

**Cost.** Modal GPU is **paid — ask first**. Local CPU affinity is ~5.5 min per
complex for a small system and far worse for MED23-sized receptors; 10 compounds
overnight is optimistic. Do not start this after 20:00 Saturday.

**Tests:** `test_boltz_metrics_do_not_overwrite_the_docking_rank` — reranking
produces a second ordering field, the Vina rank stays visible in the report.
**Done when.** Top 10 carry a second number, or the report states that the
orthogonal rerank was not run and why.
**Hours:** 1.0 + queue. **Cut this first.** A single-method screen honestly
labelled beats a second method you cannot finish.

---

## Paid / long-running / ask-first

| Where | What | Rule |
|---|---|---|
| V2, V8 | Boltz-2 on Modal GPU (`dispatch_to_modal`) | **Paid.** Ask Andrey, record the decision in `DECISIONS.md`. |
| V5 | Vina over the full ~5 k library | **Batch.** Ask before launching; default to the V4 sample. |
| V5 | `proto_tools` install + per-tool micromamba env build | Network + 30–60 s per tool, first call only. Not a hang. |
| V4 | DrugCentral / ChEMBL bulk download | Public, free. Record version + sha256 in `SOURCES.md`. |
| any | Tamarind (`TAMARIND_API_KEY`) | Sponsor service. `CLAUDE.md`: validate inputs before submitting; ask before paid or batch jobs. |
| V8 | `boltz` first run downloads ~4 GB of weights | Set `BOLTZ_CACHE` to a persistent path or it re-downloads every run. |

Never commit `.env`, poses, or downloads — `downloads/`, `outputs/`, and `runs/`
are already gitignored.

## Two things in the repo that will bite you

1. **The bundled smoke spec cannot pass.**
   `examples/proto_screen_spec.smoke.json` points `receptor_path` and
   `reference_ligand_path` at `vendor/proto-language/...`, and there is no
   `vendor/` directory in this checkout (it is gitignored, and `setup.sh` never
   creates it). `proto_tools` is not installed either, so
   `test_bundled_public_proto_smoke_spec_compiles_natively` **skips** and nobody
   sees the broken path. Expect `ready: false, blockers: ["receptor does not
   exist: ..."]` on your first `validate-proto` call. Fix the path in V5 as part
   of writing the real spec, or leave it and note it.
2. **Your compounds cannot reach the demo artifact on their own.**
   `orchestrator._hero_hypothesis_payload` (`orchestrator.py:1136`) hard-codes
   `drug_discovery.status = "blocked"` with the blocker *"Virtual screening is
   out of scope for this workflow."* A successful screen changes nothing until
   Amir reads a `CompoundShortlist`. **Agree `to_drug_discovery()` with Amir
   before V6, not after.** One 5-minute conversation, or the whole screen is
   invisible on stage.

## Order of attack, and what to drop

V1 → V3 → V4 → V5 → V6 → V7, with V2 and V8 only if the clock allows. If you are
behind at Saturday 20:00: keep V1/V3/V6/V7 and ship the abstain-or-300-compound
version. The screen that says *why it stopped* is worth more to this judging
criterion than a bigger screen with no provenance — and it is the same code
path, so nothing is wasted.
