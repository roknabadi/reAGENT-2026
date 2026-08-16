"""Free text in, a context the data can actually answer about out.

The previous resolver was eighteen hardcoded regexes emitting eighteen
lineages. Two things were wrong with that. It could only ever name a lineage,
so a question about small cell lung cancer was answered about Lung — and
pooling SCLC with every other lung tumour dilutes its master regulators below
threshold, so the honest-looking answer was the wrong one. And the eighteen
targets were written by hand: a lineage absent from the list was unreachable
however the question was phrased, and one present but absent from the loaded
Model.csv would have been offered and then failed.

Here the vocabulary IS the data. Every candidate context is read from the
Model.csv that the run loaded, at both granularities, filtered to those with
enough models to test. Nothing is resolvable that the scan cannot then answer.

Abbreviations are the one place a lookup table is unavoidable: "SCLC" shares
no substring with "Small Cell Lung Cancer" and no amount of token matching
bridges that. The table maps an abbreviation to an expansion, and the
expansion is then matched against the real vocabulary like any other text —
so a table entry can never invent a context, only help find one that exists.
Entries that no longer match anything are reported by the test rather than
silently dead.

Matching is deliberately conservative. It abstains rather than guessing,
because a confident answer about the wrong disease is worse than no answer,
and it returns the alternatives it considered so the choice is inspectable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

# Contexts that exist in the Oncotree columns but are not a disease to screen
# against. Matched as whole values, not substrings.
NOT_A_DISEASE = {"Matched Normal", "Non-Cancerous", "Normal", "Fibroblast",
                 "Unknown", "Embryonal Carcinoma"}

# Clinical shorthand and adjectival forms -> the exact Oncotree name they mean.
#
# Every value must appear verbatim in the loaded vocabulary, and
# `tests/test_resolve.py` asserts that against the real Model.csv. The reason
# for the strictness: an approximate expansion does not fail loudly, it
# mis-steers. "crc" -> "colorectal adenocarcinoma" is not an Oncotree term, and
# partial-matched "Rectal Adenocarcinoma" (n=15) over "Colon Adenocarcinoma"
# (n=74) — a confident answer about a different disease.
#
# This table only covers what token matching cannot reach: initialisms that
# share no words with their expansion, and adjectives Oncotree spells as nouns
# ("pancreatic" vs "Pancreas"). Anything the question already names in
# vocabulary words needs no entry.
ABBREVIATIONS: dict[str, str] = {
    # initialisms
    "sclc": "Small Cell Lung Cancer",
    "nsclc": "Non-Small Cell Lung Cancer",
    "luad": "Lung Adenocarcinoma",
    "lusc": "Lung Squamous Cell Carcinoma",
    "aml": "Acute Myeloid Leukemia",
    "cml": "Chronic Myeloid Leukemia, BCR-ABL1+",
    "dlbcl": "Diffuse Large B-Cell Lymphoma, NOS",
    "gbm": "Glioblastoma",
    "hcc": "Hepatocellular Carcinoma",
    "ccrcc": "Renal Clear Cell Carcinoma",
    "pdac": "Pancreatic Adenocarcinoma",
    "crc": "Colon Adenocarcinoma",
    "hnscc": "Head and Neck",
    # adjectives and lay terms Oncotree spells differently
    "pancreatic": "Pancreas",
    "ovarian": "Ovary/Fallopian Tube",
    "ovary": "Ovary/Fallopian Tube",
    "renal": "Kidney",
    "hepatic": "Liver",
    "gastric": "Stomach Adenocarcinoma",
    "stomach": "Esophagus/Stomach",
    "oesophageal": "Esophagus/Stomach",
    "esophageal": "Esophagus/Stomach",
    "colorectal": "Colon Adenocarcinoma",
    "colon": "Colon Adenocarcinoma",
    "bowel": "Bowel",
    "cutaneous": "Cutaneous Melanoma",
    "myeloma": "Plasma Cell Myeloma",
    "neuroblastoma": "Neuroblastoma",
    "glioma": "Glioblastoma",
    "ewings": "Ewing Sarcoma",
    "urothelial": "Bladder Urothelial Carcinoma",
    "cervical": "Cervical Squamous Cell Carcinoma",
    "brain": "CNS/Brain",
}

# Words that carry no discriminating signal: present in most questions, or in
# most Oncotree names, so matching on them produces noise.
STOPWORDS = {
    "the", "a", "an", "of", "in", "for", "and", "or", "to", "is", "are", "what",
    "which", "who", "how", "why", "can", "could", "would", "should", "do", "does",
    "with", "without", "that", "this", "it", "its", "on", "at", "by", "from",
    "find", "show", "give", "tell", "me", "my", "we", "us", "i", "please", "want",
    "need", "looking", "look", "help", "about", "any", "some", "all", "best",
    "good", "target", "targets", "gene", "genes", "drug", "drugs", "druggable",
    "cancer", "cancers", "tumour", "tumor", "tumours", "tumors", "cell", "cells",
    "line", "lines", "patient", "patients", "treat", "treatment", "therapy",
    "dependency", "dependencies", "transcription", "factor", "factors", "tf",
    "tfs", "protein", "proteins", "candidate", "candidates", "top", "three",
    "there", "have", "has", "be", "been", "new", "novel", "potential", "possible",
}

_WORD = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _tokens(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def _content(text: str) -> set[str]:
    return {t for t in _tokens(text) if t not in STOPWORDS and len(t) > 1}


def _overlap(cwords: set[str], qwords: set[str]) -> set[str]:
    """Context words the question names."""
    return cwords & qwords


@dataclass
class ContextMatch:
    context: str
    level: str                  # "lineage" | "subtype"
    n_models: int
    score: float
    parent_lineage: str | None = None
    why: str = ""

    def __str__(self) -> str:
        return f"{self.context} [{self.level}, n={self.n_models}]"


@dataclass
class Resolution:
    """What the question resolved to, and what it nearly resolved to."""
    match: ContextMatch | None
    alternatives: list[ContextMatch] = field(default_factory=list)
    note: str = ""
    expanded: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.match is not None


def vocabulary(model: pd.DataFrame, min_n: int = 5) -> list[ContextMatch]:
    """Every context the loaded data can answer about. Scores are filled by
    `resolve`; this is the candidate set, read from the file."""
    out: list[ContextMatch] = []
    for level, col in (("subtype", "OncotreeSubtype"), ("lineage", "OncotreeLineage")):
        if col not in model.columns:
            continue
        for name, n in model[col].dropna().value_counts().items():
            if n < min_n or name in NOT_A_DISEASE:
                continue
            parent = None
            if level == "subtype":
                lin = model.loc[model[col] == name, "OncotreeLineage"].dropna()
                parent = str(lin.iloc[0]) if len(lin) else None
            out.append(ContextMatch(context=str(name), level=level,
                                    n_models=int(n), score=0.0,
                                    parent_lineage=parent))
    return out


def _expand(question: str) -> tuple[str, list[str]]:
    """Append expansions for any abbreviation present as a whole word."""
    toks = set(_tokens(question))
    used = [ABBREVIATIONS[t] for t in toks if t in ABBREVIATIONS]
    return (question + " " + " ".join(used)).strip(), sorted(set(used))


def _score(qwords: set[str], cand: ContextMatch, raw: str) -> tuple[float, str]:
    """How well one context answers this question.

    Recall over the context's own name, not over the question: a long question
    is mostly words about drugs and dependencies, and dividing by its length
    would punish detail. A context matches when the question names it, so the
    fraction of the CONTEXT's words that appear is the signal.
    """
    cwords = _content(cand.context)
    if not cwords:
        return 0.0, ""
    hit = _overlap(cwords, qwords)
    if not hit:
        return 0.0, ""
    score = len(hit) / len(cwords)
    why = f"{'+'.join(sorted(hit))} matches {cand.context!r}"

    # A verbatim phrase is much stronger evidence than the same words scattered.
    # Both sides are tokenised first: `raw` has already had punctuation stripped,
    # so comparing the unnormalised name never matches anything containing a
    # comma or a slash — which silently cost "Chronic Myeloid Leukemia,
    # BCR-ABL1+" its phrase bonus and handed CML to the whole Myeloid lineage.
    if " ".join(_tokens(cand.context)) in raw:
        score += 0.6
        why = f"{cand.context!r} appears in the question"
    # A partial match on a multi-word name is weak: "lung" alone should not
    # pick "Lung Adenocarcinoma" over the Lung lineage.
    elif len(hit) < len(cwords):
        score *= 0.5
    # Prefer the specific answer when the question earned it, since a lineage
    # answer to a subtype question is diluted -- but only on a full match.
    if cand.level == "subtype" and hit == cwords:
        score += 0.25
    # Break ties toward the name that used more of the question. "Uveal
    # Melanoma" and "Melanoma" both match "uveal melanoma" perfectly and tie
    # exactly; without this the larger cohort wins and the qualifier the user
    # typed is discarded. Too small to overturn a real difference in score.
    score += 0.01 * len(hit)
    return score, why


def resolve(question: str, model: pd.DataFrame, *, min_n: int = 5,
            floor: float = 0.45) -> Resolution:
    """Pick the context this question is about, or abstain.

    `floor` is the confidence below which nothing is returned. A question that
    names no disease must come back empty: matching it to the nearest lineage
    would produce a full, plausible, unrelated answer.
    """
    expanded, used = _expand(question)
    qwords = _content(expanded)
    raw = " ".join(_tokens(expanded))
    if not qwords:
        return Resolution(None, note="the question names nothing to search for",
                          expanded=used)

    scored: list[ContextMatch] = []
    for cand in vocabulary(model, min_n):
        s, why = _score(qwords, cand, raw)
        if s > 0:
            scored.append(ContextMatch(cand.context, cand.level, cand.n_models,
                                       round(s, 3), cand.parent_lineage, why))
    # Best score first; on a tie the larger cohort, which is the better-powered
    # test rather than an arbitrary pick.
    scored.sort(key=lambda c: (-c.score, -c.n_models, c.context))

    if not scored or scored[0].score < floor:
        near = ", ".join(str(c) for c in scored[:3])
        return Resolution(
            None, alternatives=scored[:5], expanded=used,
            note=("no disease context in this question matched the DepMap "
                  f"{'vocabulary' if not near else 'vocabulary well enough'}"
                  + (f"; closest were {near}" if near else "")
                  + ". Name a tissue, disease or subtype, e.g. 'small cell lung "
                    "cancer', 'Ewing sarcoma', 'melanoma'."))
    return Resolution(scored[0], alternatives=scored[1:5], expanded=used,
                      note=scored[0].why)
