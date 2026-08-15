"""Structural modelling through Proto, executed on Modal.

Contracts used here were read off the installed packages, not guessed:

- ``proto_tools.tools.structure_prediction.boltz2.Boltz2Input``/``Boltz2Config``
- ``proto_tools.tools.structure_prediction.esmfold2.ESMFold2Input``/``ESMFold2Config``
- ``proto_tools.entities.complex.Complex`` (chains accept ``{"sequence", "id"}``)
- ``proto_tools.modal.client.dispatch_to_modal(key, inputs, config)``
- ``proto_tools.modal.tool_map.TOOL_MAP`` keys ``boltz2-prediction`` /
  ``esmfold2-prediction``

Model roles are enforced by ``StructuralModelRequest``: Boltz2 predicts the
TF-Mediator complex, ESMFold2 checks monomers or structured domains. ESMFold2 is
never used as an interface predictor.

Nothing here dispatches a paid job unless a human has both resolved the hero
checkpoint and explicitly enabled live execution.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import RunConfig
from .models import (
    CandidateHypothesis,
    ChainSpec,
    Interpretation,
    ModelComparison,
    StructuralModelRequest,
    StructuralModelResult,
)
from .store import canonical_json, content_hash, sha256_text

BOLTZ2_TOOL_KEY = "boltz2-prediction"
ESMFOLD2_TOOL_KEY = "esmfold2-prediction"

# Confidence floors used only to phrase the comparison, never to claim validation.
LOW_CONFIDENCE_PLDDT = 0.70
LOW_CONFIDENCE_IPTM = 0.60


class StructureExecutionError(RuntimeError):
    """A structural job failed in a way the caller must record, not swallow."""


@dataclass(frozen=True)
class ProtoAvailability:
    available: bool
    proto_tools_version: str | None = None
    modal_available: bool = False
    detail: str = ""


def proto_availability() -> ProtoAvailability:
    """Report whether the installed Proto stack can be imported. Never raises."""
    try:
        import proto_tools  # noqa: PLC0415
    except ImportError as exc:
        return ProtoAvailability(False, detail=f"proto_tools not importable: {exc}")
    version = getattr(proto_tools, "__version__", None)
    try:
        from proto_tools.modal import client  # noqa: PLC0415, F401
        modal_ok = True
        detail = "proto_tools and modal client importable"
    except ImportError as exc:
        modal_ok = False
        detail = f"modal client unavailable: {exc}"
    return ProtoAvailability(True, version, modal_ok, detail)


def proto_tool_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    try:
        import proto_tools  # noqa: PLC0415
        versions["proto_tools"] = str(getattr(proto_tools, "__version__", "unknown"))
    except ImportError:
        versions["proto_tools"] = "not installed"
    try:
        import modal  # noqa: PLC0415
        versions["modal"] = str(getattr(modal, "__version__", "unknown"))
    except ImportError:
        versions["modal"] = "not installed"
    return versions


# --------------------------------------------------------------------- requests
def _request_hash(
    model: str, chains: list[ChainSpec], config: dict[str, Any], seed: int | None
) -> str:
    """Content address for a structural job.

    Keyed on what changes the answer — model, chain sequences and roles, config,
    seed — so an identical request reuses its cached result.
    """
    return content_hash({
        "model": model,
        "chains": [
            {"chain_id": c.chain_id, "role": c.role, "sequence": c.sequence}
            for c in chains
        ],
        "config": config,
        "seed": seed,
    })


def build_requests(
    candidate: CandidateHypothesis,
    config: RunConfig,
    *,
    checkpoint_id: str | None = None,
    seed: int = 7,
) -> list[StructuralModelRequest]:
    """Build the Boltz2 complex job plus one ESMFold2 monomer check per chain.

    Returns an empty list when sequences are missing: a structural job without
    sequences is not something to guess at.
    """
    tract = candidate.tractability
    if not (tract.tf_sequence and tract.mediator_sequence):
        return []

    tf_chain = ChainSpec(
        chain_id="A", role="transcription_factor",
        sequence=tract.tf_sequence, uniprot=tract.tf_uniprot,
    )
    med_chain = ChainSpec(
        chain_id="B", role="mediator_subunit",
        sequence=tract.mediator_sequence, uniprot=tract.mediator_uniprot,
    )

    device = "modal" if config.allow_live_modal else "cpu"
    boltz_config = {"device": device, "seed": seed, "use_msa": True, "diffusion_samples": 1}
    esm_config = {"device": device, "seed": seed, "model_checkpoint": "esmfold2-fast"}

    requests = [
        StructuralModelRequest(
            request_id=f"{candidate.candidate_id}-boltz2-complex",
            candidate_id=candidate.candidate_id,
            model="boltz2",
            proto_tool_key=BOLTZ2_TOOL_KEY,
            purpose="complex_interface",
            chains=[tf_chain, med_chain],
            config=boltz_config,
            seed=seed,
            device=device,
            input_hash=_request_hash("boltz2", [tf_chain, med_chain], boltz_config, seed),
            human_approval_checkpoint_id=checkpoint_id,
        )
    ]
    for chain in (tf_chain, med_chain):
        requests.append(StructuralModelRequest(
            request_id=f"{candidate.candidate_id}-esmfold2-{chain.role}",
            candidate_id=candidate.candidate_id,
            model="esmfold2",
            proto_tool_key=ESMFOLD2_TOOL_KEY,
            purpose="monomer_confidence",
            chains=[chain],
            config=esm_config,
            seed=seed,
            device=device,
            input_hash=_request_hash("esmfold2", [chain], esm_config, seed),
            human_approval_checkpoint_id=checkpoint_id,
        ))
    return requests


# ------------------------------------------------------------------ validation
def validate_request(request: StructuralModelRequest) -> dict[str, Any]:
    """Compile the request into the real installed Proto input model.

    This is the validation-only path: it proves the request would be accepted by
    ``run_boltz2``/``run_esmfold2`` without dispatching anything.
    """
    report: dict[str, Any] = {
        "request_id": request.request_id,
        "model": request.model,
        "tool_key": request.proto_tool_key,
        "valid": False,
        "blockers": [],
    }

    availability = proto_availability()
    if not availability.available:
        report["blockers"].append(availability.detail)
        return report

    from proto_tools.entities.complex import Complex  # noqa: PLC0415

    complex_obj = Complex(chains=[
        {"sequence": chain.sequence, "id": chain.chain_id} for chain in request.chains
    ])

    try:
        if request.model == "boltz2":
            from proto_tools.tools.structure_prediction.boltz2 import (  # noqa: PLC0415
                Boltz2Config, Boltz2Input,
            )
            tool_input = Boltz2Input(complexes=[complex_obj])
            tool_config = Boltz2Config(**request.config)
            contract = "proto_tools.tools.structure_prediction.boltz2.Boltz2Input"
        else:
            from proto_tools.tools.structure_prediction.esmfold2 import (  # noqa: PLC0415
                ESMFold2Config, ESMFold2Input,
            )
            tool_input = ESMFold2Input(complexes=[complex_obj])
            tool_config = ESMFold2Config(**request.config)
            contract = "proto_tools.tools.structure_prediction.esmfold2.ESMFold2Input"
    except Exception as exc:  # pydantic validation or a changed contract
        report["blockers"].append(f"{type(exc).__name__}: {exc}")
        return report

    # Confirm the tool is actually shipped to a Modal app before promising it.
    try:
        from proto_tools.modal.client import resolve_tool  # noqa: PLC0415
        app, service, method = resolve_tool(request.proto_tool_key)
        report["modal_target"] = {"app": app, "service": service, "method": method}
    except Exception as exc:
        report["blockers"].append(f"tool not deployed: {type(exc).__name__}: {exc}")

    report["valid"] = not report["blockers"]
    report["proto_contract"] = contract
    report["num_chains"] = complex_obj.num_chains()
    report["config_resolved"] = tool_config.model_dump(mode="json")
    report["input_hash"] = request.input_hash
    return report


# ----------------------------------------------------------------- cache layer
class StructureCache:
    """Content-addressed cache of structural results.

    Keyed by ``input_hash``, so a rerun of an identical request never re-dispatches.
    Cached payloads live outside Git.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path_for(self, input_hash: str) -> Path:
        return self.root / f"{input_hash}.json"

    def get(self, input_hash: str) -> dict[str, Any] | None:
        path = self.path_for(input_hash)
        if not path.exists():
            return None
        import json  # noqa: PLC0415
        return json.loads(path.read_text(encoding="utf-8"))

    def put(self, input_hash: str, payload: dict[str, Any]) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path_for(input_hash)
        path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
        return path


