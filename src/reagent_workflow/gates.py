"""Candidate gates.

A gate failure is a recorded fact, not a filtered-out row: every rejection is
written to ``decisions/rejections.jsonl`` with the reason and the threshold that
produced it, so a reader can disagree with the rule rather than guess at it.

The gates are target-agnostic. They speak about a target gene and its
interaction partner; a TF-Mediator pair is one instance of that, not the
definition. Where a candidate declares ``target_class`` / ``partner_class``
(free text such as "transcription factor" / "Mediator subunit") the failure
reason uses those words, so a TF-Mediator run still reads naturally without the
rule assuming that pairing.
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
    "interaction_support",
    "interface_region_mapped",
    "provenance",
)


def _class_pair(candidate: CandidateHypothesis) -> str:
    """``"<target class>-<partner class>"``, or ``""`` when either is absent.

    The classes are optional free text. The gates never require them; they only
    borrow them so the reason reads in the candidate's own vocabulary.
    """
    if candidate.target_class and candidate.partner_class:
        return f"{candidate.target_class}-{candidate.partner_class}"
    return ""


def _interaction_noun(candidate: CandidateHypothesis) -> str:
    """"transcription factor-Mediator subunit interaction", else "interaction"."""
    pair = _class_pair(candidate)
    return f"{pair} interaction" if pair else "interaction"


def _contact_noun(candidate: CandidateHypothesis) -> str:
    """Same idea for the contact point; falls back to "target-partner contact"."""
    return f"{_class_pair(candidate) or 'target-partner'} contact"


def evaluate_gates(
    candidate: CandidateHypothesis, thresholds: GateThresholds
) -> GateOutcome:
    """Apply every gate. All failures are collected, not just the first."""
    dependency = candidate.dependency
    # ``candidate.mediator`` is the attribute name; what it holds is general
    # interaction evidence between the target gene and its partner.
    interaction = candidate.mediator
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

    # TODO: the threshold field is still named ``min_mediator_support`` in
    # config.py; the rule it encodes is the general interaction-support floor.
    if not interaction.is_supported:
        missing = []
        if interaction.interaction_support is None:
            missing.append("no interaction support value")
        if interaction.assay is None:
            missing.append("no named assay")
        if interaction.source_id is None:
            missing.append("no source")
        if not interaction.evidence_ids:
            missing.append("no evidence records")
        failures.append(GateFailure(
            "interaction_support",
            f"unsupported {_interaction_noun(candidate)} "
            f"({interaction.target_gene}-{interaction.partner_gene}): "
            + "; ".join(missing),
        ))
    elif (interaction.interaction_support or 0.0) < thresholds.min_mediator_support:
        failures.append(GateFailure(
            "interaction_support",
            f"unsupported {_interaction_noun(candidate)}: support "
            f"{interaction.interaction_support:.2f} is below the required "
            f"{thresholds.min_mediator_support:.2f}",
        ))

    # The negative control PROJECT.md names explicitly: association without a
    # mapped contact point. There is nothing to model or screen against, so this
    # is rejected out loud rather than passed downstream.
    if thresholds.require_mapped_interacting_region and not interaction.interacting_region_mapped:
        assay = interaction.assay or "no named assay"
        failures.append(GateFailure(
            "interface_region_mapped",
            f"{_contact_noun(candidate)} is not mapped "
            f"({interaction.target_gene}-{interaction.partner_gene}): "
            f"{assay} establishes association but identifies no interacting "
            "region, so there is no contact point to model or screen against",
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
