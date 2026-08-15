"""Small shared helpers: cached HTTP download + BH FDR correction."""

import sys
import numpy as np
import requests


def download_cached(url: str, dest_path, description: str = "", chunk_size: int = 1 << 20):
    """Stream-download `url` to `dest_path` unless it already exists and is
    non-empty. Returns dest_path. Simple size-based cache check — good
    enough for pinned, immutable release files (Figshare file IDs are
    content-addressed by release, they don't change under us)."""
    if dest_path.exists() and dest_path.stat().st_size > 0:
        print(f"[cache hit] {description or dest_path.name} ({dest_path.stat().st_size:,} bytes)")
        return dest_path

    print(f"[downloading] {description or dest_path.name} <- {url}")
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        written = 0
        with open(tmp_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=chunk_size):
                if not chunk:
                    continue
                f.write(chunk)
                written += len(chunk)
                if total:
                    pct = 100 * written / total
                    sys.stdout.write(f"\r  {written:,}/{total:,} bytes ({pct:5.1f}%)")
                    sys.stdout.flush()
        if total:
            sys.stdout.write("\n")
    tmp_path.rename(dest_path)
    print(f"[done] {dest_path} ({dest_path.stat().st_size:,} bytes)")
    return dest_path


def benjamini_hochberg(pvalues):
    """Benjamini-Hochberg FDR correction. Returns q-values in the same
    order as the input. Implemented directly (no statsmodels dependency)."""
    p = np.asarray(pvalues, dtype=float)
    n = len(p)
    if n == 0:
        return p
    order = np.argsort(p)
    ranked = p[order]
    ranks = np.arange(1, n + 1)
    q_sorted = ranked * n / ranks
    # enforce monotonicity (q-values must not decrease as p increases)
    q_sorted = np.minimum.accumulate(q_sorted[::-1])[::-1]
    q_sorted = np.clip(q_sorted, 0, 1)
    q = np.empty(n)
    q[order] = q_sorted
    return q
