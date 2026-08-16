"""Structural modelling through Proto, executed on Modal.

Contracts used here were read off the installed packages, not guessed:

- ``proto_tools.tools.structure_prediction.boltz2.Boltz2Input``/``Boltz2Config``
- ``proto_tools.tools.structure_prediction.esmfold2.ESMFold2Input``/``ESMFold2Config``
- ``proto_tools.entities.complex.Complex`` (chains accept ``{"sequence", "id"}``)
- ``proto_tools.modal.client.dispatch_to_modal(key, inputs, config)``
- ``proto_tools.tools.structure_prediction.alphafold2.AlphaFold2Input``/``Config``
- ``proto_tools.modal.tool_map.TOOL_MAP`` keys ``boltz2-prediction`` /
  ``esmfold2-prediction`` / ``alphafold2-prediction``

Model roles are enforced by ``StructuralModelRequest``. Two models predict the
target-partner complex and may vote on the interface: **Boltz2** and
**AlphaFold2**, both of which take the whole complex and, when given one, pair
heterocomplex MSAs. This module does not build or attach one: ``run_boltz2``/
``run_alphafold2`` only ever use an MSA the caller supplies through
``Boltz2Input.msas``/``AlphaFold2Input.msas`` — ``use_msa=True`` in the tool
config does not itself trigger a search on the installed path, it only decides
whether a supplied alignment is honoured. Every prediction dispatched here
therefore runs single-sequence, which is materially weaker for these two
models than an aligned run, and the request records ``use_msa: False`` rather
than asserting an alignment that was never made. The real per-chain alignment
path exists at ``scripts/pou2f3_control.py:build_msas`` (a public ColabFold
search) but is not wired into this orchestration layer. **ESMFold2** checks
monomers or structured domains and is never used as an interface predictor —
it is a single-sequence model, and counting its opinion on an interface it was
never shown would manufacture consensus.

Agreement is judged on overlapping confidence intervals across replicate seeds,
not on point estimates: two runs of a stochastic model differ, and the interval
is what separates real disagreement from replicate noise. Unanimity among these
predictors is still not independent confirmation — they share PDB-derived
training data, so they fail together on the same kinds of interface.

The two chains carry generic roles — ``target`` and ``partner`` — because this
stage is not specific to any one class of target. Any single protein pair the
pipeline has been exercised on is a test case, not its definition.

Nothing here dispatches a paid job unless a human has both resolved the hero
checkpoint and explicitly enabled live execution.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import RunConfig
from .discovery_config import StructureConfig
from .models import (
    CandidateHypothesis,
    ChainSpec,
    ConfidenceInterval,
    Interpretation,
    ModelComparison,
    StructuralModelRequest,
    StructuralModelResult,
)
from .store import canonical_json, content_hash, sha256_text

_logger = logging.getLogger(__name__)

BOLTZ2_TOOL_KEY = "boltz2-prediction"
ESMFOLD2_TOOL_KEY = "esmfold2-prediction"
ALPHAFOLD2_TOOL_KEY = "alphafold2-prediction"

# Models that take the whole complex and may therefore vote on the interface.
# ESMFold2 is deliberately absent: it is a single-sequence monomer model, and
# counting its opinion on an interface it was never shown would manufacture
# consensus rather than measure it.
INTERFACE_MODELS = ("boltz2", "alphafold2")

# Every structural threshold has exactly one definition, in discovery_config.py
# (see its module docstring); a second hardcoded copy here is how the two
# quietly drift apart. One frozen instance is enough: nothing in this module
# constructs a StructureConfig with non-default fields.
_STRUCTURE_CONFIG = StructureConfig()

# Confidence floors used only to phrase the comparison, never to claim
# validation. LOW_CONFIDENCE_PLDDT has no equivalent in StructureConfig, so it
# stays local; LOW_CONFIDENCE_IPTM is read from the canonical source instead of
# restating its value.
LOW_CONFIDENCE_PLDDT = 0.70
LOW_CONFIDENCE_IPTM = _STRUCTURE_CONFIG.iptm_warning_threshold

# Recorded on every completed Boltz2/AlphaFold2 result: this orchestration
# layer runs them single-sequence (see the module docstring for why), and a
# result must say so rather than let a reader assume the co-evolutionary
# signal these models are tuned for was present.
MSA_LIMITATION = (
    "Single-sequence mode: no MSA was built or attached for this prediction. "
    "Boltz2/AlphaFold2 only use an MSA the caller supplies; this "
    "orchestration path does not build one (the real alignment path is "
    "scripts/pou2f3_control.py:build_msas), so this run has no "
    "co-evolutionary signal and is substantially weaker without it."
)


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

    ``chain.role`` is part of the key, so changing the role vocabulary changes
    every address. That is intended: the cache must not serve a payload built
    under a different chain layout. It does mean the shipped fixture cache in
    ``fixtures/structure_cache/`` is renamed by
    ``scripts/build_structure_fixtures.py`` whenever the roles change.
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
    """Build the structural jobs for one candidate.

    By default that is a single Boltz2 complex prediction: Proto prefers Boltz2
    for complexes because it explicitly predicts them, and one good model beats
    a chorus of correlated ones. ``enable_alphafold2`` adds a second interface
    opinion and ``enable_esmfold2_monomer`` adds per-chain folding checks; both
    are off by default and cost a GPU job each per replicate.

    Returns an empty list when sequences are missing, or when the target-partner
    contact has no mapped interacting region. Modelling an unmapped association
    would manufacture an interface the evidence does not support, which is the
    failure mode PROJECT.md names.
    """
    tract = candidate.tractability
    if not (tract.target_sequence and tract.partner_sequence):
        return []
    if config.gates.require_mapped_interacting_region and not (
        candidate.interaction.ready_for_structural_modeling
    ):
        return []

    target_chain = ChainSpec(
        chain_id="A", role="target",
        sequence=tract.target_sequence, uniprot=tract.target_uniprot,
    )
    partner_chain = ChainSpec(
        chain_id="B", role="partner",
        sequence=tract.partner_sequence, uniprot=tract.partner_uniprot,
    )

    device = "modal" if config.allow_live_modal else "cpu"
    # `use_msa: True` here used to assert an alignment that this module never
    # builds or attaches (see the module docstring), so the persisted request
    # recorded a fair Boltz-2/AlphaFold2 run while every dispatch actually ran
    # single-sequence. `use_msa: False` makes that true instead of hiding it,
    # and `msa_source`/`msa_depth_per_chain`/`msa_paths` are logged here because
    # `StructuralModelRequest` has no dedicated field for them (models.py is
    # out of scope for this fix) and `config` is what actually gets persisted.
    no_msa_provenance = {
        "msa_source": None, "msa_depth_per_chain": {}, "msa_paths": [],
    }
    boltz_config = {
        "device": device, "seed": seed, "use_msa": False, "diffusion_samples": 1,
        **no_msa_provenance,
    }
    esm_config = {"device": device, "seed": seed, "model_checkpoint": "esmfold2-fast"}

    af2_config = {
        "device": device, "seed": seed, "use_msa": False,
        "pair_heterocomplex_msas": False, "num_recycles": 3,
        **no_msa_provenance,
    }

    requests = [
        StructuralModelRequest(
            request_id=f"{candidate.candidate_id}-boltz2-complex",
            candidate_id=candidate.candidate_id,
            model="boltz2",
            proto_tool_key=BOLTZ2_TOOL_KEY,
            purpose="complex_interface",
            chains=[target_chain, partner_chain],
            config=boltz_config,
            seed=seed,
            device=device,
            input_hash=_request_hash(
                "boltz2", [target_chain, partner_chain], boltz_config, seed
            ),
            human_approval_checkpoint_id=checkpoint_id,
        )
    ]
    if config.enable_alphafold2:
        # A second independent interface predictor. Independent in architecture
        # and MSA handling, NOT in training data — both learned from the PDB, so
        # their errors correlate and agreement is weaker evidence than two truly
        # independent methods would give. compare_models says so explicitly.
        requests.append(StructuralModelRequest(
            request_id=f"{candidate.candidate_id}-alphafold2-complex",
            candidate_id=candidate.candidate_id,
            model="alphafold2",
            proto_tool_key=ALPHAFOLD2_TOOL_KEY,
            purpose="complex_interface",
            chains=[target_chain, partner_chain],
            config=af2_config,
            seed=seed,
            device=device,
            input_hash=_request_hash(
                "alphafold2", [target_chain, partner_chain], af2_config, seed
            ),
            human_approval_checkpoint_id=checkpoint_id,
        ))
    for chain in (target_chain, partner_chain) if config.enable_esmfold2_monomer else ():
        # Request ids stay derived from the role, so they remain deterministic
        # for a given candidate: "-esmfold2-target" and "-esmfold2-partner".
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


# Keys `build_requests` puts in `request.config` to log MSA provenance on the
# persisted request (see its comment). They describe the request, not the
# tool: Boltz2Config/AlphaFold2Config/ESMFold2Config all set extra="forbid",
# so passing them through as **kwargs raises instead of silently ignoring them.
_CONFIG_PROVENANCE_KEYS = ("msa_source", "msa_depth_per_chain", "msa_paths")


def _tool_config_kwargs(config: dict[str, Any]) -> dict[str, Any]:
    """`request.config` minus the provenance keys, safe to unpack into a Config."""
    return {k: v for k, v in config.items() if k not in _CONFIG_PROVENANCE_KEYS}


def _tool_input_and_config(request: StructuralModelRequest, complex_obj: Any) -> tuple[Any, Any, str]:
    """Build the installed Proto input/config pair for this request's model.

    One place, because a two-branch if/else silently routed AlphaFold2 into the
    ESMFold2 contract and validated its config against the wrong model.
    """
    kwargs = _tool_config_kwargs(request.config)
    if request.model == "boltz2":
        from proto_tools.tools.structure_prediction.boltz2 import (  # noqa: PLC0415
            Boltz2Config, Boltz2Input,
        )
        return (
            Boltz2Input(complexes=[complex_obj]), Boltz2Config(**kwargs),
            "proto_tools.tools.structure_prediction.boltz2.Boltz2Input",
        )
    if request.model == "alphafold2":
        from proto_tools.tools.structure_prediction.alphafold2 import (  # noqa: PLC0415
            AlphaFold2Config, AlphaFold2Input,
        )
        return (
            AlphaFold2Input(complexes=[complex_obj]),
            AlphaFold2Config(**kwargs),
            "proto_tools.tools.structure_prediction.alphafold2.AlphaFold2Input",
        )
    from proto_tools.tools.structure_prediction.esmfold2 import (  # noqa: PLC0415
        ESMFold2Config, ESMFold2Input,
    )
    return (
        ESMFold2Input(complexes=[complex_obj]), ESMFold2Config(**kwargs),
        "proto_tools.tools.structure_prediction.esmfold2.ESMFold2Input",
    )


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
        tool_input, tool_config, contract = _tool_input_and_config(request, complex_obj)
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
    # A live result cached and replayed lost this line, because _dispatch_live
    # attaches it to the returned object but writes only metrics to the cache.
    # A replayed prediction is still a prediction.
    prediction_caveat = "Computational prediction. Not experimental evidence."
    if source != "fixture" and prediction_caveat not in limitations:
        limitations.append(prediction_caveat)
    intervals = {
        metric: ConfidenceInterval.model_validate(raw)
        for metric, raw in (payload.get("confidence_ci") or {}).items()
    }
    return StructuralModelResult(
        request_id=request.request_id,
        candidate_id=request.candidate_id,
        model=request.model,
        confidence_ci=intervals,
        replicate_seeds=list(payload.get("replicate_seeds") or []),
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

    On the live path this runs ``StructureConfig.discovery_diffusion_samples``
    predictions that differ only in seed, and returns one result carrying the
    confidence interval across them — never ``config.structure_replicates``:
    that field stays on ``RunConfig`` because it is hashed into ``config_hash``
    and every existing trace's provenance depends on it staying put, but §7 of
    discovery_config.py is the one place the actual ensemble size is decided,
    and nothing was reading it before this. Replicates cost GPU jobs, so the
    cached and fixture paths serve whatever interval was stored rather than
    re-running anything.
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

    replicates = max(1, _STRUCTURE_CONFIG.discovery_diffusion_samples)
    if replicates == 1:
        return _dispatch_live(request, caches, max_retries=max_retries)

    # Replicate seeds are derived from the request's own seed so the set is
    # deterministic: the same request reproduces the same replicates.
    base_seed = request.seed if request.seed is not None else 0
    results: list[StructuralModelResult] = []
    for index in range(replicates):
        seed = base_seed + index
        replicate_config = {**request.config, "seed": seed}
        replicate = request.model_copy(update={
            "seed": seed,
            "config": replicate_config,
            # Recomputed per replicate, not inherited from the base request.
            # All replicates used to share one input_hash, so every dispatch
            # cached to and overwrote the same entry and the same structure
            # file — only the last seed's coordinates ever survived, and an
            # ensemble could not be reconstructed from disk. Each seed now
            # gets its own address, keyed on the same inputs the hash always
            # used plus the seed that actually varies between them.
            "input_hash": _request_hash(
                request.model, list(request.chains), replicate_config, seed
            ),
            "request_id": (
                request.request_id if index == 0 else f"{request.request_id}-r{index}"
            ),
        })
        outcome = _dispatch_live(replicate, caches, max_retries=max_retries)
        results.append(outcome)
        if outcome.status == "failed":
            # Do not burn the remaining GPU jobs once one has failed; the
            # failure is recorded and the caller decides.
            break

    finished = [r for r in results if r.status in {"cached", "completed"}]
    if not finished:
        return results[0]
    aggregated = aggregate_replicates(finished, level=config.ci_confidence_level)
    aggregated = aggregated.model_copy(update={
        "request_id": request.request_id,
        # The aggregate is addressed by the base request's own hash, distinct
        # from any replicate's — a rerun of this exact request looks up that
        # address at the top of this function, so it must resolve to the
        # interval, not to whichever replicate happened to be cached last.
        "input_hash": request.input_hash,
    })
    if caches:
        caches[0].put(request.input_hash, {
            "confidence": aggregated.confidence,
            "confidence_ci": {
                metric: ci.model_dump(mode="json")
                for metric, ci in aggregated.confidence_ci.items()
            },
            "replicate_seeds": aggregated.replicate_seeds,
            "model_version": aggregated.model_version,
            "proto_tools_version": aggregated.proto_tools_version,
            "output_hash": aggregated.output_hash,
            "artifact_paths": [p for r in finished for p in r.artifact_paths],
            "limitations": aggregated.limitations,
            "unresolved_questions": aggregated.unresolved_questions,
        })
    return aggregated


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
    tool_input, tool_config, _contract = _tool_input_and_config(request, complex_obj)

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
        # A billed GPU prediction used to survive as four scalars: the structure
        # itself was dropped, so the coordinates could never be inspected,
        # re-scored, or handed to the docking stage, and a replay could not
        # reproduce anything but the numbers. Persist the full tool output
        # alongside the metrics, and keep the caveats with it.
        artifact_paths: list[str] = []
        if caches:
            artifact_path = caches[0].root / f"{request.input_hash}.output.json"
            try:
                artifact_path.parent.mkdir(parents=True, exist_ok=True)
                artifact_path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
                artifact_paths.append(str(artifact_path))
            except OSError as exc:  # disk full, permissions - do not lose the run
                _logger.warning("could not persist structural output: %s", exc)

        limitations = ["Computational prediction. Not experimental evidence."]
        if request.model in INTERFACE_MODELS:
            # Only boltz2/alphafold2 make an MSA-dependent accuracy claim;
            # ESMFold2 is a single-sequence model by design and this caveat
            # would be noise on its monomer checks.
            limitations.append(MSA_LIMITATION)

        cache_payload = {
            "confidence": confidence,
            "model_version": request.model,
            "proto_tools_version": proto_tool_versions().get("proto_tools"),
            "runtime_ms": runtime_ms,
            "output_hash": content_hash(payload),
            "artifact_paths": artifact_paths,
            "limitations": limitations,
            "unresolved_questions": [
                "Interface residue identity is not resolved well enough to define "
                "a pocket without further evidence.",
            ],
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
            limitations=limitations,
        )

    return StructuralModelResult(
        request_id=request.request_id, candidate_id=request.candidate_id,
        model=request.model, status="failed", source="none",
        chain_map=chain_map, input_hash=request.input_hash,
        seed=request.seed, config=request.config,
        error=last_error or "dispatch failed with no error recorded",
    )


# The two tools do not name the same quantity the same way. ESMFold2 emits
# `plddt`; Boltz2 emits `complex_plddt` (and `complex_iplddt`) and never a bare
# `plddt` — see proto_tools/tools/structure_prediction/boltz2/boltz2.py. Looking
# only for `plddt` meant every live Boltz2 result had no pLDDT, the whole
# comparison block was skipped, and `compare_models` reported "consistent"
# without having compared anything. Aliases are ordered: first match wins.
_CONFIDENCE_ALIASES: dict[str, tuple[str, ...]] = {
    "plddt": ("plddt", "complex_plddt", "mean_plddt"),
    "iplddt": ("iplddt", "complex_iplddt"),
    "ptm": ("ptm", "complex_ptm"),
    "iptm": ("iptm", "protein_iptm", "complex_iptm"),
    "avg_pae": ("avg_pae", "mean_pae", "pae_mean"),
}


def _extract_confidence(payload: dict[str, Any]) -> dict[str, float]:
    """Pull plddt/ptm/iptm/avg_pae out of a structure-prediction output payload.

    Normalises each tool's spelling onto one canonical name so the comparison
    sees both models' numbers.
    """
    lookup = {
        alias: canonical
        for canonical, aliases in _CONFIDENCE_ALIASES.items()
        for alias in aliases
    }
    ranks = {
        alias: index
        for _, aliases in _CONFIDENCE_ALIASES.items()
        for index, alias in enumerate(aliases)
    }
    found: dict[str, float] = {}
    found_rank: dict[str, int] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                canonical = lookup.get(key)
                if canonical is not None and isinstance(value, int | float):
                    rank = ranks[key]
                    if canonical not in found or rank < found_rank[canonical]:
                        found[canonical] = float(value)
                        found_rank[canonical] = rank
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return found


# ----------------------------------------------------- confidence intervals
def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def confidence_interval(
    metric: str, values: list[float], *, level: float = 0.95
) -> ConfidenceInterval:
    """Interval across replicate predictions of the same request.

    Uses a normal-approximation interval on the mean (mean ± z·SEM). With the
    small replicate counts affordable here — 3 is the default — that is an
    approximation, and ``method`` says so rather than dressing it up. A single
    replicate yields a degenerate interval, which is honest: one stochastic run
    tells you nothing about its own spread.
    """
    n = len(values)
    if n == 0:
        raise ValueError(f"{metric}: no values to summarise")
    point = _mean(values)
    if n == 1:
        return ConfidenceInterval(
            metric=metric, point=point, lo=point, hi=point, n=1,
            method="single replicate; no spread is measurable",
        )
    variance = sum((v - point) ** 2 for v in values) / (n - 1)
    sem = (variance / n) ** 0.5
    # z for the common levels; the table is explicit so the number is auditable.
    z = {0.90: 1.645, 0.95: 1.960, 0.99: 2.576}.get(round(level, 2), 1.960)
    margin = z * sem
    return ConfidenceInterval(
        metric=metric, point=point, lo=point - margin, hi=point + margin, n=n,
        method=(
            f"normal-approximation {int(level * 100)}% interval on the mean "
            f"(mean +/- {z}*SEM) over {n} replicate seeds; small-n approximation"
        ),
    )


def aggregate_replicates(
    results: list[StructuralModelResult], *, level: float = 0.95
) -> StructuralModelResult:
    """Collapse replicate results for one request into one carrying intervals."""
    if not results:
        raise ValueError("no replicate results to aggregate")
    usable = [r for r in results if r.status in {"cached", "completed"} and r.confidence]
    if not usable:
        return results[0]

    metrics: dict[str, list[float]] = {}
    for result in usable:
        for metric, value in result.confidence.items():
            metrics.setdefault(metric, []).append(value)

    intervals = {
        metric: confidence_interval(metric, values, level=level)
        for metric, values in metrics.items()
    }
    return usable[0].model_copy(update={
        "confidence": {m: ci.point for m, ci in intervals.items()},
        "confidence_ci": intervals,
        "replicate_seeds": [r.seed for r in usable if r.seed is not None],
    })


# ------------------------------------------------------------------ comparison
def compare_models(
    candidate_id: str,
    boltz: StructuralModelResult | None,
    esmfold: list[StructuralModelResult],
    alphafold: StructuralModelResult | None = None,
) -> ModelComparison:
    """Compare the interface predictors against each other and the monomer checks.

    Two models vote on the interface: Boltz2 and AlphaFold2, both of which were
    shown the whole complex. ESMFold2 checks whether each chain folds on its own
    and does NOT vote — it never saw the interface, so counting it would
    manufacture a third opinion rather than measure one.

    Agreement is judged by whether the models' confidence intervals overlap, not
    by whether their point estimates happen to be close. Two runs of the same
    stochastic model differ; the interval is what separates a real disagreement
    from replicate noise.
    """
    # Describe only what actually ran. Naming models that were never dispatched
    # reads as if their opinion was sought and reported.
    limitations: list[str] = []

    interface_results = {
        name: result
        for name, result in (("boltz2", boltz), ("alphafold2", alphafold))
        if result is not None
        and result.status in {"cached", "completed"}
        and result.confidence
    }
    usable_esm = [
        r for r in esmfold if r.status in {"cached", "completed"} and r.confidence
    ]

    models_compared = sorted([*interface_results, *(["esmfold2"] if usable_esm else [])])
    if len(interface_results) > 1:
        limitations.append(
            "Interface predictions come from "
            f"{', '.join(sorted(interface_results))}; they share PDB-derived "
            "training data, so agreement between them is correlated."
        )
    if usable_esm:
        limitations.append(
            "ESMFold2 checks whether each chain folds in isolation and does not "
            "vote on the interface."
        )
    confidence_ci = {
        name: dict(result.confidence_ci) for name, result in interface_results.items()
    }

    if not interface_results:
        return ModelComparison(
            candidate_id=candidate_id,
            boltz2_request_id=boltz.request_id if boltz else None,
            esmfold2_request_id=usable_esm[0].request_id if usable_esm else None,
            alphafold2_request_id=alphafold.request_id if alphafold else None,
            models_compared=models_compared,
            interface_models=[],
            interface_models_available=0,
            verdict="insufficient",
            consensus="insufficient",
            limitations=[
                *limitations,
                "No interface model produced confidence metrics, so nothing about "
                "the interface could be compared.",
            ],
        )

    agreements: list[str] = []
    disagreements: list[str] = []
    delta: dict[str, float] = {}
    ci_overlap: dict[str, bool] = {}

    # -- do the interface models agree with each other? ----------------------
    if len(interface_results) >= 2:
        names = sorted(interface_results)
        for metric in ("iptm", "plddt", "ptm"):
            present = {
                n: interface_results[n].confidence[metric]
                for n in names if metric in interface_results[n].confidence
            }
            if len(present) < 2:
                continue
            first, second = names[0], names[1]
            delta[f"{metric}_{first}_minus_{second}"] = round(
                present.get(first, 0.0) - present.get(second, 0.0), 4
            )
            intervals = {
                n: interface_results[n].confidence_ci.get(metric) for n in present
            }
            if all(intervals.values()):
                overlap = intervals[first].overlaps(intervals[second])  # type: ignore[union-attr]
                ci_overlap[metric] = overlap
                if overlap:
                    agreements.append(
                        f"{first} and {second} agree on {metric}: intervals overlap "
                        f"({intervals[first].lo:.2f}-{intervals[first].hi:.2f} vs "  # type: ignore[union-attr]
                        f"{intervals[second].lo:.2f}-{intervals[second].hi:.2f})."  # type: ignore[union-attr]
                    )
                else:
                    disagreements.append(
                        f"{first} and {second} disagree on {metric}: intervals are "
                        f"disjoint ({intervals[first].lo:.2f}-{intervals[first].hi:.2f} "  # type: ignore[union-attr]
                        f"vs {intervals[second].lo:.2f}-{intervals[second].hi:.2f})."  # type: ignore[union-attr]
                    )
            else:
                # No replicates, so no interval. Fall back to a point comparison
                # and say that is what happened.
                gap = abs(present[first] - present[second])
                limitations.append(
                    f"{metric} compared as point estimates only (no replicates), so "
                    "replicate noise cannot be separated from real disagreement."
                )
                if gap <= 0.10:
                    agreements.append(
                        f"{first} and {second} report comparable {metric} "
                        f"({present[first]:.2f} vs {present[second]:.2f})."
                    )
                else:
                    disagreements.append(
                        f"{first} and {second} differ on {metric} "
                        f"({present[first]:.2f} vs {present[second]:.2f})."
                    )
    else:
        limitations.append(
            "Only one interface model produced results, so interface agreement "
            "could not be assessed."
        )

    # -- does each interface model clear the confidence floor? ---------------
    # Falling below the floor disqualifies a model's result regardless of how
    # many others ran — that is not a claim about agreement, it is a number
    # failing to clear a floor. Clearing it is different: with a second
    # interface model present, one model's own ipTM reaching the floor is
    # corroborating context alongside the CI-overlap comparison above, so it
    # is recorded as a vote. With only one interface model available there is
    # nothing to corroborate — a lone ipTM clearing a threshold is one
    # stochastic scalar, and counting it as a vote/agreement let verdict reach
    # "consistent" on that scalar alone with no comparison ever made. That
    # case is recorded as a note instead.
    votes = 0
    for name, result in sorted(interface_results.items()):
        iptm = result.confidence.get("iptm")
        if iptm is None:
            limitations.append(f"{name} returned no ipTM, so interface confidence is unknown.")
            continue
        interval = result.confidence_ci.get("iptm")
        if interval is not None and interval.hi < LOW_CONFIDENCE_IPTM:
            disagreements.append(
                f"{name} interface confidence is low across replicates "
                f"(ipTM {interval.lo:.2f}-{interval.hi:.2f}, entirely below "
                f"{LOW_CONFIDENCE_IPTM})."
            )
        elif iptm < LOW_CONFIDENCE_IPTM:
            disagreements.append(
                f"{name} interface confidence is low (ipTM {iptm:.2f}); the "
                "predicted interface is not a reliable basis for a pocket."
            )
        elif len(interface_results) >= 2:
            votes += 1
            agreements.append(f"{name} reports interface confidence ipTM {iptm:.2f}.")
        else:
            limitations.append(
                f"{name} reports interface confidence ipTM {iptm:.2f}, clearing "
                f"the {LOW_CONFIDENCE_IPTM} floor, but that is a single model's "
                "own score with nothing to corroborate it, so it is recorded as "
                "a note rather than a vote or an agreement."
            )

    # -- monomer sanity, which informs but does not vote ---------------------
    # Goes to `limitations`, never `disagreements`: `disagreements` drives the
    # interface verdict below, and ESMFold2 never saw the interface. It used
    # to land in `disagreements` and flip a converged interface prediction to
    # "inconsistent" on a monomer's own confidence — exactly the manufactured
    # consensus this module's docstring and compare_models' docstring both say
    # ESMFold2 must not produce. These targets are disordered activation
    # domains, so a low isolated-chain pLDDT is closer to the default outcome
    # than the exception.
    for result in usable_esm:
        plddt = result.confidence.get("plddt")
        if plddt is not None and plddt < LOW_CONFIDENCE_PLDDT:
            role = next(iter(result.chain_map.values()), result.request_id)
            limitations.append(
                f"ESMFold2 monomer confidence for the {role} is low "
                f"(pLDDT {plddt:.2f}); that chain may be disordered in isolation."
            )

    available = len(interface_results)
    if available == 0:
        consensus = "insufficient"
    elif votes == available and available >= 2:
        consensus = "unanimous"
    elif votes == 0:
        consensus = "split" if available >= 2 else "insufficient"
    elif votes >= 2 or (votes == 1 and available == 1):
        consensus = "majority" if available >= 2 else "insufficient"
    else:
        consensus = "split"

    if disagreements:
        verdict = "inconsistent"
    elif agreements:
        verdict = "consistent"
    else:
        verdict = "insufficient"
        limitations.append(
            "No comparable confidence metric was present on both models, so no "
            "agreement or disagreement could be established."
        )

    if consensus == "unanimous":
        limitations.append(
            "Unanimous agreement among interface predictors is not independent "
            "confirmation: these models share training data drawn from the PDB, "
            "so they fail together on the same kinds of interface."
        )

    return ModelComparison(
        candidate_id=candidate_id,
        boltz2_request_id=boltz.request_id if boltz else None,
        esmfold2_request_id=usable_esm[0].request_id if usable_esm else None,
        alphafold2_request_id=alphafold.request_id if alphafold else None,
        models_compared=models_compared,
        interface_models=sorted(interface_results),
        interface_models_available=available,
        interface_votes=votes,
        agreements=agreements,
        disagreements=disagreements,
        confidence_delta=delta,
        confidence_ci=confidence_ci,
        ci_overlap=ci_overlap,
        consensus=consensus,
        interpretation=Interpretation.PREDICTED,
        verdict=verdict,
        limitations=limitations,
    )
