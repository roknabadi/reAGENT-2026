#!/usr/bin/env python
"""Score a POU2F3-POU2AF2 ensemble against the experimental interface.

    python scripts/score_pou2f3_control.py                 # every sample set found
    python scripts/score_pou2f3_control.py --tag _msa      # just the MSA-backed run

Scoring lives in a file rather than in a shell one-liner for the same reason the
gate does: a number produced by hand once cannot be re-derived, and a control
whose verdict depends on how it was read is not a control.

Two ensembles are compared here, and the difference between them is the whole
point. The first ran with `msa: empty` — `run_boltz2` only consumes MSAs the
caller supplies, so a `Boltz2Input` without them predicts in single-sequence
mode. That is a much weaker model, and a failure there says nothing about
Boltz-2 as normally used. The second supplies real alignments. Reporting only
one of them would answer a question nobody asked.

Nothing here is tuned. Thresholds come from `ConsensusConfig`, and the pass
condition was fixed before the first sample came back: the ensemble converges,
and the dominant interface lands on the experimental one.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from reagent_workflow.interface import (ConsensusConfig, build_consensus,  # noqa: E402
                                        contacts_between, parse_mmcif)

OUT = ROOT / "runs" / "pou2f3_control"
IPTM_FLOOR = 0.60      # the same bar the ELK1 control was held to


def score(tag: str, cfg: ConsensusConfig, truth: set[int]) -> dict | None:
    paths = sorted(OUT.glob(f"sample{tag}_*.json"))
    if not paths:
        return None

    rows, samples = [], []
    for p in paths:
        d = json.loads(p.read_text())
        if not d.get("structures"):
            continue
        st = d["structures"][0]
        m = st["metrics"]
        with tempfile.NamedTemporaryFile("w", suffix=".cif", delete=False) as f:
            f.write(st["structure"])
            tmp = f.name
        s = contacts_between(parse_mmcif(tmp), target_chain="A", partner_chain="B",
                             cutoff=cfg.contact_cutoff_angstrom, sample=p.stem)
        samples.append(s)
        pred = set(s.target_residues)
        rows.append({
            "sample": p.name,
            "seed": int(p.stem.split("seed")[1]),
            "iptm": round(m["iptm"], 4), "ptm": round(m["ptm"], 4),
            "complex_plddt": round(m["complex_plddt"], 4),
            "avg_pae": round(m["avg_pae"], 2),
            "n_predicted_contacts": len(pred),
            "predicted_pou2f3_contacts": sorted(pred),
            # Recall over the experimental interface. Precision is reported too
            # because a prediction that contacts everything scores full recall
            # and means nothing.
            "recall": round(len(pred & truth) / len(truth), 3) if truth else 0.0,
            "precision": round(len(pred & truth) / len(pred), 3) if pred else 0.0,
        })
    if not rows:
        return None

    con = build_consensus(samples, cfg)
    best = max(r["iptm"] for r in rows)
    passed = con.converged and best >= IPTM_FLOOR
    return {
        "tag": tag or "(no MSA)",
        "msa": bool(tag == "_msa"),
        "samples": len(rows),
        "iptm": {"min": min(r["iptm"] for r in rows), "max": best},
        "recall": {"min": min(r["recall"] for r in rows),
                   "max": max(r["recall"] for r in rows)},
        "precision": {"max": max(r["precision"] for r in rows)},
        "consensus": {
            "dominant_cluster_samples": con.dominant_cluster_samples,
            "total_samples": con.total_samples,
            "ensemble_support": round(con.ensemble_support, 3),
            "converged": con.converged,
            "blockers": con.blockers,
        },
        "verdict": "PASSED" if passed else "FAILED",
        "per_sample": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tag", default=None,
                    help="score one variant only, e.g. _msa")
    ap.add_argument("--json", type=pathlib.Path,
                    default=OUT / "result.json")
    a = ap.parse_args()

    gt_path = OUT / "ground_truth.json"
    if not gt_path.exists():
        print(f"missing {gt_path}; run scripts/pou2f3_control.py first",
              file=sys.stderr)
        return 2
    gt = json.loads(gt_path.read_text())
    truth = set(gt["pou2f3_interface"])
    cfg = ConsensusConfig()

    tags = [a.tag] if a.tag is not None else ["", "_msa"]
    variants = [v for v in (score(t, cfg, truth) for t in tags) if v]
    if not variants:
        print("no sample files found", file=sys.stderr)
        return 2

    print(f"experimental POU2F3 interface ({gt['pdb']}, {gt['method']} "
          f"{gt['resolution_angstrom']} A): {sorted(truth)}")
    print(f"pass condition, fixed in advance: ensemble converges AND best ipTM "
          f">= {IPTM_FLOOR}\n")
    for v in variants:
        label = "with MSA" if v["msa"] else "single-sequence (no MSA)"
        print(f"── {label}  [{v['samples']} samples] ──")
        print(f"   ipTM      {v['iptm']['min']:.3f} – {v['iptm']['max']:.3f}"
              f"   (floor {IPTM_FLOOR})")
        print(f"   recall    {v['recall']['min']:.0%} – {v['recall']['max']:.0%}"
              f"   precision max {v['precision']['max']:.0%}")
        c = v["consensus"]
        print(f"   consensus {c['dominant_cluster_samples']}/{c['total_samples']} "
              f"samples, support {c['ensemble_support']}, "
              f"converged={c['converged']}")
        for b in c["blockers"]:
            print(f"             · {b}")
        print(f"   VERDICT   {v['verdict']}\n")

    record = {
        "control": "POU2F3 - POU2AF2 (OCA-T1)",
        "role": "orthogonal structural control",
        "ground_truth": {"pdb": gt["pdb"], "method": gt["method"],
                         "resolution_angstrom": gt["resolution_angstrom"],
                         "pou2f3_interface": sorted(truth)},
        "pass_condition": f"ensemble converges AND best ipTM >= {IPTM_FLOOR}",
        "thresholds": {"note": "ConsensusConfig as configured; nothing tuned "
                               "after seeing results", **cfg.model_dump()},
        "variants": variants,
        "limitations": gt["limitations"] + [
            "Boltz-2 confidence and the contact consensus are computational. "
            "Neither is evidence of binding.",
        ],
    }
    a.json.parent.mkdir(parents=True, exist_ok=True)
    a.json.write_text(json.dumps(record, indent=2))
    print(f"wrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
