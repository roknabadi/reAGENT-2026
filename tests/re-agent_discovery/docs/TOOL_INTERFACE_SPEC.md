# Tool interface spec — Agentic query layer

Scope: the function signatures an orchestrating agent calls, and the
exhaustiveness-mode contract that controls how much evidence gets chased per
query. Not implemented yet — this is the interface, grounded in the modules
that already exist (`stage1_depmap.py`, `stage4_mediator_string.py`,
`ranking.py`) plus the one new piece this session did by hand and needs
codifying (the Paperclip literature pass).

## Design principle, restated as a contract

The agent's only decisions are **which disease context** and **how much
evidence to chase**. It never picks which tool to call next, never invents a
score, and never writes a `Claim` without going through the one tool where
that's deliberately allowed (`literature_search`, below) — and even there,
the typed `Claim` schema (`citations: list[str] = Field(min_length=1)`, and
`inference` support requiring a `note`) rejects an uncited or uncategorized
assertion at the object-construction level, not by convention. Every other
tool is a pure function: same input, same output, no LLM in the loop.

## New types (orchestration-layer only — see Open Questions)

These are NOT proposed additions to `dependency_scout.models` in the main
repo. They sit one layer above it: `RankedCandidate` etc. remain the
evidence contract; these are the query/session contract around them, scoped
to this scratch repo until there's a reason to promote them.

```python
class QueryIntent(BaseModel):
    disease_context: str | None      # e.g. "Neuroblastoma"; None = whole store
    gene: str | None = None          # optional direct gene filter
    exhaustiveness: Literal["quick", "exhaustive"] = "quick"

class StringLead(BaseModel):
    tf: str
    mediator_subunit: str
    combined_score: float
    experimental_score: float
    database_score: float
    textmining_score: float
    coexpression_score: float
    is_artifact_suspect: bool        # >=15/33 subunits hit near-uniformly (see ranking.py)

class LiteratureSearchResult(BaseModel):
    tf: str
    mediator_subunit: str
    queries_run: list[str]           # exact paperclip commands issued, for reproducibility
    claim: Claim | None              # None if nothing citable was found
    negative_note: str | None        # set instead of claim when the search came up empty
    searched_at: str                 # ISO date

class CoverageNote(BaseModel):
    scope: str                       # e.g. "literature_search", "string_leads"
    skipped_or_capped: str           # what didn't run and why -- no silent caps
    would_require: str               # what mode/budget would cover it

class QueryResult(BaseModel):
    intent: QueryIntent
    candidates: list[RankedCandidate]        # sorted by final_score, from dependency_scout.models
    leads_considered: list[StringLead]
    literature_searches: list[LiteratureSearchResult]
    coverage_notes: list[CoverageNote]
    generated_at: str
```

`QueryResult` is what the dashboard renders directly — one tab per top-level
field (Summary/Ranked ← `candidates`, Mediator ← `leads_considered`,
Literature ← `literature_searches`, Coverage/Caveats ← `coverage_notes`) — no
second data model between the query layer and the UI.

## Tools

### 1. `query_dependency`

```python
def query_dependency(disease_context: str | None, gene: str | None) -> list[DependencyEvidence]
```

Wraps the already-materialized Stage-1 store (`data/results/stage1_dependency_hits.csv`,
via `build_dependency_evidence()` in `ranking.py`). **Not exhaustiveness-sensitive** —
Stage 1 already scanned every TF against every lineage/subtype; this tool only
filters and re-normalizes percentile ranks against the full store (per
`SCORING_SPEC.md` SS2). No live computation, no API call, sub-second.

### 2. `find_mediator_leads`

```python
def find_mediator_leads(tf: str, exhaustiveness: Literal["quick", "exhaustive"]) -> list[StringLead]
```

Wraps `resolve_string_ids` + `query_tf_mediator_network` from
`stage4_mediator_string.py`, plus the artifact heuristic from `ranking.py`'s
`load_string_leads`. `quick`: serve from `stage4_mediator_string_hits.csv` if
the TF is already cached, else skip (returns `[]` and the caller records a
`CoverageNote`, never a silent empty result). `exhaustive`: force a live
STRING refresh for this TF regardless of cache age.

### 3. `literature_search`

```python
def literature_search(tf: str, mediator_subunit: str, mode: Literal["targeted", "broad"]) -> LiteratureSearchResult
```

