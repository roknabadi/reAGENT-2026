#!/usr/bin/env python
"""Build ui/data.json — everything the interface renders, from real sources.

No server, no live calls: the page loads one file. Regenerate after a new run.

Needs downloads/CRISPRGeneEffect.csv, downloads/Model.csv, downloads/lambert_tfs.csv
(all gitignored, fetch commands in team/TASKS.md) and, for the structure panel,
a local mmCIF. Anything missing is skipped with a warning rather than faked.
"""
from __future__ import annotations

import json
import pathlib
import sys
import urllib.request

from dependency_scout.depmap import analyze_gene_effects, load_tf_universe
from dependency_scout.models import MediatorLink, RankedCandidate
from dependency_scout.ranking import gate, rank_all
from dependency_scout.provenance import InputFile, RunProvenance
from dependency_scout.report import build_shortlist

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOWNLOADS, UI = ROOT / "downloads", ROOT / "ui"

LANDSCAPE_CONTEXT = "Lung"          # the worked negative case: 1588 screened, 0 pass
MIN_MODELS = 15                     # Kevin's confidence floor
ROUND02_GENES = {"IRF4", "TP63", "TCF7L2", "EBF1", "PAX8", "ZNF217", "ISL1", "MYCN",
                 "RUNX2", "CEBPB", "ETV1", "ELK1", "ELF3"}
ROUND02_CONTEXTS = ["Lymphoid", "Head and Neck", "Bowel", "Ovary/Fallopian Tube",
                    "Kidney", "Peripheral Nervous System", "Myeloid", "Skin", "Breast"]
INTERFACE = {
    "RUNX2": "examples/mediator_link_runx2_med23.json",
    "CEBPB": "examples/mediator_link_cebpb_med23.json",
    "ETV1": "examples/mediator_link_etv1_med23.json",
    "ELK1": "examples/mediator_link_elk1_med23.json",
    "POU2F3": "examples/coactivator_link_pou2f3_ocat1.json",
    "FOXO4": "examples/coactivator_link_foxo4_kix.json",
}
# Experimental structures we hold coordinates for, keyed by the target-partner
# pair each one resolves. One entry per real deposition. A pair with no entry
# gets no structure: that absence is the result, not a gap to fill with another
# pair's coordinates or with an unreviewed prediction.
STRUCTURES = {
    # ELK1 MED23-binding motif and the MED23 residues lining the pocket (PMC12015215).
    ("ELK1", "MED23"): {
        "pdb_id": "9F6Y", "resolution_a": 3.0, "method": "cryo-EM",
        "citation": "https://doi.org/10.1038/s41467-025-59014-8",
        "backbone_chain": "A", "peptide_chain": "B",
        "chains": {"A": "MED23", "B": "ELK1 MED23-binding motif"},
        "peptide_residues": list(range(374, 385)),
        "contact_residues": [339, 343, 379, 382, 383, 533, 537],
        "peptide_label": "ELK1 motif 374-384",
        "contact_label": "MED23 pocket residues",
        "sequence": "PSIHFWSTLS(p)P",
        "sequence_note": "residues 374-384 of the ELK1 transactivation domain. F378 buries deepest.",
        "site_note": "The concave face at the HR2/HR3 interface. An eleven-residue motif in a "
                     "defined groove is the tractable case - a folded-domain interface presents "
                     "no comparable site.",
    },
    # POU2F3 POU domains with the OCA-T1 peptide on DNA (PMC12755459). Every
    # highlighted residue is one the paper names: the LEELE motif and L235/L237
    # on POU2F3, and the OCA-T1 V22 whose V22E substitution abolishes binding.
    ("POU2F3", "POU2AF2"): {
        "pdb_id": "9PFP", "resolution_a": 1.7, "method": "X-ray diffraction",
        "citation": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12755459/",
        "backbone_chain": "A", "peptide_chain": "B",
        "chains": {"A": "POU2F3 POU domains", "B": "OCA-T1 (POU2AF2) peptide"},
        "peptide_residues": [22],
        "contact_residues": [188, 189, 190, 191, 192, 235, 237],
        "peptide_label": "OCA-T1 peptide (V22 marked)",
        "contact_label": "POU2F3 contact residues",
        "sequence": "LEELE",
        "sequence_note": "residues 188-192 in the alpha-1 helix of the POU_S domain; "
                         "L235 and L237 in the alpha3-alpha4 turn add a second contact.",
        "site_note": "A shallow, conserved groove cradling the coactivator peptide. The "
                     "authors' CASTpFold pocket volume is a computation on this structure, "
                     "not a measured site: ligandability here is a prediction.",
    },
}


