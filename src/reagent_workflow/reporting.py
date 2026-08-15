"""Run report: the prompt, the research, the conclusions.

A run leaves a lot of machine-readable evidence — JSONL decision logs, traces,
scorecards, structural results. None of it answers the question a reader
actually asks first: *what was this agent asked, what did it look at, and what
did it conclude?*

This builds that document from the run directory alone. It reads only artifacts
already on disk, so the report is reconstructed evidence rather than a
narration written alongside the run — if the report says a gate fired, the
rejection record is there to check.

Three sections, in the order a sceptical reader wants them:

1. **The prompt** — the objective the agent was given, the rules in force at
   each stage, and the input it was handed.
2. **The research** — what it examined, which evidence it accepted and
   rejected, what it scored, and what it modelled.
3. **The conclusions** — the recommendation, the human decisions, what was
   rejected and why, what remains unresolved, and what it does not claim.

Nothing here computes a new result. If a number appears in this report it came
from an artifact, and the artifact is named.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import (
    CandidateScorecard,
    FinalReport,
    HumanCheckpoint,
    ModelComparison,
    NextExperiment,
    Stage,
)
from .soul import load_soul
from .store import RunStore, utc_now

REPORT_SCHEMA_VERSION = "1.0"


def _fmt(value: Any, default: str = "not recorded") -> str:
    if value is None or value == "" or value == []:
        return default
    return str(value)


class RunReporter:
    """Builds the human-facing account of a run from its artifacts."""

    def __init__(self, orchestrator: Any) -> None:
        self.orchestrator = orchestrator
        self.store: RunStore = orchestrator.store
        self.soul = load_soul()

    # ----------------------------------------------------------- the prompt
    def _prompt_section(self) -> list[str]:
        state = self.store.load_state()
        bundle = self.orchestrator.load_bundle()
        lines = [
            "## 1. The prompt", "",
            "### What the agent was asked", "",
            "Find a disease-selective target dependency, establish that its "
            "interaction partner contact is documented and mapped, evaluate "
            "whether that interface is structurally tractable, and propose the "
            "experiment that would test the resulting hypothesis.", "",
            "The agent selects nothing on its own past the hero checkpoint: a "
            "named human approves before any structural execution.", "",
            "### Input it was given", "",
            f"- Candidates supplied: **{len(bundle.candidates)}**",
            f"- Evidence records supplied: **{len(bundle.evidence)}**",
            f"- Sources supplied: **{len(bundle.sources)}**",
            f"- Fixture run: **{state.fixture_run}**"
            + ("  — synthetic test data, not scientific evidence"
               if state.fixture_run else ""),
            "",
        ]
        if bundle.fixture and bundle.fixture_note:
            lines += [f"> {bundle.fixture_note}", ""]

        lines += ["### Rules in force, by stage", "",
                  "Each stage loads only the rules that govern it, so a prompt "
                  "never carries the whole constitution "
                  f"(`SOUL.md` v{self.soul.version}).", ""]
        for stage in Stage:
            rules = self.soul.for_stage(stage)
            if not rules:
                continue
            lines.append(f"**{stage}**")
            lines += [f"- {rule}" for rule in rules]
            lines.append("")
        return lines

    # --------------------------------------------------------- the research
    def _research_section(self) -> list[str]:
        state = self.store.load_state()
        evidence = self.orchestrator.load_evidence()
        sources = self.orchestrator.load_sources()
        scorecards = {c.candidate_id: c for c in self.orchestrator.load_scorecards()}
        rejections = self.store.read_jsonl(self.orchestrator.rejections_path)
        events = self.store.read_jsonl(self.store.internal_trace_path)

        lines = ["## 2. What it researched", "", "### Evidence examined", "",
                 f"- Evidence records accepted: **{len(evidence)}**",
                 f"- Distinct sources cited: **{len(sources)}**"]

        contradicting = [e for e in evidence.values() if not e.supports]
        lines.append(
            f"- Records recorded as contradicting: **{len(contradicting)}**"
            + ("  — kept visible, not discarded" if contradicting else "")
        )
        lines.append("")

        if sources:
            lines += ["| Source | Tier | Version | URL |", "|---|---|---|---|"]
            for source in sources.values():
                lines.append(
                    f"| {source.name} | {source.tier} | "
                    f"{_fmt(source.version, '—')} | {source.url} |"
                )
            lines.append("")

        if contradicting:
            lines += ["**Contradicting evidence, stated rather than dropped:**", ""]
            lines += [f"- `{e.evidence_id}` — {e.claim}" for e in contradicting]
            lines.append("")

        # -- what it scored --------------------------------------------------
        lines += ["### Candidates considered", "",
                  f"- Ingested: **{len(state.candidate_ids)}**",
                  f"- Passed the gates: **{len(state.eligible_candidate_ids)}**",
                  f"- Rejected: **{len(state.rejected_candidate_ids)}**", ""]

        if scorecards:
            lines += ["| Candidate | Score | Evidence completeness | Missing |",
                      "|---|---|---|---|"]
            for card in sorted(
                scorecards.values(), key=lambda c: -c.total_score
            ):
                lines.append(
                    f"| `{card.candidate_id}` | {card.total_score:.3f} | "
                    f"{card.evidence_completeness:.2f} | "
                    f"{', '.join(card.missing_components) or 'none'} |"
                )
            lines.append("")

        # -- what it modelled ------------------------------------------------
        comparison = self._comparison()
        if comparison is not None:
            lines += ["### Structural modelling", "",
                      f"- Models run: **{', '.join(comparison.models_compared) or 'none'}**",
                      f"- Models that may vote on the interface: "
                      f"**{', '.join(comparison.interface_models) or 'none'}** "
                      f"({comparison.interface_votes}/"
                      f"{comparison.interface_models_available} cleared the "
                      "confidence floor)",
                      f"- Consensus: **{comparison.consensus}**",
                      f"- Verdict: **{comparison.verdict}**", ""]
            if comparison.ci_overlap:
                lines += ["Confidence intervals compared across replicate seeds:", "",
                          "| Metric | Intervals overlap |", "|---|---|"]
                lines += [
                    f"| {metric} | {'yes' if overlap else 'no'} |"
                    for metric, overlap in comparison.ci_overlap.items()
                ]
                lines.append("")
            if comparison.agreements:
                lines += ["**Agreements**", ""]
                lines += [f"- {a}" for a in comparison.agreements]
                lines.append("")
            if comparison.disagreements:
                lines += ["**Disagreements**", ""]
                lines += [f"- {d}" for d in comparison.disagreements]
                lines.append("")

        lines += [f"### Work recorded", "",
                  f"The internal trace holds **{len(events)}** events. Every "
                  "decision below is reconstructable from it.", ""]
        return lines

    # ------------------------------------------------------- the conclusions
    def _conclusions_section(self) -> list[str]:
        state = self.store.load_state()
        rejections = self.store.read_jsonl(self.orchestrator.rejections_path)
        checkpoints: list[HumanCheckpoint] = self.orchestrator.load_checkpoints()
        report = self._final_report()
        experiment = self._experiment()
        comparison = self._comparison()

        lines = ["## 3. What it concluded", ""]

        if report and report.hero_candidate_id:
            lines += [
                "### Recommendation", "",
                f"- Candidate: **`{report.hero_candidate_id}`**",
                f"- Disease context: {_fmt(report.disease_context)}",
                f"- Target: {_fmt(report.transcription_factor)}",
                f"- Partner: {_fmt(report.mediator_subunit)}",
                f"- Confidence: **{report.confidence}**", "",
                _fmt(report.hero_hypothesis, ""), "",
            ]
        else:
            lines += ["### Recommendation", "",
                      "**None.** The run did not reach an approved recommendation.",
                      f" Status: `{state.status}`, stage: `{state.stage}`.", ""]

        # -- rejections, which are half the value ----------------------------
        lines += ["### What it rejected, and why", ""]
        if rejections:
            lines += ["| Candidate | Gate | Reason |", "|---|---|---|"]
            for record in rejections:
                gates = record.get("failed_gates") or []
                reasons = record.get("reasons") or []
                for gate, reason in zip(gates, reasons, strict=False):
                    lines.append(
                        f"| `{record['candidate_id']}` | `{gate}` | {reason} |"
                    )
            lines.append("")
        else:
            lines += ["No candidate was rejected.", "",
                      "> A run that only ever says yes has not demonstrated "
                      "judgement. Treat an empty rejection table as a reason to "
                      "check the gates, not as a good sign.", ""]

        # -- human decisions -------------------------------------------------
        lines += ["### Human decisions", ""]
        if checkpoints:
            lines += ["| Checkpoint | Stage | Status | Resolved by |", "|---|---|---|---|"]
            for checkpoint in checkpoints:
                lines.append(
                    f"| `{checkpoint.checkpoint_id}` | {checkpoint.stage} | "
                    f"**{checkpoint.status}** | {_fmt(checkpoint.resolved_by, '—')} |"
                )
            lines.append("")
        else:
            lines += ["No checkpoint was created.", ""]

        # -- the next experiment ---------------------------------------------
        if experiment:
            lines += ["### The experiment it proposes", "",
                      f"**Question.** {experiment.scientific_question}", "",
                      f"**Perturbation.** {experiment.perturbation}", "",
                      f"**Readout.** {experiment.readout}", "",
                      "**How each outcome would change the hypothesis**", ""]
            for outcome in experiment.possible_outcomes:
                lines.append(f"- *{outcome.outcome}* → {outcome.interpretation_change}")
            lines.append("")

        # -- what it does not claim ------------------------------------------
        lines += ["### What this does not claim", ""]
        claims = [
            "Nothing here is evidence of binding, safety, efficacy, or "
            "experimental validation. Every result is computational.",
        ]
        if comparison is not None:
            claims.append(comparison.caveat)
        if report:
            claims += list(report.limitations)
        seen: set[str] = set()
        for claim in claims:
            if claim not in seen:
                lines.append(f"- {claim}")
                seen.add(claim)
        lines.append("")

        unresolved = []
        for path in self.store.path("structure").rglob("*.json"):
            if not path.name.startswith("CAND-"):
                continue
            try:
                payload = self.store.read_json(path)
            except (OSError, ValueError):
                continue
            unresolved += list(payload.get("unresolved_questions") or [])
        if unresolved:
            lines += ["### Unresolved questions", ""]
            lines += [f"- {q}" for q in dict.fromkeys(unresolved)]
            lines.append("")
        return lines

    # ------------------------------------------------------------- artifacts
    def _final_report(self) -> FinalReport | None:
        path = self.store.path("reports", "final_report.json")
        if not path.exists():
            return None
        return FinalReport.model_validate(self.store.read_json(path))

    def _experiment(self) -> NextExperiment | None:
        path = self.store.path("reports", "next_experiment.json")
        if not path.exists():
            return None
        return NextExperiment.model_validate(self.store.read_json(path))

    def _comparison(self) -> ModelComparison | None:
        path = self.store.path("structure", "comparison.json")
        if not path.exists():
            return None
        return ModelComparison.model_validate(self.store.read_json(path))

    # ----------------------------------------------------------------- build
    def render(self) -> str:
        state = self.store.load_state()
        header = [
            f"# Run report — `{state.run_id}`", "",
            f"- Generated: {utc_now()}",
            f"- Status: **{state.status}** at stage **{state.stage}**",
            f"- Constitution: `SOUL.md` v{self.soul.version}",
            f"- Config hash: `{state.config_hash[:16]}`",
            f"- Git commit: `{_fmt(state.git_commit, 'unknown')}`", "",
        ]
        if state.fixture_run:
            header += [
                "> **FIXTURE RUN.** Synthetic test data. Nothing in this report "
                "is scientific evidence.", "",
            ]
        header += [
            "This report is reconstructed from the run directory. Every number "
            "in it comes from an artifact on disk, not from a narration written "
            "alongside the run.", "", "---", "",
        ]
        body = (
            self._prompt_section() + ["---", ""]
            + self._research_section() + ["---", ""]
            + self._conclusions_section()
        )
        return "\n".join(header + body).rstrip() + "\n"

    def write(self, path: Path | None = None) -> Path:
        target = path or self.store.path("reports", "run_report.md")
        self.store.write_atomic(target, self.render())
        return target


def write_run_report(orchestrator: Any, path: Path | None = None) -> Path:
    return RunReporter(orchestrator).write(path)
