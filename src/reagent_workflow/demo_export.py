"""`demo.json` — the single artifact the UI reads.

TASKS.md #2. One self-contained file per run: candidates, evidence, claims,
citations, gate failures, structure, poses. No live calls, no network, no
imports on the reading side. A plain HTML file opens it and renders.

Two rules make it safe to render without defensive code everywhere:

1. **Every key is always present.** Absent data is `null` or `[]`, never a
   missing key, so the UI never needs `if "x" in obj`. "No crash on missing
   fields" is the done-when for screen #9, and it is cheaper to guarantee here
   than to defend against in six screens.
2. **It is a projection, not an internal type.** Nothing here is read back into
   the pipeline. Denormalised on purpose: a candidate carries its own source
   URLs and claims so the UI never joins tables.

The vocabulary is target-agnostic: a candidate names a `target_gene` and a
`partner_gene`, not a transcription factor and a Mediator subunit. TF-Mediator
is the test case the pipeline is being exercised on, not what it is limited to.
Schema 1.1 renames those fields and keeps the old names as **deprecated
aliases**, populated with identical values so the UI can migrate on its own
schedule. The aliases are marked at each declaration and will be dropped once
the screens read the new names.

The `compounds` block is Vraj's slot (TASKS.md #5, #6). It is emitted with
`status` and an empty `poses` list so the shape exists before the docking run
does, and screen #13 can be built against it today.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import (
    CandidateScorecard,
    FinalReport,
    HumanCheckpoint,
    ModelComparison,
    NextExperiment,
    StructuralModelResult,
)
from .scoring import rank
from .store import RunStore, sha256_file, utc_now

DEMO_SCHEMA_VERSION = "1.1"

DOCKING_CAVEAT = (
    "A docking score is not a binding affinity. Poses are computational "
    "predictions and are not evidence of binding."
)
PREDICTION_CAVEAT = (
    "Predicted structures are computational results, not experimental "
    "observations. Model agreement is not validation."
)


# --------------------------------------------------------------------- schema
class DemoBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DemoSource(DemoBase):
    source_id: str
    name: str
    url: str
    tier: str
    version: str | None = None
    retrieved_at: str | None = None
    is_public: bool = False


class DemoClaim(DemoBase):
    """A statement with its support type and citations, for screen #10."""

    statement: str
    support: str
    citations: list[str] = Field(default_factory=list)
    note: str | None = None


class DemoEvidence(DemoBase):
    evidence_id: str
    claim: str
    evidence_type: str
    interpretation: str
    value: float | str | None = None
    unit: str | None = None
    supports: bool = True
    source_id: str | None = None
    source_url: str | None = None
    limitations: list[str] = Field(default_factory=list)
    contradicts: list[str] = Field(default_factory=list)


class DemoGateFailure(DemoBase):
    """Screen #11: which gate fired, and on what."""

    gate: str
    reason: str


class DemoScoreComponent(DemoBase):
    name: str
    definition: str
    raw: float | None = None
    unit: str | None = None
    normalized: float = 0.0
    weight: float = 0.0
    weighted: float = 0.0
    missing: bool = False
    uncertainty: str | None = None


class DemoSelectivity(DemoBase):
    median_target_effect: float | None = None
    median_other_effect: float | None = None
    selectivity_delta: float | None = None
    target_dependent_fraction: float | None = None
    other_dependent_fraction: float | None = None
    n_target_models: int | None = None
    n_other_models: int | None = None
    unit: str | None = None
    awaiting_dependency_data: bool = False


