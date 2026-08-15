"""Next-experiment proposal.

The output is falsifiable by construction: every possible outcome must say how
it would change the hypothesis, including the outcomes that would sink it.

The proposal is target-agnostic: it tests whether a dependency runs through a
named interaction, not through a particular class of protein. Where a candidate
declares ``target_class`` / ``partner_class`` (free text such as "kinase" /
"substrate") those words are used, so the experiment reads in the candidate's
own vocabulary; a candidate that declares neither falls back to "the target" and
"its interaction partner". The assays stay concrete either way, and the worked
example is named where the target's biology decides which readout applies.

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


def _named(gene: str, gene_class: str | None, fallback: str) -> str:
    """"the kinase AURKB" when a class is declared, else the fallback.

    ``fallback`` carries the neutral wording ("the target", "its interaction
    partner") so the sentence still reads when a candidate declares no class.
    """
    return f"the {gene_class} {gene}" if gene_class else f"{fallback} {gene}"


def propose_next_experiment(
    candidate: CandidateHypothesis,
    comparison: ModelComparison | None = None,
    *,
    applied_revisions: frozenset[str] = frozenset(),
) -> NextExperiment:
    """Propose the experiment that would most change the current belief."""
    target = candidate.target_gene
    partner = candidate.partner_gene
    target_phrase = _named(target, candidate.target_class, "the target")
    partner_phrase = _named(partner, candidate.partner_class, "its interaction partner")
    context = candidate.disease_context
    dependency = candidate.dependency

    question = (
        f"In {context} models, does the selective dependency on {target_phrase} "
        f"require its interaction with {partner_phrase}, rather than {target} "
        f"abundance alone?"
    )

    # The separation-of-function arm is the whole design: a mutant must lose the
    # interaction and keep everything else. Which activity "everything else"
    # means depends on the target's biology, so it is named as a worked example
    # rather than assumed.
    perturbation = (
        f"Two arms in {context} models and a non-dependent control lineage: "
        f"(1) degron-tagged {target} for acute depletion; (2) separation-of-function "
        f"{target} point mutants that disrupt the predicted {partner} interface while "
        f"preserving folding and the target's other activities (for a DNA-binding "
        f"target, its DNA binding), re-expressed in {target}-depleted cells."
    )

    readout = (
        f"Viability and proliferation over 7-10 days, paired with a direct readout "
        f"of the {target} downstream program (nascent transcription by PRO-seq or "
        f"TT-seq where that program is transcriptional), plus co-immunoprecipitation "
        f"of {target} with {partner} to confirm the mutants lose the interaction but "
        f"retain the target's other activities (for a DNA-binding target, chromatin "
        f"occupancy by CUT&RUN)."
    )

    positive_controls = [
        f"Acute {target} degradation, which should reduce viability in {context} models "
        f"(median gene effect {dependency.median_target_effect:.2f} "
        f"{dependency.effect_unit}).",
        "A pan-essential gene knockdown, to confirm assay sensitivity in every "
        "lineage tested.",
    ]
    negative_controls = [
        f"Non-dependent lineages, where {target} loss should not reduce viability "
        f"(out-of-context median {dependency.median_other_effect:.2f} "
        f"{dependency.effect_unit}).",
        f"{target} mutants outside the predicted interface, which should retain both "
        f"{partner} binding and rescue activity.",
        "Non-targeting guide and vehicle-only degron arms.",
    ]

    outcomes = [
        ExperimentOutcome(
            outcome=(
                f"Interface mutants fail to rescue viability and the {target} "
                f"downstream program stays off, while the target's other activities "
                "are intact."
            ),
            interpretation_change=(
                f"Supports the hypothesis that the {target}-{partner} interaction, not "
                f"{target} abundance, carries the dependency. Promotes the interface to "
                "a target-definition hypothesis worth structural follow-up."
            ),
        ),
        ExperimentOutcome(
            outcome=(
                f"Interface mutants rescue viability and the downstream program as "
                f"well as wild-type {target}."
            ),
            interpretation_change=(
                f"Refutes the {partner}-dependence of the phenotype. The dependency "
                f"on {target} would be real but independent of the {partner} "
                "interaction, and this candidate should be withdrawn from "
                "structure-based follow-up."
            ),
        ),
        ExperimentOutcome(
            outcome=(
                "Mutants lose viability but also lose the target's other activities "
                "in the specificity readout."
            ),
            interpretation_change=(
                "Uninterpretable for the interface question: the mutation is not "
                "separation-of-function. Requires new mutants before any conclusion, "
                "and the hypothesis is neither supported nor refuted."
            ),
        ),
        ExperimentOutcome(
            outcome=(
                f"Acute {target} depletion does not reduce viability in {context} models."
            ),
            interpretation_change=(
                "Contradicts the dependency evidence the candidate was selected on. "
                "The selection would need re-examination before anything downstream."
            ),
        ),
    ]

    limitations = [
        "Cell-line models do not reproduce the disease tissue microenvironment, and "
        "a dependency that holds in culture may not hold in vivo.",
    ]

    # Applied by the improvement loop when the rubric finds limitations too thin.
    if "limitations_stated" in applied_revisions:
        limitations += [
            "Viability cannot distinguish a direct requirement for the interaction "
            "from an indirect consequence of losing the target's downstream program; "
            "the downstream-program readout is what separates them, and only within "
            "its time resolution.",
            f"Co-immunoprecipitation reports association, not direct contact: a "
            f"third protein could bridge {target} and {partner}.",
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
