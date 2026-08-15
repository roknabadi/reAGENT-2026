"""BenchFlow trace export.

The export target is the **opentraces** format, which the installed BenchFlow
(0.6.8) parses natively:

- ``benchflow.traces.parsers.parse_opentraces_record`` / ``parse_opentraces_file``
- ``benchflow.cli.trace_import._detect_format`` recognises a record by
  ``schema_version``, or by carrying both ``agent`` and ``steps``
- consumed by ``benchflow tasks generate --format opentraces``

No competing format is invented here. Records use the TAO step model
(thought / action / observation) that the parser expects, and carry the internal
event ID and artifact paths on each step so the exported trace stays linked to
``traces/internal_trace.jsonl`` and the run directory.

BenchFlow is installed in its own virtualenv (``tools/benchflow``), and the repo
contains a ``benchflow/`` directory that shadows the package name, so validation
runs through that interpreter rather than importing it here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import TraceEvent, TraceManifest
from .store import RunStore, redact, sha256_file, utc_now

OPENTRACES_SCHEMA_VERSION = "0.3"
TRACE_FORMAT = "opentraces"
# The frozen BenchFlow task's own identity. These two strings, the fallback task
# prompt, and the task tags below name that task, not this pipeline's subject
# matter: the task is frozen and agent runs are compared against it by id, so
# generalising the names here would silently split the comparison. Everything
# else in this package is target-agnostic; this boundary keeps its names.
DEFAULT_TASK_ID = "reagent/tf-mediator-hero"
AGENT_NAME = "reagent-tf-mediator-workflow"

# Steps whose observation is a status line rather than tool output.
_STATUS_ONLY = {"run.started", "run.completed", "run.resumed", "stage.started"}


def find_benchflow_python(repo_root: Path | None = None) -> Path | None:
    """Locate the interpreter that can import BenchFlow, or None."""
    override = os.environ.get("BENCHFLOW_PYTHON")
    if override and Path(override).exists():
        return Path(override)
    root = repo_root or Path.cwd()
    candidate = root / "tools" / "benchflow" / "bin" / "python"
    if candidate.exists():
        return candidate
    which = shutil.which("benchflow")
    if which:
        sibling = Path(which).parent / "python"
        if sibling.exists():
            return sibling
    return None


def benchflow_version(repo_root: Path | None = None) -> str | None:
    python = find_benchflow_python(repo_root)
    if python is None:
        return None
    result = subprocess.run(
        [str(python), "-c", "import benchflow; print(benchflow.__version__)"],
        capture_output=True, text=True, timeout=60, check=False,
    )
    return result.stdout.strip() or None


# --------------------------------------------------------------------- export
def _step_from_event(event: TraceEvent, index: int) -> dict[str, Any]:
    """One internal event becomes one TAO step.

    ``thought`` carries the reasoning-level summary, ``action.tool_call`` the
    typed operation, ``observation`` the result. Internal IDs and artifact paths
    ride along so the exported trace remains traceable back to the run.
    """
    detail = redact(event.detail or {})
    thought_bits = [f"{event.stage}: {event.event_type}"]
    if event.evidence_ids:
        thought_bits.append(f"evidence={','.join(event.evidence_ids[:8])}")
    if event.error:
        thought_bits.append(f"error={event.error}")

    observation_parts = [f"status={event.status}"]
    if event.output_refs:
        observation_parts.append(f"artifacts={','.join(event.output_refs)}")
    if detail and event.event_type not in _STATUS_ONLY:
        observation_parts.append(json.dumps(detail, default=str, sort_keys=True))

    return {
        "step_id": index,
        "timestamp": event.timestamp,
        "thought": " | ".join(thought_bits),
        "action": {
            "tool_call": {
                "name": event.event_type,
                "input": {
                    "stage": event.stage,
                    "actor": event.actor,
                    "input_refs": event.input_refs,
                    "evidence_ids": event.evidence_ids,
                    **({"model": event.model} if event.model else {}),
                    **({"compute": event.compute} if event.compute else {}),
                },
            }
        },
        "observation": {
            "content": " ".join(observation_parts),
            "status": event.status,
        },
        # Link back into the run directory. Ignored by the parser, kept for audit.
        "internal_event_id": event.event_id,
        "parent_event_id": event.parent_event_id,
        "output_refs": event.output_refs,
        "latency_ms": event.latency_ms,
        "token_estimate": event.token_estimate,
    }


def build_opentraces_record(
    store: RunStore,
    events: list[TraceEvent],
    *,
    task_id: str = DEFAULT_TASK_ID,
    agent_name: str = AGENT_NAME,
    model: str | None = None,
    task_prompt: str | None = None,
) -> dict[str, Any]:
    """Build the single opentraces record describing this run."""
    state = store.load_state()
    first, last = (events[0], events[-1]) if events else (None, None)

    token_estimate = sum(e.token_estimate or 0 for e in events)
    token_usage = sum(e.token_usage or 0 for e in events)

    failed = any(e.status in {"failed", "error"} for e in events)
    if state.status == "completed":
        outcome_status = "success"
    elif state.status in {"abstained", "awaiting_human"}:
        outcome_status = "incomplete"
    elif state.status == "failed" or failed:
        outcome_status = "failure"
    else:
        outcome_status = "unknown"

    return {
        "schema_version": OPENTRACES_SCHEMA_VERSION,
        "trace_id": f"{state.run_id}-{TRACE_FORMAT}",
        "session_id": state.run_id,
        "agent": {"name": agent_name, "version": "1.0", "model": model},
        "environment": {
            "cwd": str(store.run_dir),
            "vcs": {
                "branch": os.environ.get("GIT_BRANCH", ""),
                "remote": "",
                "commit": state.git_commit or "",
            },
            "config_hash": state.config_hash,
        },
        "task": {
            "id": task_id,
            "input": task_prompt or (
                "Select one public-data-supported disease-TF-Mediator hypothesis, "
                "model the interface, and propose the next experiment."
            ),
            "tags": ["biology", "transcription", "mediator", "provenance"]
            + (["fixture"] if state.fixture_run else []),
        },
        "timestamp_start": first.timestamp if first else utc_now(),
        "timestamp_end": last.timestamp if last else utc_now(),
        "steps": [_step_from_event(event, i) for i, event in enumerate(events, start=1)],
        "outcome": {
            "status": outcome_status,
            "hero_candidate_id": state.hero_candidate_id,
            "fixture_run": state.fixture_run,
        },
        "metrics": {
            "tokens": {"input": token_usage, "output": 0},
            "token_estimate": token_estimate,
            "estimated": True,
            "steps": len(events),
        },
        "internal_trace": {
            "path": store.relative(store.internal_trace_path),
            "sha256": (
                sha256_file(store.internal_trace_path)
                if store.internal_trace_path.exists() else None
            ),
            "event_count": len(events),
        },
    }


def export_trace(
    store: RunStore,
    *,
    task_id: str = DEFAULT_TASK_ID,
    agent_name: str = AGENT_NAME,
    model: str | None = None,
) -> Path:
    """Write ``traces/benchflow_trace.jsonl``. Rewritten in full each export."""
    events = [
        TraceEvent.model_validate(record)
        for record in store.read_jsonl(store.internal_trace_path)
    ]
    if not events:
        raise ValueError(
            f"no internal trace events for run {store.run_id}; run the workflow first"
        )
    record = build_opentraces_record(
        store, events, task_id=task_id, agent_name=agent_name, model=model
    )
    store.write_atomic(
        store.benchflow_trace_path,
        json.dumps(redact(record), default=str) + "\n",
    )
    return store.benchflow_trace_path


# ----------------------------------------------------------------- validation
@dataclass
class ValidationResult:
    ok: bool
    record_count: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    benchflow_version: str | None = None
    detected_format: str | None = None
    parsed_steps: int | None = None


REQUIRED_TOP_LEVEL = ("schema_version", "trace_id", "agent", "steps", "task", "outcome")


def validate_record(record: dict[str, Any], index: int) -> list[str]:
    """Structural validation of one JSONL record against the opentraces shape."""
    errors: list[str] = []
    prefix = f"record {index}"

    for key in REQUIRED_TOP_LEVEL:
        if key not in record:
            errors.append(f"{prefix}: missing required field {key!r}")

    # _detect_format routes on this; getting it wrong sends the file to the
    # claude-code parser, which silently yields zero traces.
    if "schema_version" not in record and not (
        "agent" in record and "steps" in record
    ):
        errors.append(
            f"{prefix}: would not be detected as opentraces by BenchFlow"
        )

    agent = record.get("agent")
    if not isinstance(agent, dict) or not agent.get("name"):
        errors.append(f"{prefix}: agent.name is required")

    steps = record.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append(f"{prefix}: steps must be a non-empty list")
    else:
        for position, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                errors.append(f"{prefix} step {position}: not an object")
                continue
            if step.get("step_id") != position:
                errors.append(
                    f"{prefix} step {position}: step_id {step.get('step_id')!r} is "
                    "not sequential from 1"
                )
            if not step.get("internal_event_id"):
                errors.append(
                    f"{prefix} step {position}: lost its link to the internal trace"
                )
            action = step.get("action")
            if action is not None:
                if not isinstance(action, dict):
                    errors.append(f"{prefix} step {position}: action must be an object")
                else:
                    call = action.get("tool_call", action)
                    if not isinstance(call, dict) or not (
                        call.get("name") or call.get("tool")
                    ):
                        errors.append(
                            f"{prefix} step {position}: action.tool_call needs a name"
                        )
            observation = step.get("observation")
            if observation is not None and not isinstance(observation, dict | str):
                errors.append(
                    f"{prefix} step {position}: observation must be an object or string"
                )

    outcome = record.get("outcome")
    if isinstance(outcome, dict):
        status = outcome.get("status")
        if status not in {"success", "completed", "failure", "error", "failed",
                          "incomplete", "unknown"}:
            errors.append(f"{prefix}: outcome.status {status!r} is not recognised")
    else:
        errors.append(f"{prefix}: outcome must be an object")

    blob = json.dumps(record, default=str)
    for marker in ("hf_", "sk-", "ghp_", "BEGIN PRIVATE KEY"):
        if marker in blob and "[REDACTED]" not in blob:
            errors.append(f"{prefix}: possible credential material ({marker})")
    return errors


def validate_file(path: Path) -> ValidationResult:
    """Validate every record in the exported JSONL."""
    if not path.exists():
        return ValidationResult(False, errors=[f"{path} does not exist"])
    errors: list[str] = []
    count = 0
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        count += 1
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"record {index}: invalid JSON: {exc}")
            continue
        if not isinstance(record, dict):
            errors.append(f"record {index}: not a JSON object")
            continue
        errors.extend(validate_record(record, index))
    if count == 0:
        errors.append("trace file contains no records")
    return ValidationResult(ok=not errors, record_count=count, errors=errors)


_BENCHFLOW_VALIDATE_SNIPPET = """
import json, sys
from pathlib import Path
import benchflow
from benchflow.traces.parsers import parse_opentraces_file
from benchflow.cli.trace_import import _detect_format