class DemoCandidate(DemoBase):
    """One row of screen #9, expanded by screens #10 and #11."""

    candidate_id: str
    rank: int | None = None
    status: str  # "hero" | "eligible" | "rejected"
    target_gene: str
    partner_gene: str
    target_class: str | None = None
    """Free text, e.g. "transcription factor". Absent when uncharacterised."""
    partner_class: str | None = None
    """Free text, e.g. "Mediator subunit". Absent when uncharacterised."""
    disease_context: str
    hypothesis: str | None = None

    score: float | None = None
    evidence_completeness: float | None = None
    score_components: list[DemoScoreComponent] = Field(default_factory=list)
    selectivity: DemoSelectivity = Field(default_factory=DemoSelectivity)

    involvement: str = "unknown"
    interacting_region_mapped: bool = False
    target_region: str | None = None
    interface_tractability: str = "unknown"
    screening_concerns: list[str] = Field(default_factory=list)
    calibration_only: bool = False

    gate_eligible: bool = False
    gate_failures: list[DemoGateFailure] = Field(default_factory=list)

    claims: list[DemoClaim] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)

    # -- deprecated aliases (schema 1.1) ------------------------------------
    # The UI reads these names today, so they stay in the payload carrying the
    # same values as the fields above. Never set them by hand: the validator
    # below mirrors them, so the pair cannot drift. Delete once the screens
    # read target_gene / partner_gene / target_region.
    transcription_factor: str = ""
    mediator_subunit: str = ""
    tf_region: str | None = None

    @model_validator(mode="after")
    def _mirror_deprecated_aliases(self) -> DemoCandidate:
        self.transcription_factor = self.target_gene
        self.mediator_subunit = self.partner_gene
        self.tf_region = self.target_region
        return self


class DemoStructureResult(DemoBase):
    request_id: str
    model: str
    purpose: str | None = None
    status: str
    source: str
    confidence: dict[str, float] = Field(default_factory=dict)
    chain_map: dict[str, str] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)


class DemoStructure(DemoBase):
    """Screen #12."""

    status: str = "none"
    candidate_id: str | None = None
    target_region: str | None = None
    interface_residues: dict[str, list[int]] = Field(default_factory=dict)
    results: list[DemoStructureResult] = Field(default_factory=list)
    comparison_verdict: str | None = None
    agreements: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    confidence_delta: dict[str, float] = Field(default_factory=dict)
    caveats: list[str] = Field(default_factory=lambda: [PREDICTION_CAVEAT])

    # Deprecated alias (schema 1.1) for `target_region`, kept for the UI. This
    # model is filled in by assignment rather than at construction, so
    # `set_target_region` writes both instead of a mirroring validator.
    tf_region: str | None = None

    def set_target_region(self, region: str | None) -> None:
        """Set the mapped region and its deprecated alias together."""
        self.target_region = region
        self.tf_region = region


class DemoPose(DemoBase):
    """Screen #13. Vraj's docking output writes these; nothing here creates them."""

    ligand_id: str
    smiles: str
    score: float | None = None
    score_unit: str = "vina_kcal_per_mol"
    rank: int | None = None
    kept: bool = True
    rationale: str | None = None
    artifact_path: str | None = None
    source_id: str | None = None


class DemoCompounds(DemoBase):
    status: str = "not_run"  # "not_run" | "blocked" | "screened"
    search_region_basis: str | None = None
    poses: list[DemoPose] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    caveat: str = DOCKING_CAVEAT


class DemoCheckpoint(DemoBase):
    checkpoint_id: str
    stage: str
    status: str
    requested_decision: str
    recommended_candidate_id: str | None = None
    resolved_by: str | None = None
    resolved_at: str | None = None
    resolution: str | None = None


class DemoChainLink(DemoBase):
    """One hop of screen #14: disease → target → contact → interface → compounds.

    `step` is one of "disease", "target", "partner_contact", "interface",
    "compounds" — five hops, always in that order. Schema 1.1 renamed the second
    and third steps (from "transcription_factor" and "mediator_contact"); unlike
    the field renames there is no alias, because a duplicated hop would render
    as a duplicated row.
    """

    step: str
    value: str
    status: str  # "established" | "predicted" | "missing" | "blocked"
    detail: str | None = None


