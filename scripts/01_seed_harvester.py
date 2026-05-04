#!/usr/bin/env python3
"""Stage 01 — Seed harvester.

Pulls candidate Zimbabwean domains from every free source we have, dedupes on
(domain, source), and writes data/seeds.jsonl.

Sources (v1, all free):
- tranco       Tranco top-1M, filtered to .zw
- cc           Common Crawl CDX index, host suffix .zw
- techzim      Curated scrape of techzim.co.zw outbound .zw links
- pindula      Curated scrape of pindula.co.zw business listings
- gov-zw       Curated scrape of gov.zw portal pages
- cf-radar     Cloudflare Radar top sites by ZW (only if token present)

Each record: {"domain": "<apex>", "source": "<src>", "hint": "<free-text>"}

Idempotent: re-runs skip (domain, source) pairs already in the file unless --force.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Iterator

# Make `lib` importable when running as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import Config, data_dir  # noqa: E402
from lib.domain import is_zw_tld, normalize  # noqa: E402
from lib.http import polite_client  # noqa: E402
from lib.jsonl import append_record, read_existing_pairs  # noqa: E402

try:
    from bs4 import BeautifulSoup  # type: ignore
except ImportError:
    BeautifulSoup = None  # type: ignore[assignment]


SEEDS_PATH = data_dir() / "seeds.jsonl"
TRANCO_DOWNLOAD_URL = "https://tranco-list.eu/top-1m.csv.zip"

ALL_SOURCES = ["tranco", "cc", "techzim", "pindula", "gov-zw", "cf-radar"]


# -----------------------------
# Source: Tranco (top 1M)
# -----------------------------
async def from_tranco_csv(cfg: Config) -> AsyncIterator[dict]:
    """Read (or download) Tranco top-1M CSV, yield .zw rows."""
    csv_path: Path = cfg.tranco_csv_path
    if not csv_path.exists():
        print(f"[tranco] downloading {TRANCO_DOWNLOAD_URL} -> {csv_path}", file=sys.stderr)
        async with polite_client(
            user_agent=cfg.user_agent,
            rps_per_host=cfg.rps_per_host,
            timeout=120,
            max_concurrent=4,
        ) as c:
            resp = await c.get(TRANCO_DOWNLOAD_URL)
            if resp is None or resp.status_code != 200:
                print("[tranco] download failed; skipping source", file=sys.stderr)
                return
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                    name = zf.namelist()[0]
                    with zf.open(name) as zfh:
                        csv_path.write_bytes(zfh.read())
            except zipfile.BadZipFile:
                csv_path.write_bytes(resp.content)
    print(f"[tranco] reading {csv_path}", file=sys.stderr)
    yielded = 0
    with csv_path.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 2:
                continue
            rank, domain = row[0], row[1]
            d = normalize(domain)
            if not d or not is_zw_tld(d):
                continue
            yielded += 1
            yield {"domain": d, "source": "tranco", "hint": f"rank={rank}"}
    print(f"[tranco] yielded {yielded} .zw rows", file=sys.stderr)


# -----------------------------
# Source: Common Crawl CDX
# -----------------------------
COMMON_CRAWL_INDEX_LIST = "https://index.commoncrawl.org/collinfo.json"


async def _latest_cc_index_id(cfg: Config) -> str | None:
    async with polite_client(
        user_agent=cfg.user_agent, rps_per_host=cfg.rps_per_host, timeout=30, max_concurrent=2
    ) as c:
        resp = await c.get(COMMON_CRAWL_INDEX_LIST)
        if resp is None or resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except Exception:
            return None
        if isinstance(data, list) and data:
            return data[0].get("id")
        return None


async def from_common_crawl_cdx(cfg: Config) -> AsyncIterator[dict]:
    """Stream all CDX entries for *.zw across all pages.

    The CDX API uses page-based pagination. We first ask for `showNumPages=true`
    to learn the page count, then iterate. Each page can be tens of thousands
    of records but most are duplicates of the same hostname.
    """
    idx = await _latest_cc_index_id(cfg)
    if not idx:
        print("[cc] could not fetch index id; skipping", file=sys.stderr)
        return
    cdx_url = f"https://index.commoncrawl.org/{idx}-index"
    print(f"[cc] querying {cdx_url} for *.zw (paginated)", file=sys.stderr)
    seen: set[str] = set()
    async with polite_client(
        user_agent=cfg.user_agent, rps_per_host=cfg.rps_per_host, timeout=180, max_concurrent=2
    ) as c:
        # Discover number of pages.
        try:
            meta = await c.get(cdx_url, params={"url": "*.zw", "showNumPages": "true"})
        except Exception as exc:
            print(f"[cc] meta error: {exc}", file=sys.stderr)
            return
        if meta is None or meta.status_code != 200:
            print(f"[cc] meta HTTP {meta.status_code if meta else 'NONE'}; skipping", file=sys.stderr)
            return
        try:
            meta_obj = meta.json()
            num_pages = int(meta_obj.get("pages") or 1)
        except Exception:
            num_pages = 1
        # Cap pages to keep first run reasonable; full coverage can re-run later.
        max_pages = int(os.environ.get("CC_MAX_PAGES", "50"))
        num_pages = min(num_pages, max_pages)
        print(f"[cc] streaming {num_pages} pages", file=sys.stderr)
        for page in range(num_pages):
            try:
                resp = await c.get(
                    cdx_url,
                    params={"url": "*.zw", "output": "json", "page": str(page)},
                )
            except Exception as exc:
                print(f"[cc] page {page} error: {exc}", file=sys.stderr)
                continue
            if resp is None or resp.status_code != 200:
                print(f"[cc] page {page} HTTP {resp.status_code if resp else 'NONE'}",
                      file=sys.stderr)
                continue
            new_this_page = 0
            for line in resp.text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                url = obj.get("url") or ""
                d = normalize(url)
                if not d or not is_zw_tld(d):
                    continue
                if d in seen:
                    continue
                seen.add(d)
                new_this_page += 1
                yield {"domain": d, "source": "cc-cdx", "hint": idx}
            print(f"[cc] page {page + 1}/{num_pages}: {new_this_page} new (total {len(seen)})",
                  file=sys.stderr)
        print(f"[cc] yielded {len(seen)} unique .zw domains total", file=sys.stderr)


# -----------------------------
# Curated scrapes (techzim / pindula / gov.zw)
# -----------------------------
import re as _re
_ZW_DOMAIN_RE = _re.compile(r"\b([a-z0-9][a-z0-9\-]{0,62}(?:\.[a-z0-9][a-z0-9\-]{0,62}){1,4}\.zw)\b",
                            _re.IGNORECASE)


async def _scrape_outbound_zw(cfg: Config, urls: list[str], source_tag: str) -> AsyncIterator[dict]:
    """Scrape pages for .zw domain mentions — links AND inline text.

    Many directory pages list a domain like 'visit techzim.co.zw' in plain text
    rather than as a href. The regex sweep catches those.
    """
    if BeautifulSoup is None:
        print(f"[{source_tag}] beautifulsoup4 not installed; skipping", file=sys.stderr)
        return
    seen: set[str] = set()
    async with polite_client(
        user_agent=cfg.user_agent, rps_per_host=cfg.rps_per_host, timeout=cfg.timeout, max_concurrent=4
    ) as c:
        for url in urls:
            try:
                resp = await c.get(url)
            except Exception as exc:
                print(f"[{source_tag}] {url}: {exc}", file=sys.stderr)
                continue
            if resp is None or resp.status_code != 200:
                print(f"[{source_tag}] {url}: HTTP {resp.status_code if resp else 'NONE'}",
                      file=sys.stderr)
                continue
            try:
                soup = BeautifulSoup(resp.text, "lxml")
            except Exception:
                soup = BeautifulSoup(resp.text, "html.parser")
            # 1. Outbound href links
            candidates: set[str] = set()
            for a in soup.find_all("a", href=True):
                d = normalize(a["href"])
                if d and is_zw_tld(d):
                    candidates.add(d)
            # 2. Inline text mentions (e.g. "Visit techzim.co.zw")
            text_blob = soup.get_text(separator=" ", strip=True)
            for m in _ZW_DOMAIN_RE.findall(text_blob):
                d = normalize(m)
                if d and is_zw_tld(d):
                    candidates.add(d)
            # 3. og:url / canonical
            for tag, attr in (("link", "href"), ("meta", "content")):
                for el in soup.find_all(tag):
                    val = el.get(attr) or ""
                    d = normalize(val)
                    if d and is_zw_tld(d):
                        candidates.add(d)
            new_here = 0
            for d in candidates:
                if d in seen:
                    continue
                seen.add(d)
                new_here += 1
                yield {"domain": d, "source": source_tag, "hint": url}
            print(f"[{source_tag}] {url}: +{new_here}", file=sys.stderr)
    print(f"[{source_tag}] yielded {len(seen)} unique .zw domains", file=sys.stderr)


async def from_techzim(cfg: Config) -> AsyncIterator[dict]:
    urls = [
        "https://www.techzim.co.zw/",
        "https://www.techzim.co.zw/category/business/",
        "https://www.techzim.co.zw/category/news/",
        "https://www.techzim.co.zw/category/technology/",
        "https://www.techzim.co.zw/category/zimbabwe/",
        "https://www.techzim.co.zw/category/startups/",
        "https://www.techzim.co.zw/category/finance/",
    ]
    async for rec in _scrape_outbound_zw(cfg, urls, "techzim"):
        yield rec


async def from_pindula(cfg: Config) -> AsyncIterator[dict]:
    urls = [
        # Topic / category pages on the wiki — text bodies usually mention domains.
        "https://www.pindula.co.zw/Companies_in_Zimbabwe",
        "https://www.pindula.co.zw/Banks_in_Zimbabwe",
        "https://www.pindula.co.zw/Universities_in_Zimbabwe",
        "https://www.pindula.co.zw/Colleges_in_Zimbabwe",
        "https://www.pindula.co.zw/Schools_in_Zimbabwe",
        "https://www.pindula.co.zw/Media_in_Zimbabwe",
        "https://www.pindula.co.zw/Newspapers_in_Zimbabwe",
        "https://www.pindula.co.zw/Radio_Stations_in_Zimbabwe",
        "https://www.pindula.co.zw/Television_Stations_in_Zimbabwe",
        "https://www.pindula.co.zw/Telecommunications_Companies_in_Zimbabwe",
        "https://www.pindula.co.zw/Insurance_Companies_in_Zimbabwe",
        "https://www.pindula.co.zw/Hotels_in_Zimbabwe",
        "https://www.pindula.co.zw/Government_of_Zimbabwe",
        "https://www.pindula.co.zw/List_Of_Government_Ministries_in_Zimbabwe",
        "https://www.pindula.co.zw/NGOs_in_Zimbabwe",
        "https://www.pindula.co.zw/Mining_Companies_in_Zimbabwe",
        "https://www.pindula.co.zw/Hospitals_in_Zimbabwe",
        "https://www.pindula.co.zw/Churches_in_Zimbabwe",
        "https://www.pindula.co.zw/Stockbrokers_in_Zimbabwe",
    ]
    async for rec in _scrape_outbound_zw(cfg, urls, "pindula"):
        yield rec


async def from_gov_zw(cfg: Config) -> AsyncIterator[dict]:
    urls = [
        "https://www.gov.zw/",
        "https://www.gov.zw/services",
        "https://www.gov.zw/ministries",
        "https://www.gov.zw/departments",
        # Some ministries are reachable without www.
        "https://gov.zw/",
        # Wikipedia maintains a comprehensive list of ZW gov sites — text-mining yields ministries.
        "https://en.wikipedia.org/wiki/Government_of_Zimbabwe",
        "https://en.wikipedia.org/wiki/Cabinet_of_Zimbabwe",
        # Other useful aggregator pages that mention many .zw addresses
        "https://en.wikipedia.org/wiki/List_of_universities_in_Zimbabwe",
        "https://en.wikipedia.org/wiki/Telecommunications_in_Zimbabwe",
        "https://en.wikipedia.org/wiki/Mass_media_in_Zimbabwe",
    ]
    async for rec in _scrape_outbound_zw(cfg, urls, "gov-zw"):
        yield rec


# -----------------------------
# Source: Cloudflare Radar
# -----------------------------
async def from_cf_radar(cfg: Config) -> AsyncIterator[dict]:
    if not cfg.cf_radar_token:
        return
    url = "https://api.cloudflare.com/client/v4/radar/ranking/top"
    params = {"location": "ZW", "limit": "500"}
    headers = {"Authorization": f"Bearer {cfg.cf_radar_token}"}
    async with polite_client(
        user_agent=cfg.user_agent, rps_per_host=2.0, timeout=30, max_concurrent=2
    ) as c:
        try:
            resp = await c.client.get(url, params=params, headers=headers)
        except Exception as exc:
            print(f"[cf-radar] error: {exc}", file=sys.stderr)
            return
        if resp.status_code != 200:
            print(f"[cf-radar] HTTP {resp.status_code}; skipping", file=sys.stderr)
            return
        try:
            data = resp.json()
        except Exception:
            return
        items = (data.get("result") or {}).get("top") or []
        seen = 0
        for item in items:
            d = normalize(item.get("domain") or "")
            if not d:
                continue
            seen += 1
            yield {
                "domain": d,
                "source": "cf-radar",
                "hint": f"rank={item.get('rank', '')}",
            }
        print(f"[cf-radar] yielded {seen}", file=sys.stderr)


# -----------------------------
# Orchestration
# -----------------------------
SOURCE_FNS = {
    "tranco": from_tranco_csv,
    "cc": from_common_crawl_cdx,
    "techzim": from_techzim,
    "pindula": from_pindula,
    "gov-zw": from_gov_zw,
    "cf-radar": from_cf_radar,
}


async def harvest(sources: list[str], force: bool) -> int:
    cfg = Config.from_env()
    existing = set() if force else read_existing_pairs(SEEDS_PATH, "domain", "source")
    written = 0
    for src in sources:
        fn = SOURCE_FNS.get(src)
        if fn is None:
            print(f"[harvest] unknown source: {src}", file=sys.stderr)
            continue
        try:
            async for rec in fn(cfg):
                pair = (rec["domain"], rec["source"])
                if pair in existing:
                    continue
                existing.add(pair)
                rec.setdefault("first_seen", datetime.now(timezone.utc).isoformat())
                append_record(SEEDS_PATH, rec)
                written += 1
        except Exception as exc:
            print(f"[harvest] source {src} crashed: {exc}", file=sys.stderr)
    return written


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", default=",".join(ALL_SOURCES),
                   help=f"Comma-separated sources to run. Available: {','.join(ALL_SOURCES)}")
    p.add_argument("--force", action="store_true", help="Ignore existing seeds, re-emit all")
    args = p.parse_args()
    sources = [s.strip() for s in args.source.split(",") if s.strip()]
    n = asyncio.run(harvest(sources, args.force))
    print(f"[harvest] wrote {n} new records to {SEEDS_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
