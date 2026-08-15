"""
Ranking layer -- turns stored evidence (Stage 1 DepMap, Stage 4 STRING, and
any typed literature MediatorLink examples) into `RankedCandidate` /
`Shortlist` objects, per docs/SCORING_SPEC.md.

Imports the real shared contract from the main repo's dependency_scout
package (`pip install -e /path/to/reAGENT-2026` into this repo's venv) rather
than redefining the types here -- see CLAUDE.md: "the data contract is code,
not a doc."

Two things this module deliberately does NOT do, per the spec:
  - It never lets a STRING score feed a Claim/score directly. STRING output
    (data/results/stage4_mediator_string_hits.csv) is used only to pick which
    (TF, Mediator subunit) pairs get a candidate row at all -- a retrieval
    lead, not evidence. Every such row starts with empty `claims`, i.e.
    `enrichment_score=None`, until a literature pass produces real Claims.
  - It never invents evidence to fill a gap. `evidence_completeness` reports
    the gap honestly instead.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from dependency_scout.models import (
    Claim,
    DependencyEvidence,
    EnrichmentEvidence,
    EvidenceTier,
    GateResult,
    Involvement,
    MediatorLink,
    RankedCandidate,
    Shortlist,
    SourceRecord,
    SupportType,
)

import config

MAIN_REPO = Path(__file__).resolve().parent.parent.parent / "reAGENT-2026"
EXAMPLES_DIR = MAIN_REPO / "examples"

# STRING-artifact rule (see SCORING_SPEC.md addendum below): a TF whose STRING
# hits span an implausibly large fraction of all Mediator subunits at a near-
# flat score is very likely a whole-complex database annotation, not
# subunit-specific evidence (confirmed on CEBPB: 32/33 subunits, dscore=0.5
# flat; EBF1: 33/33 subunits, dscore=0.5 flat). Candidates like this are
# rejected out loud -- excluded from per-subunit candidate rows -- rather
# than emitting dozens of meaningless "leads."
STRING_ARTIFACT_SUBUNIT_COUNT = 15

# Statistical gate (Open Question 1 workaround): DependencyEvidence has no
# dedicated `qvalue` field, only `mann_whitney_p`. Stage 1 BH-corrects across
# ~1,639 TFs before this module ever sees a row, so the value stored in
# `mann_whitney_p` below IS the corrected q-value, not the raw p -- documented
# in every emitted SourceRecord.notes so this never gets misread downstream.
SIGNIFICANCE_ALPHA = 0.05

TIER_CEILING = {
    SupportType.DIRECT_EXPERIMENTAL: 1.00,
    SupportType.GENETIC_FUNCTIONAL: 0.65,
    SupportType.COMPUTATIONAL_PREDICTION: 0.35,
    SupportType.INFERENCE: 0.15,
}

INVOLVEMENT_CEILING = {
    Involvement.DIRECT: 1.00,
    Involvement.INDIRECT: 0.50,
    Involvement.PREDICTED: 0.30,
    Involvement.UNKNOWN: 0.00,
}

ENRICHMENT_WEIGHTS = {
    # renormalized from DISCOVERY_ARCHITECTURE.md SS7's 20/15/10/5 (regulatory
    # mechanism support has no dedicated EnrichmentEvidence field -- Open
    # Question 5 -- so it is not represented here)
    "literature_support": 0.40,
    "normal_cell_support": 0.30,
    "interface_support": 0.20,
    "tractability_support": 0.10,
}


# ---------------------------------------------------------------------------
# SS3 -- evidence-quality capping: quality caps, strength fills
# ---------------------------------------------------------------------------
def component_score(claims: list[Claim]) -> float | None:
    """None if no claims exist at all (absent, not zero). Otherwise: the best
    SupportType tier among the claims sets a ceiling; corroborating claims at
    that same tier fill toward it with diminishing returns. No amount of
    lower-tier corroboration can cross a higher tier's ceiling."""
    if not claims:
        return None
    best_ceiling = max(TIER_CEILING[c.support] for c in claims)
    n_at_best = sum(1 for c in claims if TIER_CEILING[c.support] == best_ceiling)
    corroboration = 1 - 1 / (1 + n_at_best)
    return round(best_ceiling * corroboration, 4)


