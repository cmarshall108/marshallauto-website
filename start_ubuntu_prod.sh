#!/usr/bin/env bash
# Production launcher for Ubuntu. Loads .env from the app root, then runs the web
# process (and optionally the photo-highlight worker).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# Prefer / create project venv so worker deps (OpenCV) install cleanly
# A venv built with --system-site-packages is rejected: pip then tries to
# uninstall apt-managed packages (e.g. python3-werkzeug) and fails with
# "Cannot uninstall Werkzeug ... RECORD file not found".
is_isolated_venv() {
  local py="$1"
  [[ -x "$py" ]] || return 1
  "$py" - <<'PY' >/dev/null 2>&1
import pathlib, sys
if sys.prefix == sys.base_prefix:
    raise SystemExit(1)
cfg = pathlib.Path(sys.prefix, "pyvenv.cfg")
for line in cfg.read_text().splitlines() if cfg.is_file() else []:
    key, _, value = line.partition("=")
    if key.strip() == "include-system-site-packages" and value.strip().lower() == "true":
        raise SystemExit(1)
PY
}

create_venv() {
  command -v python3 >/dev/null 2>&1 || return 1
  echo "Creating isolated Python venv at $ROOT/venv ..."
  if ! python3 -m venv "$ROOT/venv" 2>/dev/null; then
    echo "ERROR: python3 -m venv failed. Install it with:" >&2
    echo "  apt-get install -y python3-venv python3-pip" >&2
    return 1
  fi
  "$ROOT/venv/bin/python" -m pip install --upgrade pip setuptools wheel >/dev/null
}

ensure_python() {
  local candidate
  for candidate in "$ROOT/venv/bin/python" "$ROOT/.venv/bin/python"; do
    if is_isolated_venv "$candidate"; then
      PYTHON="$candidate"
      return 0
    fi
    if [[ -x "$candidate" ]]; then
      echo "WARNING: $candidate is not an isolated venv (system site-packages)." >&2
      echo "         Recreate it:  rm -rf $(dirname "$(dirname "$candidate")") && python3 -m venv venv" >&2
    fi
  done
  if create_venv && is_isolated_venv "$ROOT/venv/bin/python"; then
    PYTHON="$ROOT/venv/bin/python"
    return 0
  fi
  echo "ERROR: no isolated virtualenv available. Refusing to pip install into the" >&2
  echo "       system Python (Debian/Ubuntu packages cannot be uninstalled by pip)." >&2
  exit 1
}
ensure_python
echo "Using Python: $PYTHON ($("$PYTHON" -c 'import sys; print(sys.version.split()[0])'))"

# pip install that survives leftover distro-managed packages
pip_install() {
  if "$PYTHON" -m pip install "$@"; then
    return 0
  fi
  echo "pip install failed; retrying with --ignore-installed ..." >&2
  "$PYTHON" -m pip install --ignore-installed "$@"
}

# Ensure photo-highlight deps (OpenCV + numpy) are importable in THIS interpreter
ensure_highlight_deps() {
  if "$PYTHON" - <<'PY' >/dev/null 2>&1
import cv2  # noqa: F401
import numpy  # noqa: F401
PY
  then
    echo "Photo highlight deps OK (opencv + numpy)"
    return 0
  fi

  echo "Installing photo highlight deps (opencv-python-headless, numpy) ..."
  # Prefer full requirements when present so versions stay pinned together
  if [[ -f "$ROOT/requirements.txt" ]]; then
    if ! pip_install -r "$ROOT/requirements.txt"; then
      echo "Full requirements install failed; trying highlight packages only..." >&2
      pip_install "numpy>=1.26,<3" "opencv-python-headless>=4.8,<5"
    fi
  else
    pip_install "numpy>=1.26,<3" "opencv-python-headless>=4.8,<5"
  fi

  if ! "$PYTHON" - <<'PY'
import cv2
import numpy
print(f"opencv {cv2.__version__}, numpy {numpy.__version__}")
PY
  then
    echo "ERROR: OpenCV/numpy still missing after install." >&2
    echo "  Tried interpreter: $PYTHON" >&2
    echo "  Fix: $PYTHON -m pip install opencv-python-headless numpy" >&2
    exit 1
  fi
}
ensure_highlight_deps

