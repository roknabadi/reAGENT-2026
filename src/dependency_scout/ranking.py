"""Hard scientific gates followed by an interpretable score."""
from .models import DependencyEvidence, EnrichmentEvidence, GateResult, RankedCandidate


def clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def gate(e: DependencyEvidence, *, min_models: int = 3) -> GateResult:
    failures = []
    if e.n_target_models < min_models: failures.append(f"fewer than {min_models} target-context models")
    if e.median_target_effect > -0.5: failures.append("weak median dependency (must be <= -0.5)")
    if e.target_dependent_fraction < 0.5: failures.append("dependency occurs in fewer than half of target models")
    if e.other_dependent_fraction > 0.35: failures.append("dependency is too broad outside the target context")
    if e.selectivity_delta < 0.35: failures.append("target-versus-rest selectivity delta is below 0.35")
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
