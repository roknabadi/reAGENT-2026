"""Prompt-length protection.

A stage prompt that silently overflows produces a confident answer built on a
truncated middle. This module makes the budget explicit: it measures, compacts
at 70%, checkpoints at 85%, and raises at the hard limit rather than sending an
oversized prompt.

Compaction is lossy about prose only. Source URLs, evidence IDs, numbers with
units, contradictions, missing evidence, rejection reasons, human decisions, and
unresolved questions survive every compaction; full detail is rehydrated on
demand by stable evidence ID.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .models import Stage
from .soul import Soul

# Conservative: real tokenizers average ~4 chars/token on English prose, so
# dividing by 3 overestimates token count and errs toward compacting early.
CHARS_PER_TOKEN = 3.0

STAGE_PROMPT_MAX_TOKENS = 12_000
ACTIVE_HISTORY_MAX_TOKENS = 4_000
EVIDENCE_EXCERPT_MAX_TOKENS = 500

RESERVE_FRACTION = 0.20   # held back for tool output and the completion itself
COMPACT_THRESHOLD = 0.70
CHECKPOINT_THRESHOLD = 0.85


class BudgetAction(StrEnum):
    OK = "ok"
    COMPACT = "compact"
    CHECKPOINT = "checkpoint"
    FAIL = "fail"


class PromptTooLargeError(RuntimeError):
    """Raised at the hard limit so the run stops instead of truncating silently."""

    def __init__(self, tokens: int, limit: int, stage: str) -> None:
        self.tokens = tokens
        self.limit = limit
        self.stage = stage
        super().__init__(
            f"{stage} prompt is ~{tokens} estimated tokens, over the hard limit of "
            f"{limit}. Compact the evidence set or split the stage; the prompt was "
            "not sent."
        )


def estimate_tokens(text: str) -> int:
    """Conservative character-based estimate. Always labelled as estimated."""
    return int(len(text) / CHARS_PER_TOKEN) + 1


@dataclass
class BudgetReport:
    stage: str
    tokens: int
    limit: int
    usable: int
    fraction: float
    action: BudgetAction
    estimated: bool = True

    def as_detail(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "token_estimate": self.tokens,
            "limit": self.limit,
            "usable": self.usable,
            "fraction_used": round(self.fraction, 4),
            "action": str(self.action),
            "estimate_method": f"characters/{CHARS_PER_TOKEN} (conservative)",
            "estimated": self.estimated,
        }


@dataclass
class EvidenceSummary:
    """The compaction-safe projection of one evidence record.

    Every field here is preserved verbatim through compaction; prose beyond the
    claim excerpt is dropped and rehydrated by ``evidence_id`` when needed.
    """

    evidence_id: str
    claim: str
    interpretation: str
    source_url: str
    value: float | str | None = None
    unit: str | None = None
    supports: bool = True
    missing: bool = False
    contradicts: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def render(self, excerpt_tokens: int = EVIDENCE_EXCERPT_MAX_TOKENS) -> str:
        max_chars = int(excerpt_tokens * CHARS_PER_TOKEN)
        claim = self.claim
        if len(claim) > max_chars:
            claim = claim[: max_chars - 3].rstrip() + "..."
        parts = [f"[{self.evidence_id}] ({self.interpretation})", claim]
        if self.value is not None:
            parts.append(f"value={self.value}{f' {self.unit}' if self.unit else ''}")
        if not self.supports:
            parts.append("CONTRADICTS")
        if self.contradicts:
            parts.append(f"contradicts={','.join(self.contradicts)}")
        if self.missing:
            parts.append("MISSING")
        if self.limitations:
            parts.append(f"limits={'; '.join(self.limitations)}")
        parts.append(f"src={self.source_url}")
        return " | ".join(parts)


class ContextBudgetManager:
    """Measures and enforces the stage prompt budget."""

    def __init__(
        self,
        *,
        prompt_max: int = STAGE_PROMPT_MAX_TOKENS,
        history_max: int = ACTIVE_HISTORY_MAX_TOKENS,
        excerpt_max: int = EVIDENCE_EXCERPT_MAX_TOKENS,
        reserve_fraction: float = RESERVE_FRACTION,
    ) -> None:
        self.prompt_max = prompt_max
        self.history_max = history_max
        self.excerpt_max = excerpt_max
        self.reserve_fraction = reserve_fraction

    @property
    def reserved_tokens(self) -> int:
        return int(self.prompt_max * self.reserve_fraction)

    @property
    def usable_tokens(self) -> int:
        """What content may occupy once tool output and completion are reserved."""
        return self.prompt_max - self.reserved_tokens

    def inspect(self, text: str, *, stage: str = "unknown") -> BudgetReport:
        tokens = estimate_tokens(text)
        fraction = tokens / self.prompt_max
        if tokens >= self.prompt_max:
            action = BudgetAction.FAIL
        elif fraction >= CHECKPOINT_THRESHOLD:
            action = BudgetAction.CHECKPOINT
        elif fraction >= COMPACT_THRESHOLD:
            action = BudgetAction.COMPACT
        else:
            action = BudgetAction.OK
        return BudgetReport(
            stage=stage, tokens=tokens, limit=self.prompt_max,
            usable=self.usable_tokens, fraction=fraction, action=action,
        )

    def enforce(self, text: str, *, stage: str = "unknown") -> BudgetReport:
        """Inspect and raise at the hard limit. Callers trace the report either way."""
        report = self.inspect(text, stage=stage)
        if report.action is BudgetAction.FAIL:
            raise PromptTooLargeError(report.tokens, self.prompt_max, stage)
        return report

    def trim_history(self, history: list[str]) -> tuple[list[str], int]:
        """Keep the most recent history entries that fit; report how many dropped."""
        kept: list[str] = []
        used = 0
        for entry in reversed(history):
            cost = estimate_tokens(entry)
            if used + cost > self.history_max:
                break
            kept.append(entry)
            used += cost
        kept.reverse()
        return kept, len(history) - len(kept)

    def compact_evidence(
        self, summaries: list[EvidenceSummary], *, budget_tokens: int
    ) -> tuple[list[str], list[str]]:
        """Render evidence to fit ``budget_tokens``.

        Contradictions, non-supporting records, and missing markers are kept
        first: dropping them would make the prompt look cleaner than the data.
        Anything dropped is returned as a list of IDs so it stays rehydratable.
        """
        def priority(summary: EvidenceSummary) -> int:
            if summary.contradicts or not summary.supports:
                return 0
            if summary.missing:
                return 1
            return 2

        ordered = sorted(enumerate(summaries), key=lambda pair: (priority(pair[1]), pair[0]))
        rendered: dict[int, str] = {}
        dropped: list[str] = []
        used = 0
        for index, summary in ordered:
            line = summary.render(self.excerpt_max)
            cost = estimate_tokens(line)
            if used + cost > budget_tokens:
                dropped.append(summary.evidence_id)
                continue
            rendered[index] = line
            used += cost
        return [rendered[i] for i in sorted(rendered)], dropped


def build_stage_prompt(
    *,
    stage: Stage,
    objective: str,
    soul: Soul,
    run_state_summary: str,
    evidence_lines: list[str],
    available_actions: list[str],
    output_schema: str,
    stopping_conditions: list[str],
    history: list[str] | None = None,
) -> str:
    """Assemble a stage prompt from exactly the permitted sections.

    Nothing else goes in: no full evidence bodies, no accumulated transcript, no
    other stage's rules.
    """
    blocks = [
        f"# Objective ({stage})", objective, "",
        "# Rules in force",
        *(f"- {rule}" for rule in soul.for_stage(stage)), "",
        "# Run state", run_state_summary, "",
        "# Evidence",
        *(evidence_lines or ["(none available)"]), "",
        "# Available actions",
        *(f"- {action}" for action in available_actions), "",
        "# Output schema", output_schema, "",
        "# Stopping conditions",
        *(f"- {condition}" for condition in stopping_conditions),
    ]
    if history:
        blocks += ["", "# Recent history", *history]
    return "\n".join(blocks).rstrip() + "\n"
