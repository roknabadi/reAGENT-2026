#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

RUN_ID="${1:-}"
MATRIX="${2:-benchflow/matrix.yaml}"
TRIALS="${3:-3}"

if [[ -z "$RUN_ID" ]]; then
  echo "Usage: $0 RUN_ID [MATRIX_FILE] [TRIALS]"
  exit 2
fi
if [[ ! -f "$MATRIX" ]]; then
  echo "Matrix not found: $MATRIX (copy benchflow/matrix.example.yaml first)"
  exit 2
fi
if ! command -v bench >/dev/null 2>&1; then
  echo "BenchFlow is not installed. Run ./scripts/setup.sh"
  exit 1
fi

JOBS_DIR="benchflow/jobs/$RUN_ID"
ARTIFACT_DIR="benchflow/artifacts/$RUN_ID"
mkdir -p "$JOBS_DIR" "$ARTIFACT_DIR"

PUBLISH_ARGS=()
if [[ -n "${BENCHFLOW_HF_REPO:-}" ]]; then
  if [[ -z "${HF_TOKEN:-}" ]]; then
    echo "HF_TOKEN is required when BENCHFLOW_HF_REPO is set"
    exit 1
  fi
  PUBLISH_ARGS=(--publish-hf "$BENCHFLOW_HF_REPO" --hf-prefix "experiments/$RUN_ID")
fi

bench eval run \
  --tasks-dir benchflow/tasks/tf-mediator-hero \
  --matrix "$MATRIX" \
  --trials "$TRIALS" \
  --sandbox docker \
  --jobs-dir "$JOBS_DIR" \
  --task-manifest-out "$ARTIFACT_DIR/task-manifest.json" \
  --run-config-out "$ARTIFACT_DIR/run-config.json" \
  --health-summary-out "$ARTIFACT_DIR/health.json" \
  --canonicalize one-healthy-per-task \
  --canonical-selection-out "$ARTIFACT_DIR/canonical-selection.json" \
  "${PUBLISH_ARGS[@]}"

echo "Comparison summary: $JOBS_DIR/matrix-summary.json"
