"""
Stage 1 — Dependency mining (DepMap).

Hierarchical two-pass selective-dependency scan across the full TF universe.
See docs/DISCOVERY_ARCHITECTURE.md §3 for the locked design.

Pass 1 (lineage):  for every TF x OncotreeLineage, compare in-lineage vs.
                    out-of-lineage Chronos gene-effect scores.
Pass 2 (subtype):  for every (TF, lineage) pair that passed Pass 1, drill
                    into OncotreeSubtype within that lineage vs. the rest of
                    the panel, flagging small-n groups as low-confidence
                    instead of dropping them.

Output: data/results/stage1_dependency_hits.csv
"""

import re
import argparse

import numpy as np
import pandas as pd
from scipy import stats

import config
from utils import download_cached, benjamini_hochberg

GENE_COL_RE = re.compile(r"^(.*) \(\d+\)$")


def download_depmap_files():
    paths = {}
    for name, file_id in config.DEPMAP_FILES.items():
        url = config.DEPMAP_DOWNLOAD_URL_TMPL.format(file_id=file_id)
        dest = config.CACHE_DIR / name
        download_cached(url, dest, description=f"DepMap {config.DEPMAP_RELEASE} {name}")
        paths[name] = dest
    return paths


def load_model(path) -> pd.DataFrame:
    df = pd.read_csv(path)
    return df[["ModelID", "OncotreeLineage", "OncotreeSubtype", "OncotreePrimaryDisease"]]


def load_gene_effect(path, tf_symbols) -> pd.DataFrame:
    """Loads CRISPRGeneEffect.csv and subsets columns to the TF universe.
    Columns are formatted 'SYMBOL (ENTREZID)'; the first column is ModelID."""
    df = pd.read_csv(path, index_col=0)
    col_symbol = {}
    for col in df.columns:
        m = GENE_COL_RE.match(col)
        symbol = m.group(1) if m else col
        col_symbol[col] = symbol

    tf_set = set(tf_symbols)
    keep_cols = [c for c, sym in col_symbol.items() if sym in tf_set]
    df = df[keep_cols].rename(columns=col_symbol)
    df.index.name = "ModelID"
    return df


def _compare_groups(in_vals: np.ndarray, out_vals: np.ndarray):
    """Returns (in_median, out_median, cohens_d, pvalue, in_dependent_fraction,
    out_dependent_fraction) for one TF/context comparison. Uses Mann-Whitney U
    (two-sided) per the locked design's rank-sum test choice.

    The dependent-fraction pair feeds `DependencyEvidence.target_dependent_fraction`
    / `other_dependent_fraction` in the shared contract (src/dependency_scout/models.py)
    -- per docs/SCORING_SPEC.md Open Question 2, the per-model essentiality cutoff
    reuses config.IN_CONTEXT_DEPENDENCY_THRESHOLD rather than introducing a second
    number, so "median-based dependency flag" and "fraction of dependent models"
    agree on what "dependent" means."""
    in_median = float(np.median(in_vals))
    out_median = float(np.median(out_vals))

    n1, n2 = len(in_vals), len(out_vals)
    pooled_std = np.sqrt(
        ((n1 - 1) * np.var(in_vals, ddof=1) + (n2 - 1) * np.var(out_vals, ddof=1))
        / max(n1 + n2 - 2, 1)
    )
    cohens_d = float((np.mean(in_vals) - np.mean(out_vals)) / pooled_std) if pooled_std > 0 else 0.0

    try:
        _, pvalue = stats.mannwhitneyu(in_vals, out_vals, alternative="two-sided")
    except ValueError:
        pvalue = 1.0  # identical/degenerate groups

    in_dependent_fraction = float(np.mean(in_vals <= config.IN_CONTEXT_DEPENDENCY_THRESHOLD))
    out_dependent_fraction = float(np.mean(out_vals <= config.IN_CONTEXT_DEPENDENCY_THRESHOLD))

    return in_median, out_median, cohens_d, float(pvalue), in_dependent_fraction, out_dependent_fraction


