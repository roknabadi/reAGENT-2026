#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if command -v uv >/dev/null 2>&1; then
  UV_COMMAND="uv"
elif [[ -x "$PROJECT_ROOT/bin/uv" ]]; then
  UV_COMMAND="$PROJECT_ROOT/bin/uv"
else
  echo "uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi

if [[ ! -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
  "$UV_COMMAND" venv --python 3.12 "$PROJECT_ROOT/.venv"
fi

"$UV_COMMAND" pip install --python "$PROJECT_ROOT/.venv/bin/python" -e "$PROJECT_ROOT"

if [[ ! -f "$PROJECT_ROOT/.env" ]]; then
  cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
  echo "Created .env; add your personal TAMARIND_API_KEY."
fi

"$PROJECT_ROOT/.venv/bin/python" -m unittest discover -s tests -v
echo "Setup complete. Run: source ./activate.sh"
