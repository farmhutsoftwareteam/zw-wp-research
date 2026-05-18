"""Pindula wiki cross-lookup.

Pindula is the de-facto Zimbabwean wiki. Many ZW businesses have a page
with a structured infobox containing founder/CEO names, addresses,
phone numbers, etc. This helper resolves a domain to its likely Pindula
page (best-effort) and parses the infobox.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus

from .http import PoliteClient

try:
    from bs4 import BeautifulSoup  # type: ignore
except ImportError:  # pragma: no cover
    BeautifulSoup = None  # type: ignore[assignment]


BASE = "https://www.pindula.co.zw"


def _domain_stems(domain: str) -> list[str]:
    """Generate candidate slugs from a domain.

    delta.co.zw → ['Delta', 'Delta_Corporation', 'Delta_Holdings']

    Trimmed to 3 high-probability variants — anything beyond that is a long
    tail not worth the per-host RPS budget against pindula.co.zw.
    """
    stem = domain.split(".")[0]
    if not stem:
        return []
    title = stem.capitalize()
    qualifiers = ["", "_Zimbabwe", "_Corporation"]
    return [title + q for q in qualifiers]


async def find_page(client: PoliteClient, domain: str) -> str | None:
    """Return the Pindula URL for this domain, or None."""
    for slug in _domain_stems(domain):
        url = f"{BASE}/{slug}"
        try:
            resp = await client.get(url)
        except Exception:
            continue
        if resp is None or resp.status_code != 200 or not resp.text:
            continue
        # Must have an infobox to be worth keeping
        if 'class="infobox' in resp.text.lower():
            return url
    # Fallback: site-internal search
    q = quote_plus(domain.split(".")[0])
    url = f"{BASE}/index.php?search={q}&go=Go"
    try:
        resp = await client.get(url)
    except Exception:
        return None
    if resp is None or resp.status_code != 200 or not resp.text:
        return None
    # Search redirects to a page if there's a unique match — look for the page title
    if BeautifulSoup is None:
        return None
    try:
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception:
        soup = BeautifulSoup(resp.text, "html.parser")
    # Try the first internal link in search results
    for a in soup.select(".mw-search-result-heading a, #firstHeading"):
        href = a.get("href")
        if href and href.startswith("/") and 'class="infobox' in resp.text.lower():
            return BASE + href
    return None


_INFOBOX_KEY_RE = re.compile(r"\s+")


def _normalise_key(label: str) -> str:
    label = label.strip().lower()
    label = re.sub(r"[^a-z0-9]+", "_", label).strip("_")
    return label


async def extract_infobox(client: PoliteClient, url: str) -> dict[str, str]:
    """Fetch + parse the first infobox table on a Pindula page."""
    if BeautifulSoup is None:
        return {}
    try:
        resp = await client.get(url)
    except Exception:
        return {}
    if resp is None or resp.status_code != 200 or not resp.text:
        return {}
    try:
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception:
        soup = BeautifulSoup(resp.text, "html.parser")
    box = soup.select_one("table.infobox")
    if not box:
        return {}
    out: dict[str, str] = {}
    for row in box.select("tr"):
        th = row.find("th")
        td = row.find("td")
        if not th or not td:
            continue
        key = _normalise_key(th.get_text(separator=" ", strip=True))
        val = td.get_text(separator=" ", strip=True)
        if not key or not val:
            continue
        # Compact internal whitespace
        val = re.sub(r"\s+", " ", val)
        out[key] = val
    return out
