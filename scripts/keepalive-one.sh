#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib.sh"

INDEX="${1:?usage: keepalive-one.sh <mailbox-index>}"
require_index "$INDEX"

if [[ ! -f "$MAILBOX" ]]; then
  echo "[error] mailbox txt missing: $MAILBOX" >&2
  exit 2
fi

SID="$(cliproxy_sid "$INDEX")"
OUT="$(account_json "$INDEX")"
LOG="$LOG_DIR/keepalive-$(printf '%03d' "$INDEX").log"

if [[ ! -f "$OUT" ]]; then
  echo "[error] account file missing for index=$INDEX" >&2
  exit 2
fi

args=(
  -u "$REGISTER"
  --keepalive
  --mailbox "$MAILBOX"
  --index "$INDEX"
  --account "$OUT"
  --output "$OUT"
)
if cliproxy_ready; then
  args+=(--sid "$SID")
fi

{
  echo "[start] keepalive index=$INDEX sid=$SID $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  "$PYTHON" "${args[@]}"
  echo "[ok] keepalive index=$INDEX"
} >>"$LOG" 2>&1

chmod 600 "$OUT"
echo "[done] keepalive index=$INDEX"
