"""
Natural-language request dispatch -- turns a free-text user ask into a
QueryIntent and runs it through agent_tools.run_query(). This is the missing
piece from docs/TOOL_INTERFACE_SPEC.md: the agent's only real decisions are
disease context and exhaustiveness (see the spec's design principle), and
this module is where that classification actually happens.

handle_request() ships with a zero-dependency heuristic classifier --
substring/token-overlap matching against the real disease-context strings in
the store, plus keyword detection for exhaustiveness -- rather than a live
LLM API call. That's a deliberate choice, not a placeholder: this repo's UI
band is explicitly "no server, no live calls" (see ui/README.md, TASKS.md
Band 3), and wiring in a paid external LLM API is the kind of dependency
CLAUDE.md says to raise before adding, not decide unilaterally in a scratch
module. The `classify_fn` parameter is the seam for swapping in a real LLM
call later without touching run_query or anything downstream of it.
"""

import re
import sys
from datetime import datetime, timezone
from typing import Callable

import pandas as pd

import config
from agent_tools import QueryIntent, QueryResult, run_query

EXHAUSTIVE_KEYWORDS = [
    "exhaustive", "thorough", "comprehensive", "as much evidence as possible",
    "everything", "all evidence", "in depth", "in-depth", "deep dive", "fully",
    "as many", "full pass",
]


def _known_disease_contexts() -> list[str]:
    df = pd.read_csv(config.RESULTS_DIR / "stage1_dependency_hits.csv", low_memory=False, usecols=["context"])
    return sorted(df["context"].dropna().unique().tolist())


def _known_genes() -> set[str]:
    df = pd.read_csv(config.RESULTS_DIR / "stage1_dependency_hits.csv", low_memory=False, usecols=["tf"])
    return set(df["tf"].dropna().unique().tolist())


def _token_overlap_score(context: str, text_tokens: set[str]) -> float:
    context_tokens = set(re.findall(r"[a-z0-9]+", context.lower()))
    if not context_tokens:
        return 0.0
    return len(context_tokens & text_tokens) / len(context_tokens)


def _heuristic_classify(text: str, known_contexts: list[str], known_genes: set[str]) -> dict:
    lowered = text.lower()
    text_tokens = set(re.findall(r"[a-z0-9]+", lowered))

    exhaustiveness = "exhaustive" if any(k in lowered for k in EXHAUSTIVE_KEYWORDS) else "quick"

    gene = None
    for token in re.findall(r"\b[A-Za-z0-9]{2,10}\b", text):
        if token.upper() in known_genes:
            gene = token.upper()
            break

    disease_context = None
    substring_hits = [c for c in known_contexts if c.lower() in lowered]
    if substring_hits:
        disease_context = max(substring_hits, key=len)  # most specific match wins
    else:
        scored = [(c, _token_overlap_score(c, text_tokens)) for c in known_contexts]
        scored = [(c, s) for c, s in scored if s >= 0.6]
        if scored:
            disease_context = max(scored, key=lambda cs: (cs[1], len(cs[0])))[0]

    return {"disease_context": disease_context, "gene": gene, "exhaustiveness": exhaustiveness}


def handle_request(
    text: str,
    classify_fn: Callable[[str, list, set], dict] | None = None,
) -> QueryIntent:
    """Turns free text into a QueryIntent. `classify_fn(text, known_contexts,
    known_genes) -> dict` (keys: disease_context, gene, exhaustiveness) is the
    seam for a real LLM call; defaults to the heuristic classifier above."""
    known_contexts = _known_disease_contexts()
    known_genes = _known_genes()
    classify = classify_fn or _heuristic_classify
    parsed = classify(text, known_contexts, known_genes)
    return QueryIntent(**parsed)


def answer_request(text: str, classify_fn: Callable | None = None) -> QueryResult:
    """Parse + run in one call."""
    intent = handle_request(text, classify_fn)
    return run_query(intent, generated_at=datetime.now(timezone.utc).isoformat())


if __name__ == "__main__":
    request_text = " ".join(sys.argv[1:]) or "find candidates for neuroblastoma, as much evidence as possible"
    intent = handle_request(request_text)
    print(f"request: {request_text!r}")
    print(f"parsed:  disease_context={intent.disease_context!r} gene={intent.gene!r} exhaustiveness={intent.exhaustiveness!r}")
    result = run_query(intent, generated_at=datetime.now(timezone.utc).isoformat())
    print(f"candidates={len(result.candidates)} leads={len(result.leads_considered)} "
          f"lit_searches={len(result.literature_searches)} coverage_notes={len(result.coverage_notes)}")
    for c in result.candidates[:5]:
        print(f"  {c.dependency.gene:8s} {c.dependency.disease_context:30s} final={c.final_score:.3f}")
