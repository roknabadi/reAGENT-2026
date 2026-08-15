# Scoring spec — `RankedCandidate` from raw evidence

Drafted against `src/dependency_scout/models.py` in the main repo (read there,
not copied — field names below must stay in sync with it). Scope: how the four
score fields on `RankedCandidate` — `discovery_score`, `enrichment_score`,
`evidence_completeness`, `final_score` — get computed from the typed evidence
objects that already exist (`DependencyEvidence`, `MediatorLink`,
`EnrichmentEvidence`, `GateResult`), for an agentic ranking-on-query use case
(store everything, rank at query time against a disease-context filter)
instead of a one-shot hard-filtered pipeline run.

**Design commitment carried over from the contract itself:** `discovery_score`
and `enrichment_score` are separate fields, not one flat weighted sum across
all six components in `DISCOVERY_ARCHITECTURE.md` §7. That split is preserved
here rather than re-flattened — `discovery_score` is quantitative-only
(`DependencyEvidence`), `enrichment_score` is everything that depends on a
`Claim` (`EnrichmentEvidence` + `MediatorLink`). This matters because it keeps
"how strong is the dependency" legible on its own from "how much do we know
about the Mediator side," which is exactly the split Andrey's round-01 gate
review needed.

---

## 1. `GateResult` — statistical eligibility, computed first, scored never

Significance and power belong in the gate, not the score — a candidate either
clears the dependency bar or it doesn't; there is no partial credit for "sort
of significant."

```
eligible = (
    mann_whitney_p is not None and mann_whitney_p < 0.05        # or BH q, see Open Q1
    and target_dependent_fraction - other_dependent_fraction > 0  # direction check
    and n_target_models >= SUBTYPE_MIN_N_FLOOR                   # power floor, from config
)
failures = [reason strings for each failed clause above]
```

A candidate that fails the gate gets `discovery_score = 0.0`, is excluded from
`Shortlist.shortlist_indices` (the model already enforces this via
`shortlist_is_reviewable`), but **stays in the store** — this is the "keep
everything" part. Nothing is deleted; ineligible rows are just never ranked
or shortlisted until new evidence changes the gate.

---

## 2. `discovery_score` ← `DependencyEvidence` only

Re-derives `DISCOVERY_ARCHITECTURE.md` §7's dependency-strength (25%) +
disease-specificity (25%) pair, renormalized to sum to 1 since those are the
only two components this field covers:

```
strength    = percentile_rank(target_dependent_fraction, across ALL rows in the store)
specificity = percentile_rank(selectivity_delta,          across ALL rows in the store)

discovery_score = gate.eligible * (0.5 * strength + 0.5 * specificity)
```

**Normalize against the whole stored universe, not the query's result set.**
A disease-context query for "Kidney" should return a `discovery_score` for
PAX8 that means the same thing as a query for "Lymphoid" returning one for
IRF4 — otherwise scores aren't comparable across queries, which defeats the
point of a persistent store. Percentile rank is computed once when a row is
written/updated, not per query.

`n_target_models` doesn't enter the formula (it's already spent in the gate's
power floor) but should still ride along in the output so a caller can see
`IRF4/Plasma Cell Myeloma (n=19)` looked different from `EBF1/Burkitt
Lymphoma (n=6, low-confidence)` even at equal `discovery_score`.

---

## 3. Evidence-quality capping — the mechanism that fixes the ELK1 problem

Applies before any raw evidence number is allowed into `enrichment_score`.
This is the direct answer to "how do we weight evidence quality": **quality
caps, strength fills.** A tier ceiling by `SupportType` bounds how high a
component can possibly score; the underlying corroboration strength (below)
decides where within that ceiling it lands. Quality is never a bonus added to
strength — that's what let ELK1–MED23's STRING score reach 0.871 on
text-mining alone (`escore=0`, `dscore=0`) in the run that motivated this
spec.

```
TIER_CEILING = {
    SupportType.DIRECT_EXPERIMENTAL:      1.00,
    SupportType.GENETIC_FUNCTIONAL:       0.65,
    SupportType.COMPUTATIONAL_PREDICTION: 0.35,
    SupportType.INFERENCE:                0.15,
}

def component_score(claims: list[Claim]) -> float | None:
    if not claims:
        return None                      # absent, not zero — see §5
    best_tier = max(TIER_CEILING[c.support] for c in claims)
    n_at_best = count(c for c in claims if TIER_CEILING[c.support] == best_tier)
    corroboration = 1 - 1 / (1 + n_at_best)   # 1 source ~0.5, 2 sources ~0.67, saturates
    return best_tier * corroboration
```

One `direct_experimental` claim (e.g. the Monté et al. cryo-EM structure)
lands around `1.00 * 0.5 = 0.5`; a second independent direct claim pushes it
toward `1.00 * 0.67 ≈ 0.67`; ten independent text-mining-tier claims never
clear `0.35 * <1 = 0.35`. This directly encodes PROJECT.md's "correlation
passing itself off as contact is the failure mode this project exists to
catch": no amount of weak corroboration crosses into strong-evidence range.

**STRING scores never enter this function.** A STRING edge isn't a `Claim` —
it has no citable statement, no `SupportType`, no DOI. Per `DECISIONS.md`
("Literature triage enters the repo as evidence, never as a ranking"),
STRING output is one level further removed than even round-01's literature
triage: it's a **retrieval-priority signal for which pairs to send to
Paperclip next**, not an input to any score. A STRING hit becomes eligible
for `component_score()` only once a Paperclip search on that specific pair
returns text that gets written as an actual `Claim`.

---

## 4. `enrichment_score` ← `EnrichmentEvidence` + `MediatorLink`