def _result_from_cached(
    request: StructuralModelRequest, payload: dict[str, Any], *, source: str
) -> StructuralModelResult:
    confidence = {k: float(v) for k, v in (payload.get("confidence") or {}).items()}
    limitations = list(payload.get("limitations") or [])
    if source == "fixture":
        limitations.insert(0, (
            "SYNTHETIC TEST FIXTURE: these confidence values are demo data for "
            "exercising the workflow, not a real structure prediction."
        ))
    return StructuralModelResult(
        request_id=request.request_id,
        candidate_id=request.candidate_id,
        model=request.model,
        model_version=payload.get("model_version"),
        proto_tools_version=payload.get("proto_tools_version"),
        status="cached",
        source="fixture" if source == "fixture" else "cache",
        confidence=confidence,
        chain_map={c.chain_id: c.role for c in request.chains},
        input_hash=request.input_hash,
        output_hash=payload.get("output_hash") or sha256_text(canonical_json(payload)),
        artifact_paths=list(payload.get("artifact_paths") or []),
        runtime_ms=payload.get("runtime_ms"),
        seed=request.seed,
        config=request.config,
        unresolved_questions=list(payload.get("unresolved_questions") or []),
        limitations=limitations,
    )


# ------------------------------------------------------------------- execution
def execute_request(
    request: StructuralModelRequest,
    config: RunConfig,
    *,
    caches: list[StructureCache],
    approved: bool,
    validation_only: bool = False,
    max_retries: int = 2,
) -> StructuralModelResult:
    """Resolve one structural request.

    Order: cache, then validation-only, then live dispatch. Live dispatch needs
    both human approval and ``allow_live_modal``; without them the result is a
    recorded ``skipped``, not a silent fallback.
    """
    chain_map = {c.chain_id: c.role for c in request.chains}

    for cache in caches:
        payload = cache.get(request.input_hash)
        if payload is not None:
            source = "fixture" if payload.get("fixture") else "cache"
            return _result_from_cached(request, payload, source=source)

    validation = validate_request(request)

    if validation_only:
        return StructuralModelResult(
            request_id=request.request_id, candidate_id=request.candidate_id,
            model=request.model, status="validated_only",
            source="none", chain_map=chain_map, input_hash=request.input_hash,
            proto_tools_version=proto_tool_versions().get("proto_tools"),
            seed=request.seed, config=request.config,
            limitations=[
                "Validation only: the Proto input compiled but no prediction was run.",
                *validation["blockers"],
            ],
        )

    if not approved:
        return StructuralModelResult(
            request_id=request.request_id, candidate_id=request.candidate_id,
            model=request.model, status="skipped", source="none",
            chain_map=chain_map, input_hash=request.input_hash,
            seed=request.seed, config=request.config,
            limitations=["Not executed: the hero checkpoint has not been approved."],
        )

    if not config.allow_live_modal:
        return StructuralModelResult(
            request_id=request.request_id, candidate_id=request.candidate_id,
            model=request.model, status="skipped", source="none",
            chain_map=chain_map, input_hash=request.input_hash,
            seed=request.seed, config=request.config,
            limitations=[
                "Not executed: live Modal dispatch is disabled. Enable it "
                "explicitly (allow_live_modal) to spend compute."
            ],
        )

    if not validation["valid"]:
        return StructuralModelResult(
            request_id=request.request_id, candidate_id=request.candidate_id,
            model=request.model, status="failed", source="none",
            chain_map=chain_map, input_hash=request.input_hash,
            seed=request.seed, config=request.config,
            error="; ".join(validation["blockers"]) or "request failed validation",
        )

    return _dispatch_live(request, caches, max_retries=max_retries)


