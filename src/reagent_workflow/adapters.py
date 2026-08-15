"""Translation between `dependency_scout` and `reagent_workflow` types.

TASKS.md #3. The two packages mean the same things and share no types, so there
is no end-to-end object path: Vraj's DepMap output cannot be handed to the agent,
and the agent's candidates cannot be rendered by the shortlist report.

This is a translation layer and nothing more. Neither contract changes — contract
redesign and merging the two model packages are both out of scope this weekend.
Everything here is conversion, refusal, or a recorded loss.

Three principles, because a silent adapter is worse than no adapter:

- **Refusals are explicit.** A candidate that cannot be honestly converted is
  refused with a reason, not filled in with defaults. `awaiting_dependency_data`
  and `calibration_only` are the two that matter.
- **Losses are recorded.** Where the vocabularies do not line up, the conversion
  says so in `ConversionResult.losses` rather than quietly picking a value.
- **IDs are deterministic.** Minted source and evidence IDs are content-derived,
  so converting the same candidate twice yields the same IDs and a re-run does
  not churn the run directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dependency_scout import models as ds

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

# Deliberately bijective, so a round trip does not drift.
SUPPORT_TO_INTERPRETATION: dict[str, Interpretation] = {
    "direct_experimental": Interpretation.OBSERVED,
    "genetic_functional": Interpretation.COMPUTED,
    "computational_prediction": Interpretation.PREDICTED,
    "inference": Interpretation.INFERENCE,
}
INTERPRETATION_TO_SUPPORT: dict[Interpretation, str] = {
    value: key for key, value in SUPPORT_TO_INTERPRETATION.items()
}

INVOLVEMENT_TO_INTERACTION_TYPE: dict[str, str | None] = {
    "direct": "direct_binding",
    "indirect": "complex_member",
    "predicted": "inferred",
    "unknown": None,
}

# `StructuralTractability` has no field for the SLiM-vs-folded-domain
# distinction, and that distinction is the whole point of `InterfaceTractability`
# (a short linear motif is the tractable case; a folded domain is not). Rather
# than redesign the contract, the enum rides in `notes` under this prefix so the
# round trip is lossless and the UI can still read it.
TRACTABILITY_NOTE_PREFIX = "interface_tractability="

# `MediatorEvidence` wants a numeric support value; `MediatorLink` has typed
# claims instead. Rather than invent a number — which would let an adapted
# candidate clear the support gate on a value nobody measured — the value is
# *derived* from the strongest claim type present, by this stated rule. Every
# converted candidate records the rule and the claim it came from, so the number
# is auditable back to its evidence.
SUPPORT_VALUE_BY_STRONGEST_CLAIM: dict[str, float] = {
    "direct_experimental": 0.8,
    "genetic_functional": 0.5,
    "computational_prediction": 0.3,
    "inference": 0.2,
}
_CLAIM_STRENGTH_ORDER = (
    "direct_experimental", "genetic_functional", "computational_prediction", "inference",
)


class ConversionRefused(ValueError):
    """A candidate cannot be converted honestly. The reason is the message."""


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


def _short_hash(payload: Any) -> str:
    return content_hash(payload)[:12]


def _mint_source_id(url: str, name: str) -> str:
    return f"SRC-{_short_hash({'url': url, 'name': name})}"


def _mint_evidence_id(statement: str, support: str) -> str:
    return f"EV-{_short_hash({'s': statement, 'p': support})}"


def _tier_for(url: str, fallback: ds.EvidenceTier | None = None) -> EvidenceTier:
    if fallback is not None:
        return EvidenceTier(fallback.value)
    if url.startswith("https://"):
        return EvidenceTier.PUBLIC_PRIMARY
    return EvidenceTier.SYNTHETIC


def _source_from_citation(url: str) -> SourceRecord:
    """A citation URL becomes a minimal but valid SourceRecord.

    `SourceRecord` demands a version or a retrieval date for anything public.
    A bare citation carries neither, so the URL is recorded as the version —
    honest about the fact that no version was supplied, without inventing one.
    """
    return SourceRecord(
        source_id=_mint_source_id(url, url),
        name=url,
        url=url if url.startswith(("https://", "synthetic://")) else f"https://{url}",
        tier=_tier_for(url),
        version="as-cited",
        notes="Minted from a citation URL; no version or retrieval date was supplied.",
    )


def _claims_to_evidence(
    claims: list[ds.Claim],
    subject: str,
    evidence_type: EvidenceType,
) -> tuple[list[EvidenceRecord], list[SourceRecord], list[str]]:
    records: list[EvidenceRecord] = []
    sources: dict[str, SourceRecord] = {}
    losses: list[str] = []

    for claim in claims:
        citations = list(claim.citations)
        if not citations:
            losses.append(f"claim without a citation dropped: {claim.statement[:60]}")
            continue
        source = _source_from_citation(citations[0])
        sources.setdefault(source.source_id, source)
        if len(citations) > 1:
            # EvidenceRecord carries one source; the rest go into limitations
            # rather than disappearing.
            extra = ", ".join(citations[1:])
            losses.append(
                f"claim cites {len(citations)} sources; only the first is the "
                f"EvidenceRecord source. Others kept in limitations: {extra}"
            )
        limitations = [claim.note] if claim.note else []
        if len(citations) > 1:
            limitations.append(f"additional citations: {', '.join(citations[1:])}")
        records.append(EvidenceRecord(
            evidence_id=_mint_evidence_id(claim.statement, claim.support.value),
            evidence_type=evidence_type,
            interpretation=SUPPORT_TO_INTERPRETATION[claim.support.value],
            claim=claim.statement,
            subject=subject,
            supports=True,
            source_id=source.source_id,
            limitations=limitations,
        ))
    return records, list(sources.values()), losses


def ranked_to_hypothesis(
    ranked: ds.RankedCandidate,
    *,
    candidate_id: str | None = None,
    mediator_subunit: str | None = None,
    allow_calibration_only: bool = False,
) -> ConversionResult:
    """`dependency_scout.RankedCandidate` → `reagent_workflow.CandidateHypothesis`.

    Refuses rather than guesses when the candidate cannot stand up as a
    hypothesis: no quantitative dependency, or a calibration control that must
    never be presented as a result.
    """
    result = ConversionResult()

    if ranked.mediator.calibration_only and not allow_calibration_only:
        result.refusal = (
            f"{ranked.name}: calibration control, never a result. Pass "
            "allow_calibration_only=True only for a calibration run."
        )
        return result

    if ranked.awaiting_dependency_data or ranked.dependency is None:
        result.refusal = (
            f"{ranked.name}: awaiting quantitative dependency data. "
            "CandidateHypothesis requires the seven DepMap numbers, and "
            "inventing them is the failure this project exists to catch."
        )
        return result

    dependency = ranked.dependency
    tf = dependency.gene
    subunit = mediator_subunit or ranked.mediator.partner_gene
    identifier = candidate_id or f"CAND-{tf}-{subunit}"

    # -- source for the dependency numbers -----------------------------------
    ds_source = dependency.source
    dep_source = SourceRecord(
        source_id=_mint_source_id(ds_source.url, ds_source.name),
        name=ds_source.name,
        url=ds_source.url,
        tier=_tier_for(ds_source.url, ds_source.tier),
        version=ds_source.version,
        sha256=ds_source.sha256,
        notes=ds_source.notes,
    )
    sources: dict[str, SourceRecord] = {dep_source.source_id: dep_source}

    dep_evidence = EvidenceRecord(
        evidence_id=_mint_evidence_id(
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

    # -- mediator claims ------------------------------------------------------
    mediator_records, mediator_sources, losses = _claims_to_evidence(
        ranked.mediator.claims, f"{tf}-{subunit}", EvidenceType.INTERACTION
    )
    evidence.extend(mediator_records)
    for source in mediator_sources:
        sources.setdefault(source.source_id, source)
    result.losses.extend(losses)

    # -- enrichment claims ----------------------------------------------------
    enrichment_records, enrichment_sources, enrichment_losses = _claims_to_evidence(
        ranked.enrichment.claims, tf, EvidenceType.LITERATURE
    )
    evidence.extend(enrichment_records)
    for source in enrichment_sources:
        sources.setdefault(source.source_id, source)
    result.losses.extend(enrichment_losses)

    involvement = ranked.mediator.involvement.value
    interaction_type = INVOLVEMENT_TO_INTERACTION_TYPE[involvement]

    # `MediatorEvidence.is_supported` needs a support value, an assay, a source
    # and evidence records. Only mint a support value where the claims justify
    # one; a link with no claims stays unsupported rather than being invented.
    if mediator_records:
        interaction_support = ranked.enrichment.interface_support
        if interaction_support is None:
            # Derived from the strongest claim type, by the stated rule above —
            # not invented, and recorded so the number traces back to a claim.
            present = {claim.support.value for claim in ranked.mediator.claims}
            strongest = next(
                (kind for kind in _CLAIM_STRENGTH_ORDER if kind in present), "inference"
            )
            interaction_support = SUPPORT_VALUE_BY_STRONGEST_CLAIM[strongest]
            support_provenance = (
                f"interaction_support {interaction_support} is derived from the "
                f"strongest claim type present ({strongest}), not measured. "
                "See SUPPORT_VALUE_BY_STRONGEST_CLAIM in adapters.py."
            )
            result.losses.append(f"{identifier}: {support_provenance}")
        else:
            support_provenance = (
                f"interaction_support {interaction_support} taken from "
                "enrichment.interface_support."
            )
        assay = mediator_records[0].claim[:80]
        source_id = mediator_records[0].source_id
    else:
        interaction_support = None
        assay = None
        source_id = None
        support_provenance = "no mediator claims: the link is unsupported."

    mediator = MediatorEvidence(
        transcription_factor=tf,
        mediator_subunit=subunit,
        interaction_support=interaction_support,
        interaction_type=interaction_type,
        assay=assay,
        interpretation=(
            SUPPORT_TO_INTERPRETATION[ranked.mediator.claims[0].support.value]
            if ranked.mediator.claims else None
        ),
        interacting_region_mapped=ranked.mediator.interacting_region_mapped,
        tf_region=ranked.mediator.tf_region,
        evidence_ids=[record.evidence_id for record in mediator_records],
        source_id=source_id,
        limitations=[*ranked.mediator.screening_concerns, support_provenance],
    )

    tractability = StructuralTractability(
        domain_bounded=(
            None if ranked.mediator.tractability is ds.InterfaceTractability.UNKNOWN
            else True
        ),
        notes=[
            f"{TRACTABILITY_NOTE_PREFIX}{ranked.mediator.tractability.value}",
            *ranked.enrichment.notes,
        ],
    )
    if ranked.mediator.tractability is ds.InterfaceTractability.SHORT_LINEAR_MOTIF:
        tractability.notes.append(
            "Short linear motif: the tractable case for a small molecule."
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
        dependency=DependencyEvidence(
            gene=tf,
            disease_context=dependency.disease_context,
            n_target_models=dependency.n_target_models,
            n_other_models=dependency.n_other_models,
            median_target_effect=dependency.median_target_effect,
            median_other_effect=dependency.median_other_effect,
            target_dependent_fraction=dependency.target_dependent_fraction,
            other_dependent_fraction=dependency.other_dependent_fraction,
            selectivity_delta=dependency.selectivity_delta,
            mann_whitney_p=dependency.mann_whitney_p,
            evidence_ids=[dep_evidence.evidence_id],
            source_id=dep_source.source_id,
        ),
        mediator=mediator,
        tractability=tractability,
        normal_cell_evidence_ids=[],
        contradicting_evidence_ids=[],
        evidence_ids=[record.evidence_id for record in evidence],
    )
    result.sources = list(sources.values())
    result.evidence = evidence

    if ranked.enrichment.normal_cell_support is not None:
        result.losses.append(
            f"{identifier}: enrichment.normal_cell_support "
            f"({ranked.enrichment.normal_cell_support}) has no evidence records to "
            "attach to, so normal-cell completeness will score as missing."
        )
    return result


def hypothesis_to_ranked(
    candidate: CandidateHypothesis,
    *,
    evidence: dict[str, EvidenceRecord] | None = None,
    sources: dict[str, SourceRecord] | None = None,
    gate_failures: list[str] | None = None,
    final_score: float | None = None,
) -> ds.RankedCandidate:
    """`reagent_workflow.CandidateHypothesis` → `dependency_scout.RankedCandidate`.

    The reverse direction, so the agent's output can be rendered by
    `report.build_shortlist` without that module learning a second type.
    """
    evidence = evidence or {}
    sources = sources or {}
    dependency = candidate.dependency

    source = sources.get(dependency.source_id)
    ds_source = ds.SourceRecord(
        name=source.name if source else dependency.source_id,
        version=(source.version if source and source.version else "unversioned"),
        url=source.url if source else "synthetic://unknown",
        tier=ds.EvidenceTier(source.tier.value) if source else ds.EvidenceTier.SYNTHETIC,
        sha256=source.sha256 if source else None,
        notes=source.notes if source else None,
    )

    claims: list[ds.Claim] = []
    for evidence_id in candidate.mediator.evidence_ids:
        record = evidence.get(evidence_id)
        if record is None:
            continue
        citation_source = sources.get(record.source_id)
        citation = citation_source.url if citation_source else None
        if not citation:
            continue
        support = ds.SupportType(INTERPRETATION_TO_SUPPORT[record.interpretation])
        claims.append(ds.Claim(
            statement=record.claim,
            support=support,
            citations=[citation],
            note=(
                "; ".join(record.limitations) if record.limitations
                else ("converted from reagent_workflow evidence"
                      if support is ds.SupportType.INFERENCE else None)
            ),
        ))

    tractability = ds.InterfaceTractability.UNKNOWN
    for note in candidate.tractability.notes:
        if note.startswith(TRACTABILITY_NOTE_PREFIX):
            tractability = ds.InterfaceTractability(
                note[len(TRACTABILITY_NOTE_PREFIX):]
            )
            break

    mediator = ds.MediatorLink(
        partner_gene=candidate.mediator_subunit,
        interacting_region_mapped=candidate.mediator.interacting_region_mapped,
        tf_region=candidate.mediator.tf_region,
        claims=claims,
        tractability=tractability,
    )

    return ds.RankedCandidate(
        gene=candidate.transcription_factor,
        dependency=ds.DependencyEvidence(
            gene=dependency.gene,
            disease_context=dependency.disease_context,
            n_target_models=dependency.n_target_models,
            n_other_models=dependency.n_other_models,
            median_target_effect=dependency.median_target_effect,
            median_other_effect=dependency.median_other_effect,
            target_dependent_fraction=dependency.target_dependent_fraction,
            other_dependent_fraction=dependency.other_dependent_fraction,
            selectivity_delta=dependency.selectivity_delta,
            mann_whitney_p=dependency.mann_whitney_p,
            source=ds_source,
        ),
        gate=ds.GateResult(
            eligible=not gate_failures, failures=list(gate_failures or [])
        ),
        mediator=mediator,
        final_score=final_score,
    )


def ranked_to_input_bundle(
    ranked: list[ds.RankedCandidate],
    *,
    fixture: bool = False,
    fixture_note: str | None = None,
    allow_calibration_only: bool = False,
) -> tuple[InputBundle, list[str], list[str]]:
    """Build a runnable `InputBundle` from `dependency_scout` output.

    This is the end-to-end path: DepMap ingest produces `RankedCandidate`s, and
    the agent runs on them without either package importing the other's shapes.

    Returns the bundle, the refusals, and the recorded losses. Refusals are not
    errors — a candidate awaiting dependency data is a normal state, and the
    caller decides whether an empty bundle is a problem.
    """
    sources: dict[str, SourceRecord] = {}
    evidence: dict[str, EvidenceRecord] = {}
    candidates: list[CandidateHypothesis] = []
    refusals: list[str] = []
    losses: list[str] = []

    for item in ranked:
        result = ranked_to_hypothesis(
            item, allow_calibration_only=allow_calibration_only
        )
        losses.extend(result.losses)
        if not result.ok:
            refusals.append(result.refusal or f"{item.name}: refused")
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
    is_fixture = fixture or synthetic
    note = fixture_note
    if is_fixture and not note:
        note = (
            "Converted from dependency_scout output containing synthetic sources. "
            "Test data, not scientific evidence."
        )

    bundle = InputBundle(
        fixture=is_fixture,
        fixture_note=note,
        sources=list(sources.values()),
        evidence=list(evidence.values()),
        candidates=candidates,
    )
    return bundle, refusals, losses