Best-fit mapping of `DISCOVERY_ARCHITECTURE.md` §7's remaining four
components (mediator/lit quality 20%, safety 15%, regulatory 10%,
tractability 5% → renormalized to sum 1 within `enrichment_score`) onto the
four `EnrichmentEvidence` fields:

| Field | Weight | Source | Fed by `component_score()` on |
|---|---|---|---|
| `literature_support` | 0.40 | Mediator/lit quality | `MediatorLink.claims` |
| `normal_cell_support` | 0.30 | Safety proxy | future Stage-3 `Claim`s (HPA/UniProt) |
| `interface_support` | 0.20 | Structural interface quality | `MediatorLink.claims` where `interacting_region_mapped` |
| `tractability_support` | 0.10 | Tractability preview | future structure-lookup `Claim`s |

```
present = [f for f in the four fields if f is not None]
enrichment_score = (
    sum(weight[f] * value[f] for f in present) / sum(weight[f] for f in present)
    if present else None
)
```

Renormalizing by the weight of *present* fields (rather than treating an
absent field as 0) is what keeps a candidate honestly incomplete instead of
punished for evidence nobody collected yet — see `evidence_completeness`,
which is where the penalty for "haven't looked" actually lives.

`literature_support` and `interface_support` both draw on
`MediatorLink.claims` because both `DISCOVERY_ARCHITECTURE.md`'s "mediator/lit
quality" and "structural tractability preview" components are, in this
project, the same evidence base at different resolutions (does contact exist
at all vs. is the mapped region well-formed) — see Open Question 4 below,
this is a judgment call, not a settled mapping.

---

## 5. `evidence_completeness`

```
evidence_completeness = count(non-null among the 4 EnrichmentEvidence fields) / 4
```

Distinguishes "we checked and found nothing" (a field explicitly set to a low
number, backed by claims saying so) from "we haven't checked yet" (field is
`None`). Both are honest states; only the second should be cheap to fix by
running another stage, and only the second should NOT drag the score down the
way a genuine negative finding should.

---

## 6. `final_score`

Re-derives the architecture doc's headline "dependency+specificity dominant
at 50%, everything else secondary" at the top level, faithfully as a 50/50
split between the two `RankedCandidate` score fields:

```
final_score = 0.5 * discovery_score + 0.5 * (enrichment_score or 0) * evidence_completeness
```

Multiplying the enrichment half by `evidence_completeness` (not adding it as a
separate weighted term) means a candidate with zero enrichment work done sits
exactly at `0.5 * discovery_score` — ranked purely on the DepMap signal, never
penalized below that for missing work, and never able to out-rank a fully
enriched candidate on `discovery_score` alone once real Mediator/safety
evidence exists for the competitor.

---

## Querying by disease context

```
candidates = store.filter(disease_context matches query)   # exact or ontology-mapped
ranked = sorted(candidates, key=final_score, descending)
```

Because `discovery_score` is normalized against the whole store (not the
query's result set — §2), a "Kidney" query and a "Lymphoid" query return
scores on the same scale, so an agent comparing across disease contexts (e.g.
"which disease has the single best-supported candidate overall") doesn't need
a second normalization pass.

---

## Open questions — need a `DECISIONS.md` entry before implementing, not a
unilateral call

1. **No `qvalue` field on `DependencyEvidence`, only `mann_whitney_p`.** The
   Stage-1 pipeline BH-corrects across ~1,639 TFs; the gate above uses raw p
   as a stand-in, which is the wrong number for a store this size (38,666
   rows) — it will pass obviously-spurious hits. Either add `qvalue: float |
   None` to the contract, or the gate needs the correction applied before
   `DependencyEvidence` is constructed and `mann_whitney_p` needs to hold the
   corrected value with a rename or a documented convention.
2. **`target_dependent_fraction`/`other_dependent_fraction` are required
   fields with no current definition.** Kevin's status already flagged this.
   This spec needs an essentiality threshold to compute them — proposing
   reuse of the existing `IN_CONTEXT_DEPENDENCY_THRESHOLD = -0.5` from
   `src/config.py` (fraction of models with Chronos ≤ −0.5), so Stage 1's
   existing constant becomes the fraction's cutoff too, not a second number.
3. **`selectivity_delta` definition.** Proposing `in_median − out_median`
   (raw Chronos units), keeping Cohen's `d` out of it — `d` is already
   scale-normalized and belongs with the significance/power gate, not
   double-counted into the specificity score.
4. **`MediatorLink` is single-subunit** (`partner_gene` defaults to
   `"MED23"`), but real STRING output connects a TF to several subunits
   (MYCN → MED15/MED12/CDK8/CCNC/CDK19/MED14). Proposing the ranking layer
   emit one `RankedCandidate` **per (TF, subunit) pair**, not per TF — a TF
   with 4 candidate subunits becomes 4 rows in `Shortlist.candidates`, each
   independently gated and scored. This needs zero schema change, only a
   pipeline convention, but changes what "a candidate" means in the shortlist
   and should be confirmed with Andrey/Amir before the ranking code assumes
   it.
5. **No dedicated field for Stage 2 (ENCODE-rE2G regulatory mechanism
   support)** anywhere in `EnrichmentEvidence`. Not shoehorning it into an
   existing field silently — flagging that it's simply not scored under this
   spec until either a 5th field is added or a decision is made to fold it
   into `interface_support`'s `notes`.

None of the above are implemented yet. §1–6 (the actual scoring mechanics) can
be implemented today with zero changes to `models.py`. Open Questions 1–5
would each touch the shared contract or its interpretation and should go
through the team's decision process first.
