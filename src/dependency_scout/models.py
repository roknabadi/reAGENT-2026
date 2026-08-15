"""Auditable evidence and handoff contracts."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceTier(StrEnum):
    SYNTHETIC = "synthetic"
    PUBLIC_PRIMARY = "public_primary"
    PUBLIC_DERIVED = "public_derived"


class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    version: str
    url: str
    tier: EvidenceTier
    sha256: str | None = None
    notes: str | None = None


class DependencyEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gene: str
    disease_context: str
    n_target_models: int = Field(ge=0)
    n_other_models: int = Field(ge=0)
    median_target_effect: float
    median_other_effect: float
    target_dependent_fraction: float = Field(ge=0, le=1)
    other_dependent_fraction: float = Field(ge=0, le=1)
    selectivity_delta: float
    mann_whitney_p: float | None = Field(default=None, ge=0, le=1)
    source: SourceRecord


class SupportType(StrEnum):
    """How a claim is supported. Orthogonal to EvidenceTier, which is provenance."""
    DIRECT_EXPERIMENTAL = "direct_experimental"      # structure, crosslink, mapped two-hybrid, ITC
    GENETIC_FUNCTIONAL = "genetic_functional"        # knockdown/knockout/rescue phenotype
    COMPUTATIONAL_PREDICTION = "computational_prediction"
    INFERENCE = "inference"                          # our reading; not stated by the source


class Claim(BaseModel):
    """A statement that cannot exist without its sources."""
    model_config = ConfigDict(extra="forbid")
    statement: str
    support: SupportType
    citations: list[str] = Field(min_length=1)  # DOI or public URL, one per source
    note: str | None = None

    @model_validator(mode="after")
    def inference_is_not_a_citation_launder(self) -> "Claim":
        if self.support is SupportType.INFERENCE and not self.note:
            raise ValueError("an inference claim must record the reasoning in `note`")
        return self


class Involvement(StrEnum):
    DIRECT = "direct"        # physical contact with the interacting region mapped
    INDIRECT = "indirect"    # functional/complex evidence, no region mapped
    PREDICTED = "predicted"  # computational only
    UNKNOWN = "unknown"      # nothing found


class MediatorLink(BaseModel):
    """The TF-to-Mediator-subunit contact. Deliverable C of the stage-1 review."""
    model_config = ConfigDict(extra="forbid")
    partner_gene: str = "MED23"
    interacting_region_mapped: bool = False
    tf_region: str | None = None  # e.g. "activation domain, residues 1-89"
    claims: list[Claim] = Field(default_factory=list)

    @model_validator(mode="after")
    def mapped_region_needs_direct_evidence(self) -> "MediatorLink":
        if self.interacting_region_mapped:
            if not self.tf_region:
                raise ValueError("interacting_region_mapped requires tf_region to name the region")
            if not any(c.support is SupportType.DIRECT_EXPERIMENTAL for c in self.claims):
                raise ValueError("a mapped interacting region requires a direct_experimental claim")
        return self

    @property
    def involvement(self) -> Involvement:
        """Derived, never stored, so it cannot drift from the claims."""
        if not self.claims:
            return Involvement.UNKNOWN
        supports = {c.support for c in self.claims}
        if SupportType.DIRECT_EXPERIMENTAL in supports and self.interacting_region_mapped:
            return Involvement.DIRECT
        if supports & {SupportType.DIRECT_EXPERIMENTAL, SupportType.GENETIC_FUNCTIONAL}:
            return Involvement.INDIRECT
        if SupportType.COMPUTATIONAL_PREDICTION in supports:
            return Involvement.PREDICTED
        return Involvement.UNKNOWN

    @property
    def ready_for_structural_modeling(self) -> bool:
        return self.involvement is Involvement.DIRECT


class EnrichmentEvidence(BaseModel):
    """Optional evidence. Missing values remain missing and never improve rank."""
    literature_support: float | None = Field(default=None, ge=0, le=1)
    normal_cell_support: float | None = Field(default=None, ge=0, le=1)
    interface_support: float | None = Field(default=None, ge=0, le=1)
    tractability_support: float | None = Field(default=None, ge=0, le=1)
    notes: list[str] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)

    @model_validator(mode="after")
    def scores_require_claims(self) -> "EnrichmentEvidence":
        """A number with no source is the thing this project exists to reject."""
        scored = [n for n in ("literature_support", "normal_cell_support",
                              "interface_support", "tractability_support")
                  if getattr(self, n) is not None]
        if scored and not self.claims:
            raise ValueError(f"enrichment scores {scored} set with no supporting claims")
        return self


class GateResult(BaseModel):
    eligible: bool
    failures: list[str]


class RankedCandidate(BaseModel):
    dependency: DependencyEvidence
    enrichment: EnrichmentEvidence = Field(default_factory=EnrichmentEvidence)
    gate: GateResult
    discovery_score: float = Field(ge=0, le=1)
    enrichment_score: float | None = Field(default=None, ge=0, le=1)
    evidence_completeness: float = Field(ge=0, le=1)
    final_score: float = Field(ge=0, le=1)
    mediator: MediatorLink = Field(default_factory=MediatorLink)


class Shortlist(BaseModel):
    """Stage-1 handoff. Stops before structural modeling, by construction."""
    model_config = ConfigDict(extra="forbid")
    partner_gene: str = "MED23"
    disease_scope: str
    candidates: list[RankedCandidate]          # A + B + C
    shortlist_indices: list[int]               # D: top candidates for human review
    sources: list[SourceRecord] = Field(default_factory=list)
    stops_before: Literal["structural_modeling"] = "structural_modeling"
    awaiting_review: list[str] = Field(
        default_factory=lambda: ["TF shortlist (Kevin + Andrey)",
                                 "Mediator connection (Andrey)"])

    @model_validator(mode="after")
    def shortlist_is_reviewable(self) -> "Shortlist":
        for i in self.shortlist_indices:
            if not 0 <= i < len(self.candidates):
                raise ValueError(f"shortlist index {i} is outside the candidate table")
            if not self.candidates[i].gate.eligible:
                raise ValueError(
                    f"shortlisted candidate {self.candidates[i].dependency.gene} failed its "
                    f"dependency gate: {self.candidates[i].gate.failures}")
        return self


class SearchBoxCoordinates(BaseModel):
    mode: Literal["coordinates"] = "coordinates"
    center: tuple[float, float, float]
    size: tuple[float, float, float]


class ReferenceLigandBox(BaseModel):
    mode: Literal["reference_ligand"] = "reference_ligand"
    reference_ligand_path: str
    padding: float = Field(default=4.0, gt=0)


class ProtoScreenSpec(BaseModel):
    """Typed boundary between target discovery and Proto execution."""
    model_config = ConfigDict(extra="forbid")
    candidate_gene: str
    disease_context: str
    partner_gene: str
    structure_source: Literal["pdb", "alphafold_db", "boltz2"]
    pdb_id: str | None = None
    uniprot_accessions: list[str] = Field(default_factory=list)
    receptor_path: str | None = None
    interface_residues: dict[str, list[int]] = Field(default_factory=dict)
    search_box: SearchBoxCoordinates | ReferenceLigandBox | None = None
    ligand_smiles: list[str] = Field(default_factory=list)
    tools: list[Literal["pdb-fetch-entry", "alphafold-db-fetch", "boltz2-prediction", "ipsae", "pdockq2", "vina-docking", "boltz2-affinity"]]
    public_evidence_urls: list[str] = Field(min_length=1)
    hypothesis_only: bool = True

    @model_validator(mode="after")
    def enforce_auditable_docking(self) -> "ProtoScreenSpec":
        if self.structure_source == "pdb" and not self.pdb_id and not self.receptor_path:
            raise ValueError("PDB source requires pdb_id or receptor_path")
        if "vina-docking" in self.tools:
            if not self.receptor_path:
                raise ValueError("Vina requires a prepared public receptor_path")
            if not self.search_box:
                raise ValueError("Vina requires an explicit or reference-ligand search box")
            if not self.ligand_smiles:
                raise ValueError("Vina requires at least one public ligand SMILES")
        return self


class NextEvidenceAction(BaseModel):
    priority: int = Field(ge=1)
    action: Literal["paperclip_search", "depmap_verify", "structure_lookup", "interface_verify", "ligand_fetch", "proto_execute"]
    query: str
    reason: str
    stop_condition: str
