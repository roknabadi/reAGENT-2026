"""Bounded recursive self-improvement.

One stage, at most two iterations, against a rubric the agent cannot reach.
The loop is: execute, evaluate, name one deficiency, propose one small revision,
record it, apply it, rerun the stage, compare, stop.

Two limits are structural rather than advisory:

- the rubric lives here and is hashed; ``improve_stage`` verifies the hash is
  unchanged after every rerun, so an agent that edited its evaluator to pass
  raises instead of scoring;
- a revision must name one of the allowed targets (prompt fragments, evidence
  requests, tool routing, explanations, failure recovery). Project goals,
  scientific boundaries, scoring rules, checkpoints, the evaluator, the
  iteration limit, and spending permissions are not reachable from here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .models import ImprovementIteration, Stage
from .store import content_hash, utc_now

MAX_ITERATIONS = 2

# Revision targets the agent may act on. Anything else is refused.
ALLOWED_REVISION_TARGETS = frozenset({
    "prompt_fragment", "evidence_request", "tool_routing",
    "explanation", "failure_recovery",
})


@dataclass(frozen=True)
class Criterion:
    """One rubric line: a name, a weight, and a deterministic check."""

    name: str
    weight: float
    description: str
    check: Callable[[dict[str, Any]], float]
    remedy: str
    revision_target: str


def _has_nonempty(artifact: dict[str, Any], key: str) -> float:
    value = artifact.get(key)
    if isinstance(value, list | dict | str):
        return 1.0 if value else 0.0
    return 1.0 if value is not None else 0.0


NEXT_EXPERIMENT_RUBRIC: tuple[Criterion, ...] = (
    Criterion(
        "question_is_specific", 0.15,
        "The scientific question names the disease context, the target gene, and "
        "its interaction partner.",
        lambda a: 1.0 if len(str(a.get("scientific_question", "")).split()) >= 12 else 0.0,
        "Restate the question so it names the context, the target gene, its "
        "interaction partner, and the measured relationship.",
        "explanation",
    ),
    Criterion(
        "controls_present", 0.2,
        "Both positive and negative controls are named.",
        lambda a: 0.5 * _has_nonempty(a, "positive_controls") + 0.5 * _has_nonempty(a, "negative_controls"),
        "Name at least one positive and one negative control.",
        "explanation",
    ),
    Criterion(
        "outcomes_are_discriminating", 0.3,
        "At least two possible outcomes, each stating how it changes the hypothesis.",
        lambda a: (
            1.0 if len(a.get("possible_outcomes", [])) >= 2
            and all(
                str(o.get("interpretation_change", "")).strip()
                for o in a.get("possible_outcomes", [])
            ) else (0.5 if len(a.get("possible_outcomes", [])) >= 2 else 0.0)
        ),
        "For every possible outcome, state explicitly how that result would change "
        "the hypothesis.",
        "explanation",
    ),
    Criterion(
        "limitations_stated", 0.2,
        "Limitations are explicit and mention what the readout cannot show.",
        lambda a: (
            1.0 if len(a.get("limitations", [])) >= 2
            else (0.5 if a.get("limitations") else 0.0)
        ),
        "Add the limitations of the readout, including what it cannot distinguish.",
        "explanation",
    ),
    Criterion(
        "perturbation_and_readout", 0.15,
        "A concrete perturbation and a concrete readout are both specified.",
        lambda a: 0.5 * _has_nonempty(a, "perturbation") + 0.5 * _has_nonempty(a, "readout"),
        "Specify the perturbation and the readout concretely enough to execute.",
        "explanation",
    ),
)

RUBRICS: dict[Stage, tuple[Criterion, ...]] = {
    Stage.NEXT_EXPERIMENT: NEXT_EXPERIMENT_RUBRIC,
}


def _check_fingerprint(check: Callable[[dict[str, Any]], float]) -> str:
    """Fingerprint the scoring function itself, not just its label.

    Hashing only name/weight/description left the anti-tamper check defeated by
    the exact attack it exists to stop: swapping ``check`` for ``lambda a: 1.0``
    scores every criterion perfect while the hash is unchanged. Bytecode plus
    constants catches a substituted function body.
    """
    code = getattr(check, "__code__", None)
    if code is None:
        return f"non-function:{type(check).__name__}"
    consts = [c for c in code.co_consts if isinstance(c, str | int | float | bool | None)]
    return content_hash({
        "bytecode": code.co_code.hex(),
        "names": list(code.co_names),
        "consts": consts,
        "argcount": code.co_argcount,
    })


def rubric_hash(stage: Stage) -> str:
    """Stable hash of the rubric, including each criterion's scoring function."""
    criteria = RUBRICS.get(stage, ())
    return content_hash([
        {"name": c.name, "weight": c.weight, "description": c.description,
         "remedy": c.remedy, "target": c.revision_target,
         "check": _check_fingerprint(c.check)}
        for c in criteria
    ])


