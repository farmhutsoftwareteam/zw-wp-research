#!/usr/bin/env python3
"""
Stage 05 — Categorizer.

Read data/enriched.jsonl, ask Claude to classify each site into a fixed
taxonomy from title + meta description + visible nav links.

Taxonomy (single primary category + optional sector tags):
- news, government, business, blog, ngo, ecommerce, education, religious, other
- sector tags: finance, telecom, real-estate, agriculture, media, tech, ...

Emit data/classified.jsonl. Each record adds:

    {..., "category": "news", "sector_tags": ["media", "tech"],
     "category_confidence": 0.92}

Implementation:
- Batch by 20 sites per prompt to amortize API calls
- Use Claude Haiku (cheap, fast, high enough quality for classification)
- Spot-check 10% manually after the run for drift

Idempotent: skip domains already classified unless --force.

TODO: implement.
"""
from __future__ import annotations
import sys


def main() -> int:
    raise NotImplementedError("Stage 05 not implemented yet — see README.md")


if __name__ == "__main__":
    sys.exit(main())
