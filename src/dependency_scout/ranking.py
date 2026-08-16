"""Hard scientific gates followed by an interpretable score."""
from .models import DependencyEvidence, EnrichmentEvidence, GateResult, RankedCandidate


def clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


# Mirrors tests/re-agent_discovery/src/config.py -- keep both in sync by hand,
# there is no shared package boundary between the two repos yet.
MEDIAN_TARGET_EFFECT_MAX = -0.5
MEDIAN_OTHER_EFFECT_MIN = -0.2
SPECIFICITY_MIN_TARGET_FRACTION = 0.10
SPECIFICITY_MAX_OTHER_FRACTION = 0.05


def gate(e: DependencyEvidence, *, min_models: int = 5) -> GateResult:
    """A candidate passes on EITHER path (Kevin's specificity-first fix,
    ported from tests/re-agent_discovery/src/stage1_depmap.py::_dependency_flag,
    2026-08-16 -- replaces the old single median-only path that this function
    used to require unconditionally):
    - median: the whole group is dependent (target median <= -0.5 and the
      rest of the population median > -0.2). Rejects pan-essential genes on
      its own.
    - specificity-first: a meaningful fraction of the target group is
      dependent (>= 10%) and almost none of the rest is (<= 5%), regardless
      of the group median. Catches subpopulation-restricted dependencies
      that a pooled-subtype median dilutes away -- e.g. ASCL1/POU2F3 in
      SCLC, where the group is four mutually exclusive molecular subtypes
      and pooling them hides a real subtype-defining dependency.
    `min_models` defaults to 5, the statistical floor below which n=3-4 hits
    are noise (team/FINDINGS_DEPMAP_ROUND01.md #3). Vraj/Amir already fixed
    the fixture this broke (6c6dcc7) rather than lowering the floor back --
    the fixture now has a fifth Lung model instead."""
    failures = []
    if e.n_target_models < min_models:
        failures.append(f"fewer than {min_models} target-context models")

    median_path = (
        e.median_target_effect <= MEDIAN_TARGET_EFFECT_MAX
        and e.median_other_effect > MEDIAN_OTHER_EFFECT_MIN
    )
    specificity_path = (
        e.target_dependent_fraction >= SPECIFICITY_MIN_TARGET_FRACTION
        and e.other_dependent_fraction <= SPECIFICITY_MAX_OTHER_FRACTION
    )
    if not (median_path or specificity_path):
        # Granular reasons, not just "both paths failed" -- callers and tests
        # inspect individual phrases (e.g. "too broad") to tell a pan-essential
        # rejection apart from a merely-weak one.
        reasons = []
        if e.median_target_effect > MEDIAN_TARGET_EFFECT_MAX:
            reasons.append(f"weak median dependency (must be <= {MEDIAN_TARGET_EFFECT_MAX})")
        if e.median_other_effect <= MEDIAN_OTHER_EFFECT_MIN:
            reasons.append("dependency is too broad outside the target context "
                            f"(other median must be > {MEDIAN_OTHER_EFFECT_MIN})")
        if e.target_dependent_fraction < SPECIFICITY_MIN_TARGET_FRACTION:
            reasons.append("dependency occurs in too few target models to call it specific "
                            f"(must be >= {SPECIFICITY_MIN_TARGET_FRACTION:.0%})")
        if e.other_dependent_fraction > SPECIFICITY_MAX_OTHER_FRACTION:
            reasons.append("dependency is too broad outside the target context "
                            f"(must be <= {SPECIFICITY_MAX_OTHER_FRACTION:.0%} of other models)")
        failures.append("fails both the median-dependency path and the specificity-first "
                         "path: " + "; ".join(reasons))
    return GateResult(eligible=not failures, failures=failures)


def rank(e: DependencyEvidence, enrichment: EnrichmentEvidence | None = None) -> RankedCandidate:
    enrichment = enrichment or EnrichmentEvidence()
    discovery = (0.30 * clamp((-e.median_target_effect - 0.5) / 1.0) +
        0.30 * clamp(e.selectivity_delta / 1.25) + 0.25 * e.target_dependent_fraction +
        0.15 * (1.0 - e.other_dependent_fraction))
    optional = [enrichment.literature_support, enrichment.normal_cell_support,
        enrichment.interface_support, enrichment.tractability_support]
    observed = [v for v in optional if v is not None]
    completeness = len(observed) / len(optional)
    enrichment_score = sum(observed) / len(observed) if observed else None
    final = clamp(discovery * 0.80 + (enrichment_score or 0.0) * 0.20 * completeness)
    result_gate = gate(e)
    if not result_gate.eligible: final = 0.0
    return RankedCandidate(dependency=e, enrichment=enrichment, gate=result_gate,
        discovery_score=discovery, enrichment_score=enrichment_score,
        evidence_completeness=completeness, final_score=final)


def rank_all(records: list[DependencyEvidence]) -> list[RankedCandidate]:
    return sorted((rank(r) for r in records), key=lambda x: x.final_score, reverse=True)
