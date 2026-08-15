# Open questions — Kevin and Andrey

Every question is one line to answer. Every question has a **proposed default**
already written in code or ready to be. Reply once, in one message:

> `K1 default. K2 default. K5 → 0.6. A2 default. A5 no, require 4 residues.`

Only write the ones you disagree with. Silence on a question means the default.

**Anything unanswered by 18:00 Saturday ships as the default** and is logged in
`DECISIONS.md` as decided-by-default. That is not us overruling you — it is the
only way the pipeline runs before Sunday 09:00.

Task IDs below are from `team/TASKS.md`. Gate numbers are from
`team/CHECKPOINTS.md`.

---

# Kevin — solution logic

## 🔴 K1. BLOCKING — which DepMap disease contexts do we screen first?

Nothing downstream exists until this lands. Every candidate in the repo today
reads `awaiting quantitative dependency data`, because no real DepMap run has
happened. `depmap.py` takes **one context string per run** and matches it
case-insensitively but **exactly** against a column in the DepMap model table
(default `OncotreeLineage`, or `OncotreePrimaryDisease` for finer contexts). So
we need literal values, not descriptions.

**Proposed default:** three runs, on `OncotreeLineage` — **`Ovary/Fallopian
Tube`**, **`Skin`**, **`Lymphoid`**. Reason: the TF-addiction review already in
`SOURCES.md` (PMC11614577 L24) names PAX8→ovarian, SOX10→skin, IRF4→lymphoid
among the most selective TF dependencies pan-cancer. If our gates are calibrated,
run 1 rediscovers them; if it doesn't, the gates are wrong and we learn that
Saturday instead of Sunday.

**Unblocks:** Task 1 (real DepMap ingest), then 4, 7, 9, 11 — i.e. everything.
Also gate 1 (TF shortlist) and gate 3 (hero target), both `OPEN`.

## 🔴 K2. BLOCKING — which factor is each of your six weights?

`TASKS.md` records your weights as **25 / 25 / 20 / 15 / 10 / 5**. Nobody has
said which number belongs to which factor. Ranking currently runs on a
provisional 80/20 discovery-vs-enrichment split (`dependency_scout/ranking.py`),
while `reagent_workflow/config.py` already has six named components summing to
1.0 — but at **25/15/25/10/15/10**, which is not your split. One of the two is
wrong.

**Proposed default** (keeps the existing component names, so only one file
changes):

| Weight | Component (name already in the code) | What it measures |
|---|---|---|
| 25 | `dependency_strength` | how deep the median Chronos effect is in context |
| 25 | `disease_specificity` | in-context vs out-of-context median gap |
| 20 | `mediator_evidence_quality` | assay type × support for the TF–Mediator contact |
| 15 | `dependency_prevalence` | fraction of in-context lines that are dependent |
| 10 | `structural_tractability` | public structure / sequences / bounded domain |
| 5 | `normal_cell_completeness` | is there any normal-tissue evidence at all |

**Unblocks:** Task 4 (ranking weights), and the ordering of every table in the UI
(tasks 9, 14).

## 🟠 K3. Which genes get scored?

`depmap.py` will happily score all ~18,000 genes, or only a named set.

**Proposed default:** score every gene in the matrix, then report only genes on
the public human TF census (Lambert et al. 2018, 1,639 TFs), recorded in
`SOURCES.md`. Non-TFs stay computed so we can show the TF filter is a choice,
not a hidden hard-code.

**Unblocks:** Task 1 (`--genes`), and how long the candidate table in task 9 is.

## 🟠 K4. The four hard gate thresholds — in plain language, for correction

These are hard-coded in `dependency_scout/ranking.py` today. A candidate that
fails **any one** of them scores 0 and appears in the rejection view (task 11).
Two of them conflict with the numbers in `reagent_workflow/config.py`; we need
one number each, from you.

