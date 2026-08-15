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


class EnrichmentEvidence(BaseModel):
    """Optional evidence. Missing values remain missing and never improve rank."""
    literature_support: float | None = Field(default=None, ge=0, le=1)
    normal_cell_support: float | None = Field(default=None, ge=0, le=1)
    interface_support: float | None = Field(default=None, ge=0, le=1)
    tractability_support: float | None = Field(default=None, ge=0, le=1)
    notes: list[str] = Field(default_factory=list)


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
