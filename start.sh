#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"
ECHOC="${PROJECT_ROOT}/.venv/bin/echoc"
OPEN_WEB=false
OPEN_TERMINAL=false
NO_TMUX=false
LISTEN_HOST=""

usage() {
  cat <<'EOF'
Usage: ./start.sh [--web] [--terminal] [--no-tmux] [--host ADDRESS] [--config PATH]

Starts Echo Core using the existing echo.toml, ECHO_CONFIG, or validated
defaults. Configuration files are never created or modified.

  --web       Start the Svelte development Web Console with Echo Core.
  --terminal  Open the terminal Console with Echo Core.
  --no-tmux   Use the single-terminal Console (requires --terminal).
  --host      Bind Echo Core and the Web Console to an address such as 0.0.0.0.
  --config    Use an existing TOML file for Core and Console.
  --help      Show this help.

With no UI flag, only Echo Core and its management API are started. --web and
--terminal may be combined.
EOF
}

while (($#)); do
  case "$1" in
    --web)
      OPEN_WEB=true
      ;;
    --terminal)
      OPEN_TERMINAL=true
      ;;
    --no-tmux)
      NO_TMUX=true
      ;;
    --host)
      [[ $# -ge 2 ]] || {
        echo "--host requires an address." >&2
        exit 2
      }
      LISTEN_HOST="$2"
      shift
      ;;
    --config)
      [[ $# -ge 2 ]] || {
        echo "--config requires a path." >&2
        exit 2
      }
      export ECHO_CONFIG="$2"
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [[ "${NO_TMUX}" == true && "${OPEN_TERMINAL}" != true ]]; then
  echo "--no-tmux requires --terminal." >&2
  exit 2
fi
if [[ ! -x "${PYTHON}" || ! -x "${ECHOC}" ]]; then
  echo "Echo is not installed. Run ${PROJECT_ROOT}/install.sh first." >&2
  exit 1
fi
if [[ "${OPEN_WEB}" == true && ! -d "${PROJECT_ROOT}/console/node_modules" ]]; then
  echo "Echo Web Console is not installed. Run ${PROJECT_ROOT}/install.sh first." >&2
  exit 1
fi

cd "${PROJECT_ROOT}"
export ECHO_ENTITY_ROOT="${ECHO_ENTITY_ROOT:-${PROJECT_ROOT}/entities}"

# The project-local dotenv file is an optional secret overlay. It is ignored by
# git and loaded before Echo validates its TOML configuration. Explicit process
# environment values win over matching dotenv entries.
if [[ -f "${PROJECT_ROOT}/.env" ]]; then
  while IFS='=' read -r dotenv_name dotenv_value; do
    dotenv_name="${dotenv_name#export }"
    dotenv_name="${dotenv_name//[[:space:]]/}"
    [[ -n "${dotenv_name}" && "${dotenv_name}" != \#* ]] || continue
    [[ "${dotenv_name}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || {
      echo "Invalid variable name in ${PROJECT_ROOT}/.env: ${dotenv_name}" >&2
      exit 1
    }
    if [[ -z "${!dotenv_name+x}" ]]; then
      dotenv_value="${dotenv_value%$'\r'}"
      if [[ "${dotenv_value}" == \"*\" && "${dotenv_value}" == *\" ]]; then
        dotenv_value="${dotenv_value:1:${#dotenv_value}-2}"
      elif [[ "${dotenv_value}" == \'*\' && "${dotenv_value}" == *\' ]]; then
        dotenv_value="${dotenv_value:1:${#dotenv_value}-2}"
      fi
      export "${dotenv_name}=${dotenv_value}"
    fi
  done < "${PROJECT_ROOT}/.env"
fi

if [[ -n "${LISTEN_HOST}" ]]; then
  export ECHO_API_HOST="${LISTEN_HOST}"
fi

if [[ "${OPEN_WEB}" == true ]]; then
  export ECHO_API_PROXY_TARGET
  ECHO_API_PROXY_TARGET="$("${PYTHON}" -c 'from echo import load_config; c=load_config(); print(f"http://127.0.0.1:{c.api.port}" if c.api.host == "0.0.0.0" else c.api.base_url)')"
fi

if [[ "${OPEN_WEB}" != true && "${OPEN_TERMINAL}" != true ]]; then
  exec "${PYTHON}" -m echo.host
fi

core_pid=""
web_pid=""
cleanup() {
  if [[ -n "${web_pid}" ]]; then
    kill "${web_pid}" 2>/dev/null || true
    wait "${web_pid}" 2>/dev/null || true
  fi
  if [[ -n "${core_pid}" ]]; then
    kill "${core_pid}" 2>/dev/null || true
    wait "${core_pid}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

"${PYTHON}" -m echo.host &
core_pid=$!
sleep 1
if ! kill -0 "${core_pid}" 2>/dev/null; then
  wait "${core_pid}"
fi

if [[ "${OPEN_WEB}" == true && "${OPEN_TERMINAL}" == true ]]; then
  web_args=(--prefix "${PROJECT_ROOT}/console" run dev)
  [[ -z "${LISTEN_HOST}" ]] || web_args+=(-- --host "${LISTEN_HOST}")
  npm "${web_args[@]}" &
  web_pid=$!
elif [[ "${OPEN_WEB}" == true ]]; then
  web_args=(--prefix "${PROJECT_ROOT}/console" run dev)
  [[ -z "${LISTEN_HOST}" ]] || web_args+=(-- --host "${LISTEN_HOST}")
  npm "${web_args[@]}"
  exit $?
fi

terminal_args=(console)
if [[ "${NO_TMUX}" == true ]]; then
  terminal_args+=(--no-tmux)
fi
"${ECHOC}" "${terminal_args[@]}"
