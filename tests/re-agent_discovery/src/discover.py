"""
Simplified end-to-end discovery (2026-08-15, replaces the composite-scoring
approach in ranking.py/agent_tools.py at the team's request): given a TF
and/or a disease context, extract everything the pipeline currently knows --
dependency stats, Mediator/STRING leads, literature findings -- as one flat
table. No discovery_score/enrichment_score/final_score. Every row is raw
evidence with its own gate/status columns, not a rank.

`disease_context` must be an exact string from list_known_contexts(). Making
that decision -- e.g. resolving "small cell lung cancer" or "SCLC" to the
exact stored string "Small Cell Lung Cancer", and knowing that DepMap pools
SCLC-A/N/P/Y into that one bucket -- is deliberately left to whichever LLM is
driving this call ("LLM as judge" per the team's request), not a regex/fuzzy
heuristic inside this module. That judgment call is exactly the kind of thing
the specificity-first gate fix below exists to not silently get wrong on its
own.
"""

import json

import pandas as pd

import config
from encode_tool import query_encode_chipseq
from hpa_tool import query_hpa
from stage4_mediator_string import query_tf_mediator_network, resolve_string_ids
from uniprot_tool import get_cached as get_cached_uniprot

LITERATURE_CACHE_PATH = config.RESULTS_DIR / "literature_search_cache.json"
STRING_HITS_PATH = config.RESULTS_DIR / "stage4_mediator_string_hits.csv"
STRING_ARTIFACT_SUBUNIT_COUNT = 15


def _annotations_for(gene: str) -> dict:
    """Stage 2/3 columns: HPA (live), UniProt/PDB/ChEMBL (Paperclip cache,
    may be absent if no agent session has run it for this gene yet), ENCODE
    (live). Each field is None, not fabricated, when its source has nothing."""
    universe = pd.read_csv(config.TF_UNIVERSE_CACHE).set_index("gene_symbol")["ensembl_id"]
    ensembl_id = universe.get(gene)
    hpa = query_hpa(gene, ensembl_id) if ensembl_id else None
    uniprot = get_cached_uniprot(gene)
    encode = query_encode_chipseq(gene)

    return {
        "hpa_tissue_specificity": hpa.get("tissue_specificity") if hpa else None,
        "hpa_top_tissues": ", ".join(hpa["top_expressed_tissues"].keys()) if hpa else None,
        "uniprot_diseases": uniprot.get("diseases") if uniprot else None,
        "uniprot_domains": uniprot.get("domains") if uniprot else None,
        "chembl_n_compounds": uniprot.get("n_compounds") if uniprot else None,
        "chembl_best_pchembl": uniprot.get("best_pchembl") if uniprot else None,
        "pdb_n_structures": uniprot.get("n_structures") if uniprot else None,
        "encode_chipseq_biosamples": ", ".join(encode["biosamples"]) if encode["biosamples"] else None,
    }


def list_known_contexts() -> list[str]:
    df = pd.read_csv(config.RESULTS_DIR / "stage1_dependency_hits.csv", low_memory=False, usecols=["context"])
    return sorted(df["context"].dropna().unique().tolist())


def list_known_genes() -> list[str]:
    df = pd.read_csv(config.RESULTS_DIR / "stage1_dependency_hits.csv", low_memory=False, usecols=["tf"])
    return sorted(df["tf"].dropna().unique().tolist())


def _evidence_override_genes() -> set[str]:
    """TFs with a real literature claim on file earn a place in a
    disease-context query regardless of the dependency gate. This exists
    because Mediator-contact evidence and DepMap fitness evidence are
    independent questions -- ELK1-MED23 is the proof case: a structurally
    solved, cryo-EM-mapped contact with zero DepMap dependency signal
    anywhere (checked across all 258 context rows -- see
    PIPELINE_SUMMARY.md). Filtering it out of every disease-context query
    because it fails a fitness screen it was never expected to pass would
    hide the pipeline's own calibration case from itself."""
    if not LITERATURE_CACHE_PATH.exists():
        return set()
    cache = json.loads(LITERATURE_CACHE_PATH.read_text())
    return {key.split("::")[0] for key, entry in cache.items() if entry.get("claim")}


def _dependency_rows(tf: str | None, disease_context: str | None) -> pd.DataFrame:
    df = pd.read_csv(config.RESULTS_DIR / "stage1_dependency_hits.csv", low_memory=False)
    if tf:
        df = df[df["tf"].str.upper() == tf.upper()]
    if disease_context:
        df = df[df["context"] == disease_context]
    if not tf:
        # context-only query: TFs that cleared the dependency gate, PLUS any
        # TF with real Mediator/literature evidence regardless of gate result
        # (see _evidence_override_genes). Never silent -- discover() tags
        # which reason let each row through.
        override_genes = _evidence_override_genes()
        df = df[df["dependency_flag"] | df["tf"].isin(override_genes)]
    return df.sort_values("qvalue")