class DemoSummary(DemoBase):
    disease_context: str | None = None
    target_gene: str | None = None
    partner_gene: str | None = None
    hypothesis: str | None = None
    confidence: str = "low"
    chain: list[DemoChainLink] = Field(default_factory=list)
    headline_limitation: str | None = None

    # Deprecated aliases (schema 1.1), mirrored below. See DemoCandidate.
    transcription_factor: str | None = None
    mediator_subunit: str | None = None

    @model_validator(mode="after")
    def _mirror_deprecated_aliases(self) -> DemoSummary:
        self.transcription_factor = self.target_gene
        self.mediator_subunit = self.partner_gene
        return self


class DemoOutcome(DemoBase):
    outcome: str
    interpretation_change: str


class DemoNextExperiment(DemoBase):
    scientific_question: str | None = None
    perturbation: str | None = None
    readout: str | None = None
    positive_controls: list[str] = Field(default_factory=list)
    negative_controls: list[str] = Field(default_factory=list)
    possible_outcomes: list[DemoOutcome] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class DemoRun(DemoBase):
    run_id: str
    status: str
    stage: str
    fixture_run: bool = False
    created_at: str | None = None
    git_commit: str | None = None
    config_hash: str | None = None
    soul_version: str | None = None
    internal_trace_path: str | None = None
    benchflow_trace_path: str | None = None
    benchflow_trace_sha256: str | None = None


class DemoPayload(DemoBase):
    """The frozen shape. Bump `schema_version` if a field changes meaning."""

    schema_version: str = DEMO_SCHEMA_VERSION
    generated_at: str
    run: DemoRun
    summary: DemoSummary
    candidates: list[DemoCandidate] = Field(default_factory=list)
    rejected: list[DemoCandidate] = Field(default_factory=list)
    evidence: list[DemoEvidence] = Field(default_factory=list)
    sources: list[DemoSource] = Field(default_factory=list)
    structure: DemoStructure = Field(default_factory=DemoStructure)
    compounds: DemoCompounds = Field(default_factory=DemoCompounds)
    checkpoints: list[DemoCheckpoint] = Field(default_factory=list)
    next_experiment: DemoNextExperiment = Field(default_factory=DemoNextExperiment)
    limitations: list[str] = Field(default_factory=list)


# ----------------------------------------------------------------- projection
def _involvement(mediator: Any) -> str:
    """Mirror `dependency_scout.Involvement`, derived rather than stored."""
    if not mediator.is_supported:
        return "unknown"
    if mediator.interacting_region_mapped:
        return "direct"
    if mediator.interaction_type in {"complex_member", "genetic"}:
        return "indirect"
    if mediator.interaction_type == "inferred":
        return "predicted"
    return "indirect"


def _screening_concerns(mediator: Any, tractability: Any) -> list[str]:
    concerns: list[str] = []
    if mediator.interacting_region_mapped and not tractability.domain_bounded:
        concerns.append("interface tractability not assessed")
    if not mediator.interacting_region_mapped:
        concerns.append(
            "no mapped interacting region: nothing to define a pocket against"
        )
    for limitation in mediator.limitations:
        concerns.append(limitation)
    return concerns


def _claims_for(candidate: Any, evidence: dict[str, Any], sources: dict[str, Any]) -> list[DemoClaim]:
    """Project evidence records into claim rows with citations, for screen #10."""
    support_by_interpretation = {
        "observed": "direct_experimental",
        "computed": "genetic_functional",
        "predicted": "computational_prediction",
        "inference": "inference",
    }
    claims: list[DemoClaim] = []
    for evidence_id in candidate.evidence_ids:
        record = evidence.get(evidence_id)
        if record is None:
            continue
        source = sources.get(record.source_id)
        claims.append(DemoClaim(
            statement=record.claim,
            support=support_by_interpretation.get(str(record.interpretation), "inference"),
            citations=[source.url] if source else [],
            note="; ".join(record.limitations) or None,
        ))
    return claims