# Load .env into this shell (export KEY=VALUE lines; ignore comments/blank)
load_dotenv_file() {
  local env_file="$1" line key value
  [[ -f "$env_file" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    # skip blanks and comments
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    # only simple KEY=VALUE assignments
    [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
    key="${line%%=*}"
    value="${line#*=}"
    # strip optional surrounding single/double quotes
    if [[ "$value" =~ ^\".*\"$ ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "$value" =~ ^\'.*\'$ ]]; then
      value="${value:1:${#value}-2}"
    fi
    export "$key=$value"
  done < "$env_file"
}

ENV_FILE="$ROOT/.env"

if [[ -f "$ENV_FILE" ]]; then
  load_dotenv_file "$ENV_FILE"
  echo "Loaded environment from $ENV_FILE"
else
  echo "No .env found at $ENV_FILE — will create one if secrets are generated."
fi

# --- SECRET_KEY: generate on startup if missing/weak, persist to .env ---
generate_secret_key() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
    return 0
  fi
  if command -v python3 >/dev/null 2>&1 || [[ -n "${PYTHON:-}" ]]; then
    local py="${PYTHON:-python3}"
    "$py" - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
    return 0
  fi
  # Last resort (weaker): /dev/urandom
  if [[ -r /dev/urandom ]]; then
    od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
    echo
    return 0
  fi
  echo "ERROR: cannot generate SECRET_KEY (need openssl, python3, or /dev/urandom)." >&2
  exit 1
}

is_weak_secret() {
  local secret="${1:-}"
  case "$secret" in
    ''|'dev-secret-key-change-in-production'|'change-me-to-a-random-32-char-string')
      return 0
      ;;
  esac
  [[ ${#secret} -lt 16 ]]
}

upsert_env_var() {
  # upsert_env_var KEY VALUE  — write/replace KEY=VALUE in .env
  local key="$1" value="$2" tmp
  touch "$ENV_FILE"
  chmod 600 "$ENV_FILE" 2>/dev/null || true
  tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
  # drop existing KEY= lines, then append
  if [[ -s "$ENV_FILE" ]]; then
    grep -v -E "^${key}=" "$ENV_FILE" > "$tmp" || true
  else
    : > "$tmp"
  fi
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  mv "$tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE" 2>/dev/null || true
}

if is_weak_secret "${SECRET_KEY:-}"; then
  SECRET_KEY="$(generate_secret_key | tr -d '\r\n')"
  export SECRET_KEY
  upsert_env_var "SECRET_KEY" "$SECRET_KEY"
  echo "Generated and saved SECRET_KEY to $ENV_FILE"
else
  export SECRET_KEY
fi

export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-80}"
export FLASK_ENV=production
export FLASK_DEBUG=0
# Ensure python-dotenv / app also see the project .env when cwd differs
export DOTENV_PATH="${DOTENV_PATH:-$ENV_FILE}"

# ADMIN_PASSWORD still must be set explicitly (do not auto-generate logins)
require_strong_admin_password() {
  local admin_pw="${ADMIN_PASSWORD:-}"
  case "$admin_pw" in
    ''|'admin'|'change-me-strong-password'|'password')
      echo "ERROR: Refusing to start: set a strong ADMIN_PASSWORD in $ENV_FILE" >&2
      echo "  Example:  ADMIN_PASSWORD=\$(openssl rand -base64 18)" >&2
      exit 1
      ;;
  esac
}
require_strong_admin_password

# Optional: start photo-highlight worker alongside web (set START_HIGHLIGHT_WORKER=0 to disable)
START_HIGHLIGHT_WORKER="${START_HIGHLIGHT_WORKER:-1}"
web_pid=
worker_pid=

cleanup() {
  if [[ -n "${worker_pid:-}" ]] && kill -0 "$worker_pid" 2>/dev/null; then
    kill "$worker_pid" 2>/dev/null || true
    wait "$worker_pid" 2>/dev/null || true
  fi
  if [[ -n "${web_pid:-}" ]] && kill -0 "$web_pid" 2>/dev/null; then
    kill "$web_pid" 2>/dev/null || true
    wait "$web_pid" 2>/dev/null || true
  fi
  echo "Shutting down."
  exit 0
}
trap cleanup SIGINT SIGTERM

start_worker() {
  if [[ "$START_HIGHLIGHT_WORKER" != "1" ]]; then
    return 0
  fi
  "$PYTHON" -m app.highlight_worker &
  worker_pid=$!
  echo "Highlight worker started (pid $worker_pid)"
}

while true; do
  # Restart worker if it died
  if [[ "$START_HIGHLIGHT_WORKER" == "1" ]]; then
    if [[ -z "${worker_pid:-}" ]] || ! kill -0 "$worker_pid" 2>/dev/null; then
      start_worker
    fi
  fi

  "$PYTHON" run.py &
  web_pid=$!
  wait "$web_pid"
  status=$?
  web_pid=

  # 130 = SIGINT (Ctrl-C), 143 = SIGTERM
  if [[ $status -eq 130 || $status -eq 143 ]]; then
    cleanup
  fi

  echo "run.py exited with status $status. Restarting in 2s..."
  sleep 2
done
