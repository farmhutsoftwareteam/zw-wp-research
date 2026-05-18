"""WHOIS / RDAP lookup client.

Tries multiple free providers in order, returns first non-empty response.
Normalises to a single dict shape regardless of which provider answered.
"""
from __future__ import annotations

import re
from typing import Any

from .http import PoliteClient


PROXY_EMAIL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"@whoisguard\.com$", re.I),
    re.compile(r"@domainsbyproxy\.com$", re.I),
    re.compile(r"@privacyprotect\.org$", re.I),
    re.compile(r"@withheldforprivacy\.email$", re.I),
    re.compile(r"@whoxy\.com$", re.I),
    re.compile(r"@anonymize\.com$", re.I),
    re.compile(r"@privacyservice\..*$", re.I),
    re.compile(r"@contactprivacy\.com$", re.I),
    re.compile(r"@privacy-protect\.email$", re.I),
    re.compile(r"@(?:protectedwhois|protecteddomainservices|domaincontactprivacy)\.com$", re.I),
    re.compile(r"@perfectprivacy\.com$", re.I),
    re.compile(r"@privacy\.aboutus\.org$", re.I),
    re.compile(r"@redacted(?:forprivacy)?\..+$", re.I),
]


def is_proxy_email(email: str | None) -> bool:
    if not email:
        return False
    return any(p.search(email) for p in PROXY_EMAIL_PATTERNS)


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _normalise(raw_dict: dict[str, Any], *, source: str) -> dict[str, Any]:
    """Squash the raw provider dict into our canonical shape."""
    out = {
        "registrant_email": raw_dict.get("registrant_email"),
        "registrant_name": raw_dict.get("registrant_name"),
        "registrant_org": raw_dict.get("registrant_org"),
        "registrant_country": raw_dict.get("registrant_country"),
        "admin_email": raw_dict.get("admin_email"),
        "tech_email": raw_dict.get("tech_email"),
        "registrar": raw_dict.get("registrar"),
        "created": raw_dict.get("created"),
        "updated": raw_dict.get("updated"),
        "expires": raw_dict.get("expires"),
        "source_provider": source,
    }
    return out


def _walk_emails(blob: Any) -> list[str]:
    """Best-effort recursive scrape of any email-shaped strings in a JSON blob."""
    found: set[str] = set()
    def go(node):
        if isinstance(node, str):
            for m in _EMAIL_RE.findall(node):
                found.add(m.lower())
        elif isinstance(node, dict):
            for v in node.values():
                go(v)
        elif isinstance(node, list):
            for v in node:
                go(v)
    go(blob)
    return sorted(found)


# ---- RDAP -------------------------------------------------------------
def _parse_rdap(data: dict) -> dict | None:
    if not isinstance(data, dict):
        return None
    out: dict[str, Any] = {"registrar": None}
    entities = data.get("entities") or []
    for e in entities:
        roles = [str(r).lower() for r in (e.get("roles") or [])]
        vcard = (e.get("vcardArray") or [None, []])[1] if e.get("vcardArray") else []
        name = None
        email = None
        org = None
        country = None
        for prop in vcard:
            if not isinstance(prop, list) or len(prop) < 4:
                continue
            kind = prop[0]
            val = prop[3]
            if kind == "fn" and isinstance(val, str):
                name = val
            elif kind == "email" and isinstance(val, str):
                email = val.lower()
            elif kind == "org":
                if isinstance(val, list) and val:
                    org = val[0]
                elif isinstance(val, str):
                    org = val
            elif kind == "adr":
                if isinstance(val, list) and len(val) >= 7:
                    country = val[6]
        if "registrant" in roles:
            out["registrant_email"] = email or out.get("registrant_email")
            out["registrant_name"] = name or out.get("registrant_name")
            out["registrant_org"] = org or out.get("registrant_org")
            out["registrant_country"] = country or out.get("registrant_country")
        if "administrative" in roles or "admin" in roles:
            out["admin_email"] = email or out.get("admin_email")
        if "technical" in roles or "tech" in roles:
            out["tech_email"] = email or out.get("tech_email")
        if "registrar" in roles:
            out["registrar"] = name or org or out.get("registrar")
    for evt in data.get("events") or []:
        action = (evt.get("eventAction") or "").lower()
        if action == "registration":
            out["created"] = evt.get("eventDate")
        elif action == "last changed":
            out["updated"] = evt.get("eventDate")
        elif action == "expiration":
            out["expires"] = evt.get("eventDate")
    # Last-ditch: walk the JSON for any email at all
    if not any(out.get(k) for k in ("registrant_email", "admin_email", "tech_email")):
        emails = _walk_emails(data)
        if emails:
            out["registrant_email"] = emails[0]
    return out if any(v for v in out.values()) else None


