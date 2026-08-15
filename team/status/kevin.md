# Kevin — disease specificity / omics (@Kyung-TaeLee)

Newest on top. Format: ../README.md

## 2026-08-15 (4) — Kevin
Did: the subtype-dilution gate fix from round 3 is now **built and verified**,
not just proposed — and the pipeline is simplified per team direction (dropped
composite scoring, went back to a plain evidence table). All in
`~/coding/re-agent_discovery`.

**Gate fix, verified on the real target case.** Two changes to
`stage1_depmap.py`:
1. Added a specificity-first pass alongside the existing median test — a
   candidate now clears the gate if EITHER the group median is dependent
   (unchanged, still what rejects GTF2B/CTCF/MYC/AHCTF1 unprompted) OR
   `target_dependent_fraction >= 0.10` and `other_dependent_fraction <= 0.05`
   regardless of median (new — catches a subpopulation-restricted dependency
   a pooled-group median dilutes below every threshold).
2. Found a second bug while verifying the first fix actually worked: the old
   two-pass design only drilled into subtypes within lineages that already
   showed **lineage-level** significance. That structurally excludes a
   dependency diluted at *both* levels — Lung-lineage pooling (NSCLC + every
   other lung cancer type) dilutes an already SCLC-subtype-restricted signal
   even further; ASCL1 fell to 9.5% target-dependent fraction at Lung level,
   just under the new 10% threshold, so it would still never have reached a
   subtype-level test. Fixed by making Pass 2 unconditional — every TF x
   every `OncotreeSubtype`, not gated behind Pass-1 lineage signal. Costs
   more compute (132,677 rows now vs. 38,666 before; full re-run is 77s, not
   a real constraint), not more code — this is simpler than the old
   contingent two-stage logic, not more complex.

**Re-ran the full universe. Confirmed on the actual example from the
findings doc**, at `Small Cell Lung Cancer` (real DepMap 24Q4 data, n=25):
ASCL1 (44% dependent in-context / 1.2% outside), POU2F3 (16% / 0%), INSM1
(32% / 0.3%), NEUROD1 (12% / 0.5%) all now clear `dependency_flag=True` via
the specificity path. Real SCLC lineage biology surfaces around them too —
MYCL, NKX2-1, FOXA1/FOXA2, SOX2/SOX4 — none of which showed up under the old
gate. Statistical significance after BH correction is a separate, harder bar
that this doesn't solve on its own: only INSM1 clears q<0.05 (q=0.009);
ASCL1 is borderline (q=0.06); POU2F3/NEUROD1 don't clear it (q=0.15, 0.56).
That's expected and correctly visible — the gate now *sees* these candidates
instead of structurally excluding them, which is a different claim than
"these are statistically confirmed," and the table keeps both facts visible
rather than collapsing them into one pass/fail number.

**Also caught a real trap while building the output table**: a context can
have a deceptively low q-value with `dependency_flag=False` — e.g. POU2F3 in
Head and Neck, q=0.004, but both group medians are *positive* (a growth
*advantage* from knockout, not a dependency). Mann-Whitney only tests
"are these two distributions different," not "is this essential." Sorting by
q-value alone would surface this as if it were a strong hit; keeping
`dependency_gate` as an explicit separate column is what stops that.

