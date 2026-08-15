"""Stage orchestration.

INGEST -> GATE -> SCORE -> HERO_CHECKPOINT -> STRUCTURE -> NEXT_EXPERIMENT -> COMPLETE

Slow on purpose: each stage writes its result to disk before the next begins, so
a run can stop at the hero checkpoint, survive the process exiting, and resume
from the filesystem alone. Nothing here depends on conversation history.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .biorisk import (
    BiosafetyAssessment,
    BiosafetyRefusal,
    RiskTier,
    screen_bundle,
)
from .config import RunConfig
from .context import (
    ContextBudgetManager,
    EvidenceSummary,
    PromptTooLargeError,
    build_stage_prompt,
)
from .experiment import propose_next_experiment
from .gates import evaluate_gates
from .improvement import Revision, evaluate, improve_stage, rubric_hash
from .ingest import InputBundle, ingest
from .models import (
    CandidateHypothesis,
    CandidateScorecard,
    EvidenceRecord,
    FinalReport,
    HumanCheckpoint,
    ModelComparison,
    NextExperiment,
    RejectedCandidate,
    RunState,
    SourceRecord,
    Stage,
)
from .scoring import rank, score_candidate
from .soul import load_soul
from .store import RunStore, content_hash, git_commit, utc_now
from .structure import (
    StructureCache,
    build_requests,
    compare_models,
    execute_request,
    proto_tool_versions,
    validate_request,
)
from .trace import EventType, Tracer

FIXTURE_CACHE_DIR = Path(__file__).resolve().parent / "fixtures" / "structure_cache"


class StageError(RuntimeError):
    """A stage could not complete. The run records it and stops."""


class CheckpointBlocked(RuntimeError):
    """The run reached a human gate and stopped there."""


class Orchestrator:
    """Drives one run. Every method is safe to call on a resumed run."""

    def __init__(
        self,
        store: RunStore,
        config: RunConfig | None = None,
        *,
        repo_root: Path | None = None,
        budget: ContextBudgetManager | None = None,
    ) -> None:
        self.store = store
        self.config = config or RunConfig()
        self.repo_root = repo_root or Path.cwd()
        self.budget = budget or ContextBudgetManager()
        self.soul = load_soul()
        self.git_commit = git_commit(self.repo_root)
        self.tracer = Tracer(
            store, config_hash=self.config.hash(), git_commit=self.git_commit
        )

    # ------------------------------------------------------------------ paths
    @property
    def evidence_path(self) -> Path:
        return self.store.path("evidence", "evidence.jsonl")

    @property
    def scorecards_path(self) -> Path:
        return self.store.path("decisions", "scorecards.jsonl")

    @property
    def rejections_path(self) -> Path:
        return self.store.path("decisions", "rejections.jsonl")

    @property
    def checkpoints_path(self) -> Path:
        return self.store.path("decisions", "checkpoints.jsonl")

    @property
    def improvement_path(self) -> Path:
        return self.store.path("improvement", "iterations.jsonl")

    @property
    def candidates_path(self) -> Path:
        return self.store.path("input", "candidates.json")

    @property
    def biosafety_path(self) -> Path:
        return self.store.path("biosafety", "assessment.json")

    # --------------------------------------------------------------- gateway
    def screen_biorisk(self, bundle: InputBundle) -> BiosafetyAssessment:
        """Screen the request before any other stage, and record the verdict.

        The assessment is written to disk even when the request is refused: a
        refusal is an accountability record, not something to discard. The run
        directory therefore exists for refused runs too, containing the reason
        and nothing else.
        """
        assessment = screen_bundle(bundle)
        self.store.write_model(self.biosafety_path, assessment)

        event_type = {
            RiskTier.PERMITTED: EventType.BIORISK_SCREENED,
            RiskTier.REVIEW_REQUIRED: EventType.BIORISK_ESCALATED,
            RiskTier.REFUSED: EventType.BIORISK_REFUSED,
        }[assessment.tier]
        self.tracer.emit(
            stage=Stage.BIORISK, event_type=event_type,
            status=str(assessment.tier),
            output_refs=[self.store.relative(self.biosafety_path)],
            detail={
                "tier": str(assessment.tier),
                "policy_version": assessment.policy_version,
                "policy_hash": assessment.policy_hash,
                "countermeasure_context": assessment.countermeasure_context,
                "flags": [
                    {"category": str(f.category), "tier": str(f.tier),
                     "description": f.description}
                    for f in assessment.flags
                ],
                "rationale": assessment.rationale,
            },
        )
        return assessment

    # ------------------------------------------------------------------- init
    def init_run(self, bundle: InputBundle, *, force: bool = False) -> RunState:
        """Create the run directory and persist the validated input bundle.

        The biorisk gateway runs first. A refused request never reaches INGEST,
        and no candidate, evidence, or structural request is written for it.
        """
        self.store.create(force=force)
        assessment = self.screen_biorisk(bundle)

        if assessment.tier is RiskTier.REFUSED:
            refused_state = RunState(
                run_id=self.store.run_id, stage=Stage.BIORISK, status="refused",
                created_at=utc_now(), updated_at=utc_now(),
                config_hash=self.config.hash(), git_commit=self.git_commit,
                fixture_run=bundle.fixture,
                error=assessment.summary(),
                notes=["biorisk gateway refused this request; no stage ran"],
            )
            self.store.save_state(refused_state)
            self._refresh_manifest(bundle.fixture)
            raise BiosafetyRefusal(assessment)

        self.store.write_json(self.candidates_path, bundle.model_dump(mode="json"))
        self.store.write_json(
            self.store.path("input", "config.json"), self.config.model_dump(mode="json")
        )
        escalated = assessment.tier is RiskTier.REVIEW_REQUIRED
        state = RunState(
            run_id=self.store.run_id,
            stage=Stage.BIORISK if escalated else Stage.INGEST,
            status="awaiting_human" if escalated else "initialized",
            created_at=utc_now(),
            updated_at=utc_now(),
            config_hash=self.config.hash(),
            git_commit=self.git_commit,
            fixture_run=bundle.fixture,
            open_checkpoint_id=f"{self.store.run_id}-biorisk" if escalated else None,
        )
        self.store.save_state(state)

        if escalated:
            # Dual-use or out-of-scope rather than self-evidently hazardous, so a
            # person decides. Reuses the same checkpoint machinery as the hero
            # gate: one mechanism for "the agent stops and asks".
            checkpoint = HumanCheckpoint(
                checkpoint_id=f"{self.store.run_id}-biorisk",
                stage=Stage.BIORISK,
                requested_decision=(
                    "Biorisk screening escalated this request for human review. "
                    "Approve to let the pipeline run, or reject it."
                ),
                recommendation=assessment.rationale,
                uncertainty=[
                    f"[{f.category}] {f.description} — matched: {f.matched_text}"
                    for f in assessment.flags
                ],
                structural_readiness="blocked: biorisk review is unresolved",
                created_at=utc_now(),
            )
            self.store.append_model(self.checkpoints_path, checkpoint)
        self.tracer.emit(
            stage=Stage.INGEST, event_type=EventType.RUN_STARTED,
            output_refs=[self.store.relative(self.candidates_path)],
            detail={
                "fixture_run": bundle.fixture,
                "candidates": len(bundle.candidates),
                "sources": len(bundle.sources),
                "soul_version": self.soul.version,
            },
        )
        self._refresh_manifest(bundle.fixture)
        return state

    def _refresh_manifest(self, fixture_run: bool) -> None:
        self.store.rebuild_manifest(
            git_commit=self.git_commit,
            config_hash=self.config.hash(),
            soul_version=self.soul.version,
            schema_versions={"models": "1.0", "trace": "1.0"},
            tool_versions=proto_tool_versions(),
            fixture_run=fixture_run,
        )

    # -------------------------------------------------------------- accessors
    def load_bundle(self) -> InputBundle:
        return InputBundle.model_validate(self.store.read_json(self.candidates_path))

    def load_candidates(self) -> dict[str, CandidateHypothesis]:
        return {c.candidate_id: c for c in self.load_bundle().candidates}

    def load_evidence(self) -> dict[str, EvidenceRecord]:
        return {
            record["evidence_id"]: EvidenceRecord.model_validate(record)
            for record in self.store.read_jsonl(self.evidence_path)
        }

    def load_sources(self) -> dict[str, SourceRecord]:
        return {s.source_id: s for s in self.load_bundle().sources}

    def load_scorecards(self) -> list[CandidateScorecard]:
        return [
            CandidateScorecard.model_validate(record)
            for record in self.store.read_jsonl(self.scorecards_path)
        ]

    def load_checkpoints(self) -> list[HumanCheckpoint]:
        """Checkpoints are append-only; the latest entry per id is authoritative."""
        latest: dict[str, HumanCheckpoint] = {}
        for record in self.store.read_jsonl(self.checkpoints_path):
            checkpoint = HumanCheckpoint.model_validate(record)
            latest[checkpoint.checkpoint_id] = checkpoint
        return list(latest.values())

    def get_checkpoint(self, checkpoint_id: str) -> HumanCheckpoint | None:
        for checkpoint in self.load_checkpoints():
            if checkpoint.checkpoint_id == checkpoint_id:
                return checkpoint
        return None

    def write_context_artifacts(self) -> None:
        """Persist the compaction index and digest.

        ``evidence_index.json`` is what makes compaction reversible: a prompt can
        carry only IDs and summaries, and any detail is fetched back from here by
        stable ID. ``digest.md`` is the human-readable compact run state.
        """
        evidence = self.load_evidence()
        sources = self.load_sources()
        index = {
            evidence_id: {
                "evidence_id": evidence_id,
                "evidence_type": str(record.evidence_type),
                "interpretation": str(record.interpretation),
                "claim": record.claim,
                "value": record.value,
                "unit": record.unit,
                "supports": record.supports,
                "contradicts": record.contradicts,
                "limitations": record.limitations,
                "source_id": record.source_id,
                "source_url": (
                    sources[record.source_id].url if record.source_id in sources else None
                ),
                "rehydrate": f"agent evidence show <run_id> {evidence_id}",
            }
            for evidence_id, record in evidence.items()
        }
        self.store.write_json(self.store.path("context", "evidence_index.json"), {
            "schema_version": "1.0",
            "run_id": self.store.run_id,
            "count": len(index),
            "evidence": index,
        })

        state = self.store.load_state()
        scorecards = rank(self.load_scorecards())
        rejections = self.store.read_jsonl(self.rejections_path)
        lines = [
            f"# Run digest — {state.run_id}", "",
            f"- Stage: {state.stage}", f"- Status: {state.status}",
            f"- Fixture run: {state.fixture_run}",
            f"- Hero candidate: {state.hero_candidate_id or 'not selected'}",
            f"- Config hash: {state.config_hash}",
            f"- Git commit: {state.git_commit or 'unknown'}", "",
            "## Candidates", "",
            f"- Ingested: {len(state.candidate_ids)}",
            f"- Eligible: {', '.join(state.eligible_candidate_ids) or 'none'}",
            f"- Rejected: {', '.join(state.rejected_candidate_ids) or 'none'}", "",
            "## Ranking", "",
        ]
        lines += [
            f"- {card.candidate_id}: score {card.total_score:.3f}, completeness "
            f"{card.evidence_completeness:.2f}, missing "
            f"[{', '.join(card.missing_components) or 'none'}]"
            for card in scorecards
        ] or ["- not scored yet"]
        lines += ["", "## Rejection reasons", ""]
        lines += [
            f"- {r['candidate_id']}: {'; '.join(r['reasons'])}" for r in rejections
        ] or ["- none"]
        lines += ["", "## Evidence index", "",
                  f"{len(index)} records; rehydrate any of them by ID with "
                  "`agent evidence show <run_id> <evidence_id>`."]
        self.store.write_atomic(
            self.store.path("context", "digest.md"), "\n".join(lines) + "\n"
        )

    def rehydrate(self, evidence_id: str) -> EvidenceRecord | None:
        """Fetch full evidence detail by stable ID after compaction dropped it."""
        record = self.load_evidence().get(evidence_id)
        self.tracer.emit(
            stage="context", event_type=EventType.CONTEXT_REHYDRATED,
            evidence_ids=[evidence_id],
            status="ok" if record else "not_found",
        )
        return record

    # ----------------------------------------------------------- prompt guard
    def _evidence_summaries(self, candidate: CandidateHypothesis) -> list[EvidenceSummary]:
        evidence = self.load_evidence()
        sources = self.load_sources()
        ids = list(dict.fromkeys(
            candidate.evidence_ids
            + candidate.dependency.evidence_ids
            + candidate.interaction.evidence_ids
            + candidate.normal_cell_evidence_ids
            + candidate.contradicting_evidence_ids
        ))
        summaries: list[EvidenceSummary] = []
        for evidence_id in ids:
            record = evidence.get(evidence_id)
            if record is None:
                summaries.append(EvidenceSummary(
                    evidence_id=evidence_id, claim="referenced but not ingested",
                    interpretation="inference", source_url="", missing=True, supports=False,
                ))
                continue
            source = sources.get(record.source_id)
            summaries.append(EvidenceSummary(
                evidence_id=record.evidence_id,
                claim=record.claim,
                interpretation=str(record.interpretation),
                source_url=source.url if source else "",
                value=record.value,
                unit=record.unit,
                supports=record.supports,
                contradicts=list(record.contradicts),
                limitations=list(record.limitations),
            ))
        return summaries

    def build_prompt(
        self,
        stage: Stage,
        *,
        objective: str,
        candidate: CandidateHypothesis | None = None,
        actions: list[str],
        output_schema: str,
        stopping_conditions: list[str],
        history: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Assemble a stage prompt within budget, compacting and tracing as needed.

        Raises ``PromptTooLargeError`` at the hard limit rather than sending a
        silently truncated prompt.
        """
        state = self.store.load_state()
        state_summary = (
            f"run={state.run_id} stage={state.stage} status={state.status} "
            f"candidates={len(state.candidate_ids)} eligible={len(state.eligible_candidate_ids)} "
            f"rejected={len(state.rejected_candidate_ids)} hero={state.hero_candidate_id}"
        )
        summaries = self._evidence_summaries(candidate) if candidate else []
        trimmed_history, dropped_history = self.budget.trim_history(history or [])

        # Reserve room for the fixed sections before spending on evidence.
        skeleton = build_stage_prompt(
            stage=stage, objective=objective, soul=self.soul,
            run_state_summary=state_summary, evidence_lines=[],
            available_actions=actions, output_schema=output_schema,
            stopping_conditions=stopping_conditions, history=trimmed_history,
        )
        from .context import estimate_tokens  # noqa: PLC0415

        evidence_budget = max(0, self.budget.usable_tokens - estimate_tokens(skeleton))
        lines, dropped = self.budget.compact_evidence(summaries, budget_tokens=evidence_budget)

        prompt = build_stage_prompt(
            stage=stage, objective=objective, soul=self.soul,
            run_state_summary=state_summary, evidence_lines=lines,
            available_actions=actions, output_schema=output_schema,
            stopping_conditions=stopping_conditions, history=trimmed_history,
        )

        if dropped or dropped_history:
            self.tracer.emit(
                stage=stage, event_type=EventType.CONTEXT_COMPACTED,
                evidence_ids=dropped,
                detail={
                    "dropped_evidence_ids": dropped,
                    "dropped_history_entries": dropped_history,
                    "rehydrate_with": "agent evidence show <evidence_id>",
                },
            )

        try:
            report = self.budget.enforce(prompt, stage=str(stage))
        except PromptTooLargeError as exc:
            self.tracer.emit(
                stage=stage, event_type=EventType.CONTEXT_LIMIT_EXCEEDED,
                status="failed", error=str(exc),
                token_estimate=exc.tokens,
                detail={"limit": exc.limit},
            )
            raise
        return prompt, report.as_detail()

    # ---------------------------------------------------------------- INGEST
    def run_ingest(self) -> list[CandidateHypothesis]:
        state = self.store.load_state()
        self.tracer.emit(stage=Stage.INGEST, event_type=EventType.STAGE_STARTED)
        bundle = self.load_bundle()
        report, accepted = ingest(bundle)

        # Rewritten wholesale so a resumed INGEST does not duplicate records.
        self.evidence_path.unlink(missing_ok=True)
        for record in bundle.evidence:
            if record.evidence_id in report.accepted_evidence:
                self.store.append_model(self.evidence_path, record)

        for evidence_id in report.accepted_evidence:
            self.tracer.emit(
                stage=Stage.INGEST, event_type=EventType.EVIDENCE_ACCEPTED,
                evidence_ids=[evidence_id],
            )
        for evidence_id, reason in report.rejected_evidence:
            self.tracer.emit(
                stage=Stage.INGEST, event_type=EventType.EVIDENCE_REJECTED,
                evidence_ids=[evidence_id], status="rejected",
                detail={"reason": reason},
            )
        for candidate_id, reason in report.rejected_candidates:
            self._record_rejection(
                candidate_id, Stage.INGEST, [reason], ["input_validation"]
            )

        # Reload: _record_rejection saves state, so the copy above is stale.
        state = self.store.load_state()
        state.candidate_ids = [c.candidate_id for c in accepted]
        state.stage = Stage.GATE
        state.status = "running"
        state.completed_stages = self._advance(state.completed_stages, Stage.INGEST)
        self.store.save_state(state)
        self.write_context_artifacts()
        self.tracer.emit(
            stage=Stage.INGEST, event_type=EventType.STAGE_COMPLETED,
            output_refs=[self.store.relative(self.evidence_path)],
            detail={
                "accepted_candidates": len(accepted),
                "rejected_candidates": len(report.rejected_candidates),
                "accepted_evidence": len(report.accepted_evidence),
                "rejected_evidence": len(report.rejected_evidence),
                "warnings": report.warnings,
            },
        )
        return accepted

    # ------------------------------------------------------------------ GATE
    def run_gate(self) -> list[CandidateHypothesis]:
        state = self.store.load_state()
        self.tracer.emit(stage=Stage.GATE, event_type=EventType.STAGE_STARTED)
        candidates = self.load_candidates()
        eligible: list[CandidateHypothesis] = []

        for candidate_id in state.candidate_ids:
            candidate = candidates[candidate_id]
            outcome = evaluate_gates(candidate, self.config.gates)
            self.tracer.emit(
                stage=Stage.GATE, event_type=EventType.CANDIDATE_GATED,
                status="eligible" if outcome.eligible else "rejected",
                evidence_ids=list(candidate.evidence_ids),
                detail={
                    "candidate_id": candidate_id,
                    "passed_gates": outcome.passed_gates,
                    "failed_gates": outcome.failed_gates,
                },
            )
            if outcome.eligible:
                eligible.append(candidate)
            else:
                self._record_rejection(
                    candidate_id, Stage.GATE, outcome.reasons, outcome.failed_gates,
                    evidence_ids=list(candidate.evidence_ids),
                )

        # Reload: _record_rejection saves state, so the copy above is stale.
        state = self.store.load_state()
        state.eligible_candidate_ids = [c.candidate_id for c in eligible]
        state.stage = Stage.SCORE
        state.completed_stages = self._advance(state.completed_stages, Stage.GATE)
        self.store.save_state(state)
        self.tracer.emit(
            stage=Stage.GATE, event_type=EventType.STAGE_COMPLETED,
            output_refs=[self.store.relative(self.rejections_path)],
            detail={"eligible": len(eligible), "rejected": len(state.rejected_candidate_ids)},
        )
        return eligible

    def _record_rejection(
        self,
        candidate_id: str,
        stage: Stage,
        reasons: list[str],
        failed_gates: list[str],
        *,
        evidence_ids: list[str] | None = None,
    ) -> None:
        rejection = RejectedCandidate(
            candidate_id=candidate_id, stage=stage, reasons=reasons,
            failed_gates=failed_gates, evidence_ids=evidence_ids or [],
            rejected_at=utc_now(),
        )
        self.store.append_model(self.rejections_path, rejection)
        self.tracer.emit(
            stage=stage, event_type=EventType.CANDIDATE_REJECTED, status="rejected",
            evidence_ids=evidence_ids or [],
            output_refs=[self.store.relative(self.rejections_path)],
            detail={
                "candidate_id": candidate_id,
                "failed_gates": failed_gates,
                "reasons": reasons,
            },
        )
        state = self.store.load_state()
        if candidate_id not in state.rejected_candidate_ids:
            state.rejected_candidate_ids.append(candidate_id)
            self.store.save_state(state)

    # ----------------------------------------------------------------- SCORE
    def run_score(self) -> list[CandidateScorecard]:
        state = self.store.load_state()
        self.tracer.emit(stage=Stage.SCORE, event_type=EventType.STAGE_STARTED)
        candidates = self.load_candidates()

        self.scorecards_path.unlink(missing_ok=True)
        scorecards: list[CandidateScorecard] = []
        for candidate_id in state.eligible_candidate_ids:
            candidate = candidates[candidate_id]
            _, budget_detail = self.build_prompt(
                Stage.SCORE,
                objective=(
                    f"Score candidate {candidate_id} on each defined component. Use only "
                    "the evidence listed. Do not infer values for missing components."
                ),
                candidate=candidate,
                actions=["emit_scorecard", "abstain"],
                output_schema="CandidateScorecard",
                stopping_conditions=[
                    "every component has a raw value or is marked missing",
                    "no component is estimated from absent evidence",
                ],
            )
            scorecard = score_candidate(candidate, self.config)
            self.store.append_model(self.scorecards_path, scorecard)
            scorecards.append(scorecard)
            self.tracer.emit(
                stage=Stage.SCORE, event_type=EventType.CANDIDATE_SCORED,
                evidence_ids=list(candidate.evidence_ids),
                token_estimate=budget_detail["token_estimate"],
                output_refs=[self.store.relative(self.scorecards_path)],
                detail={
                    "candidate_id": candidate_id,
                    "total_score": scorecard.total_score,
                    "evidence_completeness": scorecard.evidence_completeness,
                    "missing_components": scorecard.missing_components,
                    "components": {
                        c.name: {"normalized": c.normalized, "weight": c.weight,
                                 "missing": c.missing}
                        for c in scorecard.components
                    },
                    "context_budget": budget_detail,
                },
            )

        state.stage = Stage.HERO_CHECKPOINT
        state.completed_stages = self._advance(state.completed_stages, Stage.SCORE)
        self.store.save_state(state)
        self.write_context_artifacts()
        self.tracer.emit(
            stage=Stage.SCORE, event_type=EventType.STAGE_COMPLETED,
            detail={"scored": len(scorecards)},
        )
        return scorecards

    # -------------------------------------------------------- HERO CHECKPOINT
    def run_hero_checkpoint(self) -> HumanCheckpoint:
        """Recommend one hypothesis, persist the case for and against it, and stop."""
        state = self.store.load_state()
        self.tracer.emit(stage=Stage.HERO_CHECKPOINT, event_type=EventType.STAGE_STARTED)
        scorecards = rank(self.load_scorecards())
        candidates = self.load_candidates()

        if not scorecards:
            checkpoint = HumanCheckpoint(
                checkpoint_id=f"{state.run_id}-hero",
                stage=Stage.HERO_CHECKPOINT,
                requested_decision=(
                    "No candidate passed the gates. Approve abstention, or supply "
                    "additional evidence and rerun."
                ),
                recommendation="Abstain: nothing here is defensible.",
                rejected_candidate_ids=list(state.rejected_candidate_ids),
                uncertainty=["every candidate failed at least one gate"],
                structural_readiness="not ready: no candidate selected",
                created_at=utc_now(),
            )
        else:
            best = scorecards[0]
            candidate = candidates[best.candidate_id]
            contradictions = list(candidate.contradicting_evidence_ids)
            has_sequences = bool(
                candidate.tractability.target_sequence
                and candidate.tractability.partner_sequence
            )
            structural_ready = (
                has_sequences and candidate.interaction.ready_for_structural_modeling
            )
            checkpoint = HumanCheckpoint(
                checkpoint_id=f"{state.run_id}-hero",
                stage=Stage.HERO_CHECKPOINT,
                requested_decision=(
                    f"Approve {best.candidate_id} "
                    f"({candidate.disease_context} / {candidate.target_gene} / "
                    f"{candidate.partner_gene}) as the hero hypothesis and authorise "
                    "structural modelling, or reject or revise it."
                ),
                recommendation=candidate.hypothesis,
                recommended_candidate_id=best.candidate_id,
                alternatives=[
                    f"{card.candidate_id} (score {card.total_score:.3f}, "
                    f"completeness {card.evidence_completeness:.2f})"
                    for card in scorecards[1:]
                ],
                supporting_evidence_ids=list(candidate.evidence_ids),
                contradicting_evidence_ids=contradictions,
                rejected_candidate_ids=list(state.rejected_candidate_ids),
                uncertainty=list(best.uncertainty_notes),
                structural_readiness=(
                    "ready: the contact has a mapped interacting region "
                    f"({candidate.interaction.target_region}) and both partner sequences"
                    if structural_ready else
                    "not ready: "
                    + ("no mapped interacting region, so there is no contact "
                       "point to model"
                       if not candidate.interaction.interacting_region_mapped
                       else "at least one partner sequence is missing")
                ),
                created_at=utc_now(),
            )

        self.store.append_model(self.checkpoints_path, checkpoint)
        state.open_checkpoint_id = checkpoint.checkpoint_id
        state.status = "awaiting_human"
        state.completed_stages = self._advance(
            state.completed_stages, Stage.HERO_CHECKPOINT
        )
        self.store.save_state(state)
        self.tracer.emit(
            stage=Stage.HERO_CHECKPOINT, event_type=EventType.CHECKPOINT_CREATED,
            status="awaiting_human",
            output_refs=[self.store.relative(self.checkpoints_path)],
            evidence_ids=checkpoint.supporting_evidence_ids,
            detail={
                "checkpoint_id": checkpoint.checkpoint_id,
                "recommended_candidate_id": checkpoint.recommended_candidate_id,
                "alternatives": checkpoint.alternatives,
                "contradicting_evidence_ids": checkpoint.contradicting_evidence_ids,
                "structural_readiness": checkpoint.structural_readiness,
            },
        )
        return checkpoint

    def resolve_checkpoint(
        self,
        checkpoint_id: str,
        decision: str,
        *,
        resolved_by: str,
        note: str | None = None,
    ) -> HumanCheckpoint:
        """Record a human decision. Only a human resolves a gate."""
        if decision not in {"approve", "reject", "revise"}:
            raise ValueError(f"unknown decision {decision!r}")
        if not resolved_by.strip():
            raise ValueError("a checkpoint resolution must name who made it")

        checkpoint = self.get_checkpoint(checkpoint_id)
        if checkpoint is None:
            raise KeyError(f"no checkpoint {checkpoint_id!r} in this run")
        if checkpoint.status != "open":
            raise ValueError(
                f"checkpoint {checkpoint_id} is already {checkpoint.status}; "
                "append a new checkpoint rather than rewriting a decision"
            )

        status_map = {"approve": "approved", "reject": "rejected", "revise": "revised"}
        resolved = checkpoint.model_copy(update={
            "status": status_map[decision],
            "resolution": note or decision,
            "resolved_by": resolved_by,
            "resolved_at": utc_now(),
            "revision_request": note if decision == "revise" else None,
        })
        self.store.append_model(self.checkpoints_path, resolved)

        state = self.store.load_state()
        state.open_checkpoint_id = None if decision != "revise" else checkpoint_id
        if decision == "approve":
            state.hero_candidate_id = checkpoint.recommended_candidate_id
            state.stage = Stage.STRUCTURE
            state.status = "running"
        elif decision == "reject":
            state.status = "abstained"
            state.stage = Stage.COMPLETE
            state.notes.append(f"hero checkpoint rejected by {resolved_by}")
        else:
            state.status = "awaiting_human"
            state.stage = Stage.HERO_CHECKPOINT
            state.notes.append(f"revision requested by {resolved_by}: {note or ''}".strip())
        self.store.save_state(state)

        self.tracer.emit(
            stage=Stage.HERO_CHECKPOINT, event_type=EventType.CHECKPOINT_RESOLVED,
            actor=f"human:{resolved_by}", status=status_map[decision],
            output_refs=[self.store.relative(self.checkpoints_path)],
            detail={
                "checkpoint_id": checkpoint_id,
                "decision": decision,
                "resolved_by": resolved_by,
                "note": note,
            },
        )
        return resolved

    # ------------------------------------------------------------- STRUCTURE
    def run_structure(self, *, validation_only: bool = False) -> ModelComparison | None:
        """Model the interface. Requires an approved checkpoint unless validating."""
        state = self.store.load_state()
        self.tracer.emit(stage=Stage.STRUCTURE, event_type=EventType.STAGE_STARTED)

        checkpoint = (
            self.get_checkpoint(f"{state.run_id}-hero") if state.hero_candidate_id else None
        )
        approved = bool(checkpoint and checkpoint.status == "approved")
        if self.config.require_human_approval_before_structure and not approved and not validation_only:
            raise CheckpointBlocked(
                "structural execution requires an approved hero checkpoint; "
                f"run: agent checkpoint resolve {state.run_id}-hero --decision approve "
                "--by <name>"
            )

        candidate_id = state.hero_candidate_id
        if candidate_id is None:
            eligible = state.eligible_candidate_ids
            if not eligible:
                raise StageError("no candidate is available for structural modelling")
            candidate_id = rank(self.load_scorecards())[0].candidate_id
        candidate = self.load_candidates()[candidate_id]

        requests = build_requests(
            candidate, self.config,
            checkpoint_id=checkpoint.checkpoint_id if checkpoint else None,
        )
        if not requests:
            self.tracer.emit(
                stage=Stage.STRUCTURE, event_type=EventType.PROTO_REQUEST_INVALID,
                status="skipped",
                detail={
                    "candidate_id": candidate_id,
                    "reason": "partner sequences are missing; no structural request built",
                },
            )
            return None

        self.store.write_json(
            self.store.path("structure", "request.json"),
            {"candidate_id": candidate_id,
             "requests": [r.model_dump(mode="json") for r in requests]},
        )

        caches = [
            StructureCache(self.store.path("structure", "cache")),
            StructureCache(FIXTURE_CACHE_DIR),
        ]
        results = []
        for request in requests:
            validation = validate_request(request)
            self.tracer.emit(
                stage=Stage.STRUCTURE,
                event_type=(
                    EventType.PROTO_REQUEST_VALIDATED if validation["valid"]
                    else EventType.PROTO_REQUEST_INVALID
                ),
                status="ok" if validation["valid"] else "invalid",
                detail={
                    "request_id": request.request_id,
                    "model": request.model,
                    "proto_contract": validation.get("proto_contract"),
                    "modal_target": validation.get("modal_target"),
                    "blockers": validation["blockers"],
                    "input_hash": request.input_hash,
                },
            )

            if not validation_only and self.config.allow_live_modal and approved:
                self.tracer.emit(
                    stage=Stage.STRUCTURE, event_type=EventType.MODAL_DISPATCHED,
                    compute="modal",
                    detail={"request_id": request.request_id,
                            "tool_key": request.proto_tool_key},
                )

            result = execute_request(
                request, self.config, caches=caches,
                approved=approved, validation_only=validation_only,
            )
            results.append(result)

            result_dir = self.store.path("structure", result.model)
            result_path = result_dir / f"{request.request_id}.json"
            self.store.write_model(result_path, result)
            result.artifact_paths.append(self.store.relative(result_path))

            if result.status == "cached":
                event_type = EventType.STRUCTURE_CACHE_HIT
            elif result.status == "completed":
                event_type = EventType.MODAL_COMPLETED
            elif result.status == "failed":
                event_type = EventType.MODAL_FAILED
            else:
                event_type = EventType.PROTO_REQUEST_VALIDATED
            self.tracer.emit(
                stage=Stage.STRUCTURE, event_type=event_type,
                status=result.status, error=result.error,
                latency_ms=result.runtime_ms,
                compute="modal" if result.source == "live_modal" else result.source,
                output_refs=[self.store.relative(result_path)],
                detail={
                    "request_id": result.request_id,
                    "model": result.model,
                    "source": result.source,
                    "confidence": result.confidence,
                    "input_hash": result.input_hash,
                    "output_hash": result.output_hash,
                    "chain_map": result.chain_map,
                    "limitations": result.limitations,
                },
            )
            self.tracer.emit(
                stage=Stage.STRUCTURE,
                event_type=(
                    EventType.BOLTZ2_OUTPUT if result.model == "boltz2"
                    else EventType.ESMFOLD2_OUTPUT
                ),
                status=result.status,
                detail={
                    "request_id": result.request_id,
                    "purpose": request.purpose,
                    "confidence": result.confidence,
                    "interpretation": str(result.interpretation),
                },
            )

        boltz = next((r for r in results if r.model == "boltz2"), None)
        esmfold = [r for r in results if r.model == "esmfold2"]
        comparison = compare_models(candidate_id, boltz, esmfold)
        comparison_path = self.store.path("structure", "comparison.json")
        self.store.write_model(comparison_path, comparison)
        self.tracer.emit(
            stage=Stage.STRUCTURE, event_type=EventType.MODEL_COMPARISON,
            status=comparison.verdict,
            output_refs=[self.store.relative(comparison_path)],
            detail={
                "verdict": comparison.verdict,
                "agreements": comparison.agreements,
                "disagreements": comparison.disagreements,
                "confidence_delta": comparison.confidence_delta,
                "caveat": comparison.caveat,
            },
        )

        state = self.store.load_state()
        state.stage = Stage.NEXT_EXPERIMENT
        state.completed_stages = self._advance(state.completed_stages, Stage.STRUCTURE)
        self.store.save_state(state)
        self.tracer.emit(
            stage=Stage.STRUCTURE, event_type=EventType.STAGE_COMPLETED,
            detail={"results": len(results), "verdict": comparison.verdict},
        )
        return comparison

    # ------------------------------------------------- NEXT EXPERIMENT + IMPROVE
    def run_next_experiment(self) -> NextExperiment:
        state = self.store.load_state()
        self.tracer.emit(stage=Stage.NEXT_EXPERIMENT, event_type=EventType.STAGE_STARTED)

        candidate_id = state.hero_candidate_id or (
            state.eligible_candidate_ids[0] if state.eligible_candidate_ids else None
        )
        if candidate_id is None:
            raise StageError("no candidate available for a next experiment")
        candidate = self.load_candidates()[candidate_id]

        comparison_path = self.store.path("structure", "comparison.json")
        comparison = (
            ModelComparison.model_validate(self.store.read_json(comparison_path))
            if comparison_path.exists() else None
        )

        applied: set[str] = set()
        first_pass = propose_next_experiment(candidate, comparison)

        def rerun(revision: Revision) -> dict[str, Any]:
            applied.add(revision.criterion)
            return propose_next_experiment(
                candidate, comparison, applied_revisions=frozenset(applied)
            ).model_dump(mode="json")

        improved_payload, iterations = improve_stage(
            run_id=state.run_id,
            stage=Stage.NEXT_EXPERIMENT,
            artifact=first_pass.model_dump(mode="json"),
            rerun=rerun,
            max_iterations=self.config.max_improvement_iterations,
        )

        for iteration in iterations:
            self.store.append_model(self.improvement_path, iteration)
            self.tracer.emit(
                stage=Stage.NEXT_EXPERIMENT, event_type=EventType.IMPROVEMENT_ITERATION,
                output_refs=[self.store.relative(self.improvement_path)],
                detail={
                    "iteration": iteration.iteration,
                    "deficiency": iteration.deficiency,
                    "proposed_revision": iteration.proposed_revision,
                    "revision_target": iteration.revision_target,
                    "before_score": iteration.before_score,
                    "after_score": iteration.after_score,
                    "applied": iteration.applied,
                    "stopping_reason": iteration.stopping_reason,
                    "rubric_hash": rubric_hash(Stage.NEXT_EXPERIMENT),
                },
            )

        experiment = NextExperiment.model_validate(improved_payload)
        experiment_path = self.store.path("reports", "next_experiment.json")
        self.store.write_model(experiment_path, experiment)

        state = self.store.load_state()
        state.stage = Stage.COMPLETE
        state.completed_stages = self._advance(
            state.completed_stages, Stage.NEXT_EXPERIMENT
        )
        self.store.save_state(state)
        self.tracer.emit(
            stage=Stage.NEXT_EXPERIMENT, event_type=EventType.NEXT_EXPERIMENT,
            output_refs=[self.store.relative(experiment_path)],
            detail={
                "candidate_id": candidate_id,
                "question": experiment.scientific_question,
                "rubric_score": evaluate(Stage.NEXT_EXPERIMENT, improved_payload).score,
            },
        )
        return experiment

    # -------------------------------------------------------------- COMPLETE
    def run_complete(self) -> FinalReport:
        state = self.store.load_state()
        candidates = self.load_candidates()
        scorecards = {card.candidate_id: card for card in self.load_scorecards()}
        checkpoints = self.load_checkpoints()

        candidate = candidates.get(state.hero_candidate_id or "")
        scorecard = scorecards.get(state.hero_candidate_id or "")

        comparison_path = self.store.path("structure", "comparison.json")
        comparison = (
            ModelComparison.model_validate(self.store.read_json(comparison_path))
            if comparison_path.exists() else None
        )
        experiment_path = self.store.path("reports", "next_experiment.json")
        experiment = (
            NextExperiment.model_validate(self.store.read_json(experiment_path))
            if experiment_path.exists() else None
        )

        limitations = [
            "Computational analysis only. No binding, safety, efficacy, or "
            "experimental validation is claimed.",
            "Scores are decision aids defined in this repository, not established "
            "biological metrics.",
        ]
        if state.fixture_run:
            limitations.insert(0, (
                "FIXTURE RUN: this run used clearly labelled synthetic test data. "
                "It demonstrates the workflow and carries no scientific weight."
            ))
        if comparison:
            limitations.append(comparison.caveat)
        if scorecard and scorecard.missing_components:
            limitations.append(
                "Missing evidence components scored zero: "
                + ", ".join(scorecard.missing_components)
            )

        status: str = "completed"
        if state.status == "abstained":
            status = "abstained"
        elif state.status == "failed":
            status = "failed"

        report = FinalReport(
            run_id=state.run_id,
            status=status,
            hero_candidate_id=state.hero_candidate_id,
            hero_hypothesis=candidate.hypothesis if candidate else None,
            disease_context=candidate.disease_context if candidate else None,
            # These two FinalReport field names are frozen by the BenchFlow
            # verifier (see `_hero_hypothesis_payload`); the values come from the
            # candidate's target-agnostic fields.
            transcription_factor=candidate.target_gene if candidate else None,
            mediator_subunit=candidate.partner_gene if candidate else None,
            confidence=self._confidence(scorecard, comparison, state.fixture_run),
            scorecard_summary=(
                {c.name: round(c.normalized * c.weight, 4) for c in scorecard.components}
                if scorecard else {}
            ),
            supporting_evidence_ids=list(candidate.evidence_ids) if candidate else [],
            contradicting_evidence_ids=(
                list(candidate.contradicting_evidence_ids) if candidate else []
            ),
            rejected_candidates=list(state.rejected_candidate_ids),
            human_decisions=[
                f"{c.checkpoint_id}: {c.status}"
                + (f" by {c.resolved_by}" if c.resolved_by else "")
                for c in checkpoints
            ],
            structural_summary=(
                f"{comparison.verdict}: "
                + "; ".join(comparison.agreements + comparison.disagreements)
                if comparison else None
            ),
            next_experiment_summary=experiment.scientific_question if experiment else None,
            limitations=limitations,
            fixture_run=state.fixture_run,
            generated_at=utc_now(),
        )
        report_path = self.store.path("reports", "final_report.md")
        self.store.write_model(self.store.path("reports", "final_report.json"), report)
        self.store.write_json(
            self.store.path("reports", "hero_hypothesis.json"),
            self._hero_hypothesis_payload(candidate, scorecard, comparison, report),
        )
        self.store.write_atomic(report_path, self._render_markdown(report, comparison, experiment))
        self.write_context_artifacts()

        state.stage = Stage.COMPLETE
        state.status = "abstained" if status == "abstained" else "completed"
        state.completed_stages = self._advance(state.completed_stages, Stage.COMPLETE)
        self.store.save_state(state)
        self.tracer.emit(
            stage=Stage.COMPLETE, event_type=EventType.REPORT_WRITTEN,
            output_refs=[self.store.relative(report_path)],
            detail={"status": status, "hero": state.hero_candidate_id},
        )
        self.tracer.emit(
            stage=Stage.COMPLETE,
            event_type=(
                EventType.RUN_ABSTAINED if status == "abstained" else EventType.RUN_COMPLETED
            ),
            status=status,
        )
        self._refresh_manifest(state.fixture_run)
        return report

    def _hero_hypothesis_payload(
        self,
        candidate: CandidateHypothesis | None,
        scorecard: CandidateScorecard | None,
        comparison: ModelComparison | None,
        report: FinalReport,
    ) -> dict[str, Any]:
        """Emit the hero result in the frozen BenchFlow task's schema.

        Shape matches ``benchflow/tasks/tf-mediator-hero/task.md`` so this
        artifact is directly checkable by that task's verifier rather than being
        a third, workflow-only format.

        The ``hero`` block deliberately keeps the keys ``transcription_factor``
        and ``mediator_subunit`` even though the rest of the pipeline is now
        target-agnostic. That task is frozen and its verifier asserts those exact
        key names (``verifier/test_output.py::test_hero_is_complete``), so
        renaming them here to ``target_gene``/``partner_gene`` would fail an
        evaluation we are scored on. ``FinalReport`` keeps the same two field
        names for the same reason. Generalise the pipeline, not this boundary.

        A fixture run produces ``synthetic://`` source URLs and will not satisfy
        the verifier's public-evidence assertions. That is the intended
        behaviour: synthetic data must not pass a public-provenance check.
        """
        evidence_records = self.load_evidence()
        sources = self.load_sources()
        components = {c.name: c for c in scorecard.components} if scorecard else {}

        def component_metric(name: str) -> dict[str, Any]:
            component = components.get(name)
            if component is None:
                return {"value": 0.0, "definition": "not scored", "source_url": ""}
            source_url = ""
            for evidence_id in component.evidence_ids:
                record = evidence_records.get(evidence_id)
                if record and record.source_id in sources:
                    source_url = sources[record.source_id].url
                    break
            return {
                "value": component.normalized,
                "definition": component.definition,
                "source_url": source_url,
                "missing": component.missing,
            }

        normal_component = components.get("normal_cell_completeness")
        if normal_component is None or normal_component.missing:
            normal_status = "missing"
        elif normal_component.normalized >= 1.0:
            normal_status = "supported"
        else:
            normal_status = "mixed"

        evidence_payload = []
        for evidence_id in (candidate.evidence_ids if candidate else []):
            record = evidence_records.get(evidence_id)
            if record is None:
                continue
            source = sources.get(record.source_id)
            evidence_payload.append({
                "claim": record.claim,
                "source_url": source.url if source else "",
                "source_type": "database",
                "interpretation": str(record.interpretation),
                "supports": record.supports,
                "evidence_id": record.evidence_id,
            })

        rejections = self.store.read_jsonl(self.rejections_path)
        structure_status = "missing"
        caveats: list[str] = []
        if comparison is not None:
            structure_status = "predicted"
            caveats = [comparison.caveat, *comparison.limitations, *comparison.disagreements]

        return {
            "schema_version": "1.0",
            "hero": {
                "disease_context": report.disease_context or "",
                "transcription_factor": report.transcription_factor or "",
                "mediator_subunit": report.mediator_subunit or "",
                "hypothesis": report.hero_hypothesis or "",
                "confidence": report.confidence,
            },
            "selection": {
                "dependency_strength": component_metric("dependency_strength"),
                "disease_specificity": component_metric("disease_specificity"),
                "normal_cell_proxy": {
                    "status": normal_status,
                    "notes": (normal_component.uncertainty if normal_component else
                              "no normal-cell evidence") or "",
                    "source_url": component_metric("normal_cell_completeness")["source_url"],
                },
                "evidence_quality": (
                    components["interaction_evidence_quality"].normalized
                    if "interaction_evidence_quality" in components else 0.0
                ),
                "structural_tractability": (
                    components["structural_tractability"].normalized
                    if "structural_tractability" in components else 0.0
                ),
            },
            "evidence": evidence_payload,
            "alternatives_rejected": [
                {"candidate": r["candidate_id"], "reason": "; ".join(r["reasons"])}
                for r in rejections
            ],
            "structure": {
                "status": structure_status,
                "public_id": (
                    candidate.tractability.experimental_structure_id if candidate else None
                ),
                "interface_residues": {},
                "caveats": caveats or ["No structural modelling was performed."],
            },
            "drug_discovery": {
                "status": "blocked",
                "search_region_basis": (
                    "No defensible search region: the interface is a prediction and "
                    "no experimental structure or reference ligand is available."
                ),
                "compounds": [],
                "blockers": [
                    "Virtual screening is out of scope for this workflow.",
                    "No public experimental structure for the interface.",
                    *(["Fixture run: synthetic data carries no scientific weight."]
                      if report.fixture_run else []),
                ],
            },
            "limitations": report.limitations,
        }

    @staticmethod
    def _confidence(
        scorecard: CandidateScorecard | None,
        comparison: ModelComparison | None,
        fixture_run: bool,
    ) -> str:
        """Confidence is capped by evidence completeness and by fixture status."""
        if scorecard is None or fixture_run:
            return "low"
        if scorecard.evidence_completeness >= 0.9 and scorecard.total_score >= 0.7:
            if comparison is None or comparison.verdict != "inconsistent":
                return "medium"
        return "low"

    def _render_markdown(
        self,
        report: FinalReport,
        comparison: ModelComparison | None,
        experiment: NextExperiment | None,
    ) -> str:
        lines = [
            f"# Run {report.run_id} — final report", "",
            f"- Status: **{report.status}**",
            f"- Generated: {report.generated_at}",
            f"- Confidence: **{report.confidence}**",
        ]
        if report.fixture_run:
            lines += ["", "> **FIXTURE RUN.** Synthetic test data. Not scientific evidence."]
        lines += ["", "## Hero hypothesis", ""]
        if report.hero_candidate_id:
            lines += [
                f"- Candidate: `{report.hero_candidate_id}`",
                f"- Disease context: {report.disease_context}",
                f"- Target gene: {report.transcription_factor}",
                f"- Partner gene: {report.mediator_subunit}", "",
                report.hero_hypothesis or "",
            ]
        else:
            lines.append("No hero hypothesis was selected; the run abstained.")

        lines += ["", "## Score contributions", ""]
        if report.scorecard_summary:
            lines += ["| Component | Weighted contribution |", "|---|---|"]
            lines += [f"| {k} | {v:.4f} |" for k, v in report.scorecard_summary.items()]
        else:
            lines.append("No scorecard was produced.")

        lines += ["", "## Human decisions", ""]
        lines += [f"- {d}" for d in report.human_decisions] or ["- none recorded"]

        lines += ["", "## Rejected candidates", ""]
        lines += [f"- {c}" for c in report.rejected_candidates] or ["- none"]

        lines += ["", "## Structural modelling", ""]
        if comparison:
            lines += [
                f"- Verdict: **{comparison.verdict}**",
                f"- Interpretation: {comparison.interpretation} (not an observation)",
                "", "**Agreements**", "",
                *[f"- {a}" for a in comparison.agreements or ["- none"]],
                "", "**Disagreements**", "",
                *[f"- {d}" for d in comparison.disagreements or ["- none"]],
                "", f"> {comparison.caveat}",
            ]
        else:
            lines.append("No structural modelling was performed.")

        lines += ["", "## Next experiment", ""]
        if experiment:
            lines += [
                f"**Question.** {experiment.scientific_question}", "",
                f"**Perturbation.** {experiment.perturbation}", "",
                f"**Readout.** {experiment.readout}", "",
                "**Possible outcomes**", "",
            ]
            for outcome in experiment.possible_outcomes:
                lines += [f"- *{outcome.outcome}* → {outcome.interpretation_change}"]
        else:
            lines.append("No next experiment was proposed.")

        lines += ["", "## Limitations", ""]
        lines += [f"- {limitation}" for limitation in report.limitations]
        lines += ["", "## Contradicting evidence", ""]
        lines += [f"- {e}" for e in report.contradicting_evidence_ids] or ["- none recorded"]
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------ misc
    @staticmethod
    def _advance(completed: list[Stage], stage: Stage) -> list[Stage]:
        return completed if stage in completed else [*completed, stage]

    def run_until_checkpoint(self) -> HumanCheckpoint:
        """INGEST through HERO_CHECKPOINT, then stop for a human."""
        self.run_ingest()
        self.run_gate()
        self.run_score()
        return self.run_hero_checkpoint()

    def resume(self) -> RunState:
        """Continue from whatever the filesystem says the stage is."""
        state = self.store.load_state()
        self.tracer.emit(
            stage=state.stage, event_type=EventType.RUN_RESUMED,
            detail={"stage": str(state.stage), "status": state.status},
        )
        if state.status == "awaiting_human":
            return state
        if state.stage is Stage.INGEST:
            self.run_ingest()
            state = self.store.load_state()
        if state.stage is Stage.GATE:
            self.run_gate()
            state = self.store.load_state()
        if state.stage is Stage.SCORE:
            self.run_score()
            state = self.store.load_state()
        if state.stage is Stage.HERO_CHECKPOINT:
            self.run_hero_checkpoint()
            return self.store.load_state()
        if state.stage is Stage.STRUCTURE:
            self.run_structure()
            state = self.store.load_state()
        if state.stage is Stage.NEXT_EXPERIMENT:
            self.run_next_experiment()
            state = self.store.load_state()
        if state.stage is Stage.COMPLETE and state.status not in {"completed", "abstained"}:
            self.run_complete()
        return self.store.load_state()


def load_bundle_file(path: Path) -> InputBundle:
    return InputBundle.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
