# zw-wp-research pipeline
#
# Usage:
#   make all        # run the full pipeline (1 -> 9): seeds, dns, detect, enrich,
#                   #   classify, verify, report, index, site
#   make seeds      # just stage 01
#   make detect     # stages 01..03
#   make enrich     # stages 01..04
#   make classify   # stages 01..05
#   make verify     # stages 01..06
#   make report     # stages 01..07
#   make index      # stage 08 (build SQLite from JSONL)
#   make site       # stage 09 (build static site from SQLite)
#   make serve      # start local http server to browse the site
#   make clean      # wipe data/ and reports/ (keeps .gitkeep)
#
# Lid-safe variants (wraps with `caffeinate -i` so the laptop doesn't sleep):
#   make seeds-awake  detect-awake  enrich-awake  classify-awake
#   make verify-awake report-awake  index-awake   site-awake  all-awake

PY := python3
SCRIPTS := scripts
SITE_DIR := reports/site

.PHONY: all seeds dns detect panels enrich classify verify report index site serve clean help \
        detect-parallel

help:
	@echo "Targets: seeds dns detect enrich classify verify report index site serve all clean"
	@echo "Lid-safe: append -awake (e.g. all-awake) to wrap any target with caffeinate -i"

# --- Pipeline ---
seeds: data/seeds.jsonl
data/seeds.jsonl:
	$(PY) $(SCRIPTS)/01_seed_harvester.py

dns: data/live.jsonl
data/live.jsonl: data/seeds.jsonl
	$(PY) $(SCRIPTS)/02_dns_resolver.py

detect: data/detections.jsonl
data/detections.jsonl: data/live.jsonl
	$(PY) $(SCRIPTS)/03_wp_detector.py

# Parallel detection: 8 shards. Run with `make detect-parallel`.
detect-parallel: data/live.jsonl
	@for i in 0 1 2 3 4 5 6 7; do \
	  $(PY) $(SCRIPTS)/03_wp_detector.py --shard $$i/8 & \
	done; wait

panels: data/panels.jsonl
data/panels.jsonl: data/detections.jsonl
	$(PY) $(SCRIPTS)/03b_panel_fingerprinter.py

# --- Lead-gen enrichment (epic #10 — runs after the main pipeline lands) ---
schema:
	$(PY) $(SCRIPTS)/14_contacts_schema.py --backfill

leadgen-dns:
	$(PY) $(SCRIPTS)/15_dns_contacts.py

leadgen-wp:
	$(PY) $(SCRIPTS)/16_wp_authors.py

leadgen-whois:
	$(PY) $(SCRIPTS)/17_whois_enrich.py

leadgen-deep:
	$(PY) $(SCRIPTS)/18_deep_scrape.py

leadgen-pindula:
	$(PY) $(SCRIPTS)/19_pindula_enrich.py

leadgen-finder:
	$(PY) $(SCRIPTS)/20_finder_enrich.py

leadgen: schema leadgen-dns leadgen-wp leadgen-whois leadgen-deep leadgen-pindula leadgen-finder
	@echo "leadgen enrichment complete"

# Sales-enablement enrichment (epic #10 follow-on)
leadgen-fresh:
	$(PY) $(SCRIPTS)/23_freshness.py

leadgen-ssl:
	$(PY) $(SCRIPTS)/24_ssl_expiry.py

leadgen-wayback:
	$(PY) $(SCRIPTS)/26_wayback.py

leadgen-psi:
	$(PY) $(SCRIPTS)/27_pagespeed.py --top-n 200

leadgen-wpscan:
	$(PY) $(SCRIPTS)/28_wpscan.py

pitch-cards:
	$(PY) $(SCRIPTS)/29_pitch_cards.py --top-n 50

pitch-html: pitch-cards
	$(PY) $(SCRIPTS)/30_pitch_html.py

pitch-open:
	open reports/pitch_cards/index.md

pitch-serve: pitch-html
	@echo "Local pitch-card viewer at http://localhost:8002"
	$(PY) -m http.server 8002 -d reports/pitch_html/

# Build a self-contained engagement-kit zip for a colleague
share-bundle: pitch-html
	$(PY) $(SCRIPTS)/31_share_bundle.py
	@echo ""
	@echo "Upload the zip to Google Drive / Dropbox / WeTransfer; share the link with your colleague."
	@echo "They unzip, double-click OPEN_THIS_FIRST.html, and they have everything offline."

leadgen-sales: leadgen-fresh leadgen-ssl leadgen-wayback leadgen-psi leadgen-wpscan pitch-cards
	@echo "sales-enablement enrichment complete"
	@echo "open reports/pitch_cards/index.md to see the top 50 leads"

# review-serve already exists for the dashboard; agent-smoke is for ops
agent-smoke:
	$(PY) $(SCRIPTS)/21_agent_smoke.py --stats

enrich: data/enriched.jsonl
data/enriched.jsonl: data/detections.jsonl
	$(PY) $(SCRIPTS)/04_traffic_enricher.py

