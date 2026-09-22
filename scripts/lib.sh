#!/usr/bin/env bash
# Shared paths for Linux register / keepalive / pool scripts.
# Source from scripts/*.sh. Override with TYPESAFE_CONSOLE_ROOT, TYPESAFE_PYTHON,
# TYPESAFE_PROTOCOL_ROOT, TYPESAFE_MAILBOX, TYPESAFE_DATA_DIR, TYPESAFE_LOG_DIR, TYPESAFE_ENV.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="${TYPESAFE_CONSOLE_ROOT:-$ROOT}"

ENV_FILE="${TYPESAFE_ENV:-$ROOT/.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

if [[ -n "${TYPESAFE_PYTHON:-}" ]]; then
  PYTHON="$TYPESAFE_PYTHON"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  echo "[error] python3 not found; set TYPESAFE_PYTHON" >&2
  exit 2
fi

PROTOCOL_ROOT="${TYPESAFE_PROTOCOL_ROOT:-}"
if [[ -z "$PROTOCOL_ROOT" ]]; then
  if [[ -f "$ROOT/../typesafe-console-protocol/scripts/register.py" ]]; then
    PROTOCOL_ROOT="$(cd "$ROOT/../typesafe-console-protocol" && pwd)"
  elif [[ -f "$ROOT/app/register.py" ]]; then
    PROTOCOL_ROOT="$ROOT"
  fi
fi

if [[ -n "$PROTOCOL_ROOT" && -f "$PROTOCOL_ROOT/scripts/register.py" ]]; then
  REGISTER="$PROTOCOL_ROOT/scripts/register.py"
elif [[ -n "$PROTOCOL_ROOT" && -f "$PROTOCOL_ROOT/app/register.py" ]]; then
  REGISTER="$PROTOCOL_ROOT/app/register.py"
elif [[ -f "$ROOT/app/register.py" ]]; then
  REGISTER="$ROOT/app/register.py"
else
  echo "[error] typesafe-console-protocol register.py not found; set TYPESAFE_PROTOCOL_ROOT" >&2
  exit 2
fi

DATA_DIR="${TYPESAFE_DATA_DIR:-$ROOT/data}"
LOG_DIR="${TYPESAFE_LOG_DIR:-$ROOT/logs}"
ACCOUNTS_DIR="$DATA_DIR/accounts"
LIVE_LIST="$DATA_DIR/live.list"
FAILED_LIST="$DATA_DIR/failed.list"

if [[ -n "${TYPESAFE_MAILBOX:-}" ]]; then
  MAILBOX="$TYPESAFE_MAILBOX"
elif [[ -f "$DATA_DIR/mailbox.txt" ]]; then
  MAILBOX="$DATA_DIR/mailbox.txt"
else
  MAILBOX="$ROOT/mailbox.txt"
fi

mkdir -p "$ACCOUNTS_DIR" "$LOG_DIR"
touch "$LIVE_LIST" "$FAILED_LIST"
chmod 600 "$LIVE_LIST" "$FAILED_LIST" 2>/dev/null || true

CLIPROXY_USER="${CLIPROXY_USER:-${CLIPROXY_ACCOUNT:-}}"
REGION="${CLIPROXY_REGION:-US}"
STICKY_MINUTES="${CLIPROXY_STICKY_MINUTES:-30}"

export PYTHONUNBUFFERED=1
export CLIPROXY_USER CLIPROXY_PASS CLIPROXY_HOST CLIPROXY_PORT
export CLIPROXY_REGION="$REGION"
export CLIPROXY_STICKY_MINUTES="$STICKY_MINUTES"

require_index() {
  local index="${1:-}"
  if ! [[ "$index" =~ ^[1-9][0-9]*$ ]]; then
    echo "[error] mailbox index must be a positive integer" >&2
    exit 2
  fi
}

account_json() {
  local index="$1"
  printf '%s/acc-%03d.json' "$ACCOUNTS_DIR" "$index"
}

cliproxy_sid() {
  local index="$1"
  printf 'ts5_w0_i%s' "$index"
}

cliproxy_ready() {
  [[ -n "${CLIPROXY_USER:-}" && -n "${CLIPROXY_PASS:-}" && -n "${CLIPROXY_HOST:-}" && -n "${CLIPROXY_PORT:-}" ]]
}

append_unique() {
  local file="$1"
  local value="$2"
  if grep -qx "$value" "$file" 2>/dev/null; then
    return 0
  fi
  echo "$value" >>"$file"
  chmod 600 "$file" 2>/dev/null || true
}
