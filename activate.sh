#!/usr/bin/env bash

REAGENT_ROOT="/Users/amir/Documents/reAGENT-2026"
export REAGENT_ROOT
export PATH="$REAGENT_ROOT/bin:$REAGENT_ROOT/.venv/bin:$PATH"
export VIRTUAL_ENV="$REAGENT_ROOT/.venv"
export PROTO_HOME="$REAGENT_ROOT/.proto"
export PROTO_MODEL_CACHE="$REAGENT_ROOT/.proto/models"
export MPLCONFIGDIR="$REAGENT_ROOT/.cache/matplotlib"
export XDG_CACHE_HOME="$REAGENT_ROOT/.cache"
export HF_HOME="$REAGENT_ROOT/.cache/huggingface"
export PAPERCLIP_CONFIG_DIR="$REAGENT_ROOT/.paperclip"
export MODAL_CONFIG_PATH="$REAGENT_ROOT/.modal.toml"
export UV_TOOL_DIR="$REAGENT_ROOT/tools"
export UV_TOOL_BIN_DIR="$REAGENT_ROOT/bin"

mkdir -p "$PROTO_HOME" "$PROTO_MODEL_CACHE" "$MPLCONFIGDIR" "$HF_HOME" "$PAPERCLIP_CONFIG_DIR"

if [ -f "$REAGENT_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$REAGENT_ROOT/.env"
  set +a
fi

echo "re:AGENT environment active: $REAGENT_ROOT"
echo "Python: $(python --version 2>&1)"