async def _lookup_rdap(domain: str, client: PoliteClient) -> dict | None:
    url = f"https://rdap.org/domain/{domain}"
    try:
        resp = await client.get(url)
    except Exception:
        return None
    if resp is None or resp.status_code != 200:
        return None
    try:
        return _parse_rdap(resp.json())
    except Exception:
        return None


# ---- Whoxy free tier --------------------------------------------------
def _parse_whoxy(data: dict) -> dict | None:
    if not isinstance(data, dict) or data.get("status") != 1:
        return None
    reg = data.get("registrant_contact") or {}
    adm = data.get("administrative_contact") or {}
    tch = data.get("technical_contact") or {}
    out = {
        "registrant_email": (reg.get("email_address") or "").lower() or None,
        "registrant_name": reg.get("full_name") or None,
        "registrant_org": reg.get("company_name") or None,
        "registrant_country": reg.get("country_name") or None,
        "admin_email": (adm.get("email_address") or "").lower() or None,
        "tech_email": (tch.get("email_address") or "").lower() or None,
        "registrar": (data.get("domain_registrar") or {}).get("registrar_name"),
        "created": data.get("create_date"),
        "updated": data.get("update_date"),
        "expires": data.get("expiry_date"),
    }
    return out if any(out.values()) else None


async def _lookup_whoxy(domain: str, client: PoliteClient) -> dict | None:
    # Public read-only endpoint (no key)
    url = f"https://api.whoxy.com/?key=free&whois={domain}"
    try:
        resp = await client.get(url)
    except Exception:
        return None
    if resp is None or resp.status_code != 200:
        return None
    try:
        return _parse_whoxy(resp.json())
    except Exception:
        return None


# ---- Who-Dat public instance -----------------------------------------
def _parse_whodat(data: dict) -> dict | None:
    if not isinstance(data, dict):
        return None
    reg = data.get("registrant") or {}
    adm = data.get("administrative") or {}
    tch = data.get("technical") or {}
    out = {
        "registrant_email": (reg.get("email") or "").lower() or None,
        "registrant_name": reg.get("name") or None,
        "registrant_org": reg.get("organization") or None,
        "registrant_country": reg.get("country") or None,
        "admin_email": (adm.get("email") or "").lower() or None,
        "tech_email": (tch.get("email") or "").lower() or None,
        "registrar": (data.get("registrar") or {}).get("name"),
        "created": data.get("created"),
        "updated": data.get("updated"),
        "expires": data.get("expires"),
    }
    return out if any(out.values()) else None


async def _lookup_whodat(domain: str, client: PoliteClient) -> dict | None:
    url = f"https://who-dat.as93.net/{domain}"
    try:
        resp = await client.get(url)
    except Exception:
        return None
    if resp is None or resp.status_code != 200:
        return None
    try:
        return _parse_whodat(resp.json())
    except Exception:
        return None


# ---- public orchestrator ---------------------------------------------
async def lookup(domain: str, client: PoliteClient) -> dict | None:
    """Try providers in order; return first non-empty normalised dict."""
    for name, fn in (
        ("rdap", _lookup_rdap),
        ("whoxy", _lookup_whoxy),
        ("whodat", _lookup_whodat),
    ):
        raw = await fn(domain, client)
        if raw:
            return _normalise(raw, source=name)
    return None
