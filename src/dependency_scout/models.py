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


class InterfaceTractability(StrEnum):
    """Whether a mapped interface is a realistic small-molecule site.

    `interacting_region_mapped` says we know *where* two proteins touch. It says
    nothing about whether a compound could wedge in there, so a short motif in a
    groove and a large domain-domain interface both read as mapped. They are not
    equally druggable, and without this the second passes a gate it should fail.
    """
    SHORT_LINEAR_MOTIF = "short_linear_motif"  # peptide motif in a defined groove
    FOLDED_DOMAIN = "folded_domain"            # large domain-domain interface
    UNKNOWN = "unknown"


class MediatorLink(BaseModel):
    """The TF-to-Mediator-subunit contact. Deliverable C of the stage-1 review."""
    model_config = ConfigDict(extra="forbid")
    partner_gene: str = "MED23"
    interacting_region_mapped: bool = False
    tf_region: str | None = None  # e.g. "activation domain, residues 1-89"
    claims: list[Claim] = Field(default_factory=list)
    tractability: InterfaceTractability = InterfaceTractability.UNKNOWN
    calibration_only: bool = False
    """A known positive used to calibrate the gates. Never a result."""

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

    @property
    def screening_concerns(self) -> list[str]:
        """Reasons a structurally valid contact may still be a poor drug target.

        Deliberately advisory, not a gate: the humans in CHECKPOINTS.md decide
        what proceeds. The point is that it is stated rather than discovered
        after the docking run.
        """
        concerns = []
        if self.tractability is InterfaceTractability.FOLDED_DOMAIN:
            concerns.append(
                "folded-domain interface: large buried surface, poor small-molecule "
                "tractability compared with a short linear motif")
        if self.tractability is InterfaceTractability.UNKNOWN and self.interacting_region_mapped:
            concerns.append("interface tractability not assessed")
        if self.calibration_only:
            concerns.append("calibration control, never a result")
        return concerns


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
    """A candidate at any stage of completeness.

    `dependency` is optional because interface evidence and dependency
    quantification are independent and arrive in either order. Requiring seven
    DepMap numbers before a verified interface finding could be recorded meant
    real evidence had nowhere to live until an unrelated stage had run.
    """
    gene: str | None = None
    """Set when there is no `dependency` to carry the gene name."""
    dependency: DependencyEvidence | None = None
    enrichment: EnrichmentEvidence = Field(default_factory=EnrichmentEvidence)
    gate: GateResult | None = None
    discovery_score: float | None = Field(default=None, ge=0, le=1)
    enrichment_score: float | None = Field(default=None, ge=0, le=1)
    evidence_completeness: float = Field(default=0.0, ge=0, le=1)
    final_score: float | None = Field(default=None, ge=0, le=1)
    mediator: MediatorLink = Field(default_factory=MediatorLink)

    @model_validator(mode="after")
    def a_candidate_needs_a_name(self) -> "RankedCandidate":
        if not self.dependency and not self.gene:
            raise ValueError("set `gene` when there is no `dependency` to name it")
        return self

    @property
    def name(self) -> str:
        return self.dependency.gene if self.dependency else (self.gene or "?")

    @property
    def disease_context(self) -> str:
        return self.dependency.disease_context if self.dependency else "not yet quantified"

    @property
    def awaiting_dependency_data(self) -> bool:
        """Not a failure. The dependency stage has not run for this candidate."""
        return self.dependency is None

    @property
    def shortlistable(self) -> tuple[bool, str | None]:
        """Whether this may enter the top-N, and why not if it may not."""
        if self.mediator.calibration_only:
            return False, "calibration control, never a result"
        if self.awaiting_dependency_data:
            return False, "awaiting quantitative dependency data"
        if self.gate and not self.gate.eligible:
            return False, "; ".join(self.gate.failures)
        return True, None


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
            ok, reason = self.candidates[i].shortlistable
            if not ok:
                raise ValueError(
                    f"{self.candidates[i].name} cannot be shortlisted: {reason}")
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
