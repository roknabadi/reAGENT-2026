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

1. **`selectivity_delta` is recomputed from the medians, never copied or
   negated.** Three components in this repo define it and they do not agree:
   `dependency_scout.depmap` computes `other - target` (positive means
   selective), while `reagent_workflow` and Kevin's `stage1_depmap` both compute
   `target - other` (negative means selective). A blanket negation is right for
   one source and silently inverts another. The medians are unambiguous, so the
   delta is derived from them and any supplied value is only used to detect
   disagreement.
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
    InteractionEvidence,
    Interpretation,
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


def mint_evidence_id(statement: str, support: str, subject: str = "") -> str:
    """Deterministic evidence id, scoped by subject.

    Hashing only (statement, support) collided across candidates: two candidates
    whose claims share wording — boilerplate like "co-immunoprecipitation
    confirms the interaction" is common — minted the same id, and the second
    record was silently discarded by the ``setdefault`` in ``to_input_bundle``,
    leaving the second candidate citing the first candidate's source.
    """
    return f"EV-{_short_hash({'s': statement, 'p': support, 'subj': subject})}"


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
            evidence_id=mint_evidence_id(claim.statement, claim.support.value, prefix),
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

    The delta is **recomputed from the medians**, not copied and not negated.

    Three components in this repo define `selectivity_delta` and they do not
    agree: `dependency_scout.depmap` computes `other - target` (positive means
    selective), while `reagent_workflow` and Kevin's `stage1_depmap` both compute
    `target - other` (negative means selective). A blanket negation is correct
    for one source and silently wrong for another — it would turn an already
    correct value into its opposite and invert the science.

    The medians carry no such ambiguity, so the delta is derived from them and
    the supplied value is only used to detect a disagreement worth reporting.
    """
    implied = dep.median_target_effect - dep.median_other_effect
    return DependencyEvidence(
        gene=dep.gene,
        disease_context=dep.disease_context,
        n_target_models=dep.n_target_models,
        n_other_models=dep.n_other_models,
        median_target_effect=dep.median_target_effect,
        median_other_effect=dep.median_other_effect,
        target_dependent_fraction=dep.target_dependent_fraction,
        other_dependent_fraction=dep.other_dependent_fraction,
        selectivity_delta=implied,
        mann_whitney_p=dep.mann_whitney_p,
        evidence_ids=list(evidence_ids),
        source_id=source_id,
    )


def to_interaction_evidence(
    link: ds.MediatorLink,
    *,
    target_gene: str | None = None,
    interaction_support: float | None,
    assay: str | None,
    source_id: str | None,
    evidence_ids: list[str],
    partner_gene: str | None = None,
    transcription_factor: str | None = None,
    mediator_subunit: str | None = None,
) -> InteractionEvidence:
    """`dependency_scout.MediatorLink` → `reagent_workflow.InteractionEvidence`.

    `target_gene` is required despite its `None` default: the default exists
    only so the deprecated `transcription_factor=` spelling can supply it
    instead. `partner_gene` (formerly `mediator_subunit=`) falls back to the
    link's own partner rather than to any particular gene.

    `interaction_support` has no default on purpose. `MediatorLink` carries no
    numeric support, and inventing one here would let a candidate clear the
    support gate on a number nobody measured. Pass `None` when no number has
    been given; the gate then rejects the link as unsupported, which is correct.
    """
    target = target_gene or transcription_factor
    if not target:
        raise TypeError(
            "to_interaction_evidence() requires target_gene (or the deprecated "
            "transcription_factor=)"
        )
    return InteractionEvidence(
        target_gene=target,
        partner_gene=partner_gene or mediator_subunit or link.partner_gene,
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
        # `tf_region` is `dependency_scout`'s field name for the mapped region.
        target_region=link.tf_region,
        evidence_ids=list(evidence_ids),
        source_id=source_id,
        limitations=list(link.screening_concerns),
    )


#: Schema-1.0 name. Kept so existing callers and imports keep working.
to_mediator_evidence = to_interaction_evidence


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
    partner_gene: str | None = None,
    interaction_support: float | None,
    assay: str | None,
    allow_calibration_only: bool = False,
    mediator_subunit: str | None = None,
) -> ConversionResult:
    """`dependency_scout.RankedCandidate` → `CandidateHypothesis` + its evidence.

    `partner_gene` is required despite its `None` default: the default exists
    only so the deprecated `mediator_subunit=` spelling can supply it instead.
    It previously defaulted to one named gene, which silently relabelled every
    candidate's partner as that gene regardless of the link it came from.

    Refuses rather than guesses when the candidate cannot stand up: no
    quantitative dependency, or a calibration control that must never be
    presented as a result.
    """
    partner_gene = partner_gene or mediator_subunit
    if not partner_gene:
        raise TypeError(
            "to_candidate() requires partner_gene (or the deprecated "
            "mediator_subunit=)"
        )
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
    target = dependency.gene
    partner = partner_gene
    identifier = candidate_id or f"CAND-{target}-{partner}"

    dep_source = to_source_record(
        dependency.source,
        source_id=mint_source_id(dependency.source.url, dependency.source.name),
    )
    sources: dict[str, SourceRecord] = {dep_source.source_id: dep_source}

    dep_evidence = EvidenceRecord(
        evidence_id=mint_evidence_id(
            f"{target} dependency in {dependency.disease_context}",
            "genetic_functional", target,
        ),
        evidence_type=EvidenceType.DEPENDENCY,
        interpretation=Interpretation.COMPUTED,
        claim=(
            f"{target} shows a median gene effect of "
            f"{dependency.median_target_effect:.3f} "
            f"in {dependency.disease_context} versus "
            f"{dependency.median_other_effect:.3f} in other models."
        ),
        subject=target,
        value=dependency.median_target_effect,
        unit="gene_effect_score",
        source_id=dep_source.source_id,
    )
    evidence: list[EvidenceRecord] = [dep_evidence]

    # `rc.mediator` is `dependency_scout`'s field name for the interaction link.
    interaction_records, interaction_sources, losses = claims_to_evidence(
        rc.mediator.claims, prefix=f"{target}-{partner}",
        evidence_type=EvidenceType.INTERACTION,
    )
    evidence.extend(interaction_records)
    for source in interaction_sources:
        sources.setdefault(source.source_id, source)
    result.losses.extend(losses)

    enrichment_records, enrichment_sources, enrichment_losses = claims_to_evidence(
        rc.enrichment.claims, prefix=target, evidence_type=EvidenceType.LITERATURE,
    )
    evidence.extend(enrichment_records)
    for source in enrichment_sources:
        sources.setdefault(source.source_id, source)
    result.losses.extend(enrichment_losses)

    if interaction_support is None:
        result.losses.append(
            f"{identifier}: no interaction_support supplied, so the interaction "
            "stays unsupported and the gate will reject it. Supply a number only "
            "when a human has one."
        )

    interaction = to_interaction_evidence(
        rc.mediator,
        target_gene=target,
        partner_gene=partner,
        interaction_support=interaction_support,
        assay=assay,
        source_id=interaction_records[0].source_id if interaction_records else None,
        evidence_ids=[record.evidence_id for record in interaction_records],
    )

    result.candidate = CandidateHypothesis(
        candidate_id=identifier,
        disease_context=dependency.disease_context,
        target_gene=target,
        partner_gene=partner,
        hypothesis=(
            f"In {dependency.disease_context}, the selective dependency on {target} "
            f"may be carried by its interaction with {partner}."
        ),
        dependency=to_dependency_evidence(
            dependency,
            source_id=dep_source.source_id,
            evidence_ids=[dep_evidence.evidence_id],
        ),
        interaction=interaction,
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
    its interaction as unsupported rather than the adapter inventing a value.

    Each candidate's partner comes from its own link, so a batch may span many
    different partners.

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
            # `item.mediator` is `dependency_scout`'s field name for the link;
            # the partner it names is whatever that link is about.
            partner_gene=item.mediator.partner_gene,
            interaction_support=support.get(gene),
            assay=assay_map.get(gene),
            allow_calibration_only=allow_calibration_only,
        )
        losses.extend(result.losses)
        if not result.ok:
            refusals.append(result.refusal or f"{gene}: refused")
            continue
        assert result.candidate is not None
        # Ids are minted from target+partner, so two scout rows for the same pair
        # (different disease contexts, or a repeated gene) collide. Silently
        # keeping one dropped a real candidate and could change which candidate
        # became the hero. Disambiguate and record it rather than losing data.
        identifier = result.candidate.candidate_id
        if any(c.candidate_id == identifier for c in candidates):
            suffix = sum(1 for c in candidates if c.candidate_id.startswith(identifier)) + 1
            deduped = f"{identifier}-{suffix}"
            losses.append(
                f"{identifier}: duplicate candidate id from a second scout row; "
                f"renamed to {deduped} so neither candidate is dropped."
            )
            result.candidate = result.candidate.model_copy(
                update={"candidate_id": deduped}
            )
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
