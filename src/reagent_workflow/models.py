"""Typed contracts for the target-discovery agent workflow.

Every stage boundary is one of these models. No undocumented dictionaries move
between stages: the filesystem is the source of truth, and these are the shapes
written to and read back from it.

The vocabulary here is deliberately target-agnostic: a run reasons about a
*target* gene and a *partner* gene, and says what kind of thing each one is in
free text (`target_class` / `partner_class`) rather than in the type system. A
transcription factor paired with a Mediator subunit is one test case for that
shape, not its definition; a kinase and its substrate use the same models.

Schema 1.1 renamed the TF/Mediator-specific field names. Every old name is
still accepted on input (via `AliasChoices`) and still readable as a property,
so bundles written against schema 1.0 load unchanged.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = "1.1"


class Stage(StrEnum):
    INGEST = "INGEST"
    GATE = "GATE"
    SCORE = "SCORE"
    HERO_CHECKPOINT = "HERO_CHECKPOINT"
    STRUCTURE = "STRUCTURE"
    NEXT_EXPERIMENT = "NEXT_EXPERIMENT"
    COMPLETE = "COMPLETE"


STAGE_ORDER: tuple[Stage, ...] = (
    Stage.INGEST,
    Stage.GATE,
    Stage.SCORE,
    Stage.HERO_CHECKPOINT,
    Stage.STRUCTURE,
    Stage.NEXT_EXPERIMENT,
    Stage.COMPLETE,
)


class EvidenceTier(StrEnum):
    """Where a record came from. `SYNTHETIC` is a test fixture, never evidence."""

    SYNTHETIC = "synthetic"
    PUBLIC_PRIMARY = "public_primary"
    PUBLIC_DERIVED = "public_derived"


class Interpretation(StrEnum):
    """What kind of claim a value is. Kept separate so they never blur."""

    OBSERVED = "observed"
    COMPUTED = "computed"
    PREDICTED = "predicted"
    INFERENCE = "inference"


class EvidenceType(StrEnum):
    DEPENDENCY = "dependency"
    EXPRESSION = "expression"
    NORMAL_TISSUE = "normal_tissue"
    INTERACTION = "interaction"
    STRUCTURE = "structure"
    LITERATURE = "literature"


class Base(BaseModel):
    """Strict base: unknown keys are an error, not a silent pass-through."""

    model_config = ConfigDict(extra="forbid")


class RenamedBase(Base):
    """Base for the models whose TF/Mediator field names were generalised.

    `populate_by_name=True` means the new canonical field name constructs the
    model just as well as the old key does, so `AliasChoices` widens what is
    accepted without ever narrowing it. `extra="forbid"` is restated rather
    than inherited so the strictness is visible at the point it applies.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SourceRecord(Base):
    """A public source, versioned enough to be re-fetched and checked."""

    schema_version: str = SCHEMA_VERSION
    source_id: str
    name: str
    url: str
    tier: EvidenceTier
    version: str | None = None
    retrieved_at: str | None = None
    license: str | None = None
    sha256: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def check_public_url_and_version(self) -> SourceRecord:
        if self.tier is EvidenceTier.SYNTHETIC:
            # Fixtures carry no public URL and must not pretend to.
            if not self.url.startswith("synthetic://"):
                raise ValueError("synthetic sources must use a synthetic:// url")
            return self
        if not self.url.startswith("https://"):
            raise ValueError(f"public source needs an https:// url, got {self.url!r}")
        if not (self.version or self.retrieved_at):
            raise ValueError("public source needs a version or a retrieval date")
        return self


class EvidenceRecord(Base):
    """One claim, with its provenance, units, and stated limitations."""

    schema_version: str = SCHEMA_VERSION
    evidence_id: str
    evidence_type: EvidenceType
    interpretation: Interpretation
    claim: str
    subject: str
    value: float | str | None = None
    unit: str | None = None
    supports: bool = True
    source_id: str
    limitations: list[str] = Field(default_factory=list)
    contradicts: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def numeric_values_need_units(self) -> EvidenceRecord:
        if isinstance(self.value, float) and not self.unit:
            raise ValueError(f"{self.evidence_id}: numeric value requires a unit")
        return self


class DependencyEvidence(Base):
    """Selective-dependency measurement for one gene in one disease context."""

    schema_version: str = SCHEMA_VERSION
    gene: str
    disease_context: str
    n_target_models: int = Field(ge=0)
    n_other_models: int = Field(ge=0)
    median_target_effect: float
    median_other_effect: float
    target_dependent_fraction: float = Field(ge=0, le=1)
    other_dependent_fraction: float = Field(ge=0, le=1)
    selectivity_delta: float
    effect_unit: str = "gene_effect_score"
    mann_whitney_p: float | None = Field(default=None, ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)
    source_id: str


