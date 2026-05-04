#!/usr/bin/env python3
"""Stage 05 — Categorizer.

Reads data/enriched.jsonl, extracts title/description/nav from each homepage
sample, batches 20 sites per `claude -p` call, and writes data/classified.jsonl.

LLM access goes through `lib.claude_cli.run_claude()` — uses the Claude Max
plan via `claude` CLI, no API key required. Pace: CLAUDE_RPS env (default 0.5).

Idempotent: skips already-classified domains unless --force.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import Config, data_dir  # noqa: E402
from lib.claude_cli import (  # noqa: E402
    arun_claude, assert_available, extract_json, ClaudeError, ClaudeRateLimitError,
)
from lib.jsonl import append_record, iter_records, read_existing_keys  # noqa: E402

try:
    from bs4 import BeautifulSoup  # type: ignore
except ImportError:
    BeautifulSoup = None  # type: ignore[assignment]

ENRICHED_PATH = data_dir() / "enriched.jsonl"
CLASSIFIED_PATH = data_dir() / "classified.jsonl"
REVIEW_PATH = data_dir() / "categorizer_review.jsonl"

CATEGORIES = [
    "news", "government", "business", "blog", "ngo",
    "ecommerce", "education", "religious", "other",
]


PROMPT_TEMPLATE = """You are categorizing Zimbabwean websites by their primary purpose.

For each site below, output ONE JSON object with keys:
- domain: the domain (echo it back)
- category: one of {categories}
- sector_tags: 0-3 short tags from {{finance, telecom, real-estate, agriculture, media, tech, health, retail, hospitality, transport, energy, mining, legal, other}}
- category_confidence: 0.0-1.0

Output a JSON array of {n} objects. NO prose. NO markdown fences. ONLY the JSON array.

Sites:
{sites}
"""


def _extract_brief(record: dict) -> str:
    """Get title + meta description + a few nav links from body_sample_b64."""
    sample_b64 = record.get("body_sample_b64") or ""
    if not sample_b64 or BeautifulSoup is None:
        return record.get("domain", "")
    try:
        html = base64.b64decode(sample_b64).decode("utf-8", errors="replace")
    except Exception:
        return record.get("domain", "")
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string.strip() if soup.title and soup.title.string else "")[:200]
    desc = ""
    md = soup.find("meta", attrs={"name": "description"}) or soup.find(
        "meta", attrs={"property": "og:description"}
    )
    if md and md.get("content"):
        desc = str(md["content"]).strip()[:300]
    nav_texts: list[str] = []
    for a in soup.find_all("a", limit=30):
        t = (a.get_text() or "").strip()
        if t and 2 <= len(t) <= 40:
            nav_texts.append(t)
    nav = ", ".join(dict.fromkeys(nav_texts))[:300]
    return f"title={title!r} | desc={desc!r} | nav={nav!r}"


def _build_prompt(batch: list[dict]) -> str:
    lines = []
    for rec in batch:
        d = rec["domain"]
        lines.append(f"- {d}: {_extract_brief(rec)}")
    return PROMPT_TEMPLATE.format(
        categories=", ".join(CATEGORIES),
        n=len(batch),
        sites="\n".join(lines),
    )


async def classify_batch(batch: list[dict], cfg: Config, batch_idx: int) -> list[dict]:
    prompt = _build_prompt(batch)
    try:
        result = await arun_claude(
            prompt,
            model=cfg.anthropic_model,
            timeout=120,
            rps=cfg.claude_rps,
        )
    except ClaudeRateLimitError as exc:
        # Bubble up — supervisor will retry the whole stage after backoff.
        print(f"[classify] rate limit not cleared after retries: {exc}", file=sys.stderr)
        raise
    except ClaudeError as exc:
        # Per-batch failure: skip this batch, will retry on next run since not written.
        print(f"[classify] batch {batch_idx} CLI error: {exc}", file=sys.stderr)
        return []
    # Spot-check sampler
    if batch_idx % 10 == 0:
        append_record(REVIEW_PATH, {
            "batch_idx": batch_idx,
            "domains": [r["domain"] for r in batch],
            "raw": result.text[:5000],
            "at": datetime.now(timezone.utc).isoformat(),
        })
    try:
        parsed = extract_json(result.text)
    except ClaudeError as exc:
        print(f"[classify] batch {batch_idx} JSON parse failed: {exc}", file=sys.stderr)
        return []
    if not isinstance(parsed, list):
        print(f"[classify] batch {batch_idx}: expected array, got {type(parsed).__name__}",
              file=sys.stderr)
        return []
    by_domain = {p.get("domain"): p for p in parsed if isinstance(p, dict)}
    out: list[dict] = []
    for rec in batch:
        d = rec["domain"]
        cls = by_domain.get(d)
        if not cls:
            print(f"[classify] no result for {d} in batch {batch_idx}", file=sys.stderr)
            continue
        cat = cls.get("category")
        if cat not in CATEGORIES:
            cat = "other"
        merged = dict(rec)
        merged["category"] = cat
        merged["sector_tags"] = (
            cls.get("sector_tags") if isinstance(cls.get("sector_tags"), list) else []
        )[:5]
        try:
            merged["category_confidence"] = float(cls.get("category_confidence") or 0.5)
        except (TypeError, ValueError):
            merged["category_confidence"] = 0.5
        merged["classified_at"] = datetime.now(timezone.utc).isoformat()
        # Drop heavy field — no longer needed downstream beyond stage 05/06 re-render
        merged.pop("body_sample_b64", None)
        out.append(merged)
    return out


async def run(batch_size: int, top_n: int | None, force: bool) -> int:
    cfg = Config.from_env()
    assert_available()
    if not ENRICHED_PATH.exists():
        print(f"[classify] no enriched.jsonl; run stage 04 first", file=sys.stderr)
        return 0
    seen = set() if force else read_existing_keys(CLASSIFIED_PATH, "domain")
    pending: list[dict] = []
    for rec in iter_records(ENRICHED_PATH):
        d = rec.get("domain")
        if not isinstance(d, str) or d in seen:
            continue
        pending.append(rec)
    # Optional: sort by Tranco rank ascending so we classify top sites first
    pending.sort(key=lambda r: (r.get("tranco_rank") or 10**9))
    if top_n:
        pending = pending[:top_n]
    print(f"[classify] {len(pending)} sites in {(len(pending) + batch_size - 1) // batch_size} batches",
          file=sys.stderr)
    written = 0
    for i in range(0, len(pending), batch_size):
        batch = pending[i: i + batch_size]
        batch_idx = i // batch_size
        results = await classify_batch(batch, cfg, batch_idx)
        for r in results:
            append_record(CLASSIFIED_PATH, r)
            written += 1
        print(f"[classify] batch {batch_idx} -> {len(results)}/{len(batch)} (total {written})",
              file=sys.stderr)
    return written


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--batch-size", type=int, default=20)
    p.add_argument("--top-n", type=int, default=None,
                   help="Only categorize top-N by Tranco rank")
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    n = asyncio.run(run(args.batch_size, args.top_n, args.force))
    print(f"[classify] wrote {n} records to {CLASSIFIED_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
