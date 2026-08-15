"""Translation between `dependency_scout` and `reagent_workflow` types.

TASKS_AMIR.md A2. Vraj's real DepMap ingest lands as
`dependency_scout.models.DependencyEvidence`; the agent only eats
`reagent_workflow.models.CandidateHypothesis`. Without this, real numbers never
reach the gates, the score, the checkpoint, or `demo.json`.

Translation only. Neither contract changes — merging the two model packages is
explicitly out of scope, and the adapter is the answer instead.

`reagent_workflow` may import `dependency_scout`; not the reverse. Imports are
inside the functions so this package still runs where the other is absent,
matching the pattern in `structure.py`.

Three things this gets right and one it refuses:

1. **`selectivity_delta` is negated, not copied.** `dependency_scout` computes
   `other.median - target.median` (positive means selective); `reagent_workflow`
   uses `target - other` (negative means selective), and `ingest` rejects a
   candidate outright when the delta disagrees with the medians by more than
   0.02. A copied value fails ingest with a confusing message.
2. `SupportType` and the mapped region together decide `interaction_type`, via
   one table below.
3. `Claim.citations[]` become `SourceRecord`s; a public source needs a `version`
   or a `retrieved_at` or the validator raises.
4. **It refuses to invent `interaction_support`.** `MediatorLink` carries no
   numeric support, so it is a required keyword argument with no default. Where
   no number has been given, pass `None` and the gate rejects the candidate as
   unsupported — which is the correct outcome, not a problem to paper over.

The adapter carries **numbers, not verdicts**. The two packages' gates disagree
on thresholds; they are not reconciled here. The workflow re-gates, and the
workflow's answer is the one that reaches `demo.json`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .ingest import InputBundle
from .models import (
    CandidateHypothesis,
    DependencyEvidence,
    EvidenceRecord,
    EvidenceTier,
    EvidenceType,
    Interpretation,
    MediatorEvidence,
    SourceRecord,
    StructuralTractability,
)
from .store import content_hash

if TYPE_CHECKING:  # pragma: no cover - typing only
    from dependency_scout import models as ds

# Bijective, so a round trip does not drift.
SUPPORT_TO_INTERPRETATION: dict[str, Interpretation] = {
    "direct_experimental": Interpretation.OBSERVED,
    "genetic_functional": Interpretation.COMPUTED,
    "computational_prediction": Interpretation.PREDICTED,
    "inference": Interpretation.INFERENCE,
}
INTERPRETATION_TO_SUPPORT: dict[Interpretation, str] = {
    value: key for key, value in SUPPORT_TO_INTERPRETATION.items()
}

# Strongest claim first; the first match wins.
_CLAIM_STRENGTH_ORDER = (
    "direct_experimental", "genetic_functional", "computational_prediction", "inference",
)

# `InterfaceTractability` has no home on `StructuralTractability`, and the
# SLiM-versus-folded-domain distinction is what decides small-molecule
# tractability. Rather than add a field (a contract change needs Andrey's
# signature), the enum rides in `notes` under this prefix, losslessly.
TRACTABILITY_NOTE_PREFIX = "interface_tractability="


class ConversionRefused(ValueError):
    """A candidate cannot be converted honestly. The reason is the message."""


def _short_hash(payload: Any) -> str:
    return content_hash(payload)[:12]


def mint_source_id(url: str, name: str = "") -> str:
    """Deterministic, so converting twice does not churn the run directory."""
    return f"SRC-{_short_hash({'url': url, 'name': name or url})}"


def mint_evidence_id(statement: str, support: str) -> str:
    return f"EV-{_short_hash({'s': statement, 'p': support})}"


def interaction_type_for(
    claims: list[ds.Claim], *, interacting_region_mapped: bool
) -> str | None:
    """Map claim support and mapped-region status onto `interaction_type`.

    | support                    | mapped | interaction_type |
    |----------------------------|--------|------------------|
    | direct_experimental        | yes    | direct_binding   |
    | direct_experimental        | no     | complex_member   |
    | genetic_functional         | -      | genetic          |
    | computational / inference  | -      | inferred         |
    """
    if not claims:
        return None
    present = {claim.support.value for claim in claims}
    strongest = next((k for k in _CLAIM_STRENGTH_ORDER if k in present), None)
    if strongest == "direct_experimental":
        return "direct_binding" if interacting_region_mapped else "complex_member"
    if strongest == "genetic_functional":
        return "genetic"
    return "inferred"


def to_source_record(src: ds.SourceRecord, *, source_id: str) -> SourceRecord:
    """`dependency_scout.SourceRecord` → `reagent_workflow.SourceRecord`.

    The receiving contract is stricter: a synthetic source must use a
    `synthetic://` URL, so a fixture cannot be mistaken for a public one. The
    scout writes `local://synthetic-fixture`, so the scheme is rewritten and the
    original preserved in `notes` rather than the record being rejected.
    """
    tier = EvidenceTier(src.tier.value)
    url = src.url
    notes = src.notes
    if tier is EvidenceTier.SYNTHETIC and not url.startswith("synthetic://"):
        rewritten = f"synthetic://{url.split('://', 1)[-1]}"
        notes = "; ".join(filter(None, [notes, f"original url: {url}"]))
        url = rewritten
    return SourceRecord(
        source_id=source_id,
        name=src.name,
        url=url,
        tier=tier,
        version=src.version,
        sha256=src.sha256,
        notes=notes,
    )


def source_from_citation(url: str) -> SourceRecord:
    """A bare citation URL becomes a valid `SourceRecord`.

    Public sources need a version or a retrieval date. A citation carries
    neither, so `version` records that it came as-cited rather than inventing a
    release number.
    """
    normalised = url if url.startswith(("https://", "synthetic://")) else f"https://{url}"
    tier = (
        EvidenceTier.SYNTHETIC if normalised.startswith("synthetic://")
        else EvidenceTier.PUBLIC_PRIMARY
    )
    return SourceRecord(
        source_id=mint_source_id(normalised),
        name=normalised,
        url=normalised,
        tier=tier,
        version="as-cited",
        notes="Minted from a citation URL; no version or retrieval date supplied.",
    )


def claims_to_evidence(
    claims: list[ds.Claim],
    *,
    prefix: str,
    source_id: str | None = None,
    evidence_type: EvidenceType = EvidenceType.INTERACTION,
) -> tuple[list[EvidenceRecord], list[SourceRecord], list[str]]:
    """Claims become evidence records plus the sources their citations imply.

    Returns `(evidence, sources, losses)`. A claim with no citation is dropped
    and recorded as a loss rather than admitted without provenance.
    """
    records: list[EvidenceRecord] = []
    sources: dict[str, SourceRecord] = {}
    losses: list[str] = []

    for claim in claims:
        citations = list(claim.citations)
        if not citations:
            losses.append(f"{prefix}: claim dropped, no citation: {claim.statement[:60]}")
            continue
        source = source_from_citation(citations[0])
        sources.setdefault(source.source_id, source)
        limitations = [claim.note] if claim.note else []
        if len(citations) > 1:
            # One EvidenceRecord carries one source; the rest are preserved
            # rather than dropped.
            limitations.append(f"additional citations: {', '.join(citations[1:])}")
            losses.append(
                f"{prefix}: claim cites {len(citations)} sources; the first is the "
                "record's source and the rest are kept in limitations."
            )
        records.append(EvidenceRecord(
            evidence_id=mint_evidence_id(claim.statement, claim.support.value),
            evidence_type=evidence_type,
            interpretation=SUPPORT_TO_INTERPRETATION[claim.support.value],
            claim=claim.statement,
            subject=prefix,
            supports=True,
            source_id=source_id or source.source_id,
            limitations=limitations,
        ))
    return records, list(sources.values()), losses


def to_dependency_evidence(
    dep: ds.DependencyEvidence, *, source_id: str, evidence_ids: list[str]
) -> DependencyEvidence:
    """`dependency_scout.DependencyEvidence` → the workflow's.

    Negates `selectivity_delta`: the two packages define it with opposite signs,
    and `ingest` rejects a candidate whose delta disagrees with its medians.
    """
    return DependencyEvidence(
        gene=dep.gene,
        disease_context=dep.disease_context,
        n_target_models=dep.n_target_models,
        n_other_models=dep.n_other_models,
        median_target_effect=dep.median_target_effect,
        median_other_effect=dep.median_other_effect,
        target_dependent_fraction=dep.target_dependent_fraction,
        other_dependent_fraction=dep.other_dependent_fraction,
        selectivity_delta=-dep.selectivity_delta,
        mann_whitney_p=dep.mann_whitney_p,
        evidence_ids=list(evidence_ids),
        source_id=source_id,
    )


def to_mediator_evidence(
    link: ds.MediatorLink,
    *,
    transcription_factor: str,
    interaction_support: float | None,
    assay: str | None,
    source_id: str | None,
    evidence_ids: list[str],
    mediator_subunit: str | None = None,
) -> MediatorEvidence:
    """`dependency_scout.MediatorLink` → `reagent_workflow.MediatorEvidence`.

    `interaction_support` has no default on purpose. `MediatorLink` carries no
    numeric support, and inventing one here would let a candidate clear the
    support gate on a number nobody measured. Pass `None` when no number has
    been given; the gate then rejects the link as unsupported, which is correct.
    """
    return MediatorEvidence(
        transcription_factor=transcription_factor,
        mediator_subunit=mediator_subunit or link.partner_gene,
        interaction_support=interaction_support,
        interaction_type=interaction_type_for(
            link.claims, interacting_region_mapped=link.interacting_region_mapped
        ),
        assay=assay,
        interpretation=(
            SUPPORT_TO_INTERPRETATION[link.claims[0].support.value]
            if link.claims else None
        ),
        interacting_region_mapped=link.interacting_region_mapped,
        tf_region=link.tf_region,
        evidence_ids=list(evidence_ids),
        source_id=source_id,
        limitations=list(link.screening_concerns),
    )


def to_structural_tractability(link: ds.MediatorLink) -> StructuralTractability:
    from dependency_scout import models as ds_models  # noqa: PLC0415

    notes = [f"{TRACTABILITY_NOTE_PREFIX}{link.tractability.value}"]
    if link.tractability is ds_models.InterfaceTractability.SHORT_LINEAR_MOTIF:
        notes.append("Short linear motif: the tractable case for a small molecule.")
    return StructuralTractability(
        domain_bounded=(
            None if link.tractability is ds_models.InterfaceTractability.UNKNOWN
            else True
        ),
        notes=notes,
    )


@dataclass
class ConversionResult:
    """What came out, plus everything that did not survive cleanly."""

    candidate: CandidateHypothesis | None = None
    sources: list[SourceRecord] = field(default_factory=list)
    evidence: list[EvidenceRecord] = field(default_factory=list)
    losses: list[str] = field(default_factory=list)
    refusal: str | None = None

    @property
    def ok(self) -> bool:
        return self.candidate is not None


def to_candidate(
    rc: ds.RankedCandidate,
    *,
    candidate_id: str | None = None,
    mediator_subunit: str = "MED23",
    interaction_support: float | None,
    assay: str | None,
    allow_calibration_only: bool = False,
) -> ConversionResult:
    """`dependency_scout.RankedCandidate` → `CandidateHypothesis` + its evidence.

    Refuses rather than guesses when the candidate cannot stand up: no
    quantitative dependency, or a calibration control that must never be
    presented as a result.
    """
    result = ConversionResult()

    if rc.mediator.calibration_only and not allow_calibration_only:
        result.refusal = (
            f"{rc.name}: calibration control, never a result. Pass "
            "allow_calibration_only=True only for a calibration run."
        )
        return result

    if rc.awaiting_dependency_data or rc.dependency is None:
        result.refusal = (
            f"{rc.name}: awaiting quantitative dependency data. "
            "CandidateHypothesis requires the seven DepMap numbers, and "
            "inventing them is the failure this project exists to catch."
        )
        return result

    dependency = rc.dependency
    tf = dependency.gene
    subunit = mediator_subunit or rc.mediator.partner_gene
    identifier = candidate_id or f"CAND-{tf}-{subunit}"

    dep_source = to_source_record(
        dependency.source,
        source_id=mint_source_id(dependency.source.url, dependency.source.name),
    )
    sources: dict[str, SourceRecord] = {dep_source.source_id: dep_source}

    dep_evidence = EvidenceRecord(
        evidence_id=mint_evidence_id(
            f"{tf} dependency in {dependency.disease_context}", "genetic_functional"
        ),
        evidence_type=EvidenceType.DEPENDENCY,
        interpretation=Interpretation.COMPUTED,
        claim=(
            f"{tf} shows a median gene effect of {dependency.median_target_effect:.3f} "
            f"in {dependency.disease_context} versus "
            f"{dependency.median_other_effect:.3f} in other models."
        ),
        subject=tf,
        value=dependency.median_target_effect,
        unit="gene_effect_score",
        source_id=dep_source.source_id,
    )
    evidence: list[EvidenceRecord] = [dep_evidence]

    mediator_records, mediator_sources, losses = claims_to_evidence(
        rc.mediator.claims, prefix=f"{tf}-{subunit}",
        evidence_type=EvidenceType.INTERACTION,
    )
    evidence.extend(mediator_records)
    for source in mediator_sources:
        sources.setdefault(source.source_id, source)
    result.losses.extend(losses)

    enrichment_records, enrichment_sources, enrichment_losses = claims_to_evidence(
        rc.enrichment.claims, prefix=tf, evidence_type=EvidenceType.LITERATURE,
    )
    evidence.extend(enrichment_records)
    for source in enrichment_sources:
        sources.setdefault(source.source_id, source)
    result.losses.extend(enrichment_losses)

    if interaction_support is None:
        result.losses.append(
            f"{identifier}: no interaction_support supplied, so the Mediator link "
            "stays unsupported and the gate will reject it. Supply a number only "
            "when a human has one."
        )

    mediator = to_mediator_evidence(
        rc.mediator,
        transcription_factor=tf,
        mediator_subunit=subunit,
        interaction_support=interaction_support,
        assay=assay,
        source_id=mediator_records[0].source_id if mediator_records else None,
        evidence_ids=[record.evidence_id for record in mediator_records],
    )

    result.candidate = CandidateHypothesis(
        candidate_id=identifier,
        disease_context=dependency.disease_context,
        transcription_factor=tf,
        mediator_subunit=subunit,
        hypothesis=(
            f"In {dependency.disease_context}, the selective dependency on {tf} "
            f"may be carried by its interaction with the Mediator subunit {subunit}."
        ),
        dependency=to_dependency_evidence(
            dependency,
            source_id=dep_source.source_id,
            evidence_ids=[dep_evidence.evidence_id],
        ),
        mediator=mediator,
        tractability=to_structural_tractability(rc.mediator),
        normal_cell_evidence_ids=[],
        contradicting_evidence_ids=[],
        evidence_ids=[record.evidence_id for record in evidence],
    )
    result.sources = list(sources.values())
    result.evidence = evidence
    return result


def to_input_bundle(
    ranked: list[ds.RankedCandidate],
    *,
    interaction_support: dict[str, float] | None = None,
    assays: dict[str, str] | None = None,
    allow_calibration_only: bool = False,
) -> tuple[InputBundle, list[str], list[str]]:
    """Build a runnable `InputBundle` from `dependency_scout` output.

    `interaction_support` and `assays` are keyed by gene symbol and supplied by a
    human. A candidate with no entry converts with `None`, so the gate rejects
    its Mediator link as unsupported rather than the adapter inventing a value.

    Returns the bundle, the refusals, and the recorded losses.
    """
    support = interaction_support or {}
    assay_map = assays or {}
    sources: dict[str, SourceRecord] = {}
    evidence: dict[str, EvidenceRecord] = {}
    candidates: list[CandidateHypothesis] = []
    refusals: list[str] = []
    losses: list[str] = []

    for item in ranked:
        gene = item.name
        result = to_candidate(
            item,
            interaction_support=support.get(gene),
            assay=assay_map.get(gene),
            allow_calibration_only=allow_calibration_only,
        )
        losses.extend(result.losses)
        if not result.ok:
            refusals.append(result.refusal or f"{gene}: refused")
            continue
        assert result.candidate is not None
        candidates.append(result.candidate)
        for source in result.sources:
            sources.setdefault(source.source_id, source)
        for record in result.evidence:
            evidence.setdefault(record.evidence_id, record)

    if not candidates:
        raise ConversionRefused(
            "no candidate could be converted: "
            + ("; ".join(refusals) if refusals else "input was empty")
        )

    synthetic = any(s.tier is EvidenceTier.SYNTHETIC for s in sources.values())
    note = (
        "Converted from dependency_scout output containing synthetic sources. "
        "Test data, not scientific evidence."
    ) if synthetic else None

    bundle = InputBundle(
        fixture=synthetic,
        fixture_note=note,
        sources=list(sources.values()),
        evidence=list(evidence.values()),
        candidates=candidates,
    )
    return bundle, refusals, losses