def thresholds() -> dict:
    """Read the live gate constants from Kevin's dependency_flag gate
    (tests/re-agent_discovery/src/config.py, applied in stage1_depmap.py),
    NOT dependency_scout.ranking.gate.

    Those are two different gates. dependency_scout.ranking.gate is a single
    median-based path (median <= -0.5 AND target_frac >= 0.5 AND
    other_frac <= 0.35 AND selectivity_delta >= 0.35) and is what this file
    used to report here -- accurately for that gate, but that gate is not
    what decides dependency_flag in the candidates below. Kevin's gate is
    two independent OR'd paths (median-based, same shape as above but a
    looser other-median floor; or a specificity-first path on raw fractions
    for subtype-restricted dependencies a group median dilutes away -- see
    stage1_depmap.py's _dependency_flag). Reporting the wrong one here drew
    a scatterplot boundary that disagreed with the dependency_flag verdicts
    actually attached to the points.
    """
    import sys as _sys
    kevin_src = ROOT / "tests" / "re-agent_discovery" / "src"
    _sys.path.insert(0, str(kevin_src))
    import config as kevin_config  # noqa: E402

    return {
        "median_route": {
            "median_target_effect_max": kevin_config.IN_CONTEXT_DEPENDENCY_THRESHOLD,
            "other_median_effect_min": kevin_config.OUT_OF_CONTEXT_NONDEPENDENCY_THRESHOLD,
        },
        "specificity_first_route": {
            "target_dependent_fraction_min": kevin_config.SPECIFICITY_FIRST_MIN_TARGET_FRACTION,
            "other_dependent_fraction_max": kevin_config.SPECIFICITY_FIRST_MAX_OTHER_FRACTION,
        },
        "route_logic": "pass if EITHER route clears -- see stage1_depmap.py _dependency_flag",
        "min_models": kevin_config.SUBTYPE_MIN_N_FLOOR,
        "confidence_floor": kevin_config.SUBTYPE_MIN_N_FULL_CONFIDENCE,
        "_source": "tests/re-agent_discovery/src/config.py (Kevin's dependency_flag gate)",
    }


def uniprot_accession(gene: str) -> tuple[str, str, int] | None:
    """Reviewed human entry for a gene symbol. Returns (accession, name, length)."""
    url = ("https://rest.uniprot.org/uniprotkb/search?query=gene_exact:"
           f"{gene}+AND+organism_id:9606+AND+reviewed:true"
           "&fields=accession,protein_name,length&format=json&size=1")
    with urllib.request.urlopen(url, timeout=30) as r:
        hits = json.load(r).get("results") or []
    if not hits:
        return None
    h = hits[0]
    name = h["proteinDescription"]["recommendedName"]["fullName"]["value"]
    return h["primaryAccession"], name, h["sequence"]["length"]


def alphafold_model(acc: str) -> dict | None:
    """AlphaFold DB entry metadata. The monomer only: AFDB holds no complexes."""
    try:
        with urllib.request.urlopen(
                f"https://alphafold.ebi.ac.uk/api/prediction/{acc}", timeout=30) as r:
            entries = json.load(r)
    except Exception as e:
        print(f"  alphafold lookup failed for {acc}: {e}", file=sys.stderr)
        return None
    return entries[0] if entries else None


def af_trace(cif: pathlib.Path) -> list[list]:
    """CA trace with per-residue pLDDT from the B-factor column of an AFDB model."""
    lines = cif.read_text().splitlines()
    i = next(k for k, l in enumerate(lines) if l.startswith("_atom_site."))
    cols = []
    while lines[i].startswith("_atom_site."):
        cols.append(lines[i].strip().split(".")[1])
        i += 1
    idx = {c: n for n, c in enumerate(cols)}
    out = []
    for l in lines[i:]:
        if l.startswith("#"):
            break
        f = l.split()
        if len(f) < len(cols) or f[idx["label_atom_id"]] != "CA":
            continue
        out.append([int(f[idx["label_seq_id"]]),
                    round(float(f[idx["Cartn_x"]]), 2),
                    round(float(f[idx["Cartn_y"]]), 2),
                    round(float(f[idx["Cartn_z"]]), 2),
                    round(float(f[idx["B_iso_or_equiv"]]), 1)])
    return out


def ca_trace(cif: pathlib.Path) -> dict:
    lines = cif.read_text().splitlines()
    i = next(k for k, l in enumerate(lines) if l.startswith("_atom_site."))
    cols = []
    while lines[i].startswith("_atom_site."):
        cols.append(lines[i].strip().split(".")[1])
        i += 1
    idx = {c: n for n, c in enumerate(cols)}
    chains: dict[str, list] = {}
    for l in lines[i:]:
        if l.startswith("#"):
            break
        f = l.split()
        if len(f) < len(cols) or f[idx["label_atom_id"]] != "CA":
            continue
        chains.setdefault(f[idx["auth_asym_id"]], []).append([
            int(f[idx["auth_seq_id"]]),
            round(float(f[idx["Cartn_x"]]), 2),
            round(float(f[idx["Cartn_y"]]), 2),
            round(float(f[idx["Cartn_z"]]), 2)])
    return chains


