#!/usr/bin/env python3
"""Stage 03 — WordPress detector.

Reads data/live.jsonl, fetches homepage + 4 probe paths per domain, scores
WordPress signals 0-100, and emits data/detections.jsonl.

Sharding: --shard i/N processes only domains where hash(domain) % N == i.

Idempotent: skips already-detected domains unless --force.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import Config, data_dir  # noqa: E402
from lib.http import polite_client, PoliteClient  # noqa: E402
from lib.jsonl import append_record, iter_records, read_existing_keys  # noqa: E402

LIVE_PATH = data_dir() / "live.jsonl"
DETECTIONS_PATH = data_dir() / "detections.jsonl"


# Signal weights — higher = more definitive
SIGNAL_WEIGHTS = {
    "meta_generator_wp": 30,
    "wp_json_valid": 25,
    "link_header_wp_api": 20,
    "wp_content_path": 15,
    "wp_includes_path": 15,  # combined with above, capped at 20
    "rss_wp_generator": 10,
    "wp_login_200": 10,
    "theme_path": 5,
    "plugin_path": 5,  # combined with theme, capped at 10
    "readme_html_wp": 8,
    "xmlrpc_present": 5,
    "wp_body_class": 5,
}


META_GEN_RE = re.compile(
    r'<meta\s+name=["\']generator["\']\s+content=["\']([^"\']+)["\']', re.IGNORECASE
)
WP_VERSION_RE = re.compile(r"WordPress\s+([\d\.]+)", re.IGNORECASE)
WP_CONTENT_RE = re.compile(r"/wp-content/", re.IGNORECASE)
WP_INCLUDES_RE = re.compile(r"/wp-includes/", re.IGNORECASE)
THEME_PATH_RE = re.compile(r"/wp-content/themes/([\w\-]+)", re.IGNORECASE)
PLUGIN_PATH_RE = re.compile(r"/wp-content/plugins/([\w\-]+)", re.IGNORECASE)
WP_BODY_CLASS_RE = re.compile(r'class=["\'][^"\']*\bwp-(singular|admin-bar|block-)', re.IGNORECASE)


def _score(signals: dict[str, bool]) -> int:
    score = 0
    # combined cap: wp-content + wp-includes <= 20
    cm = 0
    if signals.get("wp_content_path"):
        cm += SIGNAL_WEIGHTS["wp_content_path"]
    if signals.get("wp_includes_path"):
        cm += SIGNAL_WEIGHTS["wp_includes_path"]
    score += min(cm, 20)
    # combined cap: theme + plugin paths <= 10
    tp = 0
    if signals.get("theme_path"):
        tp += SIGNAL_WEIGHTS["theme_path"]
    if signals.get("plugin_path"):
        tp += SIGNAL_WEIGHTS["plugin_path"]
    score += min(tp, 10)
    for k in ("meta_generator_wp", "wp_json_valid", "link_header_wp_api",
              "rss_wp_generator", "wp_login_200", "readme_html_wp",
              "xmlrpc_present", "wp_body_class"):
        if signals.get(k):
            score += SIGNAL_WEIGHTS[k]
    return min(score, 100)


async def _probe(client: PoliteClient, base: str) -> dict[str, Any]:
    """Run all probes for one domain and return scoring data."""
    signals: dict[str, bool] = {}
    out: dict[str, Any] = {"signals": signals}
    homepage_url = base + "/"
    home = await client.get(homepage_url)
    out["homepage_status"] = home.status_code if home else None
    body_text = home.text if home is not None else ""
    out["body_sample_b64"] = base64.b64encode(
        body_text[:16384].encode("utf-8", errors="replace")
    ).decode("ascii") if body_text else None
    out["body_hash"] = hashlib.sha1(body_text.encode("utf-8", errors="replace")).hexdigest() if body_text else None

    # Link header rel=https://api.w.org
    if home is not None:
        link_hdr = home.headers.get("link", "") or home.headers.get("Link", "")
        if "api.w.org" in link_hdr:
            signals["link_header_wp_api"] = True

    if body_text:
        m = META_GEN_RE.search(body_text)
        if m:
            gen = m.group(1)
            if "wordpress" in gen.lower():
                signals["meta_generator_wp"] = True
                v = WP_VERSION_RE.search(gen)
                if v:
                    out["wp_version"] = v.group(1)
        if WP_CONTENT_RE.search(body_text):
            signals["wp_content_path"] = True
        if WP_INCLUDES_RE.search(body_text):
            signals["wp_includes_path"] = True
        themes = THEME_PATH_RE.findall(body_text)
        if themes:
            signals["theme_path"] = True
            out["themes_seen"] = list(dict.fromkeys(themes))[:5]
        plugins = PLUGIN_PATH_RE.findall(body_text)
        if plugins:
            signals["plugin_path"] = True
            out["plugins_seen"] = list(dict.fromkeys(plugins))[:20]
        if WP_BODY_CLASS_RE.search(body_text):
            signals["wp_body_class"] = True

    # /wp-json/
    wpj = await client.get(base + "/wp-json/")
    if wpj is not None and wpj.status_code == 200:
        ctype = (wpj.headers.get("content-type") or "").lower()
        if "json" in ctype:
            try:
                payload = wpj.json()
                if isinstance(payload, dict) and ("namespaces" in payload or "routes" in payload):
                    signals["wp_json_valid"] = True
            except Exception:
                pass

    # /feed/
    feed = await client.get(base + "/feed/")
    if feed is not None and feed.status_code == 200 and feed.text:
        if "<generator>" in feed.text and "wordpress" in feed.text.lower():
            signals["rss_wp_generator"] = True

    # /wp-login.php
    wpl = await client.get(base + "/wp-login.php")
    if wpl is not None and wpl.status_code == 200:
        if wpl.text and "wp-login" in wpl.text.lower():
            signals["wp_login_200"] = True

    # /readme.html (legacy WP)
    rd = await client.get(base + "/readme.html")
    if rd is not None and rd.status_code == 200 and rd.text:
        if "wordpress" in rd.text.lower():
            signals["readme_html_wp"] = True

    # /xmlrpc.php (POST without body returns 405; GET often shows "XML-RPC server accepts...")
    xrpc = await client.get(base + "/xmlrpc.php")
    if xrpc is not None and xrpc.text and "xml-rpc" in xrpc.text.lower():
        signals["xmlrpc_present"] = True

    out["score"] = _score(signals)
    return out


def _shard_match(domain: str, shard: tuple[int, int] | None) -> bool:
    if shard is None:
        return True
    i, n = shard
    h = int(hashlib.md5(domain.encode("utf-8")).hexdigest(), 16)
    return h % n == i


async def detect_one(client: PoliteClient, domain: str) -> dict | None:
    # Try https first, fall back to http on connect failure
    for scheme in ("https", "http"):
        base = f"{scheme}://{domain}"
        try:
            res = await _probe(client, base)
        except Exception as exc:
            res = None
        if res is not None and res.get("homepage_status") is not None:
            return {
                "domain": domain,
                "scheme": scheme,
                **res,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
    return None


async def run(shard: tuple[int, int] | None, force: bool, limit: int | None) -> int:
    cfg = Config.from_env()
    if not LIVE_PATH.exists():
        print(f"[detect] no live.jsonl; run stage 02 first", file=sys.stderr)
        return 0
    seen = set() if force else read_existing_keys(DETECTIONS_PATH, "domain")
    targets: list[str] = []
    for rec in iter_records(LIVE_PATH):
        d = rec.get("domain")
        if not isinstance(d, str):
            continue
        if d in seen:
            continue
        if not _shard_match(d, shard):
            continue
        targets.append(d)
        if limit and len(targets) >= limit:
            break
    print(f"[detect] processing {len(targets)} domains (rps/host={cfg.rps_per_host})",
          file=sys.stderr)
    written = 0
    async with polite_client(
        user_agent=cfg.user_agent,
        rps_per_host=cfg.rps_per_host,
        timeout=cfg.timeout,
        max_concurrent=200,
    ) as client:
        sem = asyncio.Semaphore(200)

        async def worker(d: str) -> None:
            nonlocal written
            async with sem:
                rec = await detect_one(client, d)
            if rec is not None:
                append_record(DETECTIONS_PATH, rec)
                written += 1
                if written % 50 == 0:
                    print(f"[detect] {written} done", file=sys.stderr)

        await asyncio.gather(*(worker(d) for d in targets))
    return written


def _parse_shard(s: str | None) -> tuple[int, int] | None:
    if not s:
        return None
    try:
        i, n = s.split("/")
        return int(i), int(n)
    except ValueError:
        raise SystemExit(f"--shard must be i/N, got {s!r}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--shard", default=None, help="i/N e.g. 0/8")
    p.add_argument("--force", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()
    n = asyncio.run(run(_parse_shard(args.shard), args.force, args.limit))
    print(f"[detect] wrote {n} records to {DETECTIONS_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