def _candidate_row(
    candidate: Any,
    *,
    status: str,
    rank_index: int | None,
    scorecard: CandidateScorecard | None,
    gate_failures: list[DemoGateFailure],
    evidence: dict[str, Any],
    sources: dict[str, Any],
) -> DemoCandidate:
    dependency = candidate.dependency
    mediator = candidate.mediator
    return DemoCandidate(
        candidate_id=candidate.candidate_id,
        rank=rank_index,
        status=status,
        # The deprecated aliases are filled in by DemoCandidate's validator.
        target_gene=candidate.target_gene,
        partner_gene=candidate.partner_gene,
        target_class=candidate.target_class,
        partner_class=candidate.partner_class,
        disease_context=candidate.disease_context,
        hypothesis=candidate.hypothesis,
        score=scorecard.total_score if scorecard else None,
        evidence_completeness=scorecard.evidence_completeness if scorecard else None,
        score_components=[
            DemoScoreComponent(
                name=c.name, definition=c.definition, raw=c.raw, unit=c.unit,
                normalized=c.normalized, weight=c.weight,
                weighted=round(c.normalized * c.weight, 4),
                missing=c.missing, uncertainty=c.uncertainty,
            )
            for c in (scorecard.components if scorecard else [])
        ],
        selectivity=DemoSelectivity(
            median_target_effect=dependency.median_target_effect,
            median_other_effect=dependency.median_other_effect,
            selectivity_delta=dependency.selectivity_delta,
            target_dependent_fraction=dependency.target_dependent_fraction,
            other_dependent_fraction=dependency.other_dependent_fraction,
            n_target_models=dependency.n_target_models,
            n_other_models=dependency.n_other_models,
            unit=dependency.effect_unit,
            awaiting_dependency_data=False,
        ),
        involvement=_involvement(mediator),
        interacting_region_mapped=mediator.interacting_region_mapped,
        target_region=mediator.target_region,
        interface_tractability=(
            "folded_domain" if candidate.tractability.domain_bounded
            else "unknown"
        ),
        screening_concerns=_screening_concerns(mediator, candidate.tractability),
        calibration_only=False,
        gate_eligible=status != "rejected",
        gate_failures=gate_failures,
        claims=_claims_for(candidate, evidence, sources),
        evidence_ids=list(candidate.evidence_ids),
        contradicting_evidence_ids=list(candidate.contradicting_evidence_ids),
        uncertainty=list(scorecard.uncertainty_notes) if scorecard else [],
    )


def _build_chain(
    report: FinalReport | None,
    hero: Any | None,
    structure: DemoStructure,
    compounds: DemoCompounds,
) -> list[DemoChainLink]:
    """Screen #14: the hypothesis chain, each hop labelled with how solid it is."""
    if hero is None:
        return [DemoChainLink(
            step="disease", value="none selected", status="missing",
            detail="No candidate survived the gates, or the run abstained.",
        )]
    mediator = hero.mediator
    return [
        DemoChainLink(
            step="disease", value=hero.disease_context, status="established",
            detail="Selective dependency context.",
        ),
        DemoChainLink(
            step="target", value=hero.target_gene,
            status="established",
            detail=(
                f"median gene effect {hero.dependency.median_target_effect:.2f} "
                f"vs {hero.dependency.median_other_effect:.2f} elsewhere"
            ),
        ),
        DemoChainLink(
            step="partner_contact",
            value=f"{hero.target_gene}-{hero.partner_gene}",
            status="established" if mediator.interacting_region_mapped else "missing",
            detail=(
                f"mapped region: {mediator.target_region}"
                if mediator.interacting_region_mapped
                else "no interacting region mapped"
            ),
        ),
        DemoChainLink(
            step="interface",
            value=structure.comparison_verdict or "not modelled",
            status="predicted" if structure.results else "missing",
            detail=PREDICTION_CAVEAT,
        ),
        DemoChainLink(
            step="compounds", value=compounds.status,
            status="blocked" if compounds.status != "screened" else "predicted",
            detail=compounds.caveat,
        ),
    ]