class InteractionEvidence(RenamedBase):
    """Support for a physical relationship between a target and a partner protein.

    A transcription factor and a Mediator subunit are one such pair; so are a
    kinase and its substrate. The models say nothing about which, and the
    optional `target_class` / `partner_class` on `CandidateHypothesis` are where
    a run records what kind of pair it is looking at.

    `interaction_support` is deliberately nullable: an absent value means the
    connection is unsupported, and it is never read as a zero that averages away.

    `interacting_region_mapped` is the distinction the project turns on. A
    whole-protein pull-down or a co-expression correlation says two proteins are
    associated; it does not say where they touch, and there is nothing to model
    or screen against. It mirrors `MediatorLink` in
    `src/dependency_scout/models.py` so both halves of the repo mean the same
    thing by "direct".

    Formerly `MediatorEvidence`, which remains as a module-level alias.
    """

    schema_version: str = SCHEMA_VERSION
    target_gene: str = Field(
        validation_alias=AliasChoices("target_gene", "transcription_factor")
    )
    partner_gene: str = Field(
        validation_alias=AliasChoices("partner_gene", "mediator_subunit")
    )
    interaction_support: float | None = Field(default=None, ge=0, le=1)
    interaction_type: Literal["direct_binding", "complex_member", "genetic", "inferred"] | None = None
    assay: str | None = None
    interpretation: Interpretation | None = None
    interacting_region_mapped: bool = False
    target_region: str | None = Field(
        default=None, validation_alias=AliasChoices("target_region", "tf_region")
    )
    """Named region on the target, e.g. "activation domain, residues 374-384"."""
    evidence_ids: list[str] = Field(default_factory=list)
    source_id: str | None = None
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def mapped_region_needs_direct_evidence(self) -> InteractionEvidence:
        if self.interacting_region_mapped:
            if not self.target_region:
                raise ValueError(
                    "interacting_region_mapped requires target_region to name the region"
                )
            if self.interaction_type != "direct_binding":
                raise ValueError(
                    "a mapped interacting region requires direct binding evidence, "
                    f"not {self.interaction_type!r}"
                )
        return self

    @property
    def is_supported(self) -> bool:
        """True only with a real support value, a named assay, and provenance."""
        return (
            self.interaction_support is not None
            and self.assay is not None
            and self.source_id is not None
            and bool(self.evidence_ids)
        )

    @property
    def ready_for_structural_modeling(self) -> bool:
        """Only a supported contact with a mapped region is worth modelling."""
        return self.is_supported and self.interacting_region_mapped

    # Read-only views under the schema-1.0 names. A field and a property cannot
    # share a name, which is why the fields above were renamed rather than
    # duplicated; writes must go to the canonical field.

    @property
    def transcription_factor(self) -> str:
        """Deprecated view of `target_gene`."""
        return self.target_gene

    @property
    def mediator_subunit(self) -> str:
        """Deprecated view of `partner_gene`."""
        return self.partner_gene

    @property
    def tf_region(self) -> str | None:
        """Deprecated view of `target_region`."""
        return self.target_region


#: Schema-1.0 name. Kept so existing imports and isinstance checks keep working.
MediatorEvidence = InteractionEvidence


class StructuralTractability(RenamedBase):
    """What is known about modelling this interface, before any prediction runs."""

    schema_version: str = SCHEMA_VERSION
    experimental_structure_id: str | None = None
    target_uniprot: str | None = Field(
        default=None, validation_alias=AliasChoices("target_uniprot", "tf_uniprot")
    )
    partner_uniprot: str | None = Field(
        default=None, validation_alias=AliasChoices("partner_uniprot", "mediator_uniprot")
    )
    target_sequence: str | None = Field(
        default=None, validation_alias=AliasChoices("target_sequence", "tf_sequence")
    )
    partner_sequence: str | None = Field(
        default=None, validation_alias=AliasChoices("partner_sequence", "mediator_sequence")
    )
    domain_bounded: bool | None = None
    notes: list[str] = Field(default_factory=list)

    # Read-only views under the schema-1.0 names.

    @property
    def tf_uniprot(self) -> str | None:
        """Deprecated view of `target_uniprot`."""
        return self.target_uniprot

    @property
    def mediator_uniprot(self) -> str | None:
        """Deprecated view of `partner_uniprot`."""
        return self.partner_uniprot

    @property
    def tf_sequence(self) -> str | None:
        """Deprecated view of `target_sequence`."""
        return self.target_sequence

    @property
    def mediator_sequence(self) -> str | None:
        """Deprecated view of `partner_sequence`."""
        return self.partner_sequence


