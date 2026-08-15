"""Append-only internal trace.

Every decision a run makes lands here as one event. The file is the audit
record: replaying it must reconstruct the run without conversation history.
Large artifacts are referenced by path and hash, never inlined.
"""

from __future__ import annotations

import uuid
from typing import Any

from .models import TraceEvent
from .store import RunStore, redact, utc_now

# Payloads are summaries, not content. Anything longer is a file reference.
MAX_DETAIL_CHARS = 2000


class EventType:
    """Stable event-type vocabulary. Traces are compared across runs."""

    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_ABSTAINED = "run.abstained"
    RUN_RESUMED = "run.resumed"
    STAGE_STARTED = "stage.started"
    STAGE_COMPLETED = "stage.completed"
    STAGE_FAILED = "stage.failed"
    EVIDENCE_ACCEPTED = "evidence.accepted"
    EVIDENCE_REJECTED = "evidence.rejected"
    CANDIDATE_GATED = "candidate.gated"
    CANDIDATE_REJECTED = "candidate.rejected"
    CANDIDATE_SCORED = "candidate.scored"
    CHECKPOINT_CREATED = "checkpoint.created"
    CHECKPOINT_RESOLVED = "checkpoint.resolved"
    PROTO_REQUEST_VALIDATED = "proto.request_validated"
    PROTO_REQUEST_INVALID = "proto.request_invalid"
    MODAL_DISPATCHED = "modal.dispatched"
    MODAL_COMPLETED = "modal.completed"
    MODAL_FAILED = "modal.failed"
    STRUCTURE_CACHE_HIT = "structure.cache_hit"
    BOLTZ2_OUTPUT = "structure.boltz2_output"
    ESMFOLD2_OUTPUT = "structure.esmfold2_output"
    MODEL_COMPARISON = "structure.model_comparison"
    IMPROVEMENT_ITERATION = "improvement.iteration"
    CONTEXT_COMPACTED = "context.compacted"
    CONTEXT_REHYDRATED = "context.rehydrated"
    CONTEXT_LIMIT_EXCEEDED = "context.limit_exceeded"
    NEXT_EXPERIMENT = "experiment.proposed"
    REPORT_WRITTEN = "report.written"
    TRACE_EXPORTED = "trace.exported"
    ERROR = "error"


def _truncate(detail: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in detail.items():
        if isinstance(value, str) and len(value) > MAX_DETAIL_CHARS:
            out[key] = value[:MAX_DETAIL_CHARS] + f"...[truncated {len(value)} chars]"
        else:
            out[key] = value
    return out


class Tracer:
    """Writes trace events for one run."""

    def __init__(
        self,
        store: RunStore,
        *,
        config_hash: str,
        git_commit: str | None,
        actor: str = "reagent-workflow",
    ) -> None:
        self.store = store
        self.config_hash = config_hash
        self.git_commit = git_commit
        self.actor = actor
        self._last_event_id: str | None = None

    def new_event_id(self) -> str:
        return f"ev-{uuid.uuid4().hex[:16]}"

    def emit(
        self,
        *,
        stage: str,
        event_type: str,
        status: str = "ok",
        parent_event_id: str | None = None,
        actor: str | None = None,
        input_refs: list[str] | None = None,
        output_refs: list[str] | None = None,
        evidence_ids: list[str] | None = None,
        model: str | None = None,
        token_usage: int | None = None,
        token_estimate: int | None = None,
        latency_ms: int | None = None,
        compute: str | None = None,
        error: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> TraceEvent:
        event = TraceEvent(
            timestamp=utc_now(),
            run_id=self.store.run_id,
            event_id=self.new_event_id(),
            parent_event_id=parent_event_id,
            stage=str(stage),
            event_type=event_type,
            actor=actor or self.actor,
            input_refs=input_refs or [],
            output_refs=output_refs or [],
            evidence_ids=evidence_ids or [],
            config_hash=self.config_hash,
            git_commit=self.git_commit,
            model=model,
            token_usage=token_usage,
            token_estimate=token_estimate,
            latency_ms=latency_ms,
            compute=compute,
            status=status,
            error=error,
            detail=_truncate(redact(detail or {})),
        )
        self.store.append_model(self.store.internal_trace_path, event)
        self._last_event_id = event.event_id
        return event

    def read_events(self) -> list[TraceEvent]:
        return [
            TraceEvent.model_validate(record)
            for record in self.store.read_jsonl(self.store.internal_trace_path)
        ]
