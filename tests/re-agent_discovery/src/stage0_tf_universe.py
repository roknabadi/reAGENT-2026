"""
Stage 0 — TF universe.

Pulls the Lambert et al. 2018 curated human TF list and filters to
high-confidence TFs. See docs/DISCOVERY_ARCHITECTURE.md §2.
"""

import pandas as pd

import config
from utils import download_cached


def build_tf_universe(high_confidence_only: bool = True) -> pd.DataFrame:
    """Returns a DataFrame with columns: gene_symbol, ensembl_id, dbd,
    is_tf. Caches the raw source file and the filtered universe."""
    raw_path = config.CACHE_DIR / "lambert_tf_raw.csv"
    download_cached(config.TF_LIST_URL, raw_path, description="Lambert et al. TF list")

    raw = pd.read_csv(raw_path, index_col=0)
    raw = raw.rename(
        columns={
            "Ensembl ID": "ensembl_id",
            "HGNC symbol": "gene_symbol",
            "DBD": "dbd",
            "Is TF?": "is_tf",
        }
    )

    df = raw[["gene_symbol", "ensembl_id", "dbd", "is_tf"]].copy()
    if high_confidence_only:
        df = df[df["is_tf"].astype(str).str.strip().str.lower() == "yes"]

    df = df.dropna(subset=["gene_symbol"]).drop_duplicates(subset=["gene_symbol"]).reset_index(drop=True)
    df.to_csv(config.TF_UNIVERSE_CACHE, index=False)
    return df


if __name__ == "__main__":
    tfs = build_tf_universe()
    print(f"TF universe: {len(tfs)} high-confidence TFs -> {config.TF_UNIVERSE_CACHE}")
    print(tfs.head())