class CandidateHypothesis(RenamedBase):
    """A disease-target-partner triple with everything known about it.

    `target_class` and `partner_class` are free text ("transcription factor",
    "Mediator subunit", "kinase") so a run can say what kind of pair it is
    working on without the code having to know. Nothing branches on them; they
    are there so a report can be read by a biologist.
    """

    schema_version: str = SCHEMA_VERSION
    candidate_id: str
    disease_context: str
    target_gene: str = Field(
        validation_alias=AliasChoices("target_gene", "transcription_factor")
    )
    partner_gene: str = Field(
        validation_alias=AliasChoices("partner_gene", "mediator_subunit")
    )
    target_class: str | None = None
    """What kind of target this is, e.g. "transcription factor". Free text."""
    partner_class: str | None = None
    """What kind of partner this is, e.g. "Mediator subunit". Free text."""
    hypothesis: str
    dependency: DependencyEvidence
    mediator: InteractionEvidence
    """The target-partner interaction evidence. Field name kept from schema 1.0."""
    tractability: StructuralTractability = Field(default_factory=StructuralTractability)
    normal_cell_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)

    # Read-only views under the schema-1.0 names.

    @property
    def transcription_factor(self) -> str:
        """Deprecated view of `target_gene`."""
        return self.target_gene

    @property
    def mediator_subunit(self) -> str:
        """Deprecated view of `partner_gene`."""
        return self.partner_gene


class ScoreComponent(Base):
    """One scored dimension, carrying its own definition and missingness.

    A missing component records `raw=None` and `missing=True`; the normalized
    value is 0.0 and its weight is reported but never redistributed to make the
    candidate look better.
    """

    schema_version: str = SCHEMA_VERSION
    name: str
    definition: str
    raw: float | None = None
    unit: str | None = None
    normalization: str
    normalized: float = Field(ge=0, le=1)
    weight: float = Field(ge=0, le=1)
    missing: bool = False
    uncertainty: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def missing_never_scores(self) -> ScoreComponent:
        if self.missing and self.normalized != 0.0:
            raise ValueError(f"{self.name}: missing evidence must normalize to 0.0")
        if self.raw is None and not self.missing:
            raise ValueError(f"{self.name}: absent raw value must be marked missing")
        return self


class CandidateScorecard(Base):
    """Weighted score, kept decomposed so any number can be traced back."""

    schema_version: str = SCHEMA_VERSION
    candidate_id: str
    components: list[ScoreComponent]
    total_score: float = Field(ge=0, le=1)
    evidence_completeness: float = Field(ge=0, le=1)
    missing_components: list[str] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)
    scored_at: str


class RejectedCandidate(Base):
    """A candidate that did not pass a gate, and exactly why."""

    schema_version: str = SCHEMA_VERSION
    candidate_id: str
    stage: Stage
    reasons: list[str] = Field(min_length=1)
    failed_gates: list[str] = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    rejected_at: str


class HumanCheckpoint(Base):
    """A blocking human decision, persisted so a run can stop and resume."""

    schema_version: str = SCHEMA_VERSION
    checkpoint_id: str
    stage: Stage
    requested_decision: str
    recommendation: str | None = None
    recommended_candidate_id: str | None = None
    alternatives: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    rejected_candidate_ids: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)
    structural_readiness: str | None = None
    status: Literal["open", "approved", "rejected", "revised"] = "open"
    resolution: str | None = None
    resolved_by: str | None = None
    resolved_at: str | None = None
    revision_request: str | None = None
    created_at: str

    @model_validator(mode="after")
    def resolution_needs_an_author(self) -> HumanCheckpoint:
        if self.status != "open" and not self.resolved_by:
            raise ValueError("a resolved checkpoint must name who resolved it")
        return self


#: Schema-1.0 chain roles, mapped onto their target-agnostic replacements.
LEGACY_CHAIN_ROLES: dict[str, str] = {
    "transcription_factor": "target",
    "mediator_subunit": "partner",
}


