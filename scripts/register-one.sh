#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib.sh"

INDEX="${1:?usage: register-one.sh <mailbox-index>}"
require_index "$INDEX"

if [[ ! -f "$MAILBOX" ]]; then
  echo "[error] mailbox txt missing: $MAILBOX" >&2
  exit 2
fi

SID="$(cliproxy_sid "$INDEX")"
OUT="$(account_json "$INDEX")"
LOG="$LOG_DIR/register-$(printf '%03d' "$INDEX").log"

args=(
  -u "$REGISTER"
  --mailbox "$MAILBOX"
  --index "$INDEX"
  --output "$OUT"
)
if cliproxy_ready; then
  args+=(--sid "$SID")
fi

{
  echo "[start] register index=$INDEX sid=$SID $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  "$PYTHON" "${args[@]}"
  echo "[ok] register index=$INDEX"
} >>"$LOG" 2>&1

chmod 600 "$OUT"
append_unique "$LIVE_LIST" "$INDEX"
echo "[done] index=$INDEX"