classify: data/classified.jsonl
data/classified.jsonl: data/enriched.jsonl
	$(PY) $(SCRIPTS)/05_categorizer.py

verify: data/verified.jsonl
data/verified.jsonl: data/classified.jsonl
	$(PY) $(SCRIPTS)/06_verifier.py

report: reports/report.md
reports/report.md: data/verified.jsonl
	$(PY) $(SCRIPTS)/07_reporter.py

index: reports/zwwp.db
reports/zwwp.db: reports/report.md data/panels.jsonl
	$(PY) $(SCRIPTS)/08_indexer.py

site: $(SITE_DIR)/index.html
$(SITE_DIR)/index.html: reports/zwwp.db
	$(PY) $(SCRIPTS)/09_site_builder.py

serve:
	@echo "Open http://localhost:8000"
	$(PY) -m http.server 8000 -d $(SITE_DIR)

# Local-only engagement review dashboard (NOT pushed to Vercel)
review: reports/review/index.html
reports/review/index.html: reports/cpanel_advisory.csv reports/cpanel_version_audit.csv
	$(PY) $(SCRIPTS)/13_review_dashboard.py
	@echo ""
	@echo "Open: file://$(abspath reports/review/index.html)"
	@echo "Or:   python -m http.server 8001 -d reports/review/  (then http://localhost:8001/)"

review-serve: review
	@echo "Engagement review at http://localhost:8001"
	$(PY) -m http.server 8001 -d reports/review/

all: site

clean:
	@find data -type f ! -name '.gitkeep' -delete
	@find reports -type f ! -name '.gitkeep' -delete
	@find reports -mindepth 1 -type d -empty -delete
	@echo "Cleaned data/ and reports/"

# --- Long-running supervisor ---
# `make run`     foreground, lid-safe, retries with backoff per stage
# `make run-bg`  detaches, survives terminal close; writes .pipeline.pid
# `make tail`    follow latest log
# `make status`  snapshot of progress, top WP sites, supervisor PID
# `make stop`    kill the bg supervisor

PIDFILE := .pipeline.pid

.PHONY: run run-bg tail status stop

run:
	caffeinate -i bash scripts/run_pipeline.sh

run-bg:
	@if [ -f $(PIDFILE) ] && kill -0 $$(cat $(PIDFILE)) 2>/dev/null; then \
		echo "supervisor already running (PID $$(cat $(PIDFILE))). Use 'make stop' first."; exit 1; \
	fi
	@mkdir -p logs
	@nohup caffeinate -i bash scripts/run_pipeline.sh > logs/supervisor.out 2>&1 & \
		echo $$! > $(PIDFILE); \
		echo "supervisor started PID $$(cat $(PIDFILE)). 'make tail' to follow, 'make status' for snapshot."

tail:
	@if [ -L logs/pipeline-latest.log ]; then \
		tail -F "logs/$$(readlink logs/pipeline-latest.log)"; \
	else echo "no log yet"; fi

status:
	@bash scripts/status.sh

stop:
	@if [ -f $(PIDFILE) ]; then \
		PID=$$(cat $(PIDFILE)); \
		if kill -0 $$PID 2>/dev/null; then \
			kill $$PID && echo "stopped supervisor PID $$PID"; \
		else echo "PID $$PID not running"; fi; \
		rm -f $(PIDFILE); \
	else echo "no PID file ($(PIDFILE))"; fi

# --- Live rebuild (auto-rebuild SQLite + static site every 30s) ---
LIVE_PIDFILE := .live.pid

.PHONY: live live-bg live-stop

live:
	bash scripts/live_rebuild.sh

live-bg:
	@if [ -f $(LIVE_PIDFILE) ] && kill -0 $$(cat $(LIVE_PIDFILE)) 2>/dev/null; then \
		echo "live-rebuild already running (PID $$(cat $(LIVE_PIDFILE)))"; exit 1; \
	fi
	@mkdir -p logs
	@nohup bash scripts/live_rebuild.sh > logs/live-rebuild.out 2>&1 & \
		echo $$! > $(LIVE_PIDFILE); \
		echo "live-rebuild started PID $$(cat $(LIVE_PIDFILE)). 'tail -F logs/live-rebuild.log' to follow."

live-stop:
	@if [ -f $(LIVE_PIDFILE) ]; then \
		PID=$$(cat $(LIVE_PIDFILE)); \
		if kill -0 $$PID 2>/dev/null; then \
			kill $$PID && echo "stopped live-rebuild PID $$PID"; \
		else echo "PID $$PID not running"; fi; \
		rm -f $(LIVE_PIDFILE); \
	else echo "no PID file ($(LIVE_PIDFILE))"; fi

# --- Lid-safe wrappers ---
# Pattern rule: any "<target>-awake" runs the underlying target wrapped with
# `caffeinate -i` so the Mac doesn't sleep mid-run.
%-awake:
	caffeinate -i $(MAKE) $*