class ChainSpec(Base):
    """One chain handed to a structure predictor.

    The role says which side of the pair a chain is, not what kind of protein
    it is; the biology lives in `CandidateHypothesis.target_class`.
    """

    chain_id: str
    role: Literal["target", "partner"]
    sequence: str
    uniprot: str | None = None
    region: str | None = None

    @field_validator("role", mode="before")
    @classmethod
    def accept_legacy_roles(cls, value: Any) -> Any:
        """Translate schema-1.0 role names so stored requests still load."""
        if isinstance(value, str):
            return LEGACY_CHAIN_ROLES.get(value, value)
        return value

    @model_validator(mode="after")
    def sequence_is_plausible_protein(self) -> ChainSpec:
        allowed = set("ACDEFGHIKLMNPQRSTVWY")
        bad = sorted(set(self.sequence.upper()) - allowed)
        if not self.sequence:
            raise ValueError(f"chain {self.chain_id}: empty sequence")
        if bad:
            raise ValueError(f"chain {self.chain_id}: non-amino-acid characters {bad}")
        return self


class StructuralModelRequest(Base):
    """A structural job, fully specified before anything is dispatched."""

    schema_version: str = SCHEMA_VERSION
    request_id: str
    candidate_id: str
    model: Literal["boltz2", "esmfold2"]
    proto_tool_key: Literal["boltz2-prediction", "esmfold2-prediction"]
    purpose: Literal["complex_interface", "monomer_confidence"]
    chains: list[ChainSpec] = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)
    seed: int | None = None
    device: Literal["modal", "cpu", "cuda", "proto"] = "modal"
    input_hash: str
    human_approval_checkpoint_id: str | None = None

    @model_validator(mode="after")
    def roles_match_the_model(self) -> StructuralModelRequest:
        """ESMFold2 checks monomers here; it is never an interface predictor."""
        if self.model == "esmfold2":
            if self.purpose != "monomer_confidence":
                raise ValueError("ESMFold2 may not be used as an interface predictor")
            if len(self.chains) != 1:
                raise ValueError("ESMFold2 monomer checks take exactly one chain")
            if self.proto_tool_key != "esmfold2-prediction":
                raise ValueError("esmfold2 requires the esmfold2-prediction tool key")
        if self.model == "boltz2":
            if self.purpose != "complex_interface":
                raise ValueError("Boltz2 is used here for complex interface prediction")
            if len(self.chains) < 2:
                raise ValueError("a complex prediction needs at least two chains")
            if self.proto_tool_key != "boltz2-prediction":
                raise ValueError("boltz2 requires the boltz2-prediction tool key")
        return self


class StructuralModelResult(Base):
    """Outcome of a structural job. Failure is a recorded state, not an exception."""

    schema_version: str = SCHEMA_VERSION
    request_id: str
    candidate_id: str
    model: Literal["boltz2", "esmfold2"]
    model_version: str | None = None
    proto_tools_version: str | None = None
    status: Literal["cached", "completed", "failed", "validated_only", "skipped"]
    interpretation: Interpretation = Interpretation.PREDICTED
    source: Literal["live_modal", "cache", "fixture", "none"] = "none"
    confidence: dict[str, float] = Field(default_factory=dict)
    chain_map: dict[str, str] = Field(default_factory=dict)
    input_hash: str
    output_hash: str | None = None
    artifact_paths: list[str] = Field(default_factory=list)
    runtime_ms: int | None = None
    seed: int | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    unresolved_questions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    error: str | None = None

    @model_validator(mode="after")
    def failures_explain_themselves(self) -> StructuralModelResult:
        if self.status == "failed" and not self.error:
            raise ValueError("a failed structural result must record its error")
        if self.status in {"cached", "completed"} and not self.output_hash:
            raise ValueError("a finished structural result must record an output hash")
        return self


class ModelComparison(Base):
    """Boltz2 vs ESMFold2, with agreement explicitly not counted as validation."""

    schema_version: str = SCHEMA_VERSION
    candidate_id: str
    boltz2_request_id: str | None = None
    esmfold2_request_id: str | None = None
    agreements: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    confidence_delta: dict[str, float] = Field(default_factory=dict)
    interpretation: Interpretation = Interpretation.PREDICTED
    verdict: Literal["consistent", "inconsistent", "insufficient"] = "insufficient"
    caveat: str = (
        "Model agreement is not experimental validation. Both models are "
        "predictors and can be jointly wrong."
    )
    limitations: list[str] = Field(default_factory=list)


class ExperimentOutcome(Base):
    outcome: str
    interpretation_change: str


