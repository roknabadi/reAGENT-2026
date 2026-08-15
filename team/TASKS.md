# Task split — Vraj / Amir

Scrappy first. Each task's *done-when* means the next person is unblocked, not
that the code is nice.

**Vraj takes the two ends of the pipe** — real data in, compounds and the demo
surface out. **Amir keeps the middle** — the agent that reasons between them,
and the single artifact the UI reads. Neither rewrites the other's work.

Kevin sets the logic. Andrey approves. If a task is not here, it is not this
weekend.

---

## Band 1 — critical path

Nothing reaches structure, screening, or the UI until real numbers exist.

| # | Task | Who | Done when |
|---|---|---|---|
| 1 | **Real DepMap ingest.** Official Chronos gene-effect + model table → `DependencyEvidence`. Everything in the repo is still synthetic. | Vraj | One real context yields gate-eligible candidates; release version in `SOURCES.md` |
| 2 | **`demo.json` — the one artifact.** Amir's orchestrator emits a single self-contained file the UI reads: candidates, evidence, claims, citations, gate failures, structure, poses. Frozen shape. | Amir | Schema agreed with Vraj, one file loads standalone, no live calls |
| 3 | **Model adapter.** `reagent_workflow` and `dependency_scout` mean the same things and share no types. | Amir | A candidate built in one package is consumable by the other |
| 4 | **Ranking weights.** Kevin's 25/25/20/15/10/5 replaces the 80/20 split, once he maps the six numbers. | Amir | `ranking.py` matches Kevin's spec, weights in one readable place |

Blocking: **Kevin** for #1 (which contexts) and #4 (the mapping).
**#2 is the hard dependency for every UI task — agree that shape first.**

## Band 2 — makes a demo exist

| # | Task | Who | Done when |
|---|---|---|---|
| 5 | **Compound set.** Public SMILES with provenance for the chosen pocket (ChEMBL/PubChem), tracked as `SourceRecord`. | Vraj | ≥1 ligand set with public IDs, loads into `ProtoScreenSpec` |
| 6 | **Docking run.** Vina through Proto, real receptor, real box. `ProtoScreenSpec` already refuses without both. | Vraj | Poses for one target, config + seed recorded |
| 7 | **Structure on a real target.** Boltz2/ESMFold2 on the actual hero pair, not the fixture. | Amir | Real confidence numbers, cached, comparison written |
| 8 | **Next-experiment output.** The agent proposes the experiment — an explicit judging criterion. | Amir | One concrete falsifiable experiment from a real run |

## Band 3 — the UI (Vraj owns all of it)

One **self-contained HTML file**, no server, no build step, no live calls. It
reads `demo.json` from a saved run. A demo that needs a network on stage is a
demo that fails on stage.

| # | Screen | Shows | Done when |
|---|---|---|---|
| 9 | **Candidate table** | ranked TFs, disease context, selectivity, involvement, score | renders from `demo.json`, sorts, no crash on missing fields |
| 10 | **Evidence panel** | per candidate: every claim, its support type, its citations as links | each claim traceable to a real URL |
| 11 | **Rejection view** ⭐ | which gate fired, on what evidence, for every rejected candidate | both negative controls visible with stated reasons |
| 12 | **Structure + pocket** | interface residues, confidence, the mapped region | one real target rendered |
| 13 | **Compound shortlist** | poses, scores, why each was kept — with "docking score is not affinity" stated | ranked list from a real run |
| 14 | **One-screen summary** | the hypothesis chain: disease → TF → Mediator contact → interface → compounds | fits one screen, readable from the back of a room |

**#11 is the one that wins.** Every team will show hits. Almost nobody shows a
principled rejection, and it is exactly what "make its reasoning easy to
inspect" asks for. `gate.failures[]` and `mediator.screening_concerns` are
already in the payload.

## Band 4 — if time remains

| # | Task | Who |
|---|---|---|
| 15 | BenchFlow trace export on the real pipeline, not the fixture | Amir |
| 16 | Live rejection run on stage (pan-essential TF + co-expression-only pair) | Vraj |

## Not this weekend

Contract redesign. Merging the two model packages. A UI framework, build step,
or backend. Coverage for its own sake. New data sources beyond #1 and #5.
Anything that makes an existing stage nicer without connecting a disconnected
one.

---

## Order of attack

1. Amir and Vraj agree the `demo.json` shape — **30 minutes, blocks 6 tasks**
2. Vraj: real DepMap in (#1). Amir: adapter + weights (#3, #4)
3. Amir: structure on a real target (#7). Vraj: compounds + docking (#5, #6)
4. Vraj: UI, in order 9 → 11 → 14, then the rest
5. Both: whatever is still disconnected at Saturday 9:45 PM gets a placeholder,
   not a fix

Anything not demoable by **Sunday 9:00 AM** is not in the demo.

## Hand-off rules

- Branch per task, PR to `main`. Never push to `main`.
- `git fetch origin && git merge origin/main` before you start and before you push.
- Post to `team/status/<name>.md` when a task moves. `Blocked:` names who unblocks you.
- Changing a shared type is a `DECISIONS.md` entry and Andrey approves it.
- A gate in `CHECKPOINTS.md` is `OPEN` until Andrey signs.