path = Path(sys.argv[1])
detected = _detect_format(path)
traces = parse_opentraces_file(path)
print(json.dumps({
    "benchflow_version": benchflow.__version__,
    "detected_format": detected,
    "trace_count": len(traces),
    "steps": sum(len(t.steps) for t in traces),
    "trace_ids": [t.trace_id for t in traces],
    "outcomes": [t.outcome for t in traces],
}))
"""


def validate_with_benchflow(
    path: Path, repo_root: Path | None = None
) -> ValidationResult:
    """Validate the file with the installed BenchFlow parser itself."""
    local = validate_file(path)
    python = find_benchflow_python(repo_root)
    if python is None:
        local.warnings.append(
            "BenchFlow interpreter not found; set BENCHFLOW_PYTHON or install "
            "tools/benchflow. Structural validation ran, BenchFlow parsing did not."
        )
        local.ok = False
        local.errors.append("BenchFlow validation could not run")
        return local

    result = subprocess.run(
        [str(python), "-c", _BENCHFLOW_VALIDATE_SNIPPET, str(path)],
        capture_output=True, text=True, timeout=300, check=False,
    )
    if result.returncode != 0:
        local.ok = False
        local.errors.append(
            f"BenchFlow rejected the trace: {result.stderr.strip()[-800:]}"
        )
        return local

    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        local.ok = False
        local.errors.append(f"could not read BenchFlow output: {result.stdout[-400:]}")
        return local

    local.benchflow_version = payload["benchflow_version"]
    local.detected_format = payload["detected_format"]
    local.parsed_steps = payload["steps"]

    if payload["detected_format"] != TRACE_FORMAT:
        local.ok = False
        local.errors.append(
            f"BenchFlow detected format {payload['detected_format']!r}, "
            f"expected {TRACE_FORMAT!r}"
        )
    if payload["trace_count"] != local.record_count:
        local.ok = False
        local.errors.append(
            f"BenchFlow parsed {payload['trace_count']} traces from "
            f"{local.record_count} records"
        )
    if not payload["steps"]:
        local.ok = False
        local.errors.append("BenchFlow parsed the trace but found no steps")
    return local


# ------------------------------------------------------------------- manifest
def build_trace_manifest(
    store: RunStore,
    *,
    task_id: str = DEFAULT_TASK_ID,
    agent_name: str = AGENT_NAME,
    model: str | None = None,
    reward: float | None = None,
    repo_root: Path | None = None,
) -> TraceManifest:
    """Manifest for the exported trace: identity, totals, and both hashes."""
    import platform  # noqa: PLC0415

    state = store.load_state()
    events = [
        TraceEvent.model_validate(record)
        for record in store.read_jsonl(store.internal_trace_path)
    ]
    tool_calls: dict[str, int] = {}
    for event in events:
        tool_calls[event.event_type] = tool_calls.get(event.event_type, 0) + 1

    validation = validate_file(store.benchflow_trace_path)

    return TraceManifest(
        run_id=state.run_id,
        task_id=task_id,
        agent=agent_name,
        model=model,
        git_commit=state.git_commit,
        environment={
            "python": platform.python_version(),
            "platform": platform.platform(),
            "config_hash": state.config_hash,
            "fixture_run": str(state.fixture_run),
        },
        started_at=events[0].timestamp if events else None,
        ended_at=events[-1].timestamp if events else None,
        total_input_tokens=sum(e.token_usage or 0 for e in events),
        total_output_tokens=0,
        token_estimate=sum(e.token_estimate or 0 for e in events),
        cost_usd=None,
        tool_calls=tool_calls,
        completion_status=state.status,
        reward=reward,
        internal_trace_sha256=(
            sha256_file(store.internal_trace_path)
            if store.internal_trace_path.exists() else None
        ),
        benchflow_trace_sha256=(
            sha256_file(store.benchflow_trace_path)
            if store.benchflow_trace_path.exists() else None
        ),
        benchflow_version=benchflow_version(repo_root),
        trace_format=TRACE_FORMAT,
        record_count=validation.record_count,
    )