class NextExperiment(Base):
    """The falsifiable next step, including what each result would mean."""

    schema_version: str = SCHEMA_VERSION
    candidate_id: str
    scientific_question: str
    perturbation: str
    readout: str
    positive_controls: list[str] = Field(min_length=1)
    negative_controls: list[str] = Field(min_length=1)
    possible_outcomes: list[ExperimentOutcome] = Field(min_length=2)
    limitations: list[str] = Field(min_length=1)


class RunState(Base):
    """Everything needed to resume from disk with no conversation history."""

    schema_version: str = SCHEMA_VERSION
    run_id: str
    stage: Stage
    status: Literal["initialized", "running", "awaiting_human", "completed", "failed", "abstained"]
    created_at: str
    updated_at: str
    config_hash: str
    git_commit: str | None = None
    candidate_ids: list[str] = Field(default_factory=list)
    eligible_candidate_ids: list[str] = Field(default_factory=list)
    rejected_candidate_ids: list[str] = Field(default_factory=list)
    hero_candidate_id: str | None = None
    open_checkpoint_id: str | None = None
    completed_stages: list[Stage] = Field(default_factory=list)
    fixture_run: bool = False
    error: str | None = None
    notes: list[str] = Field(default_factory=list)


class TraceEvent(Base):
    """One append-only internal trace record.

    Large artifacts are referenced by path and hash. Prompts, structures,
    papers, and credentials never go in here.
    """

    schema_version: str = SCHEMA_VERSION
    timestamp: str
    run_id: str
    event_id: str
    parent_event_id: str | None = None
    stage: str
    event_type: str
    actor: str
    input_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    config_hash: str
    git_commit: str | None = None
    model: str | None = None
    token_usage: int | None = None
    token_estimate: int | None = None
    latency_ms: int | None = None
    compute: str | None = None
    status: str = "ok"
    error: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class ArtifactRef(Base):
    path: str
    sha256: str
    bytes: int


class RunManifest(Base):
    """Content hashes for every artifact a run produced."""

    schema_version: str = SCHEMA_VERSION
    run_id: str
    created_at: str
    updated_at: str
    git_commit: str | None = None
    config_hash: str
    soul_version: str
    schema_versions: dict[str, str] = Field(default_factory=dict)
    tool_versions: dict[str, str] = Field(default_factory=dict)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    fixture_run: bool = False


class TraceManifest(Base):
    """Metadata accompanying an exported BenchFlow trace."""

    schema_version: str = SCHEMA_VERSION
    run_id: str
    task_id: str
    agent: str
    model: str | None = None
    git_commit: str | None = None
    environment: dict[str, str] = Field(default_factory=dict)
    started_at: str | None = None
    ended_at: str | None = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    token_estimate: int = 0
    estimated_tokens_are_approximate: bool = True
    cost_usd: float | None = None
    tool_calls: dict[str, int] = Field(default_factory=dict)
    completion_status: str = "unknown"
    reward: float | None = None
    internal_trace_sha256: str | None = None
    benchflow_trace_sha256: str | None = None
    benchflow_version: str | None = None
    trace_format: str = "opentraces"
    record_count: int = 0


class ImprovementIteration(Base):
    """One bounded self-improvement pass over a single stage."""

    schema_version: str = SCHEMA_VERSION
    run_id: str
    stage: Stage
    iteration: int = Field(ge=1, le=2)
    critique: str
    deficiency: str
    proposed_revision: str
    revision_target: Literal[
        "prompt_fragment", "evidence_request", "tool_routing", "explanation", "failure_recovery"
    ]
    changed_artifact: str | None = None
    applied: bool = False
    before_score: float = Field(ge=0, le=1)
    after_score: float | None = Field(default=None, ge=0, le=1)
    improved: bool | None = None
    stopping_reason: str
    recorded_at: str


class FinalReport(Base):
    """The run's answer, with its rejections and limits attached."""

    schema_version: str = SCHEMA_VERSION
    run_id: str
    status: Literal["completed", "abstained", "failed"]
    hero_candidate_id: str | None = None
    hero_hypothesis: str | None = None
    disease_context: str | None = None
    transcription_factor: str | None = None
    mediator_subunit: str | None = None
    confidence: Literal["low", "medium", "high"] = "low"
    scorecard_summary: dict[str, float] = Field(default_factory=dict)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    rejected_candidates: list[str] = Field(default_factory=list)
    human_decisions: list[str] = Field(default_factory=list)
    structural_summary: str | None = None
    next_experiment_summary: str | None = None
    limitations: list[str] = Field(default_factory=list)
    fixture_run: bool = False
    generated_at: str
