"""Next-experiment proposal.

The output is falsifiable by construction: every possible outcome must say how
it would change the hypothesis, including the outcomes that would sink it.

The generator accepts revision directives from the bounded self-improvement
loop. A directive adds reasoning the first pass skipped; it cannot loosen the
rubric or change what the experiment claims.
"""

from __future__ import annotations

from .models import (
    CandidateHypothesis,
    ExperimentOutcome,
    ModelComparison,
    NextExperiment,
)


def propose_next_experiment(
    candidate: CandidateHypothesis,
    comparison: ModelComparison | None = None,
    *,
    applied_revisions: frozenset[str] = frozenset(),
) -> NextExperiment:
    """Propose the experiment that would most change the current belief."""
    tf = candidate.transcription_factor
    subunit = candidate.mediator_subunit
    context = candidate.disease_context
    dependency = candidate.dependency

    question = (
        f"In {context} models, does the selective dependency on {tf} require its "
        f"interaction with the Mediator subunit {subunit}, rather than {tf} "
        f"abundance alone?"
    )

    perturbation = (
        f"Two arms in {context} models and a non-dependent control lineage: "
        f"(1) degron-tagged {tf} for acute depletion; (2) separation-of-function "
        f"{tf} point mutants that disrupt the predicted {subunit} interface while "
        f"preserving DNA binding, re-expressed in {tf}-depleted cells."
    )

    readout = (
        f"Viability and proliferation over 7-10 days, paired with nascent "
        f"transcription (PRO-seq or TT-seq) of the {tf} target program, plus "
        f"co-immunoprecipitation of {tf} with {subunit} to confirm the mutants "
        f"lose the interaction but retain chromatin binding (CUT&RUN)."
    )

    positive_controls = [
        f"Acute {tf} degradation, which should reduce viability in {context} models "
        f"(median gene effect {dependency.median_target_effect:.2f} "
        f"{dependency.effect_unit}).",
        "A pan-essential gene knockdown, to confirm assay sensitivity in every "
        "lineage tested.",
    ]
    negative_controls = [
        f"Non-dependent lineages, where {tf} loss should not reduce viability "
        f"(out-of-context median {dependency.median_other_effect:.2f} "
        f"{dependency.effect_unit}).",
        f"{tf} mutants outside the predicted interface, which should retain both "
        f"{subunit} binding and rescue activity.",
        "Non-targeting guide and vehicle-only degron arms.",
    ]

    outcomes = [
        ExperimentOutcome(
            outcome=(
                f"Interface mutants fail to rescue viability and the {tf} target "
                f"program stays off, while DNA binding is intact."
            ),
            interpretation_change=(
                f"Supports the hypothesis that the {tf}-{subunit} interaction, not "
                f"{tf} abundance, carries the dependency. Promotes the interface to "
                "a target-definition hypothesis worth structural follow-up."
            ),
        ),
        ExperimentOutcome(
            outcome=(
                f"Interface mutants rescue viability and transcription as well as "
                f"wild-type {tf}."
            ),
            interpretation_change=(
                f"Refutes the {subunit}-dependence of the phenotype. The dependency "
                f"on {tf} would be real but Mediator-independent, and this "
                "candidate should be withdrawn from structure-based follow-up."
            ),
        ),
        ExperimentOutcome(
            outcome=(
                "Mutants lose viability but also lose chromatin binding in CUT&RUN."
            ),
            interpretation_change=(
                "Uninterpretable for the interface question: the mutation is not "
                "separation-of-function. Requires new mutants before any conclusion, "
                "and the hypothesis is neither supported nor refuted."
            ),
        ),
        ExperimentOutcome(
            outcome=(
                f"Acute {tf} depletion does not reduce viability in {context} models."
            ),
            interpretation_change=(
                "Contradicts the dependency evidence the candidate was selected on. "
                "The selection would need re-examination before anything downstream."
            ),
        ),
    ]

    limitations = [
        "Cell-line models do not reproduce the tumour microenvironment, and a "
        "dependency that holds in culture may not hold in vivo.",
    ]

    # Applied by the improvement loop when the rubric finds limitations too thin.
    if "limitations_stated" in applied_revisions:
        limitations += [
            "Viability cannot distinguish a direct transcriptional requirement from "
            "an indirect consequence of losing the target program; the nascent "
            "transcription arm is what separates them, and only within its time "
            "resolution.",
            f"Co-immunoprecipitation reports association, not direct contact: a "
            f"third protein could bridge {tf} and {subunit}.",
            "Degron systems have basal degradation and off-target proteolysis; "
            "rescue must be compared against tagged wild-type, not untagged cells.",
        ]
        if comparison is not None and comparison.verdict != "consistent":
            limitations.append(
                "The predicted interface used to design the mutants is a "
                "computational prediction with unresolved model disagreement; "
                "mutant design should be revisited if the structural picture changes."
            )

    return NextExperiment(
        candidate_id=candidate.candidate_id,
        scientific_question=question,
        perturbation=perturbation,
        readout=readout,
        positive_controls=positive_controls,
        negative_controls=negative_controls,
        possible_outcomes=outcomes,
        limitations=limitations,
    )