def interface_support_score(link: MediatorLink) -> float | None:
    """Same as component_score, but additionally capped by the link's
    *derived* `involvement` -- not just the raw claim.support labels. This
    matters because a claim can be labeled `direct_experimental` in good
    faith and still not describe a mapped interacting region (see the CEBPB
    example: a `direct_experimental` claim about "Mediator complex exchange"
    with interacting_region_mapped=False derives involvement=INDIRECT, and
    interface quality must respect that gate, not just the raw claim label)."""
    if not link.claims:
        return None
    raw = component_score(link.claims)
    return round(min(raw, INVOLVEMENT_CEILING[link.involvement]), 4)


# ---------------------------------------------------------------------------
# SS1 -- gate (statistical eligibility, scored never)
# ---------------------------------------------------------------------------
def compute_gate(dep: DependencyEvidence) -> GateResult:
    failures = []
    if dep.mann_whitney_p is None or dep.mann_whitney_p >= SIGNIFICANCE_ALPHA:
        failures.append(f"q={dep.mann_whitney_p} not below alpha={SIGNIFICANCE_ALPHA}")
    if dep.target_dependent_fraction <= dep.other_dependent_fraction:
        failures.append("target_dependent_fraction does not exceed other_dependent_fraction")
    if dep.n_target_models < config.SUBTYPE_MIN_N_FLOOR:
        failures.append(f"n_target_models={dep.n_target_models} below floor={config.SUBTYPE_MIN_N_FLOOR}")
    return GateResult(eligible=not failures, failures=failures)


# ---------------------------------------------------------------------------
# SS2 -- discovery_score, percentile-ranked against the WHOLE stored universe
# ---------------------------------------------------------------------------
def load_universe_percentile_ranks() -> pd.DataFrame:
    """Percentile rank of target_dependent_fraction and selectivity_delta
    across all 38,666 stored TF x context rows -- computed once against the
    full store, not per query, so scores stay comparable across different
    disease-context queries."""
    df = pd.read_csv(config.RESULTS_DIR / "stage1_dependency_hits.csv")
    df["strength_pct"] = scipy_stats.rankdata(df["target_dependent_fraction"], method="average") / len(df)
    # selectivity_delta = in_median - out_median is very NEGATIVE for a real
    # hit (Chronos: more negative = more essential), so a stronger hit has a
    # smaller (more negative) delta. Rank descending (most negative -> top
    # percentile) by ranking the negation, not the raw value.
    df["specificity_pct"] = scipy_stats.rankdata(-df["selectivity_delta"], method="average") / len(df)
    return df


def compute_discovery_score(row: pd.Series, gate: GateResult) -> float:
    if not gate.eligible:
        return 0.0
    return round(0.5 * row["strength_pct"] + 0.5 * row["specificity_pct"], 4)


# ---------------------------------------------------------------------------
# SS4/5 -- enrichment_score, evidence_completeness
# ---------------------------------------------------------------------------
# Paperclip literature pass, 2026-08-15: boolean corpus-wide co-occurrence
# grep (dominated by incidental supplementary-table noise, not real hits) plus
# targeted semantic search per pair, then a broader "does this TF have ANY
# documented Mediator connection" search per TF (in case, as with RUNX2's
# validation-control run, STRING pointed at the wrong subunit). No dedicated
# primary source describes a mapped, direct contact for any of these 7 pairs.
# Recorded as a note (not a Claim -- Claim requires a real citation per pair,
# and there is no source *for* a negative result) so this search isn't
# silently repeated. claims stays [] -> involvement remains UNKNOWN, scores
# stay None -- this is an honest "checked, found nothing," not an omission.
LITERATURE_PASS_LOG = {
    ("MYCN", "MED15"): "No dedicated source found (2026-08-15). Nearest hits are MYCN's KAT2A/NIPBL "
                        "regulators and general MED15/MED14 biology, none describing a MYCN-MED15 contact.",
    ("TP63", "CCNC"): "No dedicated source found (2026-08-15). Nearest hits are Mediator kinase-module "
                       "biology (CDK8/19-cyclin C) and TP63's other roles, none co-describing TP63-CCNC.",
    ("TCF7L2", "MED15"): "No dedicated source found (2026-08-15). One near-miss: PMC11908221 reports "
                          "bHLH TCF4 (ITF2/E2-2, chr18) recruiting Mediator in gonad development -- a "
                          "different gene from TCF7L2 (HMG-box Wnt effector, chr10) despite the shared "
                          "old-nomenclature alias 'TCF-4.' Rejected as a gene-symbol collision, not evidence.",
    ("ZNF217", "MED12"): "No dedicated source found (2026-08-15). ZNF217's documented complex is "
                          "LSD1/CoREST, not Mediator; no MED12/14/CDK8/MED13-specific contact source located.",
    ("ZNF217", "MED14"): "No dedicated source found (2026-08-15) -- see ZNF217/MED12 note.",
    ("ZNF217", "CDK8"): "No dedicated source found (2026-08-15) -- see ZNF217/MED12 note.",
    ("ZNF217", "MED13"): "No dedicated source found (2026-08-15) -- see ZNF217/MED12 note.",
}


