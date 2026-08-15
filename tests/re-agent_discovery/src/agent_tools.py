"""
Agentic query layer -- five tools + one orchestrator, per docs/TOOL_INTERFACE_SPEC.md.

Four of the five tools are pure functions (deterministic, no LLM in the
loop): query_dependency, find_mediator_leads, score_candidates, and the
literature-cache reader half of literature_search. The one real exception:
literature_search cannot execute a live search itself. Paperclip is an
agent-mediated CLI/MCP tool -- there is no HTTP API this module can call, and
routing around that would also bypass the citation/verification workflow the
paperclip skill itself mandates. So literature_search is a cache-only lookup:
if the pair was already searched, it returns that result (see the permanent
negative-caching decision below); if not, it returns None and the calling
agent session must actually run the paperclip queries -- exactly as this
session did by hand for MYCN/TP63/TCF7L2/ZNF217 on 2026-08-15 -- then write
the result back with record_literature_result().

Negative-result caching (team decision, 2026-08-15): once a pair comes back
empty, that result is trusted permanently. No expiry, no re-check. Cheap and
fast; the tradeoff (a paper published after the search date will never be
found) was made deliberately, not by default.
"""

import json
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel

from dependency_scout.models import (
    Claim,
    DependencyEvidence,
    EnrichmentEvidence,
    MediatorLink,
    RankedCandidate,
)

import config
import ranking
from stage4_mediator_string import query_tf_mediator_network, resolve_string_ids

LITERATURE_CACHE_PATH = config.RESULTS_DIR / "literature_search_cache.json"
LITERATURE_TOP_N_PER_TF = 5  # exhaustive-mode cap -- see run_query()


# ---------------------------------------------------------------------------
# Orchestration-layer types (scratch-repo-local -- see TOOL_INTERFACE_SPEC.md
# Open Question 2. Not a proposed addition to dependency_scout.models: the
# merge-relevant seam with the rest of the team is RankedCandidate/
# DependencyEvidence/MediatorLink, which these tools produce -- these input
# types are internal to how this module gets there.)
# ---------------------------------------------------------------------------
class QueryIntent(BaseModel):
    disease_context: str | None = None
    gene: str | None = None
    exhaustiveness: Literal["quick", "exhaustive"] = "quick"


class StringLead(BaseModel):
    tf: str
    mediator_subunit: str
    combined_score: float
    experimental_score: float
    database_score: float
    textmining_score: float
    coexpression_score: float
    is_artifact_suspect: bool


class LiteratureSearchResult(BaseModel):
    tf: str
    mediator_subunit: str
    queries_run: list[str]
    claim: Claim | None = None
    # Region-mapping is a judgment the literature-reading agent makes while
    # searching (see the CEBPB precedent: a direct_experimental claim can
    # still describe an unmapped interaction) -- recorded here explicitly
    # rather than inferred later from the claim's SupportType alone. This is
    # a refinement on TOOL_INTERFACE_SPEC.md's original sketch, found while
    # implementing score_candidates.
    interacting_region_mapped: bool = False
    tf_region: str | None = None
    negative_note: str | None = None
    searched_at: str


class CoverageNote(BaseModel):
    scope: str
    skipped_or_capped: str
    would_require: str


class QueryResult(BaseModel):
    intent: QueryIntent
    candidates: list[RankedCandidate]
    leads_considered: list[StringLead]
    literature_searches: list[LiteratureSearchResult]
    coverage_notes: list[CoverageNote]
    generated_at: str


# ---------------------------------------------------------------------------
# Tool 1 -- query_dependency
# ---------------------------------------------------------------------------
def query_dependency(disease_context: str | None = None, gene: str | None = None) -> list[DependencyEvidence]:
    """Filters the already-exhaustive Stage-1 store. Not exhaustiveness-
    sensitive -- Stage 1 already scanned every TF against every lineage and
    subtype; there is nothing a live re-scan would add for a single query."""
    df = pd.read_csv(config.RESULTS_DIR / "stage1_dependency_hits.csv", low_memory=False)
    sig = df[(df["dependency_flag"] == True) & (df["qvalue"] < 0.05)]  # noqa: E712
    if disease_context:
        mask = sig["context"].str.contains(disease_context, case=False, na=False)
        if "parent_lineage" in sig.columns:
            mask = mask | sig["parent_lineage"].str.contains(disease_context, case=False, na=False)
        sig = sig[mask]
    if gene:
        sig = sig[sig["tf"].str.upper() == gene.upper()]
    return [ranking.build_dependency_evidence(row) for _, row in sig.iterrows()]