| # | The rule in English | Now | Conflicts with | Default |
|---|---|---|---|---|
| K4a | A gene is only a dependency if the **typical** line in the disease context is at or below **−0.5** Chronos gene effect | −0.5 | — | keep −0.5 |
| K4b | At least **50%** of the disease-context lines must be individually dependent | 0.50 | — | keep 0.50 |
| K4c | No more than **35%** of all lines outside the context may be dependent, else it is broadly essential, not selective | 0.35 | `config.py` says 0.50 | keep 0.35 (the stricter one) |
| K4d | The in-context median must be at least **0.35** more negative than the out-of-context median | 0.35 | `config.py` says 0.30 **and flips the sign** | keep 0.35, one sign convention |
| K4e | Minimum lines in the context before we score at all | 3 | `config.py` says 5 | use **5** |

Note on K4a: **−0.5 is currently doing two jobs** — it is both the gate on the
median *and* the per-cell-line cutoff for "this line is dependent" that feeds
K4b and K4c. Say so if you want them different (e.g. gate at −0.5, count a line
as dependent at −1.0, the common-essential median).

**Unblocks:** Task 1 (whether run 1 produces anything at all), task 11
(rejection view), gate 1.

## 🟡 K5. What is the normal-tissue safety bar?

Right now the code only scores whether normal-tissue evidence **exists** (0
records → 0.0, 1 → 0.5, 2+ → 1.0). It never checks whether that evidence is
*favourable*. Andrey's RUNX2 note is the live case: mapped contact, real
dependency, and the same axis is load-bearing in normal bone development.

**Proposed default:** keep presence-only scoring for the weekend, plus **one
hard rule** — if any sourced claim says the gene is required for normal
development or homeostasis of a tissue, it is recorded as a screening concern
and it cannot be the hero target. It still appears in the table with the reason
shown.

**Unblocks:** Tasks 4 and 11, gate 3.

## 🟡 K6. Compound library scope for the screen?

**Proposed default:** approved drugs only — the ChEMBL `max_phase = 4` subset
(~4k SMILES, public IDs, provenance tracked as a `SourceRecord`), plus the
ELF3–MED23 chalcone series (PMC11623927) docked as a **positive control**, never
as a result. No generative chemistry, no purchasable-space enumeration, no
in-house compounds.

**Unblocks:** Tasks 5 and 6 (compound set, docking run), task 13.

---

# Andrey — decides and signs

## 🔴 A1. Minimum Mediator involvement tier to enter structure and docking?

The contract derives four tiers: `direct` (region mapped **and** a
direct-experimental claim), `indirect` (assay but no mapped region), `predicted`
(computational only), `unknown`. Today only `direct` sets
`ready_for_structural_modeling`.

**Proposed default:** `direct` required, no downgrade. `indirect` and below get
a proposed *experiment* (task 8), never a structure or a dock.

**Unblocks:** Tasks 7 and 12, gate 4. Also decides whether anything at all is
eligible this weekend — round 01 left exactly one non-calibration `direct`
candidate (RUNX2, which you have already flagged).

## 🔴 A2. If nothing clears the bars, what do we demo?

Real possibility by Saturday night: run 1 produces zero candidates that are
selective **and** have a mapped contact.

**Proposed default:** we demo the rejection. Full candidate table, every gate
failure with the threshold that fired, ELK1–MED23 shown as the labelled
calibration positive (`calibration_only`, never a result), and one concrete
next-experiment output. **No target is named**, gate 3 stays `OPEN`, and we say
that on stage. Every team will show hits; almost nobody will show a principled
no.

**Unblocks:** Tasks 11 and 14 — the two UI screens Vraj builds first — and task
8. Answer this early even if K1 is still open; it changes what gets built, not
what gets run.

## 🟠 A3. MED23 only, or are other Tail subunits allowed as fallback?

`MediatorLink.partner_gene` and `Shortlist.partner_gene` both **default** to
`MED23`, but neither restricts the value — MED1 / MED15 / MED25 would validate
today with no code change.

