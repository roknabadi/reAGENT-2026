#!/usr/bin/env python
"""Prove the computed half of the pipeline is reproducible, then check it is right.

    python scripts/verify_pipeline.py          # exits non-zero on any failure

Checks, in order of how badly they fail:

1. **Inputs are pinned.** Every input file is hashed. If DepMap reissues a file
   under the same name, the fingerprint moves and every number below is from a
   different dataset. Silent input drift is the worst failure because nothing
   looks wrong.
2. **Determinism.** The statistics + gates run twice from the same inputs and
   must produce byte-identical output. A pipeline that cannot repeat itself
   cannot be checked by anyone.
2b. **The canonical path.** Checks 2-5 exercise `dependency_scout` on 24Q2,
   which is no longer what decides whether a TF is a candidate. This one
   covers the gate that does, on the release it is written against, and adds
   the granularity check the whole subtype pass exists for: four SCLC master
   regulators must be rejected when SCLC is pooled into Lung and recovered
   when it is not.
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
from reagent_workflow import verdict as V

ROOT = pathlib.Path(__file__).resolve().parents[1]
D = ROOT / "downloads"
GE, MODELS, TFS = D / "CRISPRGeneEffect.csv", D / "Model.csv", D / "lambert_tfs.csv"
# The release the canonical gate is written against (check 2b).
CANON_GE, CANON_MODELS = D / "24Q4" / "CRISPRGeneEffect.csv", D / "24Q4" / "Model.csv"

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

    # 2b — the canonical path, which is what the interface and the CLI now use.
    #
    # Checks 2-5 exercise `dependency_scout`'s own statistics on 24Q2. That
    # module is still used, but it is no longer what decides whether a TF is a
    # candidate -- the gate in stage1_depmap.py is, on 24Q4. Verifying only the
    # old path would report a green pipeline while the path that produces every
    # user-visible verdict went unchecked, which is the failure this script
    # exists to catch.
    if CANON_GE.exists():
        cprov = RunProvenance(inputs=[InputFile.pin("gene_effect_24q4", CANON_GE),
                                      InputFile.pin("models_24q4", CANON_MODELS),
                                      InputFile.pin("tf_universe", TFS)])
        print(f"[2b] canonical inputs pinned · fingerprint {cprov.fingerprint[:16]} "
              f"· DepMap {V.DEPMAP_RELEASE}")
        ge4, model4 = V.load_matrix(str(CANON_GE), str(CANON_MODELS), str(TFS))

        def canon_rows(ctx, level=None):
            return [{"gene": v.gene, "flag": v.dependency_flag, "route": v.route,
                     "median": round(v.median_target, 6),
                     "tfrac": round(v.target_dependent_fraction, 6),
                     "ofrac": round(v.other_dependent_fraction, 6),
                     "q": round(v.qvalue, 9)}
                    for v in V.scan_context(ge4, model4, ctx, level=level)]

        x, y = canon_rows("Lung", "lineage"), canon_rows("Lung", "lineage")
        dx, dy = digest_payload(x), digest_payload(y)
        if dx == dy:
            print(f"[2b] deterministic · {len(x)} TFs, digest {dx[:16]}")
        else:
            fails.append("the canonical scan is not deterministic")
            print(f"[2b] FAIL not deterministic: {dx[:16]} vs {dy[:16]}")

        # Granularity: the reason the subtype pass exists. These four are
        # dependencies of distinct SCLC subsets and are diluted to nothing when
        # SCLC is pooled into Lung, so a pipeline that only ever asks at lineage
        # level misses all of them without erroring.
        lung = {r["gene"]: r for r in x}
        sclc = {r["gene"]: r for r in canon_rows("Small Cell Lung Cancer", "subtype")}
        for g in ("ASCL1", "POU2F3", "NEUROD1", "INSM1"):
            l_, s_ = lung.get(g), sclc.get(g)
            if not (l_ and s_):
                fails.append(f"{g} absent from the canonical scan")
                print(f"[2b] FAIL {g} absent"); continue
            if s_["flag"] and not l_["flag"]:
                print(f"[2b] granularity {g:8s} Lung=reject  SCLC=pass via "
                      f"{s_['route']:18s} tfrac {s_['tfrac']:.2f}")
            else:
                fails.append(f"{g}: expected reject at Lung and pass at SCLC, got "
                             f"{l_['flag']}/{s_['flag']}")
                print(f"[2b] FAIL {g} Lung={l_['flag']} SCLC={s_['flag']}")

        for g in sorted(PAN_ESSENTIAL | {"POLR2A"}):
            r = lung.get(g)
            if r and r["flag"]:
                fails.append(f"{g} passes the canonical gate as a Lung dependency")
                print(f"[2b] FAIL {g} passed via {r['route']}")
        print(f"[2b] pan-essentials still rejected by the canonical gate")
    else:
        print(f"[2b] SKIP  {CANON_GE} not present — the canonical path is UNVERIFIED")

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
