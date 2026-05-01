# zw-wp-research pipeline
#
# Usage:
#   make all        # run the full pipeline (1 -> 7)
#   make seeds      # just stage 01
#   make detect     # stages 01..03
#   make enrich     # stages 01..05
#   make report     # stages 01..07
#   make clean      # wipe data/ and reports/ (keeps .gitkeep)

PY := python3
SCRIPTS := scripts

.PHONY: all seeds dns detect enrich verify report clean help

help:
	@echo "Targets: seeds dns detect enrich verify report all clean"

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

all: report

clean:
	@find data -type f ! -name '.gitkeep' -delete
	@find reports -type f ! -name '.gitkeep' -delete
	@find reports -mindepth 1 -type d -empty -delete
	@echo "Cleaned data/ and reports/"
