# Discovery Architecture — Agentic TF–Mediator Vulnerability Search

**Scope:** this document covers only the *Discovery* half of Track A (disease/cell state →
selective TF dependency → Mediator interface hypothesis). Structural modeling and virtual
screening are out of scope here.

**Design point:** unbiased, all-human-TF screen (~1,639 TFs, Lambert et al. 2018 curated
list) filtered down to Mediator-connected hits, rather than starting from a pre-selected
Mediator-adjacent TF shortlist. This is slower but avoids baking in the answer.

**Data access mode:** live public APIs/bulk downloads, cached locally after first fetch
(no local pre-staged datasets assumed).

---

## 1. Pipeline overview

```
[0] TF universe            (Lambert et al. TF list, static reference file)
      │
[1] Dependency mining      (DepMap CRISPR screens)
      │  → candidate (TF, disease/lineage context) pairs, ranked by
      │    dependency strength + selectivity
      ▼
[2] Regulatory context     (ENCODE-rE2G + TF motif/ChIP overlap)
      │  → confirms TF sits on an active enhancer program in the
      │    relevant cell type (mechanistic plausibility, not just
      │    correlation from a knockout screen)
      ▼
[3] Expression curation    (UniProt + Human Protein Atlas)
      │  → disease-context expression confirmed; normal-tissue
      │    breadth used as a safety/tolerability proxy
      ▼
[4] Mediator connectivity  (STRING PPI)
      │  → does the candidate TF have direct or short-path evidence
      │    of interaction with any of the ~26 Mediator subunits?
      ▼
[5] Convergence / ranking  (composite score + Paperclip literature pass)
      │  → single ranked table of disease–TF–Mediator hypotheses
      ▼
[6] Hero selection          → hand off to structural step
```

Each stage is implemented as an independent, cacheable data-pull + scoring module so it can
run as a Claude Code subagent later; for now this doc specifies *what* each stage computes
and *how* it talks to its data source.

**Validation controls:** ELK1–MED23 and ELF3–MED23 should be run through the full pipeline
as sanity checks. ELK1–MED23 in particular is a poor DepMap dependency example (ELK1 is not
broadly essential), so it mainly validates stage 4 (PPI) and stage 5 (structural
tractability), not stage 1. ELF3 in HER2+ breast contexts should validate stages 1–4
end-to-end and is the better calibration case for the dependency + specificity math.

---

## 2. Stage 0 — TF universe

- **Source:** Lambert et al. 2018 "The Human Transcription Factors" curated list
  (~1,639 genes, http://humantfs.ccbr.utoronto.ca/, also mirrored on GitHub as a flat
  CSV). Static reference, no API needed — download once, cache as
  `data/cache/tf_universe.csv`.
- **Output fields:** gene symbol, Ensembl ID, DNA-binding domain family, "Is TF?" confidence
  category (keep only high-confidence TFs to start).

---

## 3. Stage 1 — Dependency mining (DepMap)

**Question:** which TFs show a *selectively* strong dependency in some disease/lineage
context, rather than being pan-essential or uniformly non-essential?

- **Data:** DepMap CRISPR gene-effect scores (Chronos), current public release (24Q\*),
  plus `Model.csv` sample metadata (lineage, subtype, disease).
  - DepMap does not expose a queryable REST API for bulk scores; the practical "live" path
    is to pull the release files directly from the DepMap Figshare bulk-download endpoints
    (stable URLs per release) and cache them locally — this is a one-time ~1–2 GB pull,
    not a per-TF API call.
  - Alternative for lighter-weight, per-gene lookups: the **DepMap Portal's
    `/api/download` / `partials` gene-summary endpoints** and **cBioPortal API**
    (`www.cbioportal.org/api`) can return per-gene dependency and expression summaries
    without the full bulk file, useful for the convergence stage's fast re-checks.
