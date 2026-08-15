# Kevin — disease specificity / omics (@Kyung-TaeLee)

Newest on top. Format: ../README.md

## 2026-08-15 — Kevin
Did: built and ran an initial discovery-pipeline plan for the disease-specificity
/ omics side, mostly in a scratch repo (`~/coding/re-agent_discovery`, not this
one) before I'd read `team/` — see "Note for Andrey/Amir" below on reconciling
that with `src/dependency_scout/models.py`.

**Plan (6 stages):** TF universe (Lambert et al. 2018, 1,639 high-confidence
TFs) → DepMap dependency mining → ENCODE-rE2G regulatory context → UniProt/HPA
expression curation (disease-context expression + normal-tissue proxy) → STRING
Mediator-connectivity filter → weighted convergence/ranking. Locked three design
calls: (1) dependency mining is a hierarchical two-pass scan — lineage-level
first for power, then `OncotreeSubtype` drill-down only within lineages that
show signal, with an n≥15 full-confidence floor so small-n disease-relevant
subtypes get flagged low-confidence instead of silently dropped; (2) ranking
weights dependency strength + disease specificity at 50% combined, Mediator
evidence quality at 20% (it's already the Stage-1-analog hard filter, shouldn't
also dominate ranking or it just rewards best-studied TFs), safety proxy 15%,
rest 15%; (3) carry a top-3 shortlist into a cheap structural triage rather than
committing full compute to a single #1 pick.

**Ran the dependency-mining stage against real DepMap 24Q4 Public data**
(pinned that release directly via Figshare — depmap.org's own API/portal sits
behind a Cloudflare bot-check that blocks headless access). Unbiased scan
across all 1,639 TFs recovered real, independently-known selective
dependencies as a sanity check: IRF4 (Lymphoid → multiple myeloma), TP63 (Head
& Neck → oral cavity SCC), TCF7L2 (Bowel → colon adenocarcinoma), EBF1
(Lymphoid → DLBCL), PAX8 (Ovary/Fallopian Tube, Kidney, Uterus), ZNF217
(Lymphoid → plasma cell myeloma), ISL1 + MYCN (Peripheral Nervous System →
neuroblastoma specifically).

**Checked round-01's own candidates against this quantitative data** (relevant
to checkpoints 1 and 2):
- **RUNX2, CEBPB, ETV1 — none show a selective DepMap dependency in any
  lineage.** RUNX2's and CEBPB's best hits aren't even dependency-shaped
  (in-context median ≈ 0 or less negative than out-of-context). This is
  quantitative backing for Andrey's existing doubts — RUNX2's tractability/
  normal-tissue caveat and the CEBPB negative-control call both hold up
  numerically, and ETV1 has no dependency support either.
- **ELK1/ELF3 — also no CRISPR dependency signal anywhere,** as expected:
  their evidentiary value is structural/mechanistic, not fitness-based, so they
  shouldn't be expected to clear a DepMap-based gate 1 and need to be judged on
  Mediator-contact evidence instead (consistent with how `PROJECT.md` frames
  ELK1 already).
- **PAX8 and IRF4 are the strongest real dependencies I found, and both sit at
  `involvement: unknown` in Andrey's round-01 Mediator table** (family prior or
  nothing). Since they already clear the hardest bar — a real, selective,
  statistically strong dependency — they look like the better use of the next
  literature pass than continuing to push RUNX2/CEBPB/ETV1.

Full results table (38,666 TF×context rows, lineage + subtype level, with
n/median/Cohen's d/p/q-value per row) is in the scratch repo, not this one yet.

Note for Andrey/Amir: I wrote this before reading `team/README.md` and
`src/dependency_scout/models.py`, so my output columns (`n_in`, `n_out`,
`in_median`, `out_median`, `cohens_d`, `pvalue`, `qvalue`) don't line up with
`DependencyEvidence` (`n_target_models`, `n_other_models`, `median_target_effect`,
`median_other_effect`, `target_dependent_fraction`, `other_dependent_fraction`,
`selectivity_delta`, `mann_whitney_p`). Mapping is mostly mechanical except
`target_dependent_fraction`/`other_dependent_fraction`, which I haven't computed
yet, and deciding whether `selectivity_delta` should just be the median gap or
Cohen's d. Didn't touch `models.py` or any other file — flagging this as a
schema-mapping task rather than doing it unilaterally, since changing a shared
type is a `DECISIONS.md` entry per the README.

Next:
- Map Stage-1 output into real `DependencyEvidence` objects (with
  `target_dependent_fraction`/`other_dependent_fraction` added) for PAX8, IRF4,
  TP63, TCF7L2, EBF1, ISL1, MYCN, ZNF217, and RUNX2/CEBPB/ETV1 (as documented
  dependency-gate failures, not just omissions) — this is what unblocks
  Andrey's "no `RankedCandidate` yet" blocker on his end.
- Normal-tissue/safety-proxy pass (UniProt + HPA) — my other stage — not
  started yet.
- Reconcile the scratch-repo architecture doc into this repo's actual structure
  instead of running two parallel plans.

Blocked: none.