def _dependency_flag(in_median, out_median, in_frac, out_frac):
    """A candidate passes on EITHER path (see config.py):
    - median: the whole group is dependent (catches uniform dependencies,
      and is what rejects pan-essential genes on its own).
    - specificity-first: a meaningful fraction of the group is dependent and
      almost none of the rest is, regardless of the group median (catches
      subpopulation-restricted dependencies a pooled-subtype median dilutes
      away -- see config.py for the ASCL1/POU2F3 case this exists for)."""
    median_path = (
        in_median <= config.IN_CONTEXT_DEPENDENCY_THRESHOLD
        and out_median > config.OUT_OF_CONTEXT_NONDEPENDENCY_THRESHOLD
    )
    specificity_path = (
        in_frac >= config.SPECIFICITY_FIRST_MIN_TARGET_FRACTION
        and out_frac <= config.SPECIFICITY_FIRST_MAX_OTHER_FRACTION
    )
    return median_path or specificity_path


def pass1_lineage_scan(gene_effect: pd.DataFrame, model: pd.DataFrame, min_n: int = 5) -> pd.DataFrame:
    """Per-TF, per-lineage selectivity scan. See §3 Pass 1."""
    merged_index = gene_effect.index.intersection(model["ModelID"])
    ge = gene_effect.loc[merged_index]
    model_indexed = model.set_index("ModelID").loc[merged_index]

    lineages = model_indexed["OncotreeLineage"].dropna().unique()
    tf_skew = stats.skew(ge.values, axis=0, nan_policy="omit")
    skew_by_tf = dict(zip(ge.columns, tf_skew))

    rows = []
    for lineage in lineages:
        in_mask = (model_indexed["OncotreeLineage"] == lineage).values
        n_in, n_out = in_mask.sum(), (~in_mask).sum()
        if n_in < min_n or n_out < min_n:
            continue
        in_block = ge.values[in_mask, :]
        out_block = ge.values[~in_mask, :]
        for j, tf in enumerate(ge.columns):
            in_vals = in_block[:, j]
            out_vals = out_block[:, j]
            in_vals = in_vals[~np.isnan(in_vals)]
            out_vals = out_vals[~np.isnan(out_vals)]
            if len(in_vals) < min_n or len(out_vals) < min_n:
                continue
            in_median, out_median, cohens_d, pvalue, in_frac, out_frac = _compare_groups(in_vals, out_vals)
            rows.append(
                {
                    "tf": tf,
                    "context": lineage,
                    "context_level": "lineage",
                    "n_in": len(in_vals),
                    "n_out": len(out_vals),
                    "in_median": in_median,
                    "out_median": out_median,
                    "selectivity_delta": in_median - out_median,
                    "cohens_d": cohens_d,
                    "skew": skew_by_tf.get(tf, np.nan),
                    "pvalue": pvalue,
                    "target_dependent_fraction": in_frac,
                    "other_dependent_fraction": out_frac,
                    "dependency_flag": _dependency_flag(in_median, out_median, in_frac, out_frac),
                }
            )

    result = pd.DataFrame(rows)
    if not result.empty:
        result["qvalue"] = benjamini_hochberg(result["pvalue"].values)
        result["confidence_flag"] = "full"
    return result