def compute_enrichment(mediator: MediatorLink, tf: str | None = None) -> EnrichmentEvidence:
    lit = component_score(mediator.claims)
    iface = interface_support_score(mediator)
    notes = []
    log_entry = LITERATURE_PASS_LOG.get((tf, mediator.partner_gene)) if tf else None
    if log_entry:
        notes.append(f"Paperclip literature pass: {log_entry}")
    elif not mediator.claims:
        notes.append("no literature pass run on this (TF, subunit) pair yet -- STRING lead only")
    return EnrichmentEvidence(
        literature_support=lit,
        normal_cell_support=None,  # Stage 3 not implemented yet
        interface_support=iface,
        tractability_support=None,  # structure-lookup stage not implemented yet
        notes=notes,
        claims=mediator.claims,
    )


def compute_evidence_completeness(enrichment: EnrichmentEvidence) -> float:
    fields = [
        enrichment.literature_support,
        enrichment.normal_cell_support,
        enrichment.interface_support,
        enrichment.tractability_support,
    ]
    return round(sum(1 for f in fields if f is not None) / len(fields), 4)


def compute_enrichment_score(enrichment: EnrichmentEvidence) -> float | None:
    present = {
        name: getattr(enrichment, name)
        for name in ENRICHMENT_WEIGHTS
        if getattr(enrichment, name) is not None
    }
    if not present:
        return None
    weight_sum = sum(ENRICHMENT_WEIGHTS[n] for n in present)
    return round(sum(ENRICHMENT_WEIGHTS[n] * v for n, v in present.items()) / weight_sum, 4)


# ---------------------------------------------------------------------------
# SS6 -- final_score
# ---------------------------------------------------------------------------
def compute_final_score(discovery_score: float, enrichment_score: float | None, evidence_completeness: float) -> float:
    return round(0.5 * discovery_score + 0.5 * (enrichment_score or 0.0) * evidence_completeness, 4)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def build_dependency_evidence(row: pd.Series) -> DependencyEvidence:
    return DependencyEvidence(
        gene=row["tf"],
        disease_context=row["context"],
        n_target_models=int(row["n_in"]),
        n_other_models=int(row["n_out"]),
        median_target_effect=float(row["in_median"]),
        median_other_effect=float(row["out_median"]),
        target_dependent_fraction=float(row["target_dependent_fraction"]),
        other_dependent_fraction=float(row["other_dependent_fraction"]),
        selectivity_delta=float(row["selectivity_delta"]),
        mann_whitney_p=float(row["qvalue"]),  # BH-corrected q, not raw p -- see module docstring
        source=SourceRecord(
            name="DepMap Public 24Q4 CRISPRGeneEffect + Model",
            version=config.DEPMAP_RELEASE,
            url=f"https://figshare.com/articles/dataset/DepMap_24Q4_Public/{config.DEPMAP_ARTICLE_ID}",
            tier=EvidenceTier.PUBLIC_PRIMARY,
            notes="mann_whitney_p field holds the BH-corrected q-value from the Stage-1 scan, "
                  "not the raw Mann-Whitney p -- see docs/SCORING_SPEC.md Open Question 1.",
        ),
    )


def load_string_leads(min_n=1) -> pd.DataFrame:
    """Stage-4 STRING hits, minus TFs whose hit pattern looks like a
    whole-complex database artifact rather than subunit-specific evidence."""
    df = pd.read_csv(config.RESULTS_DIR / "stage4_mediator_string_hits.csv")
    df = df[df["is_validation_control"] == False]  # noqa: E712
    subunit_counts = df.groupby("tf")["mediator_subunit"].nunique()
    artifact_tfs = subunit_counts[subunit_counts >= STRING_ARTIFACT_SUBUNIT_COUNT].index.tolist()
    if artifact_tfs:
        print(f"[ranking] rejecting as whole-complex STRING artifacts (>= {STRING_ARTIFACT_SUBUNIT_COUNT} "
              f"of 33 subunits hit near-uniformly): {artifact_tfs}")
    return df[~df["tf"].isin(artifact_tfs)]


