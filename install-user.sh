#!/bin/bash
# Register this checkout as a desktop application. Keep its directory in place.
set -euo pipefail
PROJECT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export PYTHONPATH="$PROJECT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
else
  PYTHON_BIN=$(command -v python3)
fi
"$PYTHON_BIN" -c 'from doubao_input.settings import install_desktop; print("Installed launcher:", install_desktop())'
printf '%s\n' 'Open Doubao Say from your launcher, then Settings to configure keys and startup.' \
  'This installer does not change compositor bindings, install local models, or enable autostart.'