**Proposed default:** MED23 first. MED1/MED15/MED25 allowed **only** at the same
evidence bar (mapped interacting region + a direct-experimental claim). The
partner is recorded per candidate, never assumed, and the demo says which subunit
each candidate is about.

**Unblocks:** Tasks 1 and 7 (how wide the literature stage searches), gates 2
and 3.

## 🟠 A4. Does a folded-domain interface block docking, or only warn?

Today `screening_concerns` is **advisory, not a gate**: a large domain–domain
interface is flagged but still proceeds. This is exactly the RUNX2 case you
called "passes on paper, should not proceed on the science".

**Proposed default:** `folded_domain` blocks the hero slot and blocks docking; it
does not remove the candidate from the table, and the concern text is shown
verbatim in the rejection view.

**Unblocks:** Tasks 6 and 11, gate 3.

## 🟡 A5. What makes a *predicted* interface credible enough to dock?

Gate 4 says "the predicted interface is credible before docking" but names no
number. For anything without an experimental structure, we need a bar.

**Proposed default:** all three, or it does not go to Vina and appears in the
rejection view instead — (a) interface pLDDT ≥ 70 over the mapped region,
(b) ipTM and pTM reported alongside, (c) the interacting region was already named
in a primary source, so the prediction is confirming a claim rather than
inventing one.

**Unblocks:** Tasks 6, 7, 12, gate 4.

## 🟡 A6. What makes a docked pose worth proposing an experiment?

**Proposed default:** all three — (a) the pose contacts ≥ 3 of the named pocket
residues (for MED23: I339, L343, F379, G382, S383, V533, M537), (b) Vina score
≤ −7.0 kcal/mol, (c) the compound has a public ID. Every screen shows "docking
score is not binding affinity" on the same screen, and no pose is described as
binding.

**Unblocks:** Tasks 6 and 13, gate 5.

## 🟡 A7. Sign gate 2 now, or hold it?

Gate 2 (Mediator connection) has your round-01 evidence in it and no signature.
The orchestrator requires human approval before the structure stage runs.

**Proposed default:** sign gate 2 as *"the TF–Mediator relationship is genuinely
supported as a calibration case; no target is selected"*, and leave gates 1, 3,
4, 5 `OPEN`. That unblocks the structure stage running on the ELK1 calibration
pair without anyone claiming a target.

**Unblocks:** Task 7.

---

# Data, not opinion

Three asks. The point of all three is that the pipeline's output is only worth
something if it was not steered by hand.

1. **The TFs you believe in — write them down and withhold them.** Send Andrey
   (Kevin) or keep in your own notes (Andrey) 2–3 TFs you expect to be strong
   candidates in the K1 contexts, timestamped, and **do not tell Vraj or Amir
   until the first ranked run is posted.** If the run independently rediscovers
   them, that is a validation result we can say out loud on stage. If we know
   them first, it is just a filter we wrote.

2. **The TFs you know are dead ends.** Name at least two, with the reason in
   four words. We specifically need one **pan-essential** TF and one
   **overexpressed-but-not-a-dependency** TF, because they are the negative
   controls `PROJECT.md` requires and they are what task 11 and task 16 (live
   rejection on stage) render. A rejection view with no known-bad input proves
   nothing.
   *Default if you don't answer:* we pick them ourselves from the DepMap
   common-essential list and label them as our pick, not yours.

3. **2–3 papers that define your standard of evidence for a protein–protein
   contact.** Not a reading list — the bar itself, so the agent's literature
   stage can be checked against something.
   *Proposed default:* the ceiling is Monté 2025 (cryo-EM + Kd by SPR +
   separation-of-function mutant, doi:10.1038/s41467-025-59014-8); the floor is
   the ELF3–MED23 FP / split-luciferase format on a defined fragment
   (PMC11623927). Correct either, and add a third if the middle of that range
   matters.
