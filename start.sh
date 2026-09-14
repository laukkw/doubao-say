#!/bin/bash
set -e

PROJECT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export PYTHONPATH="$PROJECT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
# Runtime bytecode writes inside a watched plugin trigger unnecessary reloads.
export PYTHONDONTWRITEBYTECODE=1
if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
elif [[ -x "${XDG_DATA_HOME:-$HOME/.local/share}/doubao-say/runtime/bin/python" ]]; then
  PYTHON_BIN="${XDG_DATA_HOME:-$HOME/.local/share}/doubao-say/runtime/bin/python"
else
  PYTHON_BIN=$(command -v python3)
fi
exec "$PYTHON_BIN" -m doubao_input "$@"