def rank_stage1_candidates(qvalue_threshold: float = 0.05) -> list[RankedCandidate]:
    universe = load_universe_percentile_ranks()
    sig = universe[(universe["dependency_flag"] == True) & (universe["qvalue"] < qvalue_threshold)]  # noqa: E712
    string_leads = load_string_leads()

    candidates = []
    for _, row in sig.iterrows():
        dep = build_dependency_evidence(row)
        gate = compute_gate(dep)
        discovery_score = compute_discovery_score(row, gate)

        tf_leads = string_leads[string_leads["tf"] == row["tf"]]
        if tf_leads.empty:
            # No STRING lead at all -- still emit one row so the candidate
            # stays queryable, with a default MediatorLink (no claims,
            # involvement=UNKNOWN), per "keep everything" instead of dropping.
            subunit_rows = [None]
        else:
            subunit_rows = [r for _, r in tf_leads.iterrows()]

        for lead in subunit_rows:
            partner_gene = lead["mediator_subunit"] if lead is not None else "MED23"
            mediator = MediatorLink(partner_gene=partner_gene, interacting_region_mapped=False, tf_region=None, claims=[])
            enrichment = compute_enrichment(mediator, tf=row["tf"])
            completeness = compute_evidence_completeness(enrichment)
            enrichment_score = compute_enrichment_score(enrichment)
            final_score = compute_final_score(discovery_score, enrichment_score, completeness)

            candidates.append(
                RankedCandidate(
                    dependency=dep,
                    enrichment=enrichment,
                    gate=gate,
                    discovery_score=discovery_score,
                    enrichment_score=enrichment_score,
                    evidence_completeness=completeness,
                    final_score=final_score,
                    mediator=mediator,
                )
            )
    return candidates


def load_validation_controls() -> list[RankedCandidate]:
    """ELK1/RUNX2/CEBPB/ETV1 -- already-typed MediatorLink examples from the
    main repo's round-01 literature triage (examples/mediator_link_*.json).
    These have NO DepMap dependency signal (see kevin.md), so there is no
    real DependencyEvidence to attach; used here purely to validate the
    enrichment-scoring math (SS3/SS4) end-to-end against real, already-vetted
    Claim data, not to rank them against the Stage-1 candidates above."""
    results = []
    for name in ["elk1", "runx2", "cebpb", "etv1"]:
        path = EXAMPLES_DIR / f"mediator_link_{name}_med23.json"
        data = json.loads(path.read_text())
        mediator = MediatorLink(**data)
        enrichment = compute_enrichment(mediator)
        completeness = compute_evidence_completeness(enrichment)
        enrichment_score = compute_enrichment_score(enrichment)
        results.append(
            {
                "gene": name.upper(),
                "involvement": mediator.involvement.value,
                "ready_for_structural_modeling": mediator.ready_for_structural_modeling,
                "literature_support": enrichment.literature_support,
                "interface_support": enrichment.interface_support,
                "evidence_completeness": completeness,
                "enrichment_score": enrichment_score,
            }
        )
    return results


def run(qvalue_threshold: float = 0.05):
    candidates = rank_stage1_candidates(qvalue_threshold)
    candidates.sort(key=lambda c: c.final_score, reverse=True)

    out_path = config.RESULTS_DIR / "ranked_candidates.json"
    out_path.write_text(json.dumps([c.model_dump(mode="json") for c in candidates], indent=2))
    print(f"[ranking] wrote {len(candidates)} RankedCandidate rows -> {out_path}")

    print("\n[ranking] top rows by final_score:")
    for c in candidates[:15]:
        print(
            f"  {c.dependency.gene:8s} {c.dependency.disease_context:35s} "
            f"partner={c.mediator.partner_gene:8s} discovery={c.discovery_score:.3f} "
            f"enrichment={c.enrichment_score} completeness={c.evidence_completeness:.2f} "
            f"final={c.final_score:.3f}"
        )

    validation = load_validation_controls()
    val_path = config.RESULTS_DIR / "ranking_validation_controls.json"
    val_path.write_text(json.dumps(validation, indent=2))
    print(f"\n[ranking] validation controls (enrichment math only, no DepMap signal) -> {val_path}")
    for v in validation:
        print(f"  {v['gene']:8s} involvement={v['involvement']:10s} lit={v['literature_support']} "
              f"iface={v['interface_support']} enrichment_score={v['enrichment_score']}")

    return candidates, validation


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rank candidates per docs/SCORING_SPEC.md")
    parser.add_argument("--qvalue-threshold", type=float, default=0.05)
    args = parser.parse_args()
    run(qvalue_threshold=args.qvalue_threshold)
