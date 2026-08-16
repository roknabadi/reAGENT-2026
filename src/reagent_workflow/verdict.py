"""One dependency verdict, computed for one context, using the canonical gate.

The live interface and the CLI used to reach their own conclusion here: a
four-way AND (median <= -0.5, target fraction >= 0.5, other fraction <= 0.35,
selectivity >= 0.35) that no other part of the project agreed with. Kevin's
`stage1_depmap.py` passes a candidate on EITHER a median path OR a
specificity-first path, and that difference is not cosmetic — the
specificity-first path exists precisely because a median test cannot see a
dependency restricted to a subpopulation, which is the ASCL1/POU2F3 case. Two
gates meant the interface could call a TF a candidate that the canonical scan
rejected, or the reverse, with nothing to say which was right.

So nothing here decides anything. `_dependency_flag`, `_compare_groups` and
`benjamini_hochberg` are imported from that module and called; if the gate
changes there, it changes here, because it is the same function object. What
this module adds is scope: the canonical `run()` scans every TF against every
lineage and every subtype and writes a CSV, which is right for a batch job and
far too slow to sit behind a question box. `scan_context` runs the identical
comparison for a single named context.

Two honest differences from the batch run, recorded on every row rather than
smoothed over:

  qvalue_family   BH correction is family-dependent. The batch run corrects
                  across every TF x every context at once; a single-context
                  scan corrects across every TF in that one context. Both are
                  legitimate, they are not the same number, and a q-value
                  compared across the two would be wrong. The field says which.

  skew            computed across the full model matrix in the batch run and
                  reported per TF. Reproduced here because it is cheap, but
                  it is not part of the gate.

Everything the gate actually reads — medians, dependent fractions, n, the
route taken — is context-local and matches the batch run exactly.
`tests/test_verdict.py` asserts that row for row.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

# The canonical implementation lives outside the package tree. Import it rather
# than vendoring a copy: a vendored gate is a second gate the moment either
# side is edited.
_CANONICAL = Path(__file__).resolve().parents[2] / "tests" / "re-agent_discovery" / "src"
if str(_CANONICAL) not in sys.path:
    sys.path.insert(0, str(_CANONICAL))

import config as _stage1_config  # noqa: E402
from stage1_depmap import (_compare_groups, _dependency_flag,  # noqa: E402
                           benjamini_hochberg)

CANONICAL_SOURCE = "tests/re-agent_discovery/src/stage1_depmap.py"
DEPMAP_RELEASE = _stage1_config.DEPMAP_RELEASE
FDR_ALPHA = _stage1_config.FDR_ALPHA
MIN_N_FLOOR = _stage1_config.SUBTYPE_MIN_N_FLOOR
MIN_N_FULL_CONFIDENCE = _stage1_config.SUBTYPE_MIN_N_FULL_CONFIDENCE


@dataclass(frozen=True)
class Verdict:
    """One TF in one context. Every field the gate read, kept raw.

    `route` is the part that was previously thrown away: two candidates that
    both "passed" can have passed for opposite reasons, and a uniformly
    dependent lineage is a different claim from a dependency confined to a
    quarter of it.
    """
    gene: str
    context: str
    context_level: str          # "lineage" | "subtype"
    parent_lineage: str | None
    n_target: int
    n_other: int
    median_target: float
    median_other: float
    selectivity_delta: float    # in_median - out_median, negative = selective
    cohens_d: float
    target_dependent_fraction: float
    other_dependent_fraction: float
    pvalue: float
    qvalue: float
    qvalue_family: str
    dependency_flag: bool
    route: str                  # "median" | "specificity-first" | "both" | "none"
    confidence_flag: str

    @property
    def significant(self) -> bool:
        """Flag AND FDR. The batch run reports hits as the conjunction; a
        candidate that clears the gate on a q of 0.4 is not a hit there and
        must not become one here."""
        return self.dependency_flag and self.qvalue < FDR_ALPHA

    @property
    def low_n(self) -> bool:
        return self.n_target < MIN_N_FULL_CONFIDENCE


def _route(in_median: float, out_median: float, in_frac: float, out_frac: float) -> str:
    """Which path passed the candidate. Reads the same config constants the
    gate does, so it cannot drift from it, and is derived from the flag rather
    than deciding anything: `_dependency_flag` remains the only authority on
    whether a candidate passes at all."""
    med = (in_median <= _stage1_config.IN_CONTEXT_DEPENDENCY_THRESHOLD
           and out_median > _stage1_config.OUT_OF_CONTEXT_NONDEPENDENCY_THRESHOLD)
    spec = (in_frac >= _stage1_config.SPECIFICITY_FIRST_MIN_TARGET_FRACTION
            and out_frac <= _stage1_config.SPECIFICITY_FIRST_MAX_OTHER_FRACTION)
    return {(True, True): "both", (True, False): "median",
            (False, True): "specificity-first", (False, False): "none"}[(med, spec)]


@lru_cache(maxsize=4)
def load_matrix(gene_effect_path: str, model_path: str, tf_path: str):
    """Gene-effect matrix restricted to the TF universe, plus the model table.

    Cached because it is ~430 MB of CSV and every question needs the same
    parse. Keyed on the paths, so pointing at a different release reloads.
    """
    from dependency_scout.depmap import gene_symbol, load_tf_universe

    universe = load_tf_universe(tf_path)
    header = pd.read_csv(gene_effect_path, nrows=0)
    id_col = header.columns[0]
    keep = [c for c in header.columns[1:] if gene_symbol(c) in universe]
    ge = pd.read_csv(gene_effect_path, usecols=[id_col] + keep)
    ge = ge.rename(columns={id_col: "ModelID"}).set_index("ModelID")
    ge.columns = [gene_symbol(c) for c in ge.columns]

    model = pd.read_csv(model_path,
                        usecols=["ModelID", "OncotreeLineage", "OncotreeSubtype",
                                 "OncotreePrimaryDisease"])
    return ge, model


def contexts_available(model: pd.DataFrame, min_n: int = MIN_N_FLOOR) -> pd.DataFrame:
    """Every context the data can actually answer a question about, with its n.

    Both granularities, because the whole point of the subtype pass is that a
    lineage-level answer can be wrong by dilution. Nothing is hardcoded: this
    is whatever is in the Model.csv that was loaded.
    """
    rows = []
    for level, col in (("lineage", "OncotreeLineage"), ("subtype", "OncotreeSubtype")):
        counts = model[col].dropna().value_counts()
        for name, n in counts.items():
            if n < min_n:
                continue
            parent = None
            if level == "subtype":
                lin = model.loc[model[col] == name, "OncotreeLineage"].dropna()
                parent = lin.iloc[0] if len(lin) else None
            rows.append({"context": name, "level": level, "n_models": int(n),
                         "parent_lineage": parent})
    return pd.DataFrame(rows).sort_values(["level", "n_models"], ascending=[True, False])


def scan_context(gene_effect: pd.DataFrame, model: pd.DataFrame, context: str,
                 *, level: str | None = None, min_n: int = MIN_N_FLOOR) -> list[Verdict]:
    """Every TF in the universe against one context, through the canonical gate.

    `level` picks the column; when omitted it is resolved from the data — a
    subtype match wins over a lineage match, because the specific answer is
    the one the subtype pass exists to find.
    """
    idx = gene_effect.index.intersection(model["ModelID"])
    ge = gene_effect.loc[idx]
    mi = model.set_index("ModelID").loc[idx]

    if level is None:
        if context in set(mi["OncotreeSubtype"].dropna()):
            level = "subtype"
        elif context in set(mi["OncotreeLineage"].dropna()):
            level = "lineage"
        else:
            return []
    col = "OncotreeSubtype" if level == "subtype" else "OncotreeLineage"
    if context not in set(mi[col].dropna()):
        return []

    in_mask = (mi[col] == context).values
    n_in, n_out = int(in_mask.sum()), int((~in_mask).sum())
    if n_in < min_n or n_out < min_n:
        return []

    parent = None
    if level == "subtype":
        lin = mi.loc[in_mask, "OncotreeLineage"].dropna()
        parent = str(lin.iloc[0]) if len(lin) else None
    confidence = ("full" if n_in >= MIN_N_FULL_CONFIDENCE
                  else "low-confidence (small n)")

    in_block, out_block = ge.values[in_mask, :], ge.values[~in_mask, :]
    raw = []
    for j, tf in enumerate(ge.columns):
        iv = in_block[:, j][~np.isnan(in_block[:, j])]
        ov = out_block[:, j][~np.isnan(out_block[:, j])]
        if len(iv) < min_n or len(ov) < min_n:
            continue
        im, om, d, p, ifr, ofr = _compare_groups(iv, ov)
        raw.append((tf, len(iv), len(ov), im, om, d, p, ifr, ofr))
    if not raw:
        return []

    # BH across this context's TF family. See the module docstring: this is a
    # different family from the batch run's, and the field records that.
    qs = benjamini_hochberg(np.array([r[6] for r in raw]))

    out = [
        Verdict(gene=tf, context=context, context_level=level, parent_lineage=parent,
                n_target=n_i, n_other=n_o, median_target=im, median_other=om,
                selectivity_delta=im - om, cohens_d=d,
                target_dependent_fraction=ifr, other_dependent_fraction=ofr,
                pvalue=p, qvalue=float(q),
                qvalue_family=f"BH across {len(raw)} TFs in {context}",
                dependency_flag=bool(_dependency_flag(im, om, ifr, ofr)),
                route=_route(im, om, ifr, ofr), confidence_flag=confidence)
        for (tf, n_i, n_o, im, om, d, p, ifr, ofr), q in zip(raw, qs)
    ]
    # Most selective first, then by how much of the context is dependent.
    out.sort(key=lambda v: (v.selectivity_delta, -v.target_dependent_fraction))
    return out


def to_candidate(v: Verdict, release: str = DEPMAP_RELEASE):
    """A `Verdict` in the shared transport shape, so the rest of the pipeline
    is unchanged and there is still exactly one gate.

    The `GateResult` here REPORTS the canonical decision; it does not take one.
    `dependency_scout.ranking.apply_gates` would re-derive eligibility from a
    four-way AND and disagree — that second gate is what this change removes,
    so its output must never be attached to a candidate.

    Note the sign flip. `dependency_scout` defines selectivity as
    `other - target`, positive when a dependency is selective; the canonical
    scan defines it as `in - out`, negative for the same case. Both are in the
    codebase and neither is wrong, so the conversion is done once, here, rather
    than being re-derived at each consumer.
    """
    from dependency_scout.models import (DependencyEvidence, EvidenceTier,
                                         GateResult, RankedCandidate, SourceRecord)

    article = getattr(_stage1_config, "DEPMAP_ARTICLE_ID", None)

    failures: list[str] = []
    if not v.dependency_flag:
        failures.append(f"fails the canonical dependency gate on both paths "
                        f"(median {v.median_target:+.2f} in vs {v.median_other:+.2f} "
                        f"out; {v.target_dependent_fraction:.0%} of {v.context} "
                        f"dependent vs {v.other_dependent_fraction:.0%} elsewhere)")
    elif v.qvalue >= FDR_ALPHA:
        failures.append(f"clears the gate via the {v.route} path but q={v.qvalue:.2f} "
                        f"does not meet FDR {FDR_ALPHA}")

    return RankedCandidate(
        dependency=DependencyEvidence(
            gene=v.gene, disease_context=v.context,
            n_target_models=v.n_target, n_other_models=v.n_other,
            median_target_effect=v.median_target,
            median_other_effect=v.median_other,
            target_dependent_fraction=v.target_dependent_fraction,
            other_dependent_fraction=v.other_dependent_fraction,
            selectivity_delta=v.median_other - v.median_target,   # scout's sign
            mann_whitney_p=min(max(v.pvalue, 0.0), 1.0),
            source=SourceRecord(
                name="DepMap CRISPR gene effect (Chronos)",
                version=f"Public {release}",
                url=(f"https://figshare.com/articles/dataset/DepMap_{release}_Public/"
                     f"{article}" if article else "https://depmap.org/portal/download/"),
                tier=EvidenceTier.PUBLIC_DERIVED,
                notes=(f"{v.context_level}-level scan, gate from {CANONICAL_SOURCE}; "
                       + (f"passed on the {v.route} path; q={v.qvalue:.3g} "
                          f"({v.qvalue_family})" if v.dependency_flag
                          else "no path passed")))),
        gate=GateResult(eligible=v.significant, failures=failures))


def shortlist(verdicts: list[Verdict], n: int = 3,
              require_significant: bool = True) -> list[Verdict]:
    """The top n that clear the gate. Ordering is by selectivity, already
    applied by `scan_context`; this only filters and truncates."""
    passing = [v for v in verdicts
               if (v.significant if require_significant else v.dependency_flag)]
    return passing[:n]
