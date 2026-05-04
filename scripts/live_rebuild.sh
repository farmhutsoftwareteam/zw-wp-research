#!/usr/bin/env bash
# Live site rebuilder.
# Every $INTERVAL seconds:
#   1. If any data/*.jsonl has changed since last build, run stage 08 + 09.
#   2. Otherwise, sleep.
# The site at reports/site/ has <meta http-equiv="refresh" content="30">,
# so a browser tab on http://localhost:8000 picks up changes automatically.
#
# Usage:
#   make live          # foreground
#   make live-bg       # background (writes .live.pid)

set -uo pipefail

PY="${PY:-.venv/bin/python}"
INTERVAL="${LIVE_INTERVAL:-30}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p logs
LOG="logs/live-rebuild.log"

log() {
  printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*" | tee -a "$LOG"
}

last_sig=""
trap 'log "live-rebuild stopping"; exit 0' INT TERM

log "live-rebuild starting (interval ${INTERVAL}s)"
while true; do
  # Signature = total line counts across pipeline files.
  sig=$(for f in data/seeds.jsonl data/live.jsonl data/detections.jsonl \
                data/panels.jsonl data/enriched.jsonl data/classified.jsonl \
                data/verified.jsonl; do
          [[ -e "$f" ]] && wc -l < "$f" || echo 0
        done | tr '\n' '-')
  if [[ "$sig" != "$last_sig" ]]; then
    log "data changed ($sig); rebuilding 08 + 09"
    if "$PY" scripts/08_indexer.py >> "$LOG" 2>&1 && \
       "$PY" scripts/09_site_builder.py >> "$LOG" 2>&1; then
      log "rebuild OK"
    else
      log "rebuild FAILED — will retry in ${INTERVAL}s"
    fi
    last_sig="$sig"
  fi
  sleep "$INTERVAL"
done