def _string_leads_for(tf: str, exhaustiveness: str) -> tuple[pd.DataFrame, bool]:
    """Returns (leads, is_artifact_suspect). `quick`: cache only, live STRING
    call only if this TF has never been queried before. `exhaustive`: always
    force a live refresh."""
    cached = pd.read_csv(STRING_HITS_PATH) if STRING_HITS_PATH.exists() else pd.DataFrame()
    have_cached = not cached.empty and tf in set(cached["tf"])

    if have_cached and exhaustiveness != "exhaustive":
        tf_rows = cached[cached["tf"] == tf]
    else:
        id_map = resolve_string_ids([tf] + config.MEDIATOR_SUBUNITS)
        if tf not in id_map:
            return pd.DataFrame(), False
        mediator_ids = [id_map[m] for m in config.MEDIATOR_SUBUNITS if m in id_map]
        id_to_symbol = {id_map[m]: m for m in config.MEDIATOR_SUBUNITS if m in id_map}
        edges = query_tf_mediator_network(id_map[tf], mediator_ids)
        rows = []
        for e in edges:
            if e["preferredName_A"] == tf and e["stringId_B"] in id_to_symbol:
                partner = id_to_symbol[e["stringId_B"]]
            elif e["preferredName_B"] == tf and e["stringId_A"] in id_to_symbol:
                partner = id_to_symbol[e["stringId_A"]]
            else:
                continue
            rows.append(
                {
                    "tf": tf, "mediator_subunit": partner, "is_validation_control": False,
                    "combined_score": e.get("score"), "experimental_score": e.get("escore"),
                    "database_score": e.get("dscore"), "textmining_score": e.get("tscore"),
                    "coexpression_score": e.get("ascore"),
                    "has_experimental_or_database": (e.get("escore") or 0) > 0 or (e.get("dscore") or 0) > 0,
                }
            )
        tf_rows = pd.DataFrame(rows)
        merged = pd.concat([cached[cached["tf"] != tf], tf_rows], ignore_index=True) if not cached.empty else tf_rows
        merged.to_csv(STRING_HITS_PATH, index=False)

    if tf_rows.empty:
        return tf_rows, False
    is_artifact = tf_rows["mediator_subunit"].nunique() >= STRING_ARTIFACT_SUBUNIT_COUNT
    return tf_rows, is_artifact


def _literature_for(tf: str) -> dict:
    if not LITERATURE_CACHE_PATH.exists():
        return {}
    cache = json.loads(LITERATURE_CACHE_PATH.read_text())
    return {k: v for k, v in cache.items() if k.startswith(f"{tf}::")}


def discover(
    tf: str | None = None,
    disease_context: str | None = None,
    exhaustiveness: str = "quick",
    include_annotations: bool = True,
) -> pd.DataFrame:
    """One flat table: every (TF, disease context, Mediator subunit lead)
    combination this pipeline can currently support with evidence. No
    scoring -- dependency_gate/qvalue and literature_status are the only
    verdict columns, and both stay raw and inspectable."""
    if not tf and not disease_context:
        raise ValueError("give at least a TF or a disease_context")
    if disease_context and disease_context not in list_known_contexts():
        raise ValueError(f"{disease_context!r} is not an exact known context -- see list_known_contexts()")

    dep_rows = _dependency_rows(tf, disease_context)
    genes = [tf.upper()] if tf else dep_rows["tf"].unique().tolist()
    override_genes = _evidence_override_genes() if (disease_context and not tf) else set()

    out_rows = []
    for gene in genes:
        gene_dep_rows = dep_rows if tf else dep_rows[dep_rows["tf"] == gene]
        leads, is_artifact = _string_leads_for(gene, exhaustiveness)
        lit = _literature_for(gene)
        annotations = _annotations_for(gene) if include_annotations else {}

        for _, drow in gene_dep_rows.iterrows():
            base = {
                "tf": gene,
                "disease_context": drow["context"],
                "context_level": drow["context_level"],
                "parent_lineage": drow.get("parent_lineage"),
                "n_in": int(drow["n_in"]), "n_out": int(drow["n_out"]),
                "median_effect_in": round(drow["in_median"], 3),
                "median_effect_out": round(drow["out_median"], 3),
                "dependent_fraction_in": round(drow["target_dependent_fraction"], 3),
                "dependent_fraction_out": round(drow["other_dependent_fraction"], 3),
                "qvalue": drow["qvalue"],
                "dependency_gate": "pass" if drow["dependency_flag"] else "fail",
                "confidence": drow["confidence_flag"],
                "inclusion_reason": "dependency_gate" if drow["dependency_flag"]
                    else ("mediator_literature_override" if gene in override_genes else "dependency_gate"),
                **annotations,
            }
            if is_artifact:
                out_rows.append({**base, "mediator_subunit": None, "string_combined_score": None,
                                  "string_experimental_score": None,
                                  "literature_status": "N/A -- STRING pattern rejected as whole-complex artifact"})
                continue
            if leads.empty:
                out_rows.append({**base, "mediator_subunit": None, "string_combined_score": None,
                                  "string_experimental_score": None,
                                  "literature_status": "no STRING evidence to any Mediator subunit"})
                continue
            for _, lrow in leads.sort_values("combined_score", ascending=False).iterrows():
                key = f"{gene}::{lrow['mediator_subunit']}"
                entry = lit.get(key)
                if entry is None:
                    lit_status = "not yet searched"
                elif entry.get("claim"):
                    lit_status = f"CLAIM FOUND: {entry['claim']['statement'][:140]}"
                else:
                    lit_status = f"checked, nothing found -- {entry.get('negative_note', '')[:140]}"
                out_rows.append({
                    **base,
                    "mediator_subunit": lrow["mediator_subunit"],
                    "string_combined_score": lrow["combined_score"],
                    "string_experimental_score": lrow["experimental_score"],
                    "literature_status": lit_status,
                })

    return pd.DataFrame(out_rows)


if __name__ == "__main__":
    import sys
    args = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
    table = discover(tf=args.get("tf"), disease_context=args.get("context"),
                      exhaustiveness=args.get("exhaustiveness", "quick"))
    pd.set_option("display.width", 220)
    pd.set_option("display.max_colwidth", 60)
    print(table.to_string(index=False))
