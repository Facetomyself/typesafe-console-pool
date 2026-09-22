#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib.sh"

TARGET="${TARGET:-5}"
KEEPALIVE_SECONDS="${KEEPALIVE_SECONDS:-14400}"
MAX_INDEX="${MAX_INDEX:-200}"
SLEEP_BETWEEN="${SLEEP_BETWEEN:-0}"
CONCURRENCY="${CONCURRENCY:-20}"
INFLIGHT_LIST="$DATA_DIR/inflight.list"
: > "$INFLIGHT_LIST"
chmod 600 "$INFLIGHT_LIST" 2>/dev/null || true

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
  grep -qx "$index" "$INFLIGHT_LIST" 2>/dev/null && return 0
  return 1
}

claim_next_index() {
  local lock="$DATA_DIR/pool.lock"
  (
    flock 9
    local i
    for ((i=1; i<=MAX_INDEX; i++)); do
      if is_used "$i"; then
        continue
      fi
      append_unique "$INFLIGHT_LIST" "$i"
      echo "$i"
      exit 0
    done
    exit 1
  ) 9>"$lock"
}

reap_one() {
  local pid="$1"
  local index="$2"
  local rc=0
  wait "$pid" || rc=$?
  remove_value "$INFLIGHT_LIST" "$index"
  if [[ "$rc" -eq 0 ]]; then
    echo "[pool] live=$(live_count)/$TARGET index=$index"
  else
    append_unique "$FAILED_LIST" "$index"
    echo "[pool] failed index=$index live=$(live_count)/$TARGET"
  fi
}

echo "[pool] start target=$TARGET concurrency=$CONCURRENCY root=$ROOT $(date -u +%Y-%m-%dT%H:%M:%SZ)"

declare -A WORKERS=()
trap 'for pid in "${!WORKERS[@]}"; do kill "$pid" 2>/dev/null || true; done; wait || true' EXIT INT TERM
while [[ "$(live_count)" -lt "$TARGET" || ${#WORKERS[@]} -gt 0 ]]; do
  for pid in "${!WORKERS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      continue
    fi
    reap_one "$pid" "${WORKERS[$pid]}"
    unset "WORKERS[$pid]"
  done
  if [[ "$(live_count)" -ge "$TARGET" ]]; then
    if [[ ${#WORKERS[@]} -eq 0 ]]; then
      break
    fi
    sleep 1
    continue
  fi
  if [[ ${#WORKERS[@]} -ge "$CONCURRENCY" ]]; then
    sleep 1
    continue
  fi
  found="$(claim_next_index || true)"
  if [[ -z "$found" ]]; then
    if [[ ${#WORKERS[@]} -eq 0 ]]; then
      echo "[error] no unused mailbox index left; live=$(live_count)" >&2
      exit 2
    fi
    sleep 1
    continue
  fi
  echo "[pool] register index=$found live=$(live_count)/$TARGET inflight=${#WORKERS[@]}"
  "$SCRIPT_DIR/register-one.sh" "$found" &
  WORKERS[$!]="$found"
  if [[ "$SLEEP_BETWEEN" -gt 0 ]]; then
    sleep "$SLEEP_BETWEEN"
  fi
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
