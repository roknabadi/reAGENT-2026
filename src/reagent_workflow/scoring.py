"""Candidate scoring.

Components stay separate and each carries its own definition, normalization,
weight, missingness, and source evidence. A missing component normalizes to 0.0
and is listed in ``missing_components``: absent evidence never helps a candidate,
and its weight is never redistributed to the components that happen to exist.
"""

from __future__ import annotations

from .config import RunConfig
from .models import CandidateHypothesis, CandidateScorecard, ScoreComponent
from .store import utc_now


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _linear(value: float, low: float, high: float) -> float:
    """Map ``low``..``high`` onto 0..1, clamped at both ends."""
    if high == low:
        return 0.0
    return _clamp((value - low) / (high - low))


def score_candidate(candidate: CandidateHypothesis, config: RunConfig) -> CandidateScorecard:
    dependency = candidate.dependency
    mediator = candidate.mediator
    tractability = candidate.tractability
    weights = config.weights
    components: list[ScoreComponent] = []

    # -- dependency strength -------------------------------------------------
    components.append(ScoreComponent(
        name="dependency_strength",
        definition=(
            "Median gene-effect score of the target gene across models in the disease "
            "context. More negative means a stronger dependency."
        ),
        raw=dependency.median_target_effect,
        unit=dependency.effect_unit,
        normalization="linear from -0.3 (none) to -1.5 (maximal), clamped to [0,1]",
        normalized=_linear(-dependency.median_target_effect, 0.3, 1.5),
        weight=weights.dependency_strength,
        uncertainty=(
            None if dependency.mann_whitney_p is not None
            else "no significance test reported for the context-vs-rest comparison"
        ),
        evidence_ids=list(dependency.evidence_ids),
    ))

    # -- dependency prevalence ----------------------------------------------
    components.append(ScoreComponent(
        name="dependency_prevalence",
        definition=(
            "Fraction of models in the disease context that are dependent on the gene "
            f"(gene effect below {config.dependent_effect_cutoff})."
        ),
        raw=dependency.target_dependent_fraction,
        unit="fraction",
        normalization="identity on [0,1]",
        normalized=_clamp(dependency.target_dependent_fraction),
        weight=weights.dependency_prevalence,
        uncertainty=(
            f"based on {dependency.n_target_models} models in context"
            if dependency.n_target_models < 15 else None
        ),
        evidence_ids=list(dependency.evidence_ids),
    ))

    # -- disease specificity -------------------------------------------------
    components.append(ScoreComponent(
        name="disease_specificity",
        definition=(
            "Difference between the in-context and out-of-context median gene effect "
            "(target minus other). More negative means more selective."
        ),
        raw=dependency.selectivity_delta,
        unit=dependency.effect_unit,
        normalization="linear from 0.0 (none) to -0.8 (highly selective), clamped to [0,1]",
        normalized=_linear(-dependency.selectivity_delta, 0.0, 0.8),
        weight=weights.disease_specificity,
        uncertainty=(
            f"{dependency.other_dependent_fraction:.0%} of out-of-context models are "
            "also dependent"
        ),
        evidence_ids=list(dependency.evidence_ids),
    ))

    # -- normal-cell evidence completeness -----------------------------------
    # This scores whether the safety-window evidence exists at all, not whether
    # it is favourable. No records means missing, which means zero.
    normal_ids = list(candidate.normal_cell_evidence_ids)
    has_normal = bool(normal_ids)
    components.append(ScoreComponent(
        name="normal_cell_completeness",
        definition=(
            "Presence of normal-tissue or normal-cell evidence bearing on a possible "
            "safety window. Measures completeness of the evidence, not its favourability."
        ),
        raw=float(len(normal_ids)) if has_normal else None,
        unit="records" if has_normal else None,
        normalization="0 records -> 0.0; 1 record -> 0.5; 2 or more -> 1.0",
        normalized=(0.0 if not has_normal else (0.5 if len(normal_ids) == 1 else 1.0)),
        weight=weights.normal_cell_completeness,
        missing=not has_normal,
        uncertainty=(
            "no normal-cell evidence supplied; the safety window is unassessed"
            if not has_normal else None
        ),
        evidence_ids=normal_ids,
    ))

    # -- Mediator evidence quality -------------------------------------------
    # Direct binding assays outrank inference; an unsupported link is missing.
    interaction_rank = {
        "direct_binding": 1.0, "complex_member": 0.7,
        "genetic": 0.5, "inferred": 0.25, None: 0.0,
    }
    mediator_supported = mediator.is_supported
    if mediator_supported:
        support = mediator.interaction_support or 0.0
        type_factor = interaction_rank.get(mediator.interaction_type, 0.0)
        mediator_value = _clamp(support * type_factor)
    else:
        mediator_value = 0.0
    components.append(ScoreComponent(
        name="mediator_evidence_quality",
        definition=(
            "Strength of the TF-Mediator interaction evidence, weighted by assay type "
            "(direct binding 1.0, complex membership 0.7, genetic 0.5, inferred 0.25). "
            "Requires a support value, a named assay, a source, and evidence records."
        ),
        raw=mediator.interaction_support if mediator_supported else None,
        unit="support_fraction" if mediator_supported else None,
        normalization="support value multiplied by the assay-type factor, clamped to [0,1]",
        normalized=mediator_value,
        weight=weights.mediator_evidence_quality,
        missing=not mediator_supported,
        uncertainty=(
            "; ".join(mediator.limitations) if mediator.limitations
            else (None if mediator_supported else "TF-Mediator link is unsupported")
        ),
        evidence_ids=list(mediator.evidence_ids),
    ))

    # -- structural tractability ---------------------------------------------
    # Scores what is known before any prediction runs. A predicted structure is
    # not counted here; that would let a prediction vouch for itself.
    tract_points = 0.0
    tract_notes: list[str] = []
    if tractability.experimental_structure_id:
        tract_points += 0.5
        tract_notes.append(f"experimental structure {tractability.experimental_structure_id}")
    if tractability.tf_sequence and tractability.mediator_sequence:
        tract_points += 0.3
        tract_notes.append("both sequences available")
    if tractability.domain_bounded:
        tract_points += 0.2
        tract_notes.append("interaction is domain-bounded")
    has_tractability = tract_points > 0.0
    components.append(ScoreComponent(
        name="structural_tractability",
        definition=(
            "Pre-existing structural handles: public experimental structure (0.5), "
            "both partner sequences available (0.3), interaction bounded to a defined "
            "domain (0.2). Predicted structures are excluded by design."
        ),
        raw=tract_points if has_tractability else None,
        unit="points" if has_tractability else None,
        normalization="sum of available handles, clamped to [0,1]",
        normalized=_clamp(tract_points),
        weight=weights.structural_tractability,
        missing=not has_tractability,
        uncertainty=(
            "; ".join(tract_notes) if tract_notes
            else "no structural handle: no public structure, sequences, or bounded domain"
        ),
        evidence_ids=[],
    ))

    total = sum(component.normalized * component.weight for component in components)
    missing = [component.name for component in components if component.missing]
    present_weight = sum(c.weight for c in components if not c.missing)

    uncertainty_notes = [
        f"{component.name}: {component.uncertainty}"
        for component in components if component.uncertainty
    ]
    if missing:
        uncertainty_notes.append(
            "missing components scored 0.0 and their weight was not redistributed: "
            + ", ".join(missing)
        )

    return CandidateScorecard(
        candidate_id=candidate.candidate_id,
        components=components,
        total_score=_clamp(total),
        evidence_completeness=_clamp(present_weight),
        missing_components=missing,
        uncertainty_notes=uncertainty_notes,
        scored_at=utc_now(),
    )


def rank(scorecards: list[CandidateScorecard]) -> list[CandidateScorecard]:
    """Highest total first; ties broken by evidence completeness, then id."""
    return sorted(
        scorecards,
        key=lambda card: (-card.total_score, -card.evidence_completeness, card.candidate_id),
    )
