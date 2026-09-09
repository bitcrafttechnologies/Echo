#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="${PROJECT_ROOT}/.venv"

export CI=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_NO_INPUT=1

command -v python3 >/dev/null 2>&1 || {
  echo "Echo requires Python 3.11 or newer." >&2
  exit 1
}
command -v npm >/dev/null 2>&1 || {
  echo "Echo Web Console requires Node.js and npm." >&2
  exit 1
}

python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' || {
  echo "Echo requires Python 3.11 or newer." >&2
  exit 1
}

python3 -m venv "${VENV_PATH}"
"${VENV_PATH}/bin/python" -m pip install --upgrade pip
"${VENV_PATH}/bin/python" -m pip install "${PROJECT_ROOT}[test]" "uvicorn[standard]"

npm --prefix "${PROJECT_ROOT}/console" ci --no-audit --no-fund
npm --prefix "${PROJECT_ROOT}/console" run build

echo "Echo is installed. Configuration files were left unchanged."
echo "Run ${PROJECT_ROOT}/start.sh, optionally with --web or --terminal."