class EvaluatorTamperError(RuntimeError):
    """Raised if the rubric changed during an improvement loop."""


@dataclass
class Evaluation:
    score: float
    per_criterion: dict[str, float]

    @property
    def weakest(self) -> str | None:
        if not self.per_criterion:
            return None
        return min(self.per_criterion, key=lambda name: self.per_criterion[name])


def evaluate(stage: Stage, artifact: dict[str, Any]) -> Evaluation:
    """Score a stage artifact against the fixed rubric."""
    criteria = RUBRICS.get(stage)
    if not criteria:
        raise ValueError(f"no rubric defined for stage {stage}")
    per_criterion = {c.name: max(0.0, min(1.0, c.check(artifact))) for c in criteria}
    score = sum(per_criterion[c.name] * c.weight for c in criteria)
    return Evaluation(score=round(min(1.0, score), 4), per_criterion=per_criterion)


@dataclass
class Revision:
    """One small, targeted change the agent proposes to itself."""

    criterion: str
    target: str
    instruction: str

    def __post_init__(self) -> None:
        if self.target not in ALLOWED_REVISION_TARGETS:
            raise ValueError(
                f"revision target {self.target!r} is outside what the agent may revise"
            )


def propose_revision(stage: Stage, evaluation: Evaluation) -> Revision | None:
    """Name exactly one deficiency and one small revision for it."""
    weakest = evaluation.weakest
    if weakest is None:
        return None
    criteria = {c.name: c for c in RUBRICS.get(stage, ())}
    criterion = criteria.get(weakest)
    if criterion is None or evaluation.per_criterion[weakest] >= 1.0:
        return None
    return Revision(
        criterion=criterion.name,
        target=criterion.revision_target,
        instruction=criterion.remedy,
    )


def improve_stage(
    *,
    run_id: str,
    stage: Stage,
    artifact: dict[str, Any],
    rerun: Callable[[Revision], dict[str, Any]],
    max_iterations: int = MAX_ITERATIONS,
) -> tuple[dict[str, Any], list[ImprovementIteration]]:
    """Run the bounded improvement loop over one stage artifact.

    Returns the best artifact and the iteration records. The revised artifact is
    kept only if it actually scores higher; a regression is recorded and rolled
    back rather than shipped.
    """
    iterations: list[ImprovementIteration] = []
    if max_iterations < 1 or stage not in RUBRICS:
        return artifact, iterations

    baseline_rubric = rubric_hash(stage)
    current = artifact
    current_eval = evaluate(stage, current)

    for iteration_index in range(1, min(max_iterations, MAX_ITERATIONS) + 1):
        revision = propose_revision(stage, current_eval)
        if revision is None:
            iterations.append(ImprovementIteration(
                run_id=run_id, stage=stage, iteration=iteration_index,
                critique="No rubric criterion scores below its maximum.",
                deficiency="none",
                proposed_revision="none",
                revision_target="explanation",
                applied=False,
                before_score=current_eval.score,
                after_score=current_eval.score,
                improved=False,
                stopping_reason="rubric satisfied; nothing to revise",
                recorded_at=utc_now(),
            ))
            break

        before_score = current_eval.score
        revised = rerun(revision)

        if rubric_hash(stage) != baseline_rubric:
            raise EvaluatorTamperError(
                f"the {stage} rubric changed during improvement; refusing to score"
            )

        revised_eval = evaluate(stage, revised)
        improved = revised_eval.score > before_score
        last_iteration = iteration_index >= min(max_iterations, MAX_ITERATIONS)

        if improved:
            stopping_reason = (
                "iteration limit reached" if last_iteration
                else "improvement applied; continuing within the iteration limit"
            )
        else:
            stopping_reason = "revision did not improve the rubric score; rolled back"

        iterations.append(ImprovementIteration(
            run_id=run_id, stage=stage, iteration=iteration_index,
            critique=(
                f"Criterion {revision.criterion!r} scored "
                f"{current_eval.per_criterion[revision.criterion]:.2f} of 1.00."
            ),
            deficiency=revision.criterion,
            proposed_revision=revision.instruction,
            revision_target=revision.target,
            changed_artifact=f"reports/next_experiment.json ({stage})",
            applied=improved,
            before_score=before_score,
            after_score=revised_eval.score,
            improved=improved,
            stopping_reason=stopping_reason,
            recorded_at=utc_now(),
        ))

        if not improved:
            break
        current, current_eval = revised, revised_eval
        if last_iteration:
            break

    return current, iterations