def pass2_subtype_scan(gene_effect: pd.DataFrame, model: pd.DataFrame, min_n: int = None) -> pd.DataFrame:
    """Every TF x every OncotreeSubtype, unconditionally -- NOT gated behind
    Pass-1 lineage-level significance (2026-08-15 fix). The old design only
    drilled into subtypes within lineages that already showed lineage-level
    signal, for TFs that already passed at that level. That structurally
    cannot find a subtype-restricted dependency that is diluted below
    threshold at BOTH the subtype level (e.g. pooled SCLC-A/N/P/Y) AND the
    parent-lineage level (pooled with every other lung cancer type) -- which
    is exactly the ASCL1/POU2F3 case (see config.py). Same
    _compare_groups/_dependency_flag logic as Pass 1, just keyed on subtype
    instead of lineage; costs more compute than the old gated version, not
    more code."""
    min_n = min_n if min_n is not None else config.SUBTYPE_MIN_N_FLOOR
    merged_index = gene_effect.index.intersection(model["ModelID"])
    ge = gene_effect.loc[merged_index]
    model_indexed = model.set_index("ModelID").loc[merged_index]

    subtype_to_lineage = (
        model_indexed[["OncotreeSubtype", "OncotreeLineage"]]
        .dropna()
        .drop_duplicates(subset="OncotreeSubtype")
        .set_index("OncotreeSubtype")["OncotreeLineage"]
    )
    subtypes = model_indexed["OncotreeSubtype"].dropna().unique()

    rows = []
    for subtype in subtypes:
        in_mask = (model_indexed["OncotreeSubtype"] == subtype).values
        n_in, n_out = in_mask.sum(), (~in_mask).sum()
        if n_in < min_n or n_out < min_n:
            continue
        confidence = "full" if n_in >= config.SUBTYPE_MIN_N_FULL_CONFIDENCE else "low-confidence (small n)"
        parent_lineage = subtype_to_lineage.get(subtype)

        in_block = ge.values[in_mask, :]
        out_block = ge.values[~in_mask, :]
        for j, tf in enumerate(ge.columns):
            in_vals = in_block[:, j]
            out_vals = out_block[:, j]
            in_vals = in_vals[~np.isnan(in_vals)]
            out_vals = out_vals[~np.isnan(out_vals)]
            if len(in_vals) < min_n or len(out_vals) < min_n:
                continue
            in_median, out_median, cohens_d, pvalue, in_frac, out_frac = _compare_groups(in_vals, out_vals)
            rows.append(
                {
                    "tf": tf,
                    "context": subtype,
                    "context_level": "subtype",
                    "parent_lineage": parent_lineage,
                    "n_in": len(in_vals),
                    "n_out": len(out_vals),
                    "in_median": in_median,
                    "out_median": out_median,
                    "selectivity_delta": in_median - out_median,
                    "cohens_d": cohens_d,
                    "skew": np.nan,
                    "pvalue": pvalue,
                    "target_dependent_fraction": in_frac,
                    "other_dependent_fraction": out_frac,
                    "dependency_flag": _dependency_flag(in_median, out_median, in_frac, out_frac),
                    "confidence_flag": confidence,
                }
            )

    result = pd.DataFrame(rows)
    if not result.empty:
        result["qvalue"] = benjamini_hochberg(result["pvalue"].values)
    return result


def run(min_n_lineage: int = 5) -> pd.DataFrame:
    tf_universe = pd.read_csv(config.TF_UNIVERSE_CACHE)
    paths = download_depmap_files()

    print("[stage1] loading Model.csv ...")
    model = load_model(paths["Model.csv"])

    print("[stage1] loading CRISPRGeneEffect.csv (subset to TF universe) ...")
    gene_effect = load_gene_effect(paths["CRISPRGeneEffect.csv"], tf_universe["gene_symbol"])
    print(f"[stage1] gene effect matrix: {gene_effect.shape[0]} models x {gene_effect.shape[1]} TFs")

    print("[stage1] Pass 1 — lineage-level scan ...")
    pass1 = pass1_lineage_scan(gene_effect, model, min_n=min_n_lineage)
    print(f"[stage1] Pass 1: {len(pass1)} TF-lineage tests, "
          f"{(pass1['dependency_flag'] & (pass1['qvalue'] < config.FDR_ALPHA)).sum()} significant hits")

    print("[stage1] Pass 2 — subtype-level scan, every TF x every subtype ...")
    pass2 = pass2_subtype_scan(gene_effect, model)
    print(f"[stage1] Pass 2: {len(pass2)} TF-subtype tests, "
          f"{(pass2['dependency_flag'] & (pass2['qvalue'] < config.FDR_ALPHA)).sum()} significant hits")

    combined = pd.concat([pass1, pass2], ignore_index=True, sort=False)
    out_path = config.RESULTS_DIR / "stage1_dependency_hits.csv"
    combined.to_csv(out_path, index=False)
    print(f"[stage1] wrote {len(combined)} rows -> {out_path}")
    return combined


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stage 1: DepMap dependency mining")
    parser.add_argument("--min-n-lineage", type=int, default=5)
    args = parser.parse_args()
    run(min_n_lineage=args.min_n_lineage)