Codifies the manual pass run this session: `targeted` runs a boolean
co-occurrence grep (`paperclip grep --bool '"TF" AND "SUBUNIT"' /papers/`,
filtered for real interaction language, not incidental table co-mention) plus
one semantic search naming both genes; `broad` additionally runs a per-TF
search with no subunit named, to catch the case (already seen once, on
RUNX2) where STRING pointed at the wrong subunit but a real contact exists
elsewhere. **This is the one tool where an LLM reads and judges the search
results** — assigning `SupportType`, checking for a mapped region, and
catching gene-symbol collisions (the TCF7L2/bHLH-TCF4 case this session).
The `Claim` Pydantic model is what keeps that judgment honest: no citation,
no claim; an `inference`-tier judgment must carry its reasoning in `note`.
When nothing citable turns up, the tool returns `negative_note` instead of
`claim` — an absence is recorded, not silently dropped, so the same search
never has to run twice.

### 4. `score_candidates`

```python
def score_candidates(
    dependency: list[DependencyEvidence],
    leads: dict[str, list[StringLead]],
    claims: dict[tuple[str, str], list[Claim]],
) -> list[RankedCandidate]
```

Wraps `ranking.py`'s `compute_gate` / `compute_discovery_score` /
`compute_enrichment` / `compute_final_score` unchanged. Pure function, zero
API calls, zero LLM involvement — this is the deterministic core the rest of
the system exists to feed.

### 5/6. `query_safety_proxy`, `query_regulatory_context` — stubs

```python
def query_safety_proxy(gene: str, exhaustiveness) -> EnrichmentEvidence          # Stage 3, UniProt/HPA -- not implemented
def query_regulatory_context(gene: str, disease_context: str, exhaustiveness)    # Stage 2, ENCODE-rE2G -- not implemented
```

Listed for interface completeness so `score_candidates`'s `EnrichmentEvidence`
inputs have a defined future source; not built this session.

## Exhaustiveness contract

| Tool | `quick` | `exhaustive` |
|---|---|---|
| `query_dependency` | full store (always) | same — not mode-sensitive |
| `find_mediator_leads` | cached only, skip if absent | live STRING refresh, forced |
| `literature_search` | **skipped entirely** unless a claim is already cached | runs for every non-artifact-suspect lead, capped (see below) |
| `score_candidates` | always runs | always runs |

**Concrete cap for exhaustive-mode literature search** (the cost risk flagged
last turn): run only leads with `experimental_score > 0` OR top-5 by
`combined_score` per TF, whichever is larger. Anything past that cap gets a
`CoverageNote` (`scope="literature_search"`, `skipped_or_capped="N leads below
threshold for GENE"`, `would_require="raise the cap / run manually"`) — never
a silent truncation.

## Orchestrator entry point

```python
def run_query(intent: QueryIntent) -> QueryResult
```

Sequence, always in this fixed order regardless of mode (mode only changes
each tool's internal behavior, never the order or which tools exist):
`query_dependency` → per surviving candidate, `find_mediator_leads` → per
non-artifact lead, `literature_search` (if exhaustive) → `score_candidates`
→ assemble `QueryResult` with `coverage_notes` for everything skipped.

## Open questions

1. **Where does this run?** Two options, not yet decided: (a) a plain Python
   module (`agent_tools.py`) that a Claude Code session calls directly — no
   new infrastructure, matches how this session has been working; (b) an MCP
   server exposing the same five functions as tools, matching how Paperclip
   and Tamarind are already wired into this project's `.mcp.json`. (a) is
   faster to stand up; (b) is reusable outside this one Claude Code session
   (e.g. from the eventual dashboard's backend). Leaning (a) for the
   hackathon demo, (b) if there's time after.
2. **`QueryResult` promotion.** If the dashboard or another team member's
   code ends up depending on `QueryResult`/`StringLead`/`LiteratureSearchResult`,
   they stop being scratch-repo-local and need the same `DECISIONS.md`
   treatment as `models.py` itself.
3. **Literature-search determinism.** Unlike the other tools, re-running
   `literature_search` on the same pair isn't guaranteed to return the exact
   same `Claim` (corpus updates, search ranking drift — see the paperclip
   skill's own note on result-set determinism). Worth deciding whether
   `LiteratureSearchResult.searched_at` alone is enough provenance, or
   whether a result needs to be cached and treated as frozen once found.
