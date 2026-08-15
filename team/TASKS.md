# Task split — Vraj / Amir

Scrappy first. Each task says what "done" means, because done means the next
person is unblocked, not that the code is nice.

**Vraj takes the two ends of the pipe** — real data in, compounds and demo out.
**Amir keeps the middle** — the agent that reasons between them. That is what
each already has built, so nobody rewrites the other's work.

Kevin sets the logic. Andrey approves. If a task is not here, it is not this
weekend.

---

## Now — the critical path

Nothing reaches structure or screening until real dependency numbers exist.
Everything else is parallel to this.

| # | Task | Who | Done when |
|---|---|---|---|
| 1 | **Real DepMap ingest.** Official Chronos gene-effect + model table → `DependencyEvidence`. Fixtures are synthetic; this is the first real data in the repo. | Vraj | One real context produces gate-eligible candidates, source + release version recorded in `SOURCES.md` |
| 2 | **Model adapter.** `reagent_workflow` and `dependency_scout` mean the same things and share no types, so no object crosses the whole pipe. | Amir | A candidate built in one package is consumable by the other, both suites green |
| 3 | **Ranking weights.** Kevin's 25/25/20/15/10/5 replaces the current 80/20 split, once he says which factor each number is. | Amir | `ranking.py` matches Kevin's spec; weights readable in one place |

Blocking: Kevin for #1 (which contexts to screen) and #3 (the weight mapping).

## Next — makes a demo exist

| # | Task | Who | Done when |
|---|---|---|---|
| 4 | **Compound set.** Public SMILES with provenance for the chosen pocket. ChEMBL/PubChem, tracked as `SourceRecord`. | Vraj | ≥1 ligand set with public IDs, loads into `ProtoScreenSpec` |
| 5 | **Docking run.** Vina through Proto against a real receptor and a real box. `ProtoScreenSpec` already refuses to run without both. | Vraj | Poses out for one target, config + seed recorded |
| 6 | **Structure stage on a real target.** Boltz2/ESMFold2 on the actual hero pair, not the fixture. | Amir | Real confidence numbers, cached, comparison written |
| 7 | **Demo surface.** The thing judges look at: candidates, evidence with citations, and *why each rejection happened*. | Vraj | Runs from a saved run directory, no live calls needed |

## Then — the parts that win points

| # | Task | Who | Done when |
|---|---|---|---|
| 8 | **Rejection demo.** Run the pipeline live on a pan-essential TF and on a co-expression-only pair; show the gate firing with its reason. | Vraj | Two negative controls rejected on screen with stated reasons |
| 9 | **Next-experiment output.** The agent proposes the experiment. Closing the loop is an explicit judging criterion. | Amir | One concrete falsifiable experiment from a real run |
| 10 | **BenchFlow trace export.** Already built; needs to run on the real pipeline, not the fixture. | Amir | Trace validates against the frozen task |

## Not this weekend

Contract redesign. Merging the two model packages. Test coverage for its own
sake. New data sources beyond what tasks 1 and 4 need. Anything that makes an
existing stage nicer without connecting a disconnected one.

---

## Hand-off rules

- Branch per task, PR to `main`. Never push to `main`.
- `git fetch origin && git merge origin/main` before you start and before you push.
- Post to `team/status/<name>.md` when a task moves. `Blocked:` names who unblocks you.
- Changing a shared type is a `DECISIONS.md` entry and Andrey approves it.
- A gate in `CHECKPOINTS.md` is `OPEN` until Andrey signs. Do not proceed past it.
