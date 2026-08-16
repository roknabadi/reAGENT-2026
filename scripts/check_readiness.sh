#!/usr/bin/env bash
set -u

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$ROOT/bin:$ROOT/.venv/bin:$PATH"
export PROTO_HOME="$ROOT/.proto"
export PROTO_MODEL_CACHE="$ROOT/.proto/models"
export MPLCONFIGDIR="$ROOT/.cache/matplotlib"
export XDG_CACHE_HOME="$ROOT/.cache"
export HF_HOME="$ROOT/.cache/huggingface"
export PAPERCLIP_CONFIG_DIR="$ROOT/.paperclip"
# Only override Modal's config location if this repo actually has one --
# working credentials usually live in ~/.modal.toml, and pointing at a
# nonexistent file here would break Modal auth.
if [[ -f "$ROOT/.modal.toml" ]]; then
  export MODAL_CONFIG_PATH="$ROOT/.modal.toml"
fi

mkdir -p "$PROTO_HOME" "$PROTO_MODEL_CACHE" "$MPLCONFIGDIR" "$HF_HOME" "$PAPERCLIP_CONFIG_DIR"

failed=0
check() {
  label="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    printf 'OK   %s\n' "$label"
  else
    printf 'FAIL %s\n' "$label"
    failed=1
  fi
}

# Gitignored, machine-local checkouts (vendor/, apps/): missing is normal on a
# fresh clone, not a readiness failure.
check_optional() {
  label="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    printf 'OK   %s\n' "$label"
  else
    printf 'SKIP %s (optional, gitignored)\n' "$label"
  fi
}

check "Python >= 3.12" python -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'
check "Proto imports" python -c 'import proto_language, proto_tools'
check "PARADE constraints registered" python -c 'from proto_language import parade_utr_activity_constraint, parade_utr_specificity_constraint, parade_utr_stability_constraint'
check "Modal CLI" modal --version
check "Paperclip CLI" paperclip --version
check "BenchFlow CLI" benchflow --version
check "Claude Code" claude --version
check_optional "Proto source" git -C "$ROOT/vendor/proto-language" rev-parse HEAD
check_optional "BenchFlow source" git -C "$ROOT/vendor/benchflow" rev-parse HEAD
check_optional "Sundial app" test -d "$ROOT/apps/Sundial.app"
check "Paperclip skill for Claude" test -f "$ROOT/.claude/skills/paperclip/SKILL.md"
check "Paperclip skill for Codex" test -f "$ROOT/.agents/skills/paperclip/SKILL.md"

echo
echo "Interactive checks still required:"
echo "  paperclip login"
echo "  modal setup"
echo "  Biohub API key: https://biohub.ai"
echo "  Benchling tenant: https://hackathon.bnchdev.org"
echo "  Claude MCP OAuth: run claude, then /mcp"

exit "$failed"
