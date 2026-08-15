"""Candidate gates.

A gate failure is a recorded fact, not a filtered-out row: every rejection is
written to ``decisions/rejections.jsonl`` with the reason and the threshold that
produced it, so a reader can disagree with the rule rather than guess at it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import GateThresholds
from .models import CandidateHypothesis


@dataclass(frozen=True)
class GateFailure:
    gate: str
    reason: str


@dataclass(frozen=True)
class GateOutcome:
    candidate_id: str
    eligible: bool
    failures: list[GateFailure]
    passed_gates: list[str]

    @property
    def reasons(self) -> list[str]:
        return [failure.reason for failure in self.failures]

    @property
    def failed_gates(self) -> list[str]:
        return [failure.gate for failure in self.failures]


GATE_NAMES = (
    "dependency_strength",
    "sample_support",
    "broad_essentiality",
    "disease_specificity",
    "mediator_support",
    "provenance",
)


def evaluate_gates(
    candidate: CandidateHypothesis, thresholds: GateThresholds
) -> GateOutcome:
    """Apply every gate. All failures are collected, not just the first."""
    dependency = candidate.dependency
    mediator = candidate.mediator
    failures: list[GateFailure] = []

    if dependency.median_target_effect > thresholds.max_median_target_effect:
        failures.append(GateFailure(
            "dependency_strength",
            f"weak dependency: median effect {dependency.median_target_effect:.3f} in "
            f"{dependency.disease_context} is above the required "
            f"{thresholds.max_median_target_effect:.3f}",
        ))

    if dependency.n_target_models < thresholds.min_target_models:
        failures.append(GateFailure(
            "sample_support",
            f"inadequate sample support: {dependency.n_target_models} models in context, "
            f"minimum is {thresholds.min_target_models}",
        ))

    if dependency.other_dependent_fraction > thresholds.max_other_dependent_fraction:
        failures.append(GateFailure(
            "broad_essentiality",
            f"dependency is too broad: {dependency.other_dependent_fraction:.0%} of other "
            f"models are also dependent, maximum is "
            f"{thresholds.max_other_dependent_fraction:.0%}",
        ))

    if dependency.selectivity_delta > thresholds.max_selectivity_delta:
        failures.append(GateFailure(
            "disease_specificity",
            f"weak disease specificity: selectivity delta {dependency.selectivity_delta:.3f}, "
            f"required at or below {thresholds.max_selectivity_delta:.3f}",
        ))

    if not mediator.is_supported:
        missing = []
        if mediator.interaction_support is None:
            missing.append("no interaction support value")
        if mediator.assay is None:
            missing.append("no named assay")
        if mediator.source_id is None:
            missing.append("no source")
        if not mediator.evidence_ids:
            missing.append("no evidence records")
        failures.append(GateFailure(
            "mediator_support",
            f"unsupported Mediator interaction "
            f"({mediator.transcription_factor}-{mediator.mediator_subunit}): "
            + "; ".join(missing),
        ))
    elif (mediator.interaction_support or 0.0) < thresholds.min_mediator_support:
        failures.append(GateFailure(
            "mediator_support",
            f"unsupported Mediator interaction: support "
            f"{mediator.interaction_support:.2f} is below the required "
            f"{thresholds.min_mediator_support:.2f}",
        ))

    provenance_problems = []
    if not dependency.source_id:
        provenance_problems.append("dependency evidence has no source")
    if not dependency.evidence_ids:
        provenance_problems.append("dependency evidence has no evidence records")
    if not candidate.evidence_ids:
        provenance_problems.append("candidate cites no evidence")
    if provenance_problems:
        failures.append(GateFailure(
            "provenance", "missing provenance: " + "; ".join(provenance_problems)
        ))

    failed = {failure.gate for failure in failures}
    return GateOutcome(
        candidate_id=candidate.candidate_id,
        eligible=not failures,
        failures=failures,
        passed_gates=[name for name in GATE_NAMES if name not in failed],
    )
