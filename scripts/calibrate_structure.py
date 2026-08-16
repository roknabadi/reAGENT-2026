#!/usr/bin/env python
"""Structural calibration on a real, known-answer target pair.

Everything the structural stage has been exercised on so far is synthetic. This
runs it on **ELK1-MED23**, which the project uses as a positive control because
the answer is already known: a 3.0 A cryo-EM structure of MED23 bound to the
phosphorylated ELK1 transactivation domain (PDB 9F6Y), with the binding motif
mapped to ELK1 residues 374-384.

That is what makes it worth GPU time. A consensus machine that cannot recover a
known interface is miscalibrated, and we would rather find that out on a case
with an answer than on a novel candidate where a wrong answer looks like a
result.

    python scripts/calibrate_structure.py              # validate, cost, no spend
    python scripts/calibrate_structure.py --dispatch   # live GPU run

Sequences are fetched from UniProt at run time rather than vendored, so the run
records which release it used. Nothing is written into a run directory: this is a
calibration of the structural stage, not a target claim, and ELK1-MED23 must
never enter a shortlist as a result.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from reagent_workflow.config import RunConfig  # noqa: E402
from reagent_workflow.models import (  # noqa: E402
    CandidateHypothesis,
    DependencyEvidence,
    InteractionEvidence,
    Interpretation,
    StructuralTractability,
)
from reagent_workflow.structure import (  # noqa: E402
    StructureCache,
    build_requests,
    compare_models,
    execute_request,
    proto_availability,
    validate_request,
)

# The calibration pair, and the evidence that makes it a known answer.
# Originally transcribed from the round-01 handoff in team/DECISIONS.md. That
# handoff has already had one transcription error corrected (a helix
# mis-assignment), so the constants below were re-checked rather than trusted.
#
# VERIFIED against UniProt at run time, on every invocation:
#   - ELK1 P19419 residues 374-384 are literally PSIHFWSTLSP, matching the
#     motif the handoff names (`verify_motif` refuses to continue otherwise).
#   - Both accessions carry the gene symbol they are named for, and both appear
#     as chains of 9F6Y in UniProt's own cross-references (`verify_accession`).
#     The transcribed *values* were checked from the start; that the accessions
#     pointed at the right *proteins* was assumed, and for MED23 it was false.
#
# VERIFIED against the primary paper via Paperclip (Monté et al., Nat. Commun.
# 2025, PMC12015215) on 2026-08-15. Every transcribed value held:
#   - L23: the motif is "PSIHFWSTLS p P ... MED23 Binding Motif", bound "on the
#     concave face of MED23, within the MED23 core, at the interface between HR2
#     and HR3"; Fig. 2 caption gives the interface as "MED23-Elk-1 (374-384)".
#   - L23: "F378-Elk-1 is surrounded in MED23 by side chains from residues I339,
#     L343 (H19), F379, G382, S383 (H21), and V533 and M537 (H28)". These are
#     MED23 residues. MED23 has its own residue 383, distinct from the ELK1
#     S383 phosphosite — the coincidence is real and the handoff got it right.
#   - Table 1 (L19): "MED23 Elk-1 (EMD-50242) (PDB 9F6Y)" at map resolution 3.0 A
#     (FSC 0.143). 9F76 is apo MED23, not the complex.
#   - L614: "Elk-1 [S383p] binds to MED23 with a Kd of 81 nM", by surface plasmon
#     resonance (L342).
#
# Only the motif position is used as a model input. The rest is recorded so the
# calibration can be judged against what the structure actually shows.
ELK1 = "P19419"
ELK1_GENE = "ELK1"
# MED23 is Q9ULK4, 1368 aa. This constant read O75448 until 2026-08-15, which is
# MED24 - a different subunit of the same Mediator tail module, 989 aa. Every
# line above verifies the ELK1 half against the paper; nothing verified the
# partner accession at all, so the control modelled the ELK1 motif against the
# wrong protein and then scored the answer using MED23 residue numbering. See
# team/FINDINGS_ELK1_CONTROL.md. `verify_accession` now checks both sides.
MED23 = "Q9ULK4"
MED23_GENE = "MED23"
ELK1_MOTIF = (374, 384)          # PSIHFWSTLS(p)P, the mapped MED23-binding motif
ELK1_MOTIF_SEQ = "PSIHFWSTLSP"   # asserted against the fetched sequence
ELK1_CONTEXT = (330, 428)        # motif plus flanking TAD, kept modelling tractable
STRUCTURE_PDB = "9F6Y"
CITATION = "doi:10.1038/s41467-025-59014-8 (PMC12015215) — verified at source 2026-08-15"


def verify_accession(accession: str, expected_gene: str,
                     gene_names: list[str], pdb_crossrefs: list[str]) -> list[str]:
    """Check that an accession is the protein the calibration means.

    This is the check that was missing. `verify_motif` verified the ELK1 half
    exhaustively, the partner half was verified not at all, and O75448 (MED24)
    sat in place of Q9ULK4 (MED23): same complex, adjacent subunit, plausible
    length, and every downstream number scored MED23 residue positions against
    MED24 coordinates. A wrong accession is the cheapest way to model the wrong
    protein and still get confident-looking numbers back.

    Two independent checks, because either alone can pass by accident: the gene
    symbol UniProt itself reports, and whether the reference structure lists
    this entry. MED24 fails both.
    """
    problems: list[str] = []
    names = [g.upper() for g in (gene_names or [])]
    if expected_gene.upper() not in names:
        problems.append(
            f"{accession} has gene names {gene_names or '[]'}, which do not "
            f"include {expected_gene}: this is not the protein named in the "
            f"calibration constants."
        )
    if STRUCTURE_PDB not in (pdb_crossrefs or []):
        problems.append(
            f"PDB {STRUCTURE_PDB} is not among UniProt's cross-references for "
            f"{accession} ({pdb_crossrefs or '[]'}): this entry is not a chain "
            f"of the reference structure."
        )
    return problems


def verify_motif(elk1_seq: str) -> list[str]:
    """Check the transcribed motif against the fetched primary record.

    Returns the list of problems. A silent mismatch here would model the wrong
    region of the wrong protein and still produce confident-looking numbers,
    which is the failure this whole calibration exists to catch. The
    cross-reference half of that check now lives in `verify_accession`, which
    applies it to both chains rather than only to ELK1.
    """
    problems: list[str] = []
    start, end = ELK1_MOTIF
    if len(elk1_seq) < end:
        problems.append(f"ELK1 sequence is {len(elk1_seq)} aa, shorter than motif end {end}")
        return problems
    observed = elk1_seq[start - 1:end]
    if observed != ELK1_MOTIF_SEQ:
        problems.append(
            f"motif mismatch: residues {start}-{end} are {observed!r}, "
            f"expected {ELK1_MOTIF_SEQ!r}. The transcribed position may be wrong, "
            "or the UniProt entry has been renumbered."
        )
    return problems


def fetch_sequence(accession: str, name: str) -> tuple[str, str, list[str], list[str]]:
    """Return (sequence, source_url, pdb_crossrefs, gene_names) from UniProt.

    The gene names are what let the caller check that the accession is the
    protein it thinks it is, so they are returned rather than dropped.
    """
    from proto_tools.tools.database_retrieval.uniprot import (  # noqa: PLC0415
        UniProtFetchConfig,
        UniProtFetchInput,
        run_uniprot_fetch,
    )

    output = run_uniprot_fetch(
        UniProtFetchInput(uniprot_id=accession, target_name=name),
        UniProtFetchConfig(),
    )
    payload = output.model_dump(mode="json")
    sequence = payload.get("sequence") or ""
    if not sequence:
        raise RuntimeError(f"UniProt returned no sequence for {accession}")
    return (
        sequence,
        payload.get("source_url") or f"https://www.uniprot.org/uniprotkb/{accession}",
        list(payload.get("pdb_crossrefs") or []),
        list(payload.get("gene_names") or []),
    )


def build_candidate(
    *, full_partner: bool, elk1_seq: str, med23_seq: str
) -> CandidateHypothesis:
    """A real candidate for the structural stage only.

    The dependency block carries the real fact that ELK1 has no established
    selective dependency, rather than invented numbers: this pair is a
    structural control, and pretending otherwise is the failure the project
    exists to catch.
    """
    start, end = ELK1_CONTEXT
    target_region_seq = elk1_seq[start - 1:end]
    partner_seq = med23_seq if full_partner else med23_seq[:600]

    return CandidateHypothesis(
        candidate_id="CALIB-ELK1-MED23",
        disease_context="calibration control (not a disease claim)",
        target_gene="ELK1",
        partner_gene="MED23",
        target_class="transcription factor",
        partner_class="Mediator subunit",
        hypothesis=(
            "CALIBRATION CONTROL. ELK1 residues 374-384 bind the MED23 core; a "
            "3.0 A cryo-EM structure exists (PDB 9F6Y). Used to check whether the "
            "consensus machinery recovers a known interface. Never a result."
        ),
        dependency=DependencyEvidence(
            gene="ELK1",
            disease_context="calibration control (not a disease claim)",
            n_target_models=0, n_other_models=0,
            median_target_effect=0.0, median_other_effect=0.0,
            target_dependent_fraction=0.0, other_dependent_fraction=0.0,
            selectivity_delta=0.0,
            evidence_ids=[], source_id="SRC-CALIB-NOTE",
        ),
        interaction=InteractionEvidence(
            target_gene="ELK1", partner_gene="MED23",
            interaction_support=0.95,
            interaction_type="direct_binding",
            assay="cryo-EM structure of the MED23-ELK1 TAD complex (PDB 9F6Y)",
            interpretation=Interpretation.OBSERVED,
            interacting_region_mapped=True,
            target_region=(
                f"transactivation domain, MED23-binding motif residues "
                f"{ELK1_MOTIF[0]}-{ELK1_MOTIF[1]}"
            ),
            # Real evidence ids, not placeholders: is_supported requires them,
            # and an empty list correctly refused to build any request at all.
            evidence_ids=["EV-CALIB-9F6Y", "EV-CALIB-MOTIF"],
            source_id="SRC-CALIB-STRUCTURE",
            limitations=[
                "Calibration control. A recovered interface here says the method "
                "works on a case it may resemble, not that a novel prediction is "
                "correct.",
            ],
        ),
        tractability=StructuralTractability(
            target_uniprot=ELK1, partner_uniprot=MED23,
            target_sequence=target_region_seq,
            partner_sequence=partner_seq,
            domain_bounded=True,
            notes=[
                "interface_tractability=short_linear_motif",
                f"ELK1 modelled as residues {ELK1_CONTEXT[0]}-{ELK1_CONTEXT[1]} "
                f"(motif {ELK1_MOTIF[0]}-{ELK1_MOTIF[1]} plus flanking context)",
                # 1-600 is not an arbitrary cut: the published pocket runs
                # I339-M537, so the truncation keeps every residue the result is
                # scored against.
                f"MED23 modelled as {'full length' if full_partner else 'residues 1-600'}",
                f"experimental reference: PDB {STRUCTURE_PDB}, {CITATION}",
            ],
        ),
        evidence_ids=[],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dispatch", action="store_true",
                        help="actually run on Modal (spends GPU credits)")
    parser.add_argument("--replicates", type=int, default=3)
    parser.add_argument("--truncate-partner", action="store_true",
                        help="model MED23 1-600 instead of full length, to cut cost")
    args = parser.parse_args()

    availability = proto_availability()
    print(f"proto_tools available : {availability.available} ({availability.detail})")

    print("\nfetching real sequences from UniProt...")
    elk1_seq, elk1_url, elk1_pdbs, elk1_genes = fetch_sequence(ELK1, "ELK1")
    med23_seq, med23_url, med23_pdbs, med23_genes = fetch_sequence(MED23, "MED23")
    print(f"  ELK1  {ELK1}: {len(elk1_seq)} aa  gene {elk1_genes}   {elk1_url}")
    print(f"  MED23 {MED23}: {len(med23_seq)} aa  gene {med23_genes}   {med23_url}")

    # Both chains, not just ELK1: checking one side and assuming the other is
    # how MED24 got modelled as MED23 for a day.
    problems = (verify_accession(ELK1, ELK1_GENE, elk1_genes, elk1_pdbs)
                + verify_accession(MED23, MED23_GENE, med23_genes, med23_pdbs)
                + verify_motif(elk1_seq))
    motif = elk1_seq[ELK1_MOTIF[0] - 1:ELK1_MOTIF[1]]
    print(f"\nverifying transcribed constants against the primary record:")
    print(f"  residues {ELK1_MOTIF[0]}-{ELK1_MOTIF[1]} = {motif}  "
          f"(expected {ELK1_MOTIF_SEQ})")
    print(f"  {ELK1} is {ELK1_GENE} and a chain of {STRUCTURE_PDB}: "
          f"{elk1_pdbs}")
    print(f"  {MED23} is {MED23_GENE} and a chain of {STRUCTURE_PDB}: "
          f"{med23_pdbs}")
    if problems:
        print("\nREFUSING: the transcribed constants do not match the record.",
              file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2
    print("  both accessions and the motif confirmed against UniProt.")
    print("  primary paper verified via Paperclip (PMC12015215): 9F6Y is the "
          "MED23-Elk-1 complex at 3.0 A, interface residues and Kd 81 nM (SPR) "
          "all confirmed. See the header for line-pinned quotes.")

    candidate = build_candidate(
        full_partner=not args.truncate_partner,
        elk1_seq=elk1_seq, med23_seq=med23_seq,
    )
    config = RunConfig().model_copy(update={
        "structure_replicates": args.replicates,
        "allow_live_modal": args.dispatch,
    })

    requests = build_requests(candidate, config)
    print(f"\nrequests built: {len(requests)}")
    total_residues = 0
    for request in requests:
        residues = sum(len(c.sequence) for c in request.chains)
        total_residues += residues * config.structure_replicates
        report = validate_request(request)
        status = "VALID" if report["valid"] else f"INVALID {report['blockers']}"
        print(f"  {request.model:<11} {request.purpose:<19} "
              f"{residues:>5} aa  {status}")
        if not report["valid"]:
            return 2

    jobs = len(requests) * config.structure_replicates
    print(f"\nlive cost estimate: {jobs} GPU jobs "
          f"({len(requests)} requests x {config.structure_replicates} replicates), "
          f"{total_residues} residue-predictions total")

    if not args.dispatch:
        print("\nvalidation only. Re-run with --dispatch to spend GPU credits.")
        return 0

    if not availability.modal_available:
        print("\nBLOCKED: the Modal client is not importable.", file=sys.stderr)
        return 3

    caches = [StructureCache(REPO_ROOT / "outputs" / "structure_cache_calibration")]
    results = []
    for request in requests:
        print(f"\ndispatching {request.request_id} ...")
        result = execute_request(
            request, config, caches=caches, approved=True, validation_only=False
        )
        print(f"  status={result.status} source={result.source} "
              f"confidence={result.confidence}")
        results.append(result)

    boltz = next((r for r in results if r.model == "boltz2"), None)
    alphafold = next((r for r in results if r.model == "alphafold2"), None)
    esmfold = [r for r in results if r.model == "esmfold2"]
    comparison = compare_models(candidate.candidate_id, boltz, esmfold, alphafold)

    print("\n=== calibration result ===")
    print(f"consensus : {comparison.consensus}")
    print(f"verdict   : {comparison.verdict}")
    print(f"CI overlap: {comparison.ci_overlap}")
    for line in comparison.agreements:
        print(f"  + {line}")
    for line in comparison.disagreements:
        print(f"  - {line}")
    print(f"\n{comparison.caveat}")

    out = REPO_ROOT / "outputs" / "calibration_elk1_med23.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "candidate": candidate.model_dump(mode="json"),
        "comparison": comparison.model_dump(mode="json"),
        "results": [r.model_dump(mode="json") for r in results],
        "sources": {"ELK1": elk1_url, "MED23": med23_url,
                    "structure": STRUCTURE_PDB, "citation": CITATION},
    }, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\nwritten: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
