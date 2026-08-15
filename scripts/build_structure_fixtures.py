#!/usr/bin/env python
"""Regenerate the cached structural demo outputs.

The structure cache is content-addressed, so the filename is derived from the
request (model, chain sequences, config, seed). Editing the fixture bundle
changes those hashes; rerun this script to keep the cached demo working:

    python scripts/build_structure_fixtures.py

The values written here are SYNTHETIC. They exist so the demo can exercise the
Boltz2/ESMFold2 comparison path without live Modal access, and every result
built from them is labelled as a fixture.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from reagent_workflow.config import RunConfig  # noqa: E402
from reagent_workflow.ingest import InputBundle  # noqa: E402
from reagent_workflow.store import canonical_json, content_hash  # noqa: E402
from reagent_workflow.structure import build_requests  # noqa: E402

FIXTURE_DIR = REPO_ROOT / "src" / "reagent_workflow" / "fixtures"
CACHE_DIR = FIXTURE_DIR / "structure_cache"

# Synthetic confidence values, chosen to exercise a specific comparison outcome:
# a plausible complex prediction paired with one partner that is only weakly
# ordered in isolation, so the comparison reports a real disagreement rather
# than a uniformly confident and uninformative "consistent".
CONFIDENCE = {
    "boltz2": {"plddt": 0.81, "ptm": 0.74, "iptm": 0.66, "avg_pae": 8.4},
    "esmfold2:transcription_factor": {"plddt": 0.62, "ptm": 0.58, "avg_pae": 12.1},
    "esmfold2:mediator_subunit": {"plddt": 0.88, "ptm": 0.83, "avg_pae": 5.7},
}

UNRESOLVED = {
    "boltz2": [
        "The interface is a prediction and has no experimental support.",
        "Interface residue identity is not resolved well enough to define a pocket.",
    ],
    "esmfold2": [
        "Monomer confidence does not speak to the complex interface.",
    ],
}


def main() -> int:
    bundle = InputBundle.model_validate(
        json.loads((FIXTURE_DIR / "candidates.fixture.json").read_text(encoding="utf-8"))
    )
    config = RunConfig()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    written = []
    for candidate in bundle.candidates:
        for request in build_requests(candidate, config):
            if request.model == "boltz2":
                key = "boltz2"
            else:
                role = next(chain.role for chain in request.chains)
                key = f"esmfold2:{role}"

            payload = {
                "fixture": True,
                "note": (
                    "SYNTHETIC cached structural output for the workflow demo. "
                    "Not a real prediction and not experimental evidence."
                ),
                "model": request.model,
                "model_version": f"{request.model}-fixture",
                "proto_tools_version": "fixture",
                "confidence": CONFIDENCE[key],
                "runtime_ms": 0,
                "unresolved_questions": UNRESOLVED[request.model],
                "limitations": [
                    "Computational prediction, not experimental evidence.",
                    "Cached demo output; no Modal job was run.",
                ],
            }
            payload["output_hash"] = content_hash(payload)
            path = CACHE_DIR / f"{request.input_hash}.json"
            path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
            written.append((request.request_id, path.name))

    for request_id, name in written:
        print(f"{request_id} -> {name}")
    if not written:
        print("no structural requests could be built from the fixture", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