def main() -> int:
    ge, models = DOWNLOADS / "CRISPRGeneEffect.csv", DOWNLOADS / "Model.csv"
    tf_list = DOWNLOADS / "lambert_tfs.csv"
    if not ge.exists():
        print(f"missing {ge}; see team/TASKS.md", file=sys.stderr)
        return 1

    universe = load_tf_universe(tf_list)
    print(f"TF universe: {len(universe)}")

    # 1. The landscape: every TF in one context, with its gate verdict.
    landscape = []
    for c in rank_all(analyze_gene_effects(ge, models, context=LANDSCAPE_CONTEXT,
                                           genes=universe,
                                           source_version="DepMap Public 24Q2")):
        d = c.dependency
        landscape.append({
            "gene": d.gene, "n": d.n_target_models,
            "median": round(d.median_target_effect, 3),
            "sel": round(d.selectivity_delta, 3),
            "tfrac": round(d.target_dependent_fraction, 3),
            "ofrac": round(d.other_dependent_fraction, 3),
            "p": d.mann_whitney_p,
            "pass": c.gate.eligible,
            "why": c.gate.failures,
            "low_n": d.n_target_models < MIN_MODELS,
        })
    print(f"landscape: {len(landscape)} TFs, {sum(x['pass'] for x in landscape)} pass")

    # 2. Real candidates across lineages, joined to typed interface evidence.
    candidates, seen = [], set()
    for ctx in ROUND02_CONTEXTS:
        for c in rank_all(analyze_gene_effects(ge, models, context=ctx,
                                               genes=ROUND02_GENES,
                                               source_version="DepMap Public 24Q2")):
            if c.dependency.n_target_models < MIN_MODELS or not c.gate.eligible:
                continue
            if (p := INTERFACE.get(c.dependency.gene)):
                c.mediator = MediatorLink.model_validate_json(
                    (ROOT / p).read_text(encoding="utf-8"))
            candidates.append(c)
            seen.add(c.dependency.gene)
    # Genes added here have no dependency evidence in this run's context set
    # at all (they failed the loop above, or were never DepMap-tested here) --
    # their evidence for being on this page is purely the Mediator/literature
    # interface record, independent of dependency screening. ELK1 is the
    # proof case: structurally solved contact, zero DepMap signal anywhere.
    # Tracked explicitly so the UI never has to guess why a row with no
    # dependency data is present -- and never silently reads as "passed."
    mediator_literature_override_genes = set()
    for gene, p in INTERFACE.items():
        if gene not in seen:
            candidates.append(RankedCandidate(
                gene=gene,
                mediator=MediatorLink.model_validate_json(
                    (ROOT / p).read_text(encoding="utf-8"))))
            mediator_literature_override_genes.add(gene)

    sl = build_shortlist(candidates, disease_scope="pan-cancer, DepMap 24Q2")
    rows = []
    for i, c in enumerate(sl.candidates):
        ok, reason = c.shortlistable
        d = c.dependency
        is_override = c.name in mediator_literature_override_genes
        rows.append({
            "gene": c.name, "context": c.disease_context,
            "n": d.n_target_models if d else None,
            "median": round(d.median_target_effect, 3) if d else None,
            "sel": round(d.selectivity_delta, 3) if d else None,
            "tfrac": round(d.target_dependent_fraction, 3) if d else None,
            "ofrac": round(d.other_dependent_fraction, 3) if d else None,
            "score": round(c.final_score, 3) if c.final_score is not None else None,
            # Never leave this ambiguous: a candidate with no dependency
            # evidence in this run is a dependency-gate FAIL, not an unknown
            # -- it must never render as if it cleared DepMap screening.
            "gate_pass": False if is_override else (c.gate.eligible if c.gate else None),
            "gate_why": (["no DepMap dependency evidence in this run -- included via "
                          "Mediator/literature evidence instead, see inclusion_reason"]
                         if is_override else (c.gate.failures if c.gate else [])),
            "inclusion_reason": "mediator_literature_override" if is_override else "dependency_gate",
            "awaiting": c.awaiting_dependency_data,
            "shortlisted": i in sl.shortlist_indices,
            "blocked_because": reason,
            # MediatorLink.partner_gene defaults to MED23. A candidate with no
            # interface record has no proposed partner at all, and emitting the
            # default would publish a pairing nobody claimed.
            "partner": c.mediator.partner_gene if c.mediator.claims else None,
            "involvement": c.mediator.involvement.value,
            "region": c.mediator.tf_region,
            "region_mapped": c.mediator.interacting_region_mapped,
            "tractability": c.mediator.tractability.value,
            "control": c.mediator.calibration_only,
            "concerns": c.mediator.screening_concerns,
            "ready": c.mediator.ready_for_structural_modeling,
            "claims": [{"statement": cl.statement, "support": cl.support.value,
                        "citations": cl.citations, "note": cl.note}
                       for cl in c.mediator.claims],
        })

    # 3. Structures, one per target-partner pair we have a real deposition for.
    structures = {}
    for (gene, partner), meta in STRUCTURES.items():
        cif = DOWNLOADS / f"{meta['pdb_id']}.cif"
        if not cif.exists():
            try:
                urllib.request.urlretrieve(
                    f"https://files.rcsb.org/download/{meta['pdb_id']}.cif", cif)
            except Exception as e:  # offline is fine; the panel just says so
                print(f"structure {meta['pdb_id']} skipped: {e}", file=sys.stderr)
                continue
        structures[f"{gene}|{partner}"] = {
            **meta, "gene": gene, "partner": partner, "trace": ca_trace(cif)}
        print(f"structure {gene}-{partner} ({meta['pdb_id']}):",
              {k: len(v) for k, v in structures[f"{gene}|{partner}"]["trace"].items()})
    pairs = {f"{r['gene']}|{r['partner']}" for r in rows if r["partner"]}
    print(f"structures: {len(structures)} of {len(pairs)} candidate pairs have coordinates")

    # 3b. For every target with no solved complex, the AlphaFold monomer. This is
    # the target alone, predicted, never the interface — but per-residue pLDDT
    # says out loud how much of the protein is ordered enough to have a pocket,
    # which is the reason most of these pairs have no complex to begin with.
    predicted = {}
    for gene in sorted({r["gene"] for r in rows
                        if f"{r['gene']}|{r['partner']}" not in structures}):
        try:
            up = uniprot_accession(gene)
        except Exception as e:
            print(f"  uniprot lookup failed for {gene}: {e}", file=sys.stderr)
            continue
        if not up:
            print(f"  no reviewed human entry for {gene}", file=sys.stderr)
            continue
        acc, name, length = up
        meta = alphafold_model(acc)
        if not meta:
            continue
        cif = DOWNLOADS / f"AF-{acc}.cif"
        if not cif.exists():
            try:
                urllib.request.urlretrieve(meta["cifUrl"], cif)
            except Exception as e:
                print(f"  alphafold download failed for {gene}: {e}", file=sys.stderr)
                continue
        trace = af_trace(cif)
        if not trace:
            continue
        ordered = sum(1 for p in trace if p[4] >= 70)
        predicted[gene] = {
            "gene": gene, "uniprot": acc, "protein_name": name, "length": length,
            "mean_plddt": round(meta.get("globalMetricValue") or 0.0, 1),
            "ordered_fraction": round(ordered / len(trace), 3),
            "model_version": meta.get("latestVersion"),
            "source_url": f"https://alphafold.ebi.ac.uk/entry/{acc}",
            "cif_url": meta["cifUrl"],
            "trace": trace,
        }
        print(f"  alphafold {gene} ({acc}): {len(trace)} res, "
              f"mean pLDDT {predicted[gene]['mean_plddt']}, "
              f"{predicted[gene]['ordered_fraction']:.0%} ordered")
    print(f"predicted monomers: {len(predicted)}")

    prov = RunProvenance(
        inputs=[InputFile.pin("gene_effect", ge), InputFile.pin("models", models),
                InputFile.pin("tf_universe", tf_list)]
        + ([InputFile.pin("structure_cif", cif)] if cif.exists() else []),
        notes=["landscape, candidates and gates are computed and byte-reproducible",
               "papers shown in the interface are retrieved live and are not pinned"],
    )
    print("input fingerprint:", prov.fingerprint[:16])

    UI.mkdir(exist_ok=True)
    payload = {
        "provenance": prov.model_dump(mode="json"),
        "fingerprint": prov.fingerprint,
        "generated_from": "DepMap Public 24Q2 Chronos; Lambert et al. TF catalogue v1.01",
        "landscape_context": LANDSCAPE_CONTEXT,
        "thresholds": thresholds(),
        "landscape": landscape,
        "candidates": rows,
        "structures": structures,
        "predicted": predicted,
        # The hero renders one fixed complex and says which; keep it addressable.
        "structure": next(iter(structures.values()), None),
    }
    (UI / "data.json").write_text(json.dumps(payload, separators=(",", ":")) + "\n",
                                 encoding="utf-8")
    kb = (UI / "data.json").stat().st_size / 1024
    print(f"wrote ui/data.json ({kb:.0f} KB), {len(rows)} candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