# ---------------------------------------------------------------------------
# Tool 2 -- find_mediator_leads
# ---------------------------------------------------------------------------
def _string_rows_to_leads(df: pd.DataFrame, artifact_tfs: set[str]) -> list[StringLead]:
    return [
        StringLead(
            tf=row["tf"],
            mediator_subunit=row["mediator_subunit"],
            combined_score=float(row["combined_score"]),
            experimental_score=float(row["experimental_score"] or 0),
            database_score=float(row["database_score"] or 0),
            textmining_score=float(row["textmining_score"] or 0),
            coexpression_score=float(row["coexpression_score"] or 0),
            is_artifact_suspect=row["tf"] in artifact_tfs,
        )
        for _, row in df.iterrows()
    ]


def find_mediator_leads(tf: str, exhaustiveness: Literal["quick", "exhaustive"] = "quick") -> list[StringLead]:
    cache_path = config.RESULTS_DIR / "stage4_mediator_string_hits.csv"
    cached = pd.read_csv(cache_path) if cache_path.exists() else pd.DataFrame()
    have_cached = not cached.empty and tf in set(cached["tf"])

    if exhaustiveness == "quick":
        if not have_cached:
            return []
        tf_rows = cached[cached["tf"] == tf]
        artifact_tfs = _artifact_tfs(cached)
        return _string_rows_to_leads(tf_rows, artifact_tfs)

    # exhaustive: force a live STRING refresh for this TF, merge into the cache
    id_map = resolve_string_ids([tf] + config.MEDIATOR_SUBUNITS)
    if tf not in id_map:
        return []
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
                "coexpression_score": e.get("ascore"), "neighborhood_score": e.get("nscore"),
                "fusion_score": e.get("fscore"), "phylogenetic_score": e.get("pscore"),
                "has_experimental_or_database": (e.get("escore") or 0) > 0 or (e.get("dscore") or 0) > 0,
            }
        )
    fresh = pd.DataFrame(rows)
    merged = pd.concat([cached[cached["tf"] != tf], fresh], ignore_index=True) if not cached.empty else fresh
    merged.to_csv(cache_path, index=False)

    if fresh.empty:
        return []
    artifact_tfs = _artifact_tfs(merged)
    return _string_rows_to_leads(fresh, artifact_tfs)


def _artifact_tfs(df: pd.DataFrame) -> set[str]:
    if df.empty:
        return set()
    counts = df.groupby("tf")["mediator_subunit"].nunique()
    return set(counts[counts >= ranking.STRING_ARTIFACT_SUBUNIT_COUNT].index)


# ---------------------------------------------------------------------------
# Tool 3 -- literature_search (cache-only; see module docstring)
# ---------------------------------------------------------------------------
def _load_literature_cache() -> dict:
    if LITERATURE_CACHE_PATH.exists():
        return json.loads(LITERATURE_CACHE_PATH.read_text())
    return {}


def _cache_key(tf: str, mediator_subunit: str) -> str:
    return f"{tf}::{mediator_subunit}"


def literature_search(
    tf: str, mediator_subunit: str, mode: Literal["targeted", "broad"] = "targeted"
) -> LiteratureSearchResult | None:
    """Cache-only. Returns None if this pair has never been searched -- the
    caller must run the actual paperclip queries and call
    record_literature_result(). `mode` is accepted for interface symmetry
    with TOOL_INTERFACE_SPEC.md but does not change lookup behavior; it is
    read by whoever performs the live search, not by this cache reader."""
    entry = _load_literature_cache().get(_cache_key(tf, mediator_subunit))
    return LiteratureSearchResult(**entry) if entry else None


def record_literature_result(
    tf: str,
    mediator_subunit: str,
    queries_run: list[str],
    searched_at: str,
    claim: Claim | None = None,
    interacting_region_mapped: bool = False,
    tf_region: str | None = None,
    negative_note: str | None = None,
) -> LiteratureSearchResult:
    """Write path -- called by an agent session after actually running the
    paperclip searches. Exactly one of claim/negative_note must be set."""
    if (claim is None) == (negative_note is None):
        raise ValueError("exactly one of claim or negative_note must be set")
    result = LiteratureSearchResult(
        tf=tf,
        mediator_subunit=mediator_subunit,
        queries_run=queries_run,
        claim=claim,
        interacting_region_mapped=interacting_region_mapped,
        tf_region=tf_region,
        negative_note=negative_note,
        searched_at=searched_at,
    )
    cache = _load_literature_cache()
    cache[_cache_key(tf, mediator_subunit)] = json.loads(result.model_dump_json())
    LITERATURE_CACHE_PATH.write_text(json.dumps(cache, indent=2))
    return result


