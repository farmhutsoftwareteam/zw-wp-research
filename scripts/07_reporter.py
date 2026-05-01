#!/usr/bin/env python3
"""
Stage 07 — Reporter.

Read data/verified.jsonl + data/classified.jsonl + data/enriched.jsonl, join
on domain, emit final artifacts:

- reports/report.md          narrative summary by category, top 10 per
                             category, plugin trend analysis, screenshots
- reports/top_zw_wordpress.csv  flat CSV: domain, rank, score, category,
                                sector_tags, theme, plugins, last_verified

Optional Claude pass: use the synthesizer to write the prose section of
report.md (intro, methodology, top-line findings, plugin observations).

Idempotent: re-runs overwrite reports/* — they're derived artifacts.

TODO: implement.
"""
from __future__ import annotations
import sys


def main() -> int:
    raise NotImplementedError("Stage 07 not implemented yet — see README.md")


if __name__ == "__main__":
    sys.exit(main())