**Composite scoring dropped, per team direction.** `ranking.py`'s
gate/discovery_score/enrichment_score/tier-ceiling machinery and
`agent_tools.py`'s orchestrator are left in place but no longer the active
path — retired in place, not deleted, in case anything still references them.
New entry point is `discover(tf=None, disease_context=None) ->
pd.DataFrame` in `discover.py`: given a TF and/or an exact disease-context
string, returns one flat table — dependency stats, every Mediator/STRING
lead (or the honest reason there's none), literature status per lead — no
score, no rank. Disease-context resolution (matching a user's loose phrasing
to the exact stored string, including judgment calls like "SCLC pools
molecular subtypes DepMap doesn't itself split") is left to whichever LLM is
driving the call rather than a fuzzy-match heuristic inside the function.

Next: HPA/UniProt/ENCODE (Stages 2/3) still stubbed — building those next,
UniProt via Paperclip's `/proteins/` corpus rather than a second REST client
since Paperclip already indexes it.

Blocked: none.

## 2026-08-15 (3) — Kevin
Did: closing out the two Band-1 items blocking on me in `TASKS.md`, plus the
literature pass `FINDINGS_DEPMAP_ROUND01.md` §5 flagged as the critical next
step. All work through this point is in `~/coding/re-agent_discovery`
(scratch repo); relevant results below are what actually needs to leave it.

**Task #4 (ranking weights) — mapping is done, pointing Amir at it.** The
25/25/20/15/10/5 spec is written up in full in `docs/SCORING_SPEC.md` (scratch
repo) with a working reference implementation in `src/ranking.py` there —
`compute_gate` / `compute_discovery_score` / `component_score` /
`compute_enrichment_score` / `compute_final_score`. The one thing worth
importing along with the six numbers: evidence *quality* is a ceiling on a
component's score, not an addend — a `SupportType.DIRECT_EXPERIMENTAL` claim
caps at 1.0, `computational_prediction` caps at 0.35, etc., and corroborating
claims at the same tier fill toward the ceiling with diminishing returns
rather than summing unboundedly. Validated against the round-01 examples:
ELK1→0.75, RUNX2→0.667, CEBPB→0.5 (correctly capped despite a
`direct_experimental`-labeled claim, because `interacting_region_mapped` is
false), ETV1→0.175. Ran this against real STRING data too — it's what caught
that ELK1–MED23's STRING combined score (0.871) is 100% text-mining
(`escore=0`), which is exactly the failure mode the tier-ceiling design exists
to prevent from leaking into a real score.

**`selectivity_delta` sign convention — recommending `dependency_scout`'s
convention wins.** Hit this exact bug myself independently: my own
`ranking.py` percentile-ranked `selectivity_delta` ascending and it put
IRF4 — the single strongest hit in the whole 38,666-row store — at the
*bottom* of the distribution, because `in_median - out_median` is very
negative for a real hit. Given real DepMap output already emits
`dependency_scout`'s positive-is-selective convention (per
`FINDINGS_DEPMAP_ROUND01.md`), and it's the package `models.py` itself lives
in, `reagent_workflow` should flip to match rather than the reverse — fewer
real artifacts need to change. Logged as a decision below; needs Andrey's
sign-off since it's a shared-type-adjacent call per `CONTRIBUTING.md`.

**IRF4 and PAX8 broad literature pass — still empty, now checked properly.**
`FINDINGS_DEPMAP_ROUND01.md` §5 and I independently landed on the same next
step: these two clear the dependency bar hardest and have `involvement:
unknown`, so they're the obvious candidates to chase. STRING gives zero leads
for either (checked last round), so this pass was unguided: direct
"IRF4/PAX8 + Mediator" search, then broadened to any coactivator contact at
all (following the POU2F3–OCA-T1 precedent — a mapped contact doesn't have to
be a literal Mediator subunit). Nothing for either gene, from any angle.
IRF4's real literature is IRE1/LAMP5/SOX9/PD-L1 regulation; PAX8's is a gene
network paper, an unrelated Mediator/nuclear-receptor review, and one
off-target Arabidopsis auxin paper. **The empty intersection Vraj found in
round 02 is now checked from every angle I have, not just STRING's** — real
dependencies exist, none has a discoverable physical contact of any kind, to
anything. That's the actual state of the hypothesis, not a gap in the search.

**Subpopulation-dependency gate fix — my call: yes to option 2, additive not
replacing.** Vraj's POU2F3 finding (dependency of SCLC-P specifically, a
subpopulation within one `OncotreeSubtype` bucket DepMap doesn't further
split) is a real blind spot my own gate has too — a median-based test cannot
see a dependency that defines a subpopulation rather than the whole context,
which is arguably the more common shape for real transcriptional addiction.
Recommend adding the specificity-first gate (very low
`other_dependent_fraction` + meaningful `target_dependent_fraction`,
regardless of median) as an **additional** admission path alongside the
existing median gate, not a replacement — the median gate is what correctly
rejects GTF2B/CTCF/MYC/AHCTF1 unprompted on real data, and a candidate should
clear if it passes *either* test. Still needs Andrey's sign-off per
`FINDINGS_DEPMAP_ROUND01.md`'s own framing, but this is the direction I'd
implement it in. Not implemented yet — happy to build it in
`dependency_scout/ranking.gate` if that's the green light.

Also built and tested (scratch repo, not yet needed by anyone else so not
detailed here): Stage 4 STRING Mediator-connectivity filter with a
whole-complex-artifact rejection rule (caught EBF1 and CEBPB both showing the
same flat near-uniform pattern across ~all 33 subunits — a database
complex-level annotation, not subunit-specific evidence), a cache-only
literature-search tool with permanent negative caching, and a tab-split
evidence dashboard reading off the typed outputs. None of this blocks anyone
else's task; flagging it exists in case Band 3 wants a second reference for
what `demo.json` needs to carry per candidate.

Next:
- Reconcile my scratch-repo `ranking.py`/`stage1_depmap.py` against
  `dependency_scout`'s real modules once the sign convention is settled —
  right now they'd silently disagree.
- If the specificity-first gate gets a green light, implement it directly in
  `dependency_scout/ranking.gate` rather than the scratch repo, since that's
  where it actually needs to live.
- Nothing else queued; Band 1 #1 and #4 are the only things that were
  blocking on me and both have an answer above now.

Blocked: none — waiting on Andrey for the sign-convention and gate-fix
sign-off, not blocked from other work in the meantime.

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
