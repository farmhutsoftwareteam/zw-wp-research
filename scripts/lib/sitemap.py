"""Sitemap discovery — find the contact-relevant URLs for a domain."""
from __future__ import annotations

import gzip
import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

from .http import PoliteClient


CONTACT_STEMS = (
    "contact", "about", "team", "staff", "people",
    "who-we-are", "our-people", "leadership", "management",
    "privacy", "terms", "legal", "impressum",
    "imprint", "company", "owners", "directors",
)


def is_contact_url(url: str) -> bool:
    """Path-stem match — case-insensitive, slashes-tolerant."""
    if not url:
        return False
    path = (urlparse(url).path or "").lower()
    if not path:
        return False
    parts = [p for p in path.split("/") if p]
    for stem in CONTACT_STEMS:
        if stem in path:
            for p in parts:
                if stem in p:
                    return True
    return False


async def _fetch_sitemap_bytes(client: PoliteClient, url: str) -> bytes | None:
    try:
        resp = await client.get(url)
    except Exception:
        return None
    if resp is None or resp.status_code != 200 or not resp.content:
        return None
    content = resp.content
    # Handle .gz filenames without Content-Encoding hints
    if url.endswith(".gz"):
        try:
            content = gzip.decompress(content)
        except Exception:
            pass
    return content


def _parse_sitemap(content: bytes) -> tuple[list[str], list[str]]:
    """Returns (sub_sitemaps, page_urls)."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return [], []
    sub: list[str] = []
    pages: list[str] = []
    tag = root.tag.lower()
    for child in root.iter():
        ctag = child.tag.lower()
        if ctag.endswith("loc"):
            loc = (child.text or "").strip()
            if not loc:
                continue
            parent_tag = ""
            # walk up — etree doesn't track parents; check siblings
            # heuristic: if URL ends in xml/xml.gz, treat as sub-sitemap
            if loc.endswith((".xml", ".xml.gz")) or "sitemap" in loc.lower():
                sub.append(loc)
            else:
                pages.append(loc)
    return sub, pages


async def discover_contact_urls(
    client: PoliteClient,
    base_url: str,
    *,
    max_urls: int = 15,
) -> list[str]:
    """Return up to `max_urls` contact-relevant URLs for `base_url`."""
    base_url = base_url.rstrip("/")
    discovered: list[str] = []

    # 1. robots.txt for Sitemap: directives
    sitemap_urls: list[str] = []
    try:
        resp = await client.get(base_url + "/robots.txt")
        if resp is not None and resp.status_code == 200 and resp.text:
            for line in resp.text.splitlines():
                m = re.match(r"\s*Sitemap:\s*(\S+)\s*$", line, re.I)
                if m:
                    sitemap_urls.append(m.group(1).strip())
    except Exception:
        pass
    # 2. Standard sitemap locations
    for path in ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml"):
        url = base_url + path
        if url not in sitemap_urls:
            sitemap_urls.append(url)

    seen_sitemaps: set[str] = set()
    candidate_urls: list[str] = []
    queue = list(sitemap_urls)
    # BFS but cap depth — sitemap-of-sitemaps × 1 level deep.
    while queue:
        sm = queue.pop(0)
        if sm in seen_sitemaps:
            continue
        seen_sitemaps.add(sm)
        content = await _fetch_sitemap_bytes(client, sm)
        if not content:
            continue
        sub, pages = _parse_sitemap(content)
        candidate_urls.extend(pages)
        # Add page-oriented sub-sitemaps only — skip post-* / news-* etc.
        for s in sub:
            low = s.lower()
            if any(k in low for k in ("post", "news", "product", "video", "media")):
                continue
            if s not in seen_sitemaps:
                queue.append(s)
        # Cap total work
        if len(candidate_urls) > 10_000:
            break

    # Filter to contact stems, dedupe, cap.
    for u in candidate_urls:
        if is_contact_url(u) and u not in discovered:
            discovered.append(u)
            if len(discovered) >= max_urls:
                break
    return discovered
