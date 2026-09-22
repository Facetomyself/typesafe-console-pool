#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib.sh"

TARGET="${TARGET:-5}"
KEEPALIVE_SECONDS="${KEEPALIVE_SECONDS:-14400}"
MAX_INDEX="${MAX_INDEX:-200}"
SLEEP_BETWEEN="${SLEEP_BETWEEN:-12}"

live_count() {
  if [[ ! -s "$LIVE_LIST" ]]; then
    echo 0
    return
  fi
  sort -n -u "$LIVE_LIST" | wc -l
}

is_used() {
  local index="$1"
  grep -qx "$index" "$LIVE_LIST" 2>/dev/null && return 0
  grep -qx "$index" "$FAILED_LIST" 2>/dev/null && return 0
  return 1
}

echo "[pool] start target=$TARGET root=$ROOT $(date -u +%Y-%m-%dT%H:%M:%SZ)"

while [[ "$(live_count)" -lt "$TARGET" ]]; do
  found=""
  for ((i=1; i<=MAX_INDEX; i++)); do
    if is_used "$i"; then
      continue
    fi
    found="$i"
    break
  done
  if [[ -z "$found" ]]; then
    echo "[error] no unused mailbox index left; live=$(live_count)" >&2
    exit 2
  fi
  echo "[pool] register index=$found live=$(live_count)/$TARGET"
  if "$SCRIPT_DIR/register-one.sh" "$found"; then
    echo "[pool] live=$(live_count)/$TARGET"
  else
    append_unique "$FAILED_LIST" "$found"
    echo "[pool] failed index=$found live=$(live_count)/$TARGET"
  fi
  sleep "$SLEEP_BETWEEN"
done

echo "[pool] registered $(live_count) accounts; entering keepalive loop"

while true; do
  mapfile -t indexes < <(sort -n -u "$LIVE_LIST")
  kept=0
  for index in "${indexes[@]}"; do
    echo "[pool] keepalive index=$index"
    if "$SCRIPT_DIR/keepalive-one.sh" "$index"; then
      kept=$((kept + 1))
    else
      echo "[pool] keepalive failed index=$index"
    fi
    sleep 5
  done
  echo "[pool] keepalive pass kept=$kept/$(live_count) $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [[ "$KEEPALIVE_SECONDS" -le 0 ]]; then
    break
  fi
  sleep "$KEEPALIVE_SECONDS"
done
