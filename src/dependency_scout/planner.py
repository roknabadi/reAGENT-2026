"""Bounded recursive evidence plan; every recursion has an explicit stop condition."""
from .models import NextEvidenceAction, RankedCandidate


def next_actions(candidate: RankedCandidate) -> list[NextEvidenceAction]:
    gene, context = candidate.dependency.gene, candidate.dependency.disease_context
    return [
        NextEvidenceAction(priority=1, action="depmap_verify", query=f"{gene} {context} DepMap dependency", reason="Confirm the discovery statistic in the official public release.", stop_condition="Release version, model IDs, and calculation are recorded."),
        NextEvidenceAction(priority=2, action="paperclip_search", query=f"{gene} dependency {context} transcription coactivator Mediator", reason="Find primary mechanistic evidence and candidate interaction partners.", stop_condition="At least one primary source supports the mechanism, or mark unsupported."),
        NextEvidenceAction(priority=3, action="structure_lookup", query=f"{gene} coactivator complex PDB structure interface", reason="A public complex is needed before structure-based screening.", stop_condition="Resolve a PDB complex/validated model with chain mapping, or abstain."),
        NextEvidenceAction(priority=4, action="interface_verify", query=f"{gene} interface residues mutagenesis coactivator", reason="Docking needs a defensible pocket or interface search region.", stop_condition="Reference ligand or supported interface residues are recorded, or block docking."),
        NextEvidenceAction(priority=5, action="ligand_fetch", query=f"{gene} inhibitors ChEMBL PubChem SMILES", reason="Build a public, provenance-tracked ligand set.", stop_condition="Canonical SMILES and public compound identifiers are recorded."),
        NextEvidenceAction(priority=6, action="proto_execute", query=f"Proto structure QC and Vina screen for {gene}", reason="Run the structural hypothesis through typed Proto tools.", stop_condition="All Proto outputs, configs, seeds, and failures are exported."),
    ]
