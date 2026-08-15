"""Input validation.

The typed boundary for everything upstream — omics, literature, interaction
databases. Those systems are not built here; this module states exactly what
they must hand over, and rejects records that cannot be audited.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import (
    SCHEMA_VERSION,
    CandidateHypothesis,
    EvidenceRecord,
    EvidenceTier,
    SourceRecord,
)

IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
# Real HGNC symbols are uppercase alphanumeric with hyphens (HLA-A, NKX2-1).
# Underscores and dots are permitted because upstream fixtures and alias tables
# use them, and rejecting a candidate over punctuation is not a scientific
# judgment — the gates are where candidates should fail.
GENE_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9._-]{0,20}$")


class InputBundle(BaseModel):
    """What ``input/candidates.json`` must contain."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    fixture: bool = False
    fixture_note: str | None = None
    sources: list[SourceRecord] = Field(min_length=1)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    candidates: list[CandidateHypothesis] = Field(min_length=1)

    @model_validator(mode="after")
    def fixtures_declare_themselves(self) -> InputBundle:
        synthetic = [s for s in self.sources if s.tier is EvidenceTier.SYNTHETIC]
        if synthetic and not self.fixture:
            raise ValueError(
                "bundle contains synthetic sources but is not marked fixture=true; "
                "synthetic data is for tests, not scientific evidence"
            )
        if self.fixture and not self.fixture_note:
            raise ValueError("a fixture bundle must carry a fixture_note saying so")
        return self


@dataclass
class IngestReport:
    """Accepted and rejected records, with a reason for every rejection."""

    accepted_evidence: list[str] = field(default_factory=list)
    rejected_evidence: list[tuple[str, str]] = field(default_factory=list)
    accepted_candidates: list[str] = field(default_factory=list)
    rejected_candidates: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rejected_candidates


def _validate_evidence(
    record: EvidenceRecord, sources: dict[str, SourceRecord]
) -> list[str]:
    problems: list[str] = []
    if not IDENTIFIER_RE.match(record.evidence_id):
        problems.append(f"invalid evidence identifier {record.evidence_id!r}")
    source = sources.get(record.source_id)
    if source is None:
        problems.append(f"references unknown source {record.source_id!r}")
    else:
        if source.tier is not EvidenceTier.SYNTHETIC:
            if not source.url.startswith("https://"):
                problems.append(f"source {source.source_id} has a non-public url")
            if not (source.version or source.retrieved_at):
                problems.append(
                    f"source {source.source_id} has neither a version nor a retrieval date"
                )
    if isinstance(record.value, float) and not record.unit:
        problems.append("numeric value carries no unit")
    if not record.claim.strip():
        problems.append("empty claim")
    return problems


def _validate_candidate(
    candidate: CandidateHypothesis,
    sources: dict[str, SourceRecord],
    evidence: dict[str, EvidenceRecord],
) -> list[str]:
    problems: list[str] = []
    if not IDENTIFIER_RE.match(candidate.candidate_id):
        problems.append(f"invalid candidate identifier {candidate.candidate_id!r}")
    for label, symbol in (
        ("target_gene", candidate.target_gene),
        ("partner_gene", candidate.partner_gene),
    ):
        if not GENE_SYMBOL_RE.match(symbol):
            problems.append(f"{label} {symbol!r} is not a plausible gene symbol")
    if candidate.dependency.gene != candidate.target_gene:
        problems.append(
            f"dependency gene {candidate.dependency.gene!r} does not match the "
            f"candidate target {candidate.target_gene!r}"
        )
    if candidate.dependency.disease_context != candidate.disease_context:
        problems.append("dependency disease context does not match the candidate context")
    if candidate.mediator.target_gene != candidate.target_gene:
        problems.append("interaction evidence names a different target gene")
    if candidate.mediator.partner_gene != candidate.partner_gene:
        problems.append("interaction evidence names a different partner gene")

    if candidate.dependency.source_id not in sources:
        problems.append(f"dependency cites unknown source {candidate.dependency.source_id!r}")
    if candidate.mediator.source_id and candidate.mediator.source_id not in sources:
        problems.append(f"interaction evidence cites unknown source {candidate.mediator.source_id!r}")

    all_ids = (
        list(candidate.evidence_ids)
        + list(candidate.dependency.evidence_ids)
        + list(candidate.mediator.evidence_ids)
        + list(candidate.normal_cell_evidence_ids)
        + list(candidate.contradicting_evidence_ids)
    )
    for evidence_id in all_ids:
        if evidence_id not in evidence:
            problems.append(f"references unknown evidence {evidence_id!r}")

    # A selectivity delta that disagrees with its own medians is a data error,
    # not a weak candidate; catching it here keeps the gate honest.
    expected_delta = candidate.dependency.median_target_effect - candidate.dependency.median_other_effect
    if abs(expected_delta - candidate.dependency.selectivity_delta) > 0.02:
        problems.append(
            f"selectivity_delta {candidate.dependency.selectivity_delta:.3f} does not "
            f"match the reported medians (expected {expected_delta:.3f})"
        )
    return problems


def ingest(bundle: InputBundle) -> tuple[IngestReport, list[CandidateHypothesis]]:
    """Validate a bundle. Returns the report and the candidates that survived."""
    report = IngestReport()
    sources = {source.source_id: source for source in bundle.sources}

    if len(sources) != len(bundle.sources):
        report.warnings.append("duplicate source_id values were collapsed")

    valid_evidence: dict[str, EvidenceRecord] = {}
    for record in bundle.evidence:
        problems = _validate_evidence(record, sources)
        if problems:
            report.rejected_evidence.append((record.evidence_id, "; ".join(problems)))
        else:
            valid_evidence[record.evidence_id] = record
            report.accepted_evidence.append(record.evidence_id)

    accepted: list[CandidateHypothesis] = []
    for candidate in bundle.candidates:
        problems = _validate_candidate(candidate, sources, valid_evidence)
        if problems:
            report.rejected_candidates.append((candidate.candidate_id, "; ".join(problems)))
        else:
            accepted.append(candidate)
            report.accepted_candidates.append(candidate.candidate_id)

    if bundle.fixture:
        report.warnings.append(
            "FIXTURE RUN: inputs are synthetic test data and carry no scientific weight."
        )
    return report, accepted


def load_bundle(payload: dict[str, Any]) -> InputBundle:
    return InputBundle.model_validate(payload)
