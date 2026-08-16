"""Every threshold for the structural-to-drug arm, in one place (spec §37).

Scattered constants are how two parts of a pipeline quietly disagree about what
"converged" means. Nothing downstream may hardcode a cutoff; it reads it here.

Values are operational workflow thresholds, not claims about biological
probability. They decide what the agent is willing to act on, not what is true.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class StructureMode(StrEnum):
    """§2. Exploratory discovery is allowed, but only when it is labelled.

    ESTABLISHED_INTERFACE models a contact that independent evidence already
    supports. EXPLORATORY_INTERFACE asks the model where a region might sit, and
    is permitted only for top-ranked candidates, after human approval, with full
    sequence provenance. Its output interpretation is fixed at
    computational_prediction and no amount of ensemble agreement can raise it.
    """
    ESTABLISHED_INTERFACE = "established_interface"
    EXPLORATORY_INTERFACE = "exploratory_interface"


class StructureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    top_candidates: int = Field(default=3, ge=1, le=5)   # §3: hard ceiling of 5

    # §4 — what to model. The activation region, not the whole TF.
    ad_window_length: int = Field(default=70, ge=50, le=100)
    ad_window_overlap: int = Field(default=35, ge=0)

    # §7 — an ensemble, never one structure
    discovery_diffusion_samples: int = Field(default=5, ge=2, le=25)
    refinement_diffusion_samples: int = Field(default=10, ge=2, le=25)
    write_full_pae: bool = True
    write_full_pde: bool = True

    # §9–11 — consensus
    contact_cutoff_angstrom: float = Field(default=4.5, gt=0)
    min_dominant_cluster_fraction: float = Field(default=0.60, ge=0, le=1)
    preferred_cluster_fraction: float = Field(default=0.80, ge=0, le=1)
    min_contact_occupancy: float = Field(default=0.60, ge=0, le=1)

    # §8 — a reference threshold, never a reason an interaction is true
    iptm_warning_threshold: float = Field(default=0.60, ge=0, le=1)

    # §16 — box derived from coordinates, never hardcoded
    box_padding_angstrom: float = Field(default=5.5, ge=3, le=12)
    max_box_dimension_angstrom: float = Field(default=30.0, gt=0)

    @property
    def ad_window_step(self) -> int:
        return max(1, self.ad_window_length - self.ad_window_overlap)


class ChemistryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    time_budget_minutes: int = Field(default=25, ge=5, le=120)   # §22
    benchmark_ligands: int = Field(default=20, ge=5)             # §23

    fast_vina_target_n: int = Field(default=250, ge=1)
    fast_vina_min_n: int = Field(default=100, ge=1)
    fast_vina_max_n: int = Field(default=500, ge=1)
    runtime_headroom: float = Field(default=0.70, gt=0, le=1)

    # §21 — controls are what make the enrichment falsifiable
    enriched_fraction: float = Field(default=0.70, ge=0, le=1)
    diverse_control_fraction: float = Field(default=0.20, ge=0, le=1)
    counter_hypothesis_fraction: float = Field(default=0.10, ge=0, le=1)

    conformers_per_compound: int = Field(default=3, ge=1, le=20)
    conformer_seed: int = 0xC0FFEE          # §20: fixed, so the proxy repeats

    fast_vina_exhaustiveness: int = Field(default=8, ge=1)       # §24
    fast_vina_poses: int = Field(default=5, ge=1)
    redock_n: int = Field(default=30, ge=1)                      # §27
    redock_exhaustiveness: int = Field(default=32, ge=1)
    redock_poses: int = Field(default=10, ge=1)

    boltz_ligand_n: int = Field(default=8, ge=1, le=20)          # §28
    final_shortlist_n: int = Field(default=5, ge=1, le=10)       # §32

    # §30 — Boltz affinity training domain
    boltz_affinity_max_atoms: int = Field(default=128, ge=1)
    boltz_affinity_caution_atoms: int = Field(default=56, ge=1)

    # §25 — a pose that misses the site fails whatever it scores
    min_interface_residues_contacted: int = Field(default=3, ge=1)
    min_ligand_fraction_in_site: float = Field(default=0.50, ge=0, le=1)

    def library_arms(self, n: int) -> dict[str, int]:
        """Split a library size into its three arms, losing nobody to rounding."""
        enriched = round(n * self.enriched_fraction)
        diverse = round(n * self.diverse_control_fraction)
        return {"enriched": enriched, "diverse_control": diverse,
                "counter_hypothesis": max(0, n - enriched - diverse)}


class DiscoveryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    structure: StructureConfig = Field(default_factory=StructureConfig)
    chemistry: ChemistryConfig = Field(default_factory=ChemistryConfig)
    structure_mode: StructureMode = StructureMode.ESTABLISHED_INTERFACE
    partner_gene: str = "MED23"
    """The coactivator asked about. A default, not a restriction: the pipeline is
    general across target classes and this is one worked test case."""

    @property
    def fractions_sum_to_one(self) -> bool:
        c = self.chemistry
        return abs(c.enriched_fraction + c.diverse_control_fraction
                   + c.counter_hypothesis_fraction - 1.0) < 1e-9