def build_demo_payload(orchestrator: Any) -> DemoPayload:
    """Project a completed (or partial) run into the UI payload."""
    store: RunStore = orchestrator.store
    state = store.load_state()
    bundle = orchestrator.load_bundle()
    candidates = {c.candidate_id: c for c in bundle.candidates}
    evidence = orchestrator.load_evidence()
    sources = orchestrator.load_sources()
    scorecards = {c.candidate_id: c for c in orchestrator.load_scorecards()}

    # -- rejections, keyed for the candidate rows (screen #11) ---------------
    rejection_records = store.read_jsonl(orchestrator.rejections_path)
    failures_by_candidate: dict[str, list[DemoGateFailure]] = {}
    for record in rejection_records:
        gates = record.get("failed_gates") or []
        reasons = record.get("reasons") or []
        rows = [
            DemoGateFailure(gate=gate, reason=reason)
            for gate, reason in zip(gates, reasons, strict=False)
        ]
        # More reasons than gates would silently drop the tail.
        for extra in reasons[len(gates):]:
            rows.append(DemoGateFailure(gate="unspecified", reason=extra))
        failures_by_candidate.setdefault(record["candidate_id"], []).extend(rows)

    # -- structure (screen #12) ---------------------------------------------
    structure = DemoStructure()
    comparison_path = store.path("structure", "comparison.json")
    if comparison_path.exists():
        comparison = ModelComparison.model_validate(store.read_json(comparison_path))
        structure.candidate_id = comparison.candidate_id
        structure.comparison_verdict = comparison.verdict
        structure.agreements = list(comparison.agreements)
        structure.disagreements = list(comparison.disagreements)
        structure.confidence_delta = dict(comparison.confidence_delta)
        structure.caveats = [comparison.caveat, *comparison.limitations]

    purposes: dict[str, str] = {}
    request_path = store.path("structure", "request.json")
    if request_path.exists():
        for request in store.read_json(request_path).get("requests", []):
            purposes[request["request_id"]] = request.get("purpose", "")

    for model_dir in ("boltz2", "esmfold2"):
        for path in sorted(store.path("structure", model_dir).glob("*.json")):
            result = StructuralModelResult.model_validate(store.read_json(path))
            structure.results.append(DemoStructureResult(
                request_id=result.request_id, model=result.model,
                purpose=purposes.get(result.request_id),
                status=result.status, source=result.source,
                confidence=dict(result.confidence), chain_map=dict(result.chain_map),
                limitations=list(result.limitations),
                unresolved_questions=list(result.unresolved_questions),
            ))
    if structure.results:
        structure.status = "predicted"
    hero = candidates.get(state.hero_candidate_id or "")
    if hero is not None:
        structure.set_target_region(hero.mediator.target_region)

    # -- compounds: Vraj's slot, shape present before the run exists --------
    compounds = DemoCompounds(
        status="not_run",
        search_region_basis=None,
        blockers=[
            "Virtual screening is outside this workflow; poses are written by "
            "the docking stage (TASKS.md #5, #6).",
        ],
    )
    if structure.status == "none":
        compounds.status = "blocked"
        compounds.blockers.append("No modelled interface to define a pocket against.")

    # -- candidate rows ------------------------------------------------------
    ranked_ids = [c.candidate_id for c in rank(list(scorecards.values()))]
    rows: list[DemoCandidate] = []
    for index, candidate_id in enumerate(ranked_ids, start=1):
        candidate = candidates.get(candidate_id)
        if candidate is None:
            continue
        rows.append(_candidate_row(
            candidate,
            status="hero" if candidate_id == state.hero_candidate_id else "eligible",
            rank_index=index, scorecard=scorecards.get(candidate_id),
            gate_failures=failures_by_candidate.get(candidate_id, []),
            evidence=evidence, sources=sources,
        ))

    rejected_rows: list[DemoCandidate] = []
    for candidate_id in state.rejected_candidate_ids:
        candidate = candidates.get(candidate_id)
        if candidate is None:
            continue
        rejected_rows.append(_candidate_row(
            candidate, status="rejected", rank_index=None,
            scorecard=scorecards.get(candidate_id),
            gate_failures=failures_by_candidate.get(candidate_id, []),
            evidence=evidence, sources=sources,
        ))

    # -- reports -------------------------------------------------------------
    report = None
    report_path = store.path("reports", "final_report.json")
    if report_path.exists():
        report = FinalReport.model_validate(store.read_json(report_path))

    experiment = DemoNextExperiment()
    experiment_path = store.path("reports", "next_experiment.json")
    if experiment_path.exists():
        loaded = NextExperiment.model_validate(store.read_json(experiment_path))
        experiment = DemoNextExperiment(
            scientific_question=loaded.scientific_question,
            perturbation=loaded.perturbation, readout=loaded.readout,
            positive_controls=list(loaded.positive_controls),
            negative_controls=list(loaded.negative_controls),
            possible_outcomes=[
                DemoOutcome(outcome=o.outcome, interpretation_change=o.interpretation_change)
                for o in loaded.possible_outcomes
            ],
            limitations=list(loaded.limitations),
        )

    checkpoints = [
        DemoCheckpoint(
            checkpoint_id=c.checkpoint_id, stage=str(c.stage), status=c.status,
            requested_decision=c.requested_decision,
            recommended_candidate_id=c.recommended_candidate_id,
            resolved_by=c.resolved_by, resolved_at=c.resolved_at,
            resolution=c.resolution,
        )
        for c in orchestrator.load_checkpoints()
    ]

    limitations = list(report.limitations) if report else []
    if state.fixture_run and not any("FIXTURE" in x.upper() for x in limitations):
        limitations.insert(0, (
            "FIXTURE RUN: synthetic test data. Not scientific evidence."
        ))

    summary = DemoSummary(
        disease_context=hero.disease_context if hero else None,
        # The deprecated aliases are filled in by DemoSummary's validator.
        target_gene=hero.target_gene if hero else None,
        partner_gene=hero.partner_gene if hero else None,
        hypothesis=hero.hypothesis if hero else None,
        confidence=report.confidence if report else "low",
        chain=_build_chain(report, hero, structure, compounds),
        headline_limitation=limitations[0] if limitations else None,
    )

    return DemoPayload(
        generated_at=utc_now(),
        run=DemoRun(
            run_id=state.run_id, status=state.status, stage=str(state.stage),
            fixture_run=state.fixture_run, created_at=state.created_at,
            git_commit=state.git_commit, config_hash=state.config_hash,
            soul_version=orchestrator.soul.version,
            internal_trace_path=(
                store.relative(store.internal_trace_path)
                if store.internal_trace_path.exists() else None
            ),
            benchflow_trace_path=(
                store.relative(store.benchflow_trace_path)
                if store.benchflow_trace_path.exists() else None
            ),
            benchflow_trace_sha256=(
                sha256_file(store.benchflow_trace_path)
                if store.benchflow_trace_path.exists() else None
            ),
        ),
        summary=summary,
        candidates=rows,
        rejected=rejected_rows,
        evidence=[
            DemoEvidence(
                evidence_id=record.evidence_id, claim=record.claim,
                evidence_type=str(record.evidence_type),
                interpretation=str(record.interpretation),
                value=record.value, unit=record.unit, supports=record.supports,
                source_id=record.source_id,
                source_url=(
                    sources[record.source_id].url if record.source_id in sources else None
                ),
                limitations=list(record.limitations),
                contradicts=list(record.contradicts),
            )
            for record in evidence.values()
        ],
        sources=[
            DemoSource(
                source_id=source.source_id, name=source.name, url=source.url,
                tier=str(source.tier), version=source.version,
                retrieved_at=source.retrieved_at,
                is_public=source.url.startswith("https://"),
            )
            for source in sources.values()
        ],
        structure=structure,
        compounds=compounds,
        checkpoints=checkpoints,
        next_experiment=experiment,
        limitations=limitations,
    )


def export_demo_json(orchestrator: Any, path: Path | None = None) -> Path:
    """Write `demo.json` into the run directory (or `path`)."""
    payload = build_demo_payload(orchestrator)
    target = Path(path) if path else orchestrator.store.path("demo.json")
    orchestrator.store.write_json(target, payload.model_dump(mode="json"))
    return target