def _dispatch_live(
    request: StructuralModelRequest, caches: list[StructureCache], *, max_retries: int
) -> StructuralModelResult:
    """Dispatch to Modal through Proto's own client, with bounded retries.

    Retries cover transient dispatch failures only. A validation or
    not-deployed error is terminal — retrying it just spends time.
    """
    from proto_tools.entities.complex import Complex  # noqa: PLC0415
    from proto_tools.modal.client import ModalDispatchError, dispatch_to_modal  # noqa: PLC0415

    chain_map = {c.chain_id: c.role for c in request.chains}
    complex_obj = Complex(chains=[
        {"sequence": c.sequence, "id": c.chain_id} for c in request.chains
    ])
    if request.model == "boltz2":
        from proto_tools.tools.structure_prediction.boltz2 import (  # noqa: PLC0415
            Boltz2Config, Boltz2Input,
        )
        tool_input: Any = Boltz2Input(complexes=[complex_obj])
        tool_config: Any = Boltz2Config(**request.config)
    else:
        from proto_tools.tools.structure_prediction.esmfold2 import (  # noqa: PLC0415
            ESMFold2Config, ESMFold2Input,
        )
        tool_input = ESMFold2Input(complexes=[complex_obj])
        tool_config = ESMFold2Config(**request.config)

    last_error: str | None = None
    for attempt in range(1, max_retries + 1):
        started = time.monotonic()
        try:
            output = dispatch_to_modal(request.proto_tool_key, tool_input, tool_config)
        except ModalDispatchError as exc:
            # Credentials, deployment, and environment errors do not improve on retry.
            last_error = f"{type(exc).__name__}: {exc}"
            break
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt == max_retries:
                break
            time.sleep(min(2 ** attempt, 8))
            continue

        runtime_ms = int((time.monotonic() - started) * 1000)
        payload = output.model_dump(mode="json")
        confidence = _extract_confidence(payload)
        cache_payload = {
            "confidence": confidence,
            "model_version": request.model,
            "proto_tools_version": proto_tool_versions().get("proto_tools"),
            "runtime_ms": runtime_ms,
            "output_hash": content_hash(payload),
        }
        if caches:
            caches[0].put(request.input_hash, cache_payload)
        return StructuralModelResult(
            request_id=request.request_id, candidate_id=request.candidate_id,
            model=request.model, status="completed", source="live_modal",
            confidence=confidence, chain_map=chain_map,
            input_hash=request.input_hash, output_hash=content_hash(payload),
            runtime_ms=runtime_ms, seed=request.seed, config=request.config,
            proto_tools_version=proto_tool_versions().get("proto_tools"),
            limitations=["Computational prediction. Not experimental evidence."],
        )

    return StructuralModelResult(
        request_id=request.request_id, candidate_id=request.candidate_id,
        model=request.model, status="failed", source="none",
        chain_map=chain_map, input_hash=request.input_hash,
        seed=request.seed, config=request.config,
        error=last_error or "dispatch failed with no error recorded",
    )


