#!/usr/bin/env python3
"""Stage 06 — Verifier (Playwright).

Reads data/classified.jsonl, picks top-N by Tranco rank, renders each in a real
browser, takes a screenshot, and fingerprints active theme + plugins from asset
URLs. Emits data/verified.jsonl.

Concurrency capped at 5 parallel browsers (Playwright is RAM-heavy).

Idempotent: skips already-verified domains unless --force.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import Config, data_dir, reports_dir  # noqa: E402
from lib.jsonl import append_record, iter_records, read_existing_keys  # noqa: E402

CLASSIFIED_PATH = data_dir() / "classified.jsonl"
VERIFIED_PATH = data_dir() / "verified.jsonl"
SCREENSHOT_DIR = reports_dir() / "site" / "screenshots"


THEME_PATH_RE = re.compile(r"/wp-content/themes/([\w\-]+)", re.IGNORECASE)
PLUGIN_PATH_RE = re.compile(r"/wp-content/plugins/([\w\-]+)", re.IGNORECASE)


async def verify_one(domain: str, scheme: str, ctx, sem: asyncio.Semaphore) -> dict | None:
    async with sem:
        page = await ctx.new_page()
        plugins: set[str] = set()
        themes: set[str] = set()

        def on_request(req):
            url = req.url
            for m in PLUGIN_PATH_RE.findall(url):
                plugins.add(m)
            for m in THEME_PATH_RE.findall(url):
                themes.add(m)

        page.on("request", on_request)
        target = f"{scheme}://{domain}/"
        try:
            try:
                resp = await page.goto(target, wait_until="load", timeout=20_000)
            except Exception:
                # Try http if https failed
                if scheme == "https":
                    target = f"http://{domain}/"
                    try:
                        resp = await page.goto(target, wait_until="load", timeout=20_000)
                    except Exception:
                        await page.close()
                        return None
                else:
                    await page.close()
                    return None
            if resp is None:
                await page.close()
                return None
            content = await page.content()
            for m in PLUGIN_PATH_RE.findall(content):
                plugins.add(m)
            for m in THEME_PATH_RE.findall(content):
                themes.add(m)
            body_kb = len(content.encode("utf-8")) // 1024
            if body_kb < 5:
                # Looks like parked / redirected to nothing
                await page.close()
                return None
            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            png = SCREENSHOT_DIR / f"{domain}.png"
            try:
                await page.screenshot(path=str(png), full_page=False, type="png")
                screenshot_rel = f"screenshots/{domain}.png"
            except Exception:
                screenshot_rel = None
            return {
                "domain": domain,
                "scheme_used": target.split(":", 1)[0],
                "homepage_kb": body_kb,
                "theme": next(iter(themes), None),
                "themes_seen": sorted(themes),
                "plugins": sorted(plugins),
                "screenshot": screenshot_rel,
                "verified_at": datetime.now(timezone.utc).isoformat(),
            }
        finally:
            try:
                await page.close()
            except Exception:
                pass


async def run(top_n: int, concurrency: int, force: bool) -> int:
    if not CLASSIFIED_PATH.exists():
        print(f"[verify] no classified.jsonl; run stage 05 first", file=sys.stderr)
        return 0
    seen = set() if force else read_existing_keys(VERIFIED_PATH, "domain")
    cfg = Config.from_env()
    candidates = []
    for rec in iter_records(CLASSIFIED_PATH):
        d = rec.get("domain")
        if not isinstance(d, str) or d in seen:
            continue
        candidates.append(rec)
    candidates.sort(key=lambda r: (r.get("tranco_rank") or 10**9))
    candidates = candidates[:top_n]
    print(f"[verify] verifying {len(candidates)} sites (concurrency={concurrency})",
          file=sys.stderr)
    if not candidates:
        return 0

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("ERROR: playwright not installed. Run: pip install playwright && python -m playwright install chromium",
              file=sys.stderr)
        return 0

    written = 0
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=cfg.user_agent,
            ignore_https_errors=True,
        )
        sem = asyncio.Semaphore(concurrency)

        async def worker(rec):
            nonlocal written
            scheme = rec.get("scheme") or "https"
            out = await verify_one(rec["domain"], scheme, ctx, sem)
            if out is None:
                return
            merged = dict(rec)
            merged.update(out)
            append_record(VERIFIED_PATH, merged)
            written += 1
            print(f"[verify] {rec['domain']} ok ({written}/{len(candidates)})", file=sys.stderr)

        await asyncio.gather(*(worker(r) for r in candidates))
        await ctx.close()
        await browser.close()
    return written


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--top-n", type=int, default=100)
    p.add_argument("--concurrency", type=int, default=5)
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    n = asyncio.run(run(args.top_n, args.concurrency, args.force))
    print(f"[verify] wrote {n} records to {VERIFIED_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
