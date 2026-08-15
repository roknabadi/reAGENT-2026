"""Validate and compile structural specs against installed Proto input models."""
from pathlib import Path
from typing import Any
from proto_tools.tools.molecular_docking.vina import VinaDockingInput, VinaReferenceLigandBox, VinaSearchBox
from .models import ProtoScreenSpec, ReferenceLigandBox, SearchBoxCoordinates


def validate_proto_spec(spec: ProtoScreenSpec) -> dict[str, Any]:
    compiled: dict[str, Any] = {"tools": spec.tools, "ready": True, "blockers": []}
    if "vina-docking" not in spec.tools:
        compiled["ready"] = False
        compiled["blockers"].append("vina-docking is not requested")
        return compiled
    assert spec.receptor_path is not None and spec.search_box is not None
    if not Path(spec.receptor_path).exists():
        compiled["ready"] = False
        compiled["blockers"].append(f"receptor does not exist: {spec.receptor_path}")
        return compiled
    if isinstance(spec.search_box, SearchBoxCoordinates):
        box = VinaSearchBox(center=spec.search_box.center, size=spec.search_box.size)
    elif isinstance(spec.search_box, ReferenceLigandBox):
        ligand_path = Path(spec.search_box.reference_ligand_path)
        if not ligand_path.exists():
            compiled["ready"] = False
            compiled["blockers"].append(f"reference ligand does not exist: {ligand_path}")
            return compiled
        box = VinaReferenceLigandBox(reference_ligand=ligand_path, padding=spec.search_box.padding)
    else:
        raise TypeError("Unsupported search box")
    native = VinaDockingInput(receptor=Path(spec.receptor_path), ligands=spec.ligand_smiles, search_box=box)
    compiled["vina_input"] = native.model_dump(mode="json", exclude_none=True)
    compiled["proto_contract"] = "proto_tools.tools.molecular_docking.vina.VinaDockingInput"
    return compiled
