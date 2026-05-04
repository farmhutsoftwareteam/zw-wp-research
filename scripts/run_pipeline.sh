#!/usr/bin/env bash
# Long-running supervisor for the zw-wp-research pipeline.
#
#   - Runs each stage in order (01 -> 09).
#   - Every JSONL stage is idempotent: re-running skips records already done.
#   - On stage failure, retries with exponential backoff (30s, 60s, 120s, ...
#     capped at 30min). Caps at $MAX_RETRIES_PER_STAGE attempts before aborting.
#   - Logs both timestamps and full stage stdout/stderr to logs/pipeline-<ts>.log.
#   - Sends a macOS notification on every stage transition.
#   - Safe to Ctrl-C — JSONL files are written incrementally; just re-run.
#
# Usage (foreground, lid-safe):
#   make run
# Usage (background, survives terminal close):
#   make run-bg
#   make tail   # follow log
#   make status # snapshot
#   make stop   # kill the bg run

set -uo pipefail

PY="${PY:-.venv/bin/python}"
MAX_RETRIES_PER_STAGE="${MAX_RETRIES_PER_STAGE:-10}"
INITIAL_BACKOFF="${INITIAL_BACKOFF:-30}"
MAX_BACKOFF="${MAX_BACKOFF:-1800}"   # 30 min

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p logs
LOG_FILE="logs/pipeline-$(date +%Y%m%d-%H%M%S).log"
ln -snf "$(basename "$LOG_FILE")" logs/pipeline-latest.log

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG_FILE"
}

notify() {
  # macOS desktop notification — best-effort, never fails the pipeline.
  local title="zw-wp-research"
  local subtitle="${1:-stage}"
  local message="${2:-}"
  osascript -e "display notification \"${message//\"/\\\"}\" with title \"$title\" subtitle \"${subtitle//\"/\\\"}\"" >/dev/null 2>&1 || true
}

run_stage() {
  local name="$1"; shift
  local cmd=("$@")
  local attempt=1
  local backoff=$INITIAL_BACKOFF
  while (( attempt <= MAX_RETRIES_PER_STAGE )); do
    log ">>> stage [$name] attempt $attempt: ${cmd[*]}"
    # Stream stage output into the log too.
    if "${cmd[@]}" 2>&1 | tee -a "$LOG_FILE"; then
      log "<<< stage [$name] OK"
      notify "$name" "ok"
      return 0
    fi
    log "!!! stage [$name] attempt $attempt failed; sleeping ${backoff}s"
    notify "$name" "attempt $attempt failed; retrying"
    sleep "$backoff"
    backoff=$(( backoff * 2 ))
    (( backoff > MAX_BACKOFF )) && backoff=$MAX_BACKOFF
    attempt=$(( attempt + 1 ))
  done
  log "XXX stage [$name] EXHAUSTED ${MAX_RETRIES_PER_STAGE} retries — aborting"
  notify "$name" "ABORT after ${MAX_RETRIES_PER_STAGE} tries"
  return 1
}

trap 'log "received signal — exiting (idempotent: re-run to resume)"; exit 130' INT TERM

log "============================================================"
log "pipeline supervisor starting"
log "log file: $LOG_FILE"
log "python:   $PY"
log "============================================================"
notify "pipeline" "starting"

run_stage "01-seeds"     "$PY" scripts/01_seed_harvester.py            || exit 1
run_stage "02-dns"       "$PY" scripts/02_dns_resolver.py              || exit 1
run_stage "03-detect"    "$PY" scripts/03_wp_detector.py               || exit 1
run_stage "03b-panels"   "$PY" scripts/03b_panel_fingerprinter.py      || exit 1
run_stage "04-enrich"    "$PY" scripts/04_traffic_enricher.py          || exit 1
run_stage "05-classify"  "$PY" scripts/05_categorizer.py               || exit 1
run_stage "06-verify"    "$PY" scripts/06_verifier.py --top-n 200      || exit 1
run_stage "07-report"    "$PY" scripts/07_reporter.py                  || exit 1
run_stage "08-index"     "$PY" scripts/08_indexer.py                   || exit 1
run_stage "09-site"      "$PY" scripts/09_site_builder.py              || exit 1

log "============================================================"
log "PIPELINE COMPLETE"
log "============================================================"
notify "pipeline" "complete — open http://localhost:8000 after make serve"