def _extract_confidence(payload: dict[str, Any]) -> dict[str, float]:
    """Pull plddt/ptm/iptm/avg_pae out of a structure-prediction output payload."""
    wanted = ("plddt", "ptm", "iptm", "avg_pae")
    found: dict[str, float] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in wanted and isinstance(value, int | float) and key not in found:
                    found[key] = float(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return found


# ------------------------------------------------------------------ comparison
def compare_models(
    candidate_id: str,
    boltz: StructuralModelResult | None,
    esmfold: list[StructuralModelResult],
) -> ModelComparison:
    """Compare the complex prediction against the monomer checks.

    The monomer checks say whether each partner has a well-formed structured
    domain. They cannot confirm the interface, and this comparison says so.
    """
    agreements: list[str] = []
    disagreements: list[str] = []
    delta: dict[str, float] = {}
    limitations = [
        "Boltz2 predicts the complex; ESMFold2 checks monomers only.",
        "ESMFold2 is not used as an interface predictor, so it cannot confirm "
        "or refute the predicted interface.",
    ]

    usable_esm = [r for r in esmfold if r.status in {"cached", "completed"} and r.confidence]
    boltz_usable = boltz is not None and boltz.status in {"cached", "completed"} and boltz.confidence

    if not boltz_usable or not usable_esm:
        return ModelComparison(
            candidate_id=candidate_id,
            boltz2_request_id=boltz.request_id if boltz else None,
            esmfold2_request_id=usable_esm[0].request_id if usable_esm else None,
            verdict="insufficient",
            limitations=[
                *limitations,
                "Insufficient results to compare: at least one model produced no "
                "confidence metrics.",
            ],
        )

    assert boltz is not None
    boltz_plddt = boltz.confidence.get("plddt")
    esm_plddts = [r.confidence["plddt"] for r in usable_esm if "plddt" in r.confidence]

    if boltz_plddt is not None and esm_plddts:
        mean_esm = sum(esm_plddts) / len(esm_plddts)
        delta["plddt_boltz2_minus_esmfold2_mean"] = round(boltz_plddt - mean_esm, 4)
        if abs(boltz_plddt - mean_esm) <= 0.10:
            agreements.append(
                f"Per-residue confidence is comparable (Boltz2 pLDDT {boltz_plddt:.2f} vs "
                f"ESMFold2 mean {mean_esm:.2f}); both models find the chains foldable."
            )
        else:
            disagreements.append(
                f"Per-residue confidence differs (Boltz2 pLDDT {boltz_plddt:.2f} vs "
                f"ESMFold2 mean {mean_esm:.2f}); the complex context changes how "
                "confidently at least one chain is placed."
            )
        for result in usable_esm:
            plddt = result.confidence.get("plddt")
            if plddt is not None and plddt < LOW_CONFIDENCE_PLDDT:
                role = next(iter(result.chain_map.values()), result.request_id)
                disagreements.append(
                    f"ESMFold2 monomer confidence for the {role} is low "
                    f"(pLDDT {plddt:.2f}); that chain may be disordered in isolation."
                )

    iptm = boltz.confidence.get("iptm")
    if iptm is None:
        limitations.append("Boltz2 returned no ipTM, so interface confidence is unknown.")
    elif iptm < LOW_CONFIDENCE_IPTM:
        disagreements.append(
            f"Boltz2 interface confidence is low (ipTM {iptm:.2f}); the predicted "
            "interface is not a reliable basis for a pocket definition."
        )
    else:
        agreements.append(f"Boltz2 reports interface confidence ipTM {iptm:.2f}.")

    verdict = "consistent" if not disagreements else "inconsistent"
    return ModelComparison(
        candidate_id=candidate_id,
        boltz2_request_id=boltz.request_id,
        esmfold2_request_id=usable_esm[0].request_id,
        agreements=agreements,
        disagreements=disagreements,
        confidence_delta=delta,
        interpretation=Interpretation.PREDICTED,
        verdict=verdict,
        limitations=limitations,
    )
