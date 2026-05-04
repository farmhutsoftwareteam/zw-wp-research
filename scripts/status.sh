#!/usr/bin/env bash
# Quick health snapshot of the pipeline.
# Shows: per-stage record counts, last modified time, top WP sites so far,
# pipeline supervisor PID (if running), latest log tail.

set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mtime() {
  if [[ ! -e "$1" ]]; then echo "—"; return; fi
  if stat -f "%Sm" -t "%Y-%m-%d %H:%M" "$1" 2>/dev/null; then return; fi
  stat -c "%y" "$1" 2>/dev/null | cut -c1-16
}

bold() { printf "\033[1m%s\033[0m\n" "$*"; }

bold "=== pipeline supervisor ==="
if [[ -f .pipeline.pid ]] && kill -0 "$(cat .pipeline.pid)" 2>/dev/null; then
  printf "RUNNING  pid=%s  log=%s\n" "$(cat .pipeline.pid)" "$(readlink logs/pipeline-latest.log 2>/dev/null || echo none)"
else
  printf "not running\n"
fi
echo

bold "=== JSONL stages ==="
for f in data/seeds.jsonl data/live.jsonl data/detections.jsonl data/panels.jsonl data/enriched.jsonl data/classified.jsonl data/verified.jsonl; do
  if [[ -e "$f" ]]; then
    n=$(wc -l < "$f")
    printf "%-28s  %7d records  modified %s\n" "$f" "$n" "$(mtime "$f")"
  else
    printf "%-28s  %7s\n" "$f" "absent"
  fi
done
echo

bold "=== derived artifacts ==="
for f in reports/report.md reports/top_zw_wordpress.csv reports/zwwp.db reports/site/index.html; do
  if [[ -e "$f" ]]; then
    sz=$(du -h "$f" | cut -f1)
    printf "%-32s  %6s  modified %s\n" "$f" "$sz" "$(mtime "$f")"
  else
    printf "%-32s  %6s\n" "$f" "absent"
  fi
done

if [[ -d reports/screenshots ]]; then
  shots=$(find reports/screenshots -name '*.png' | wc -l | tr -d ' ')
  printf "%-32s  %6d  screenshots\n" "reports/screenshots/" "$shots"
fi
echo

bold "=== WP detection summary ==="
if [[ -e data/detections.jsonl ]]; then
  .venv/bin/python -c "
import sys, orjson
buckets = {'0':0, '1-29':0, '30-69':0, '70-89':0, '90-100':0}
total = 0
high = []
for line in open('data/detections.jsonl', 'rb'):
    line=line.strip()
    if not line: continue
    try: r=orjson.loads(line)
    except Exception: continue
    s = r.get('score') or 0
    total += 1
    if s == 0: buckets['0'] += 1
    elif s < 30: buckets['1-29'] += 1
    elif s < 70: buckets['30-69'] += 1
    elif s < 90: buckets['70-89'] += 1
    else: buckets['90-100'] += 1
    if s >= 70:
        high.append((s, r.get('domain')))
print(f'  total examined: {total}')
print(f'  score buckets: {buckets}')
print(f'  WP-positive (score >= 70): {len([h for h in high if h[0] >= 70])}')
high.sort(reverse=True)
for s, d in high[:8]:
    print(f'    {s:3d}  {d}')
" 2>/dev/null || echo "  (could not parse)"
else
  printf "  no detections yet\n"
fi
echo

bold "=== host panel breakdown ==="
if [[ -e data/panels.jsonl ]]; then
  .venv/bin/python -c "
import orjson
from collections import Counter
c = Counter()
for line in open('data/panels.jsonl', 'rb'):
    line=line.strip()
    if not line: continue
    try: r=orjson.loads(line)
    except Exception: continue
    c[r.get('host_panel') or 'none/unknown'] += 1
for k, v in c.most_common():
    print(f'  {k:18s} {v:5d}')
" 2>/dev/null || echo "  (could not parse)"
else
  printf "  no panels yet\n"
fi
echo

bold "=== latest log (tail) ==="
LOG=$(readlink logs/pipeline-latest.log 2>/dev/null)
if [[ -n "$LOG" && -e "logs/$LOG" ]]; then
  tail -8 "logs/$LOG"
else
  printf "  no log\n"
fi