- **Context granularity (locked): hierarchical two-pass.** A single granularity level
  can't do this job — lineage-level alone would dilute/miss a HER2+-breast-shaped hit
  (ELF3's actual case), while subtype-level alone risks noise from tiny cell-line groups.
  1. **Pass 1 — lineage level** (`Model.csv` `Lineage`, ~30 categories): robust,
     well-powered screen across all ~1,639 TFs. Compute a **context-specificity score**
     per TF per lineage — Cohen's d or median-gap (in-lineage vs. all-other-lines) — plus
     an overall **skewness/variance** measure of the full gene-effect vector (Chronos
     scores are approximately normal for pan-essential genes and heavy-tailed for
     selective dependencies; this is the standard DepMap "strongly selective" heuristic).
     Flag TF–lineage pairs with in-context median effect ≤ −0.5, out-of-context median
     effect > −0.2, rank-sum test BH-corrected across ~1,639 TFs.
  2. **Pass 2 — subtype/molecular drill-down**, run *only* within lineages that showed any
     signal in Pass 1: repeat the same comparison at `OncotreeSubtype` / molecular-subgroup
     / driver-oncogene-status resolution. Require a hard minimum n≥15 cell lines per group
     to report a result at full confidence; groups below that threshold are still reported
     but explicitly flagged **low-confidence (small n)** rather than dropped, so a true
     HER2+-style hit isn't silently discarded for lack of statistical power.
- **Output:** table of (TF, context, context level [lineage/subtype], dependency strength,
  selectivity score, p-value, confidence flag) — this is the raw candidate list that feeds
  every downstream stage.

---

## 4. Stage 2 — Regulatory context (ENCODE-rE2G)

**Question:** is the candidate TF actually sitting on an active regulatory program in the
relevant cell type, giving a mechanistic (not just statistical) story?

- **Data:** ENCODE Registry of candidate cis-regulatory elements-to-gene (rE2G) predictions.
  - Distributed as biosample-specific prediction files on the ENCODE portal
    (`www.encodeproject.org`), queryable via the ENCODE REST API
    (`/search/?type=Prediction&...`, JSON responses) filtered to biosamples matching the
    Stage 1 disease context (e.g. breast epithelial / HER2+ cell lines).
  - rE2G itself predicts **enhancer → target gene** links, not TF → target directly. To
    connect a specific TF to that regulatory program, overlap rE2G-linked enhancers with
    TF binding evidence:
    - If available for the TF/cell type: ENCODE ChIP-seq peaks for that TF (same portal,
      `/search/?type=Experiment&target=<TF>`).
    - Otherwise: motif scan (e.g. JASPAR PWM for the TF) over the rE2G-nominated enhancer
      sequences as a plausibility proxy.
- **Output:** for each candidate TF–context pair, a regulon size (# of rE2G-linked genes
  plausibly bound by the TF) and whether any of those target genes overlap known
  disease-driver genes for that context (cross-check against COSMIC/OncoKB gene lists —
  nice-to-have, not blocking).
- **Note:** this stage is evidence-*enriching*, not a hard filter — absence of an rE2G
  biosample match for a given cell type should down-weight, not eliminate, a candidate.

---

## 5. Stage 3 — Expression curation (UniProt + HPA)

**Question:** is the protein actually expressed where the dependency shows up, and how
broadly is it expressed in normal tissue (safety proxy)?

- **UniProt REST API** (`rest.uniprot.org`, no key required):
  - Confirm canonical protein, subcellular localization, known domains (DNA-binding domain,
    transactivation domain, any annotated disordered regions — relevant later for structural
    tractability), and existing disease annotations (`DISEASE` comments).
- **Human Protein Atlas**:
  - Per-gene JSON endpoint (`proteinatlas.org/<ENSG>.json`) or bulk TSV download.
  - Pull: RNA tissue specificity category (`Tissue enhanced` / `Group enriched` /
    `Low tissue specificity`), the **tissue specificity score**, and RNA/protein levels in
    the disease-relevant tissue vs. the full tissue panel.
- **Safety proxy metric:** normal-tissue breadth score = number of normal tissues with
  meaningful expression (or inverse of HPA's specificity score). Narrow normal-tissue
  expression + high expression in the disease context = better safety proxy. This is a
  *proxy* only — flag it explicitly as such in all outputs, not a substitute for real
  tolerability data.
- **Output:** per-TF expression confirmation flag, tissue specificity score, safety-proxy
  score.

---

## 6. Stage 4 — Mediator connectivity filter (STRING)

**Question:** does this TF have direct or near-direct evidence of physically or
functionally interacting with Mediator?

- **Reference set:** all Mediator subunits — MED1, MED4, MED6–MED31, the kinase module
  (MED12, MED12L, MED13, MED13L, CDK8, CDK19, CCNC), i.e. ~30 gene symbols. Store as a
  static reference file, same pattern as the TF universe list.
- **Data:** STRING REST API (`string-db.org/api`), no key required.
  - `/api/json/network` or `/api/json/interaction_partners` for each candidate TF against
    the full Mediator subunit set, requesting **all evidence channels** (experimental,
    database, text-mining, co-expression) with their individual sub-scores, not just the
    combined score.
- **Scoring:**
  - Direct edge TF↔any Mediator subunit: take the STRING combined confidence score.
  - No direct edge: optionally check 1-hop bridging (TF → intermediate → Mediator subunit)
    via STRING's network expansion, but weight this much lower and require ≥2 corroborating
    evidence channels.
  - Prioritize interactions with **experimental/database** evidence over pure text-mining,
    since text-mining scores are trivially inflated by co-citation in review articles.
- **Output:** per-TF best Mediator subunit hit, combined score, evidence-channel breakdown,
  direct-vs-bridged flag. TFs with no STRING evidence at any threshold are dropped here —
  this is the one genuinely hard filter in the pipeline, per the project's stated scope.

---

## 7. Stage 5 — Convergence / ranking

Composite score per surviving (TF, disease context, Mediator subunit) triple:

| Component | Source | Notes |
|---|---|---|
| Dependency strength | Stage 1 | in-context median gene effect |
| Disease specificity | Stage 1 | context-vs-rest selectivity score |
| Normal-tissue safety proxy | Stage 3 | HPA tissue-specificity score |
| Regulatory mechanism support | Stage 2 | regulon size / driver-gene overlap (bonus, not required) |
| Evidence quality | Stage 4 + literature | STRING evidence-channel mix + Paperclip literature hit count/quality |
| Structural tractability (preview only) | UniProt domains + PDB/AlphaFold lookup | folded domain interface vs. pure IDR-IDR contact; presence of any solved structure for TF or subunit |

- Normalize each component to 0–1 within the surviving candidate set, apply a weighted sum.
  **Weights (locked):**

  | Component | Weight | Rationale |
  |---|---|---|
  | Dependency strength | 25% | |
  | Disease specificity | 25% | together the dominant 50% — this is the actual novel discovery signal |
  | Mediator/literature evidence quality | 20% | already used as the Stage 4 hard filter; kept moderate here (not dominant) so ranking doesn't just reward the best-studied TFs via STRING's text-mining bias |
  | Normal-tissue safety proxy | 15% | |
  | Regulatory mechanism support (Stage 2) | 10% | enrichment, not required |
  | Structural tractability preview | 5% | tiebreaker only — real assessment happens in the structural step |

  Keep these as a config, not hardcoded, so they can be re-tuned if the ranked output looks
  obviously wrong on the validation controls.
- **Literature pass:** for the top ~10–20 candidates by composite score, run a Paperclip
  literature search per (TF, disease, Mediator subunit) triple to (a) catch known prior
  work — including surfacing if a candidate is *already* a described interaction like
  ELK1–MED23/ELF3–MED23, which should be flagged as "known, use as validation" rather than
  presented as novel — and (b) pull supporting/contradicting evidence quality into the score.
- **Output:** single ranked table with full provenance (why each candidate ranked where it
  did, per component) so the choice is defensible, not a black box.
- **Hero handoff (locked): top-3 → cheap triage → commit to 1 (occasionally 2).** Carry the
  top-3 composite-score candidates into a fast, cheap structural triage — AlphaFold-Multimer
  confidence (ipTM/pLDDT at the predicted TF–Mediator interface) plus a check for whether
  either partner has a folded domain at the contact vs. a pure IDR–IDR interaction. Only the
  survivor(s) of that triage proceed to full docking + virtual screening (the expensive
  Modal/Tamarind compute), since running that step on all 3 candidates isn't worth the cost.
  This hedges against a #1-ranked pick turning out structurally unmodelable without tripling
  downstream compute.

---

## 8. Practical notes on "live public API" access

| Source | Access pattern | Auth | Caching guidance |
|---|---|---|---|
| DepMap | Bulk file download (Figshare release URLs) | none | download once per release, cache full CRISPR + Model files locally (~1–2 GB) |
| DepMap (light lookups) | Portal `/api/download` partials, or cBioPortal API | none | cache per-gene responses |
| ENCODE-rE2G | ENCODE portal REST API (`/search/?type=Prediction`) | none | cache per-biosample prediction file |
| UniProt | `rest.uniprot.org` REST API | none | cache per-accession JSON |
| HPA | `proteinatlas.org/<ENSG>.json` or bulk TSV | none | cache per-gene JSON, or one bulk TSV pull |
| STRING | `string-db.org/api` REST API | none | cache per-TF interaction_partners JSON; STRING asks for a registered `caller_identity` string on repeated calls and rate-limits heavy batch use — batch requests and respect their fair-use notes |

All five sources are free/no-key, which fits the "live" access choice — the main practical
cost is DepMap's bulk file size and STRING's batch rate limits, both handled by local
caching under `data/cache/`.

---

## 9. Decisions locked

1. **Stage 1 context granularity** — hierarchical two-pass: lineage-level screen first,
   subtype/molecular drill-down only within lineages showing signal, n≥15 confidence
   threshold (see §3).
2. **Stage 5 weighting** — dependency strength + disease specificity dominant at 50%
   combined; Mediator/literature evidence quality moderate at 20% (already gated at Stage
   4, not re-dominant here); safety proxy 15%; regulatory mechanism support 10%; structural
   tractability 5% tiebreaker (see §7).
3. **Hero handoff** — top-3 by composite score into a cheap AlphaFold-Multimer confidence
   triage, commit full docking/virtual-screening compute to the triage survivor(s) only
   (see §7).

No open decisions remain before implementation starts on Stage 1.
