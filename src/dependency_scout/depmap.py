"""Streaming-friendly adapter for public DepMap model and gene-effect matrices."""

import re
from pathlib import Path
import pandas as pd
from scipy.stats import mannwhitneyu
from .models import DependencyEvidence, EvidenceTier, SourceRecord

GENE_SUFFIX = re.compile(r"\s+\(\d+\)$")


def gene_symbol(column: str) -> str:
    return GENE_SUFFIX.sub("", str(column)).strip()


def load_tf_universe(path: str | Path) -> set[str]:
    """HGNC symbols from the Lambert et al. 2018 human TF catalogue.

    Screening 1.6k transcription factors instead of all 18k genes is the point:
    a TF-dependency question does not get a better answer from ribosomal genes,
    and the full matrix is 382 MB.
    """
    frame = pd.read_csv(path, low_memory=False)
    is_tf = frame["Is TF?"].astype(str).str.strip().str.casefold() == "yes"
    return set(frame.loc[is_tf, "HGNC symbol"].dropna().astype(str))


def _detect_column(frame: pd.DataFrame, names: tuple[str, ...]) -> str:
    lowered = {str(c).lower(): str(c) for c in frame.columns}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    # The published CRISPRGeneEffect.csv ships its ModelID column with an empty
    # header, which pandas names "Unnamed: 0". Every other column is a gene.
    first = str(frame.columns[0])
    if first.startswith("Unnamed:") or first == "":
        return first
    raise ValueError(f"Could not find any of {names}; columns={list(frame.columns)[:20]}")


def load_context_map(model_path: str | Path, context_column: str | None = None) -> tuple[dict[str, str], str]:
    frame = pd.read_csv(model_path, low_memory=False)
    model_col = _detect_column(frame, ("ModelID", "DepMap_ID", "Model Id"))
    if context_column is None:
        context_col = _detect_column(frame, ("OncotreeLineage", "OncotreePrimaryDisease", "Primary Disease", "lineage"))
    elif context_column not in frame.columns:
        raise ValueError(f"Context column {context_column!r} is absent")
    else:
        context_col = context_column
    subset = frame[[model_col, context_col]].dropna()
    return dict(zip(subset[model_col].astype(str), subset[context_col].astype(str), strict=False)), context_col


def analyze_gene_effects(gene_effect_path: str | Path, model_path: str | Path, *, context: str,
    genes: set[str] | None = None, context_column: str | None = None, dependency_threshold: float = -0.5,
    source_version: str = "DepMap Public 26Q1", synthetic: bool = False) -> list[DependencyEvidence]:
    context_map, resolved_context_col = load_context_map(model_path, context_column)
    header = pd.read_csv(gene_effect_path, nrows=0)
    model_col = _detect_column(header, ("ModelID", "DepMap_ID", "Model Id"))
    selected = [c for c in header.columns if c != model_col and (genes is None or gene_symbol(c) in genes)]
    if not selected:
        raise ValueError("No requested genes were found in the gene-effect matrix")
    frame = pd.read_csv(gene_effect_path, usecols=[model_col, *selected], low_memory=False)
    frame["_context"] = frame[model_col].astype(str).map(context_map)
    target_mask = frame["_context"].astype(str).str.casefold() == context.casefold()
    source = SourceRecord(name="Synthetic DepMap-shaped fixture" if synthetic else "DepMap Public",
        version="test-fixture-v1" if synthetic else source_version,
        url="local://synthetic-fixture" if synthetic else "https://depmap.org/portal/data_page/",
        tier=EvidenceTier.SYNTHETIC if synthetic else EvidenceTier.PUBLIC_PRIMARY,
        notes=f"Context column: {resolved_context_col}; Chronos-style gene effects; lower is more dependent")
    records = []
    for column in selected:
        target = pd.to_numeric(frame.loc[target_mask, column], errors="coerce").dropna()
        other = pd.to_numeric(frame.loc[~target_mask, column], errors="coerce").dropna()
        if target.empty or other.empty:
            continue
        test = mannwhitneyu(target, other, alternative="less")
        target_frac = float((target < dependency_threshold).mean())
        other_frac = float((other < dependency_threshold).mean())
        records.append(DependencyEvidence(gene=gene_symbol(column), disease_context=context,
            n_target_models=len(target), n_other_models=len(other), median_target_effect=float(target.median()),
            median_other_effect=float(other.median()), target_dependent_fraction=target_frac,
            other_dependent_fraction=other_frac, selectivity_delta=float(other.median() - target.median()),
            mann_whitney_p=float(test.pvalue), source=source))
    return records
