---
name: virtual-screening
description: >
  Use when modelling a target complex, defining a druggable site, assembling an
  approved-drug library, docking through Proto/Vina, reranking with Boltz-2, or
  producing a compound shortlist. Read this BEFORE any docking run. It states
  what must be true before a screen is meaningful, and when to abstain instead.
---

# Virtual screening: minimal constraints

The screen is the easiest part of this pipeline to run and the easiest to run
meaninglessly. A docking program will return a ranked list against any pocket
you hand it, including one that does not exist. These constraints exist so a
shortlist means something.

The pipeline:

```text
complex prediction → interface/pocket definition → approved-drug library
→ Proto/Vina screen → Boltz-2 reranking → human pose review → shortlist
```

Each arrow is a gate. If a step cannot be satisfied, **abstain and say why** —
an abstention with a reason is a result; a shortlist built on an undefined site
is not.

## 1. Model the target complex

Input is the target plus its interaction partner.

- **Boltz-2 through Proto is the preferred complex model.** It explicitly
  predicts biomolecular complexes, which is the question being asked.
- **ESMFold is a monomer sanity check only.** It tells you whether a chain folds
  in isolation. It cannot confirm, refuse, or locate an interface, and must
  never be presented as interface evidence.
- Record the model, version, config, seed, and input/output hashes. A prediction
  whose inputs cannot be reproduced is not evidence of anything.

`StructuralModelRequest` enforces the role split: asking ESMFold2 for a complex
interface raises rather than quietly returning a monomer.

## 2. Define a druggable site

**Proceed only if the model gives a localized, credible interface.** Concretely:

- the interface is confined to identifiable residues, not spread over the whole
  chain;
- interface confidence (ipTM, or an equivalent) is high enough to act on, and
  the number is recorded rather than described;
- the pocket or groove could plausibly accommodate a small molecule.

A short linear motif binding a defined groove is the tractable case. A large
flat folded-domain interface with a big buried surface is a poor small-molecule
target, and saying so early is cheaper than discovering it after the screen.

**If no defensible site exists, abstain.** Do not dock blindly, do not centre a
box on a whole protein, and do not let a low-confidence prediction become a
search region by default.

## 3. Approved-drug library

- **DrugCentral is the primary source** for the initial repurposing screen: it
  provides explicit FDA/EMA/PMDA-approved sets with downloadable SDF and SMILES.
- **ChEMBL is secondary**: use `max_phase = 4` for approved/marketed compounds,
  and keep its richer target and bioactivity annotations for follow-up.
- Keep small molecules only. Standardise to parent structures; remove salts,
  mixtures, biologics, and duplicates.
- Every compound keeps its source, its public identifier, and its approval
  status. A compound with no provenance does not enter the screen.

## 4. Primary screen in Proto

- Dock the approved-drug library against the **defined interface pocket** using
  Proto/Vina.
- Rank by docking score **and** pose/interface plausibility — a good score in a
  pose that makes no contact with the interface is not a hit.
- **Keep multiple poses for top compounds.** A single score is a fragile thing
  to build a claim on.
- `ProtoScreenSpec` already refuses a Vina request without a prepared receptor,
  an explicit or reference-ligand search box, and at least one ligand. That
  refusal is the constraint working, not an obstacle to route around.

## 5. Orthogonal reranking

Run the top subset through **Boltz-2 structure/binding prediction** as an
independent reranking signal; it supports joint complex structure and affinity
prediction. Two methods agreeing raises confidence in the ranking. It does not
validate binding — both are predictors and can be jointly wrong.

## 6. Human pose review

Top poses and chemistry are inspected by hand before anything is called a
shortlist. This is checkpoint 5 in `team/CHECKPOINTS.md` and it is a human gate,
not a formality.

## Output

Top ~10 compounds, each with:

| Field | Note |
|---|---|
| drug name / approval status | with its source identifier |
| docking score | with units, and the caveat below |
| predicted pose | referenced by artifact path |
| key target/partner contacts | which interface residues the pose touches |
| Boltz confidence / affinity metric | when available |
| known target / mechanism | its actual approved use |
| reason to prioritize **or reject** | the rejections are half the value |

## What must never be claimed

- **A docking score is not a binding affinity.** State this wherever scores are
  shown.
- A pose is a prediction, not a complex.
- Model agreement between Vina and Boltz-2 is agreement, not validation.
- Nothing here demonstrates binding, safety, or efficacy. An approved drug is
  approved for *its* indication, not for this target.

## Related

- `skills/proto/SKILL.md` — the tool contracts and how to run them
- `skills/boltz/SKILL.md` — Boltz specifics and gotchas
- `SOUL.md` — the `SCREENING` stage loads `no-site-no-dock`,
  `score-is-not-affinity`, `compounds-carry-provenance`, `human-before-shortlist`
