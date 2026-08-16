"""Validate and compile structural specs against installed Proto input models."""
from pathlib import Path
from typing import Any
from proto_tools.tools.molecular_docking.vina import VinaDockingInput, VinaReferenceLigandBox, VinaSearchBox
from .models import ProtoScreenSpec, ReferenceLigandBox, SearchBoxCoordinates

PROTO_PREFIX = "proto_tools:"


def resolve_path(path: str | Path) -> Path:
    """Expand `proto_tools:<relative>` to the installed package location.

    Assets that ship inside proto_tools live at a different absolute path
    depending on whether it was pip-installed or checked out under vendor/, and
    on whose machine it is. Committing either one guarantees the spec breaks for
    everyone else, so specs name the asset and this resolves it per machine.
    """
    s = str(path)
    if not s.startswith(PROTO_PREFIX):
        return Path(s)
    import proto_tools
    return Path(proto_tools.__file__).parent / s[len(PROTO_PREFIX):]


def validate_proto_spec(spec: ProtoScreenSpec) -> dict[str, Any]:
    compiled: dict[str, Any] = {"tools": spec.tools, "ready": True, "blockers": []}
    if "vina-docking" not in spec.tools:
        compiled["ready"] = False
        compiled["blockers"].append("vina-docking is not requested")
        return compiled
    assert spec.receptor_path is not None and spec.search_box is not None
    receptor = resolve_path(spec.receptor_path)
    if not receptor.exists():
        compiled["ready"] = False
        compiled["blockers"].append(f"receptor does not exist: {receptor}")
        return compiled
    if isinstance(spec.search_box, SearchBoxCoordinates):
        box = VinaSearchBox(center=spec.search_box.center, size=spec.search_box.size)
    elif isinstance(spec.search_box, ReferenceLigandBox):
        ligand_path = resolve_path(spec.search_box.reference_ligand_path)
        if not ligand_path.exists():
            compiled["ready"] = False
            compiled["blockers"].append(f"reference ligand does not exist: {ligand_path}")
            return compiled
        box = VinaReferenceLigandBox(reference_ligand=ligand_path, padding=spec.search_box.padding)
    else:
        raise TypeError("Unsupported search box")
    native = VinaDockingInput(receptor=receptor, ligands=spec.ligand_smiles, search_box=box)
    compiled["vina_input"] = native.model_dump(mode="json", exclude_none=True)
    compiled["proto_contract"] = "proto_tools.tools.molecular_docking.vina.VinaDockingInput"
    return compiled
