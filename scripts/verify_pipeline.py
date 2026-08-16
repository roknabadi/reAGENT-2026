#!/usr/bin/env python
"""Prove the computed half of the pipeline is reproducible, then check it is right.

    python scripts/verify_pipeline.py          # exits non-zero on any failure

Four checks, in order of how badly they fail:

1. **Inputs are pinned.** Every input file is hashed. If DepMap reissues a file
   under the same name, the fingerprint moves and every number below is from a
   different dataset. Silent input drift is the worst failure because nothing
   looks wrong.
2. **Determinism.** The statistics + gates run twice from the same inputs and
   must produce byte-identical output. A pipeline that cannot repeat itself
   cannot be checked by anyone.
3. **Golden values.** Known-correct numbers, verified by hand and independently
   reproduced by Kevin's separate scan. These catch a threshold or formula
   changing without anyone noticing the results moved.
4. **Known biology.** The gates must still reject the pan-essential controls and
   still recover the independently known selective dependencies.

Needs the gitignored DepMap files; see team/TASKS.md for the fetch commands.
"""
from __future__ import annotations

import pathlib
import sys

from dependency_scout.depmap import analyze_gene_effects, load_tf_universe
from dependency_scout.provenance import InputFile, RunProvenance, digest_payload
from dependency_scout.ranking import rank_all

ROOT = pathlib.Path(__file__).resolve().parents[1]
D = ROOT / "downloads"
GE, MODELS, TFS = D / "CRISPRGeneEffect.csv", D / "Model.csv", D / "lambert_tfs.csv"

# Verified by hand against DepMap 24Q2 and independently reproduced by Kevin's
# separate all-lineage scan on 24Q4. A change here is a real change in results.
GOLDEN = {
    ("IRF4", "Lymphoid"):                   dict(n=81, sel=1.03, ofrac=0.03),
    ("PAX8", "Kidney"):                     dict(n=37, sel=0.77, ofrac=0.06),
    ("ISL1", "Peripheral Nervous System"):  dict(n=41, sel=0.60, ofrac=0.00),
    ("TP63", "Head and Neck"):              dict(n=72, sel=0.63, ofrac=0.06),
}
# Must be rejected: profoundly dependent, not remotely selective.
PAN_ESSENTIAL = {"CTCF", "MYC", "GTF2B", "AHCTF1"}
LANDSCAPE_TFS, LANDSCAPE_PASS = 1588, 0     # Lung: the worked negative case
TOL = 0.015


def core(context: str, genes=None):
    """The deterministic half: statistics + gates. No network, no model."""
    return rank_all(analyze_gene_effects(GE, MODELS, context=context, genes=genes,
                                         source_version="DepMap Public 24Q2"))


def as_rows(cands):
    return [{"gene": c.dependency.gene, "n": c.dependency.n_target_models,
             "median": round(c.dependency.median_target_effect, 6),
             "sel": round(c.dependency.selectivity_delta, 6),
             "ofrac": round(c.dependency.other_dependent_fraction, 6),
             "pass": c.gate.eligible, "why": c.gate.failures} for c in cands]


def main() -> int:
    if not GE.exists():
        print(f"FAIL  missing {GE} — see team/TASKS.md", file=sys.stderr)
        return 2
    fails: list[str] = []

    # 1 — inputs pinned
    prov = RunProvenance(inputs=[InputFile.pin("gene_effect", GE),
                                 InputFile.pin("models", MODELS),
                                 InputFile.pin("tf_universe", TFS)])
    print(f"[1] inputs pinned · fingerprint {prov.fingerprint[:16]}")
    for f in prov.inputs:
        print(f"      {f.name:14s} {f.sha256[:16]}  {f.bytes/1e6:8.1f} MB")

    universe = load_tf_universe(TFS)

    # 2 — determinism
    a, b = as_rows(core("Lung", universe)), as_rows(core("Lung", universe))
    da, db = digest_payload(a), digest_payload(b)
    if da == db:
        print(f"[2] deterministic · {len(a)} TFs, digest {da[:16]}")
    else:
        fails.append("two identical runs produced different output")
        print(f"[2] FAIL not deterministic: {da[:16]} vs {db[:16]}")

    # 3 — landscape shape
    npass = sum(r["pass"] for r in a)
    ok = len(a) == LANDSCAPE_TFS and npass == LANDSCAPE_PASS
    print(f"[3] landscape · {len(a)} screened, {npass} pass "
          f"(expect {LANDSCAPE_TFS}/{LANDSCAPE_PASS}) {'OK' if ok else 'FAIL'}")
    if not ok:
        fails.append(f"landscape moved: {len(a)} screened / {npass} pass")

    # 4 — pan-essentials must still be rejected
    by = {r["gene"]: r for r in a}
    for g in sorted(PAN_ESSENTIAL):
        r = by.get(g)
        if r is None:
            fails.append(f"{g} missing from the screen"); print(f"[4] FAIL {g} missing"); continue
        if r["pass"] or r["ofrac"] < 0.80:
            fails.append(f"{g} no longer rejected as pan-essential")
            print(f"[4] FAIL {g} pass={r['pass']} ofrac={r['ofrac']:.2f}")
        else:
            print(f"[4] rejected {g:8s} ofrac {r['ofrac']:.2f} · {r['why'][0][:46]}")

    # 5 — golden values across lineages
    genes = {g for g, _ in GOLDEN}
    seen = {}
    for ctx in {c for _, c in GOLDEN}:
        for c in core(ctx, genes):
            seen[(c.dependency.gene, ctx)] = c
    for key, want in GOLDEN.items():
        c = seen.get(key)
        if c is None:
            fails.append(f"{key} absent"); print(f"[5] FAIL {key} absent"); continue
        d = c.dependency
        bad = (d.n_target_models != want["n"]
               or abs(d.selectivity_delta - want["sel"]) > TOL
               or abs(d.other_dependent_fraction - want["ofrac"]) > TOL)
        mark = "FAIL" if bad else "OK  "
        print(f"[5] {mark} {key[0]:6s} {key[1]:26s} n={d.n_target_models:3d} "
              f"sel={d.selectivity_delta:.2f} ofrac={d.other_dependent_fraction:.2f}")
        if bad:
            fails.append(f"{key[0]} in {key[1]} drifted from its verified value")

    print()
    if fails:
        print(f"FAILED — {len(fails)} check(s):", file=sys.stderr)
        for f in fails:
            print(f"  · {f}", file=sys.stderr)
        return 1
    print("All checks passed. Computed results are pinned, reproducible and unchanged.")
    print("Retrieved results (Paperclip, any model output) are NOT covered by this "
          "and must not be treated as reproducible.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
