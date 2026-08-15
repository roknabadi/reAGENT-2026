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
# VERIFIED independently against UniProt at build time:
#   - ELK1 P19419 residues 374-384 are literally PSIHFWSTLSP, matching the
#     motif the handoff names (checked by `verify_motif` below, which runs on
#     every invocation and refuses to continue if the sequence has moved).
#   - PDB 9F6Y appears in UniProt's own cross-references for P19419.
#
# NOT verified here, because it needs the primary paper and Paperclip is not
# authenticated in this environment (`paperclip login` opens a browser):
#   - that 9F6Y is specifically the MED23-ELK1 complex, and its 3.0 A resolution
#   - the MED23 interface residues (I339, L343, F379, G382, S383, V533, M537)
#   - the reported SPR Kd of 81 nM
# Those remain second-hand. They are not used as inputs to the model — only the
# motif position is — but do not cite them from this file.
ELK1 = "P19419"
MED23 = "O75448"
ELK1_MOTIF = (374, 384)          # PSIHFWSTLS(p)P, the mapped MED23-binding motif
ELK1_MOTIF_SEQ = "PSIHFWSTLSP"   # asserted against the fetched sequence
ELK1_CONTEXT = (330, 428)        # motif plus flanking TAD, kept modelling tractable
STRUCTURE_PDB = "9F6Y"
CITATION = "doi:10.1038/s41467-025-59014-8 (PMC12015215) — second-hand, unverified here"


def verify_motif(elk1_seq: str, pdb_crossrefs: list[str]) -> list[str]:
    """Check the transcribed constants against the fetched primary record.

    Returns the list of problems. A silent mismatch here would model the wrong
    region of the wrong protein and still produce confident-looking numbers,
    which is the failure this whole calibration exists to catch.
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
    if STRUCTURE_PDB not in (pdb_crossrefs or []):
        problems.append(
            f"PDB {STRUCTURE_PDB} is not among UniProt's cross-references "
            f"{pdb_crossrefs}; the structure reference may be wrong."
        )
    return problems


def fetch_sequence(accession: str, name: str) -> tuple[str, str, list[str]]:
    """Return (sequence, source_url, pdb_crossrefs) from UniProt via Proto."""
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
    elk1_seq, elk1_url, elk1_pdbs = fetch_sequence(ELK1, "ELK1")
    med23_seq, med23_url, _ = fetch_sequence(MED23, "MED23")
    print(f"  ELK1  {ELK1}: {len(elk1_seq)} aa   {elk1_url}")
    print(f"  MED23 {MED23}: {len(med23_seq)} aa   {med23_url}")

    problems = verify_motif(elk1_seq, elk1_pdbs)
    motif = elk1_seq[ELK1_MOTIF[0] - 1:ELK1_MOTIF[1]]
    print(f"\nverifying transcribed constants against the primary record:")
    print(f"  residues {ELK1_MOTIF[0]}-{ELK1_MOTIF[1]} = {motif}  "
          f"(expected {ELK1_MOTIF_SEQ})")
    print(f"  UniProt PDB cross-refs: {elk1_pdbs}")
    if problems:
        print("\nREFUSING: the transcribed constants do not match the record.",
              file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2
    print("  both constants confirmed against UniProt.")
    print("  NOT confirmed here (needs the primary paper; paperclip is not "
          "authenticated): that 9F6Y is the MED23 complex, its resolution, the "
          "interface residues, and the reported Kd.")

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
