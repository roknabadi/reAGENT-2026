#!/usr/bin/env bash
set -euo pipefail

VERIFIER_DIR="${BENCHFLOW_VERIFIER_DIR:-/verifier}"
python3 "$VERIFIER_DIR/score.py"
