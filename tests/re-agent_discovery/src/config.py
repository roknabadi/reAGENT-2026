"""
Shared config/constants for the discovery pipeline.

Pinning a specific DepMap release here (rather than resolving "latest" at
runtime) is a deliberate choice: depmap.org's own API/portal sits behind a
Cloudflare bot-check that blocks headless access, and Figshare's search API
doesn't reliably surface DepMap's newest quarterly release by title search.
The files below are individually verified to exist and match DepMap's
documented CRISPR/Model schema. Bump DEPMAP_RELEASE + DEPMAP_ARTICLE_ID +
DEPMAP_FILES if a newer release should be used instead.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "data" / "cache"
RESULTS_DIR = PROJECT_ROOT / "data" / "results"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Stage 0 — TF universe (Lambert et al. 2018, "The Human Transcription Factors")
# ---------------------------------------------------------------------------
TF_LIST_URL = "http://humantfs.ccbr.utoronto.ca/download/v_1.01/DatabaseExtract_v_1.01.csv"
TF_UNIVERSE_CACHE = CACHE_DIR / "tf_universe.csv"

# ---------------------------------------------------------------------------
# Stage 1 — DepMap (pinned release; see module docstring)
# ---------------------------------------------------------------------------
DEPMAP_RELEASE = "24Q4"
DEPMAP_ARTICLE_ID = 27993248  # figshare.com/articles/dataset/DepMap_24Q4_Public/27993248

# file_id verified via https://api.figshare.com/v2/articles/27993248
DEPMAP_FILES = {
    "CRISPRGeneEffect.csv": 51064667,
    "Model.csv": 51065297,
}
DEPMAP_DOWNLOAD_URL_TMPL = "https://ndownloader.figshare.com/files/{file_id}"

# Minimum cell lines required for a Pass-2 (subtype-level) group to be
# reported at full confidence; below this it's still reported but flagged.
SUBTYPE_MIN_N_FULL_CONFIDENCE = 15
# Absolute floor — groups smaller than this are too small to test at all.
SUBTYPE_MIN_N_FLOOR = 5

# Dependency-flag thresholds (see docs/DISCOVERY_ARCHITECTURE.md §3)
IN_CONTEXT_DEPENDENCY_THRESHOLD = -0.5
OUT_OF_CONTEXT_NONDEPENDENCY_THRESHOLD = -0.2

# Specificity-first path (2026-08-15): a median-based test cannot see a
# dependency that defines a subpopulation within a pooled OncotreeSubtype
# rather than the whole group -- e.g. ASCL1 (39% dependent in SCLC, 1%
# outside) and POU2F3 (17% in, 0% outside) both fail the median test above
# because SCLC-A/N/P/Y are pooled into one "Small Cell Lung Cancer" subtype
# by DepMap, diluting a subtype-restricted signal below every median
# threshold. This path passes a candidate on fraction-of-dependent-models
# alone, regardless of the group median, as an OR alongside the median test
# -- never a replacement, since the median test is what correctly rejects
# pan-essential genes (GTF2B, CTCF, MYC, AHCTF1 all still fail this: their
# out-of-context fraction is far above the ceiling below).
SPECIFICITY_FIRST_MIN_TARGET_FRACTION = 0.10
SPECIFICITY_FIRST_MAX_OTHER_FRACTION = 0.05
FDR_ALPHA = 0.05

# ---------------------------------------------------------------------------
# Stage 4 — Mediator subunit reference set (used by later stages)
# ---------------------------------------------------------------------------
MEDIATOR_SUBUNITS = [
    "MED1", "MED4", "MED6", "MED7", "MED8", "MED9", "MED10", "MED11",
    "MED12", "MED12L", "MED13", "MED13L", "MED14", "MED15", "MED16",
    "MED17", "MED18", "MED19", "MED20", "MED21", "MED22", "MED23",
    "MED24", "MED25", "MED26", "MED27", "MED28", "MED29", "MED30",
    "MED31", "CDK8", "CDK19", "CCNC",
]