# ---------------------------------------------------------------------------
# Tool 4 -- score_candidates
# ---------------------------------------------------------------------------
def score_candidates(
    dependency: list[DependencyEvidence],
    leads: dict[str, list[StringLead]],
    literature: dict[tuple[str, str], LiteratureSearchResult],
) -> list[RankedCandidate]:
    """Pure function -- zero API calls, zero LLM involvement. Wraps
    ranking.py's gate/discovery/enrichment/final-score math unchanged."""
    universe = ranking.load_universe_percentile_ranks()
    candidates = []
    for dep in dependency:
        match = universe[(universe["tf"] == dep.gene) & (universe["context"] == dep.disease_context)]
        if match.empty:
            continue
        row = match.iloc[0]
        gate = ranking.compute_gate(dep)
        discovery_score = ranking.compute_discovery_score(row, gate)

        tf_leads = leads.get(dep.gene, [])
        real_leads = [l for l in tf_leads if not l.is_artifact_suspect]
        subunit_names = [l.mediator_subunit for l in real_leads] or [None]

        for subunit in subunit_names:
            partner_gene = subunit or "MED23"
            lit = literature.get((dep.gene, partner_gene))
            mediator = MediatorLink(
                partner_gene=partner_gene,
                interacting_region_mapped=lit.interacting_region_mapped if lit else False,
                tf_region=lit.tf_region if lit else None,
                claims=[lit.claim] if lit and lit.claim else [],
            )
            enrichment = ranking.compute_enrichment(mediator, tf=dep.gene)
            completeness = ranking.compute_evidence_completeness(enrichment)
            enrichment_score = ranking.compute_enrichment_score(enrichment)
            final_score = ranking.compute_final_score(discovery_score, enrichment_score, completeness)
            candidates.append(
                RankedCandidate(
                    dependency=dep, enrichment=enrichment, gate=gate,
                    discovery_score=discovery_score, enrichment_score=enrichment_score,
                    evidence_completeness=completeness, final_score=final_score, mediator=mediator,
                )
            )
    candidates.sort(key=lambda c: c.final_score, reverse=True)
    return candidates


# ---------------------------------------------------------------------------
# Tools 5/6 -- stubs (Stage 2/3 not implemented)
# ---------------------------------------------------------------------------
def query_safety_proxy(gene: str, exhaustiveness: Literal["quick", "exhaustive"] = "quick") -> EnrichmentEvidence | None:
    return None  # Stage 3 (UniProt/HPA) not implemented


def query_regulatory_context(gene: str, disease_context: str, exhaustiveness: Literal["quick", "exhaustive"] = "quick"):
    return None  # Stage 2 (ENCODE-rE2G) not implemented


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def run_query(intent: QueryIntent, generated_at: str) -> QueryResult:
    coverage_notes: list[CoverageNote] = []

    dependency = query_dependency(intent.disease_context, intent.gene)

    leads_by_tf: dict[str, list[StringLead]] = {}
    for dep in dependency:
        tf_leads = find_mediator_leads(dep.gene, intent.exhaustiveness)
        if not tf_leads and intent.exhaustiveness == "quick":
            coverage_notes.append(
                CoverageNote(
                    scope="find_mediator_leads",
                    skipped_or_capped=f"{dep.gene}: no cached STRING data",
                    would_require="exhaustiveness=exhaustive to force a live STRING query",
                )
            )
        leads_by_tf[dep.gene] = tf_leads

    literature: dict[tuple[str, str], LiteratureSearchResult] = {}
    all_leads_considered: list[StringLead] = []
    for tf, tf_leads in leads_by_tf.items():
        all_leads_considered.extend(tf_leads)
        if intent.exhaustiveness != "exhaustive":
            continue  # quick mode never triggers a literature pass

        real_leads = sorted((l for l in tf_leads if not l.is_artifact_suspect), key=lambda l: l.combined_score, reverse=True)
        capped = [l for l in real_leads if l.experimental_score > 0] or real_leads[:LITERATURE_TOP_N_PER_TF]
        skipped = [l for l in real_leads if l not in capped]
        if skipped:
            coverage_notes.append(
                CoverageNote(
                    scope="literature_search",
                    skipped_or_capped=f"{tf}: {len(skipped)} lead(s) below the top-{LITERATURE_TOP_N_PER_TF}/experimental>0 cap",
                    would_require="raise LITERATURE_TOP_N_PER_TF or run literature_search on them manually",
                )
            )
        for lead in capped:
            result = literature_search(tf, lead.mediator_subunit)
            if result is None:
                coverage_notes.append(
                    CoverageNote(
                        scope="literature_search",
                        skipped_or_capped=f"{tf}/{lead.mediator_subunit}: never searched, no cached result",
                        would_require="an agent session must run literature_search's paperclip queries "
                                       "and call record_literature_result()",
                    )
                )
                continue
            literature[(tf, lead.mediator_subunit)] = result

    candidates = score_candidates(dependency, leads_by_tf, literature)

    return QueryResult(
        intent=intent,
        candidates=candidates,
        leads_considered=all_leads_considered,
        literature_searches=list(literature.values()),
        coverage_notes=coverage_notes,
        generated_at=generated_at,
    )
