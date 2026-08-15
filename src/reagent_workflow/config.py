"""Run configuration: gate thresholds and scoring weights.

These are frozen for the duration of a run and hashed into ``config_hash``, so
every trace event and scorecard says which rules produced it. The agent's
self-improvement step may not touch this file — scoring rules are outside what
it is allowed to revise.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .store import content_hash

# Gene-effect convention (DepMap-style): more negative means stronger dependency.
# A model is "dependent" below DEPENDENT_EFFECT_CUTOFF.
DEPENDENT_EFFECT_CUTOFF = -0.5


class GateThresholds(BaseModel):
    """Minimum evidence a candidate needs before it can be scored at all."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_median_target_effect: float = -0.5
    """Median effect in the disease context must be at or below this."""

    min_target_models: int = 5
    """Fewer models than this is inadequate sample support."""

    max_other_dependent_fraction: float = 0.5
    """Above this the gene is broadly essential, not a selective dependency."""

    max_selectivity_delta: float = -0.3
    """target minus other median effect; must be at least this negative."""

    min_mediator_support: float = 0.3
    """Below this the target-partner interaction is treated as unsupported.

    The field name is a schema-1.0 spelling. Renaming it would change every
    run's ``config_hash``, so it is left alone deliberately; the rule it encodes
    is the general interaction-support floor."""

    require_mapped_interacting_region: bool = True
    """PROJECT.md names a whole-protein pull-down with no mapped interacting
    region as a negative control the agent must reject out loud. Correlation
    passing itself off as contact is the failure this gate exists to catch."""


class ScoringWeights(BaseModel):
    """Component weights. Must sum to 1.0 so the total stays in [0, 1]."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dependency_strength: float = Field(default=0.25, ge=0, le=1)
    dependency_prevalence: float = Field(default=0.15, ge=0, le=1)
    disease_specificity: float = Field(default=0.25, ge=0, le=1)
    normal_cell_completeness: float = Field(default=0.10, ge=0, le=1)
    mediator_evidence_quality: float = Field(default=0.15, ge=0, le=1)
    """Weight of the general target-partner interaction evidence component.

    Schema-1.0 spelling, kept for the same ``config_hash`` reason as
    ``GateThresholds.min_mediator_support``."""
    structural_tractability: float = Field(default=0.10, ge=0, le=1)

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> ScoringWeights:
        total = sum(self.model_dump().values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"scoring weights must sum to 1.0, got {total}")
        return self


class RunConfig(BaseModel):
    """The frozen decision rules for one run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    config_version: str = "1.0"
    gates: GateThresholds = Field(default_factory=GateThresholds)
    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    dependent_effect_cutoff: float = DEPENDENT_EFFECT_CUTOFF
    require_human_approval_before_structure: bool = True
    allow_live_modal: bool = False
    """Live Modal dispatch stays off unless a human turns it on explicitly."""

    max_improvement_iterations: int = Field(default=2, ge=0, le=2)

    enable_alphafold2: bool = True
    """Second independent interface predictor alongside Boltz2.

    Both take the whole complex and pair heterocomplex MSAs, so both can vote on
    the interface. ESMFold2 stays monomer-only and never votes.
    """

    structure_replicates: int = Field(default=3, ge=1, le=10)
    """Replicate predictions per model, varying only the seed.

    One prediction is a point estimate from a stochastic process. Replicates
    give a spread, which is what makes "the models agree" checkable rather than
    an impression. Costs N GPU jobs per model, so it is configurable — and every
    replicate beyond the first only runs on the live path.
    """

    ci_confidence_level: float = Field(default=0.95, gt=0, lt=1)

    def hash(self) -> str:
        return content_hash(self.model_dump(mode="json"))
