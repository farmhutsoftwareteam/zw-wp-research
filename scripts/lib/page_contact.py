"""Page-level contact extraction.

Lifted out of stage 10 so stages 10 and 18 share one implementation.
Extracts emails, phones, addresses, social links, schema.org JSON-LD
Person/Organization, plus a conservative person-name + email pairing
heuristic for /about + /team pages.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(
    r"(\+263[\s\-]?\d[\d\s\-]{6,}|\b0\d[\d\s\-]{7,}\b|\+?\d[\d\s\-]{8,15})"
)

# A 2–4 token Capitalised Name like "James Mutasa" — conservative to avoid
# matching UI strings ("Submit Form", "Click Here", etc.).
_NAME_RE = re.compile(
    r"^[A-Z][a-zA-Z'\-]{1,29}(?:\s[A-Z][a-zA-Z'\-]{1,29}){1,3}$"
)

_UI_BLACKLIST = {
    "click here", "submit form", "submit", "send message", "send", "send us",
    "contact us", "get in touch", "read more", "learn more", "view more",
    "home page", "follow us", "privacy policy", "terms conditions",
    "company name", "first name", "last name", "full name", "user name",
}


def _looks_like_name(s: str) -> bool:
    if not s or not s.strip():
        return False
    s = s.strip()
    if len(s) < 6 or len(s) > 60:
        return False
    if any(c.isdigit() for c in s):
        return False
    if s.lower() in _UI_BLACKLIST:
        return False
    return bool(_NAME_RE.match(s))


def _classify_social_url(href: str) -> str | None:
    if not href:
        return None
    host = (urlparse(href).hostname or "").lower()
    if not host:
        return None
    if "twitter.com" in host or "x.com" in host:
        return "twitter"
    if "facebook.com" in host or "fb.com" in host:
        return "facebook"
    if "linkedin.com" in host:
        return "linkedin"
    if "instagram.com" in host:
        return "instagram"
    return None


def extract_contact(soup, source_url: str = "") -> dict[str, list]:
    """Return {emails, phones, addresses, socials, persons} from a BeautifulSoup."""
    out: dict[str, list[Any]] = defaultdict(list)

    # 1. mailto: hrefs
    for a in soup.find_all("a", href=True):
        href = (a["href"] or "").strip()
        if href.lower().startswith("mailto:"):
            email = href[7:].split("?", 1)[0].strip()
            if email and email not in out["emails"]:
                out["emails"].append(email)
        elif href.lower().startswith("tel:"):
            phone = href[4:].strip()
            if phone and phone not in out["phones"]:
                out["phones"].append(phone)
        kind = _classify_social_url(href)
        if kind:
            out["socials"].append({"kind": kind, "url": href})

    # 2. Inline text emails + phones
    text = soup.get_text(separator=" ", strip=True)
    for m in EMAIL_RE.findall(text):
        m = m.strip(". ,")
        if m and m not in out["emails"]:
            out["emails"].append(m)
    for m in PHONE_RE.findall(text):
        m = re.sub(r"\s+", " ", m).strip()
        if m and m not in out["phones"]:
            out["phones"].append(m)

    # 3. schema.org JSON-LD (Person, Organization, LocalBusiness)
    for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = s.string or ""
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            t = node.get("@type")
            types = t if isinstance(t, list) else [t] if t else []
            types = [str(x).lower() for x in types]
            email = node.get("email")
            tel = node.get("telephone")
            name = node.get("name")
            addr = node.get("address")
            if isinstance(email, str) and email not in out["emails"]:
                out["emails"].append(email)
            if isinstance(tel, str) and tel not in out["phones"]:
                out["phones"].append(tel)
            if isinstance(addr, dict):
                parts = [addr.get("streetAddress"), addr.get("addressLocality"),
                         addr.get("addressRegion"), addr.get("addressCountry")]
                a_str = ", ".join([p for p in parts if isinstance(p, str) and p])
                if a_str and a_str not in out["addresses"]:
                    out["addresses"].append(a_str)
            if "person" in types and isinstance(name, str):
                person = {"name": name}
                if isinstance(email, str):
                    person["email"] = email
                if isinstance(node.get("jobTitle"), str):
                    person["job_title"] = node["jobTitle"]
                out["persons"].append(person)
            cp = node.get("contactPoint")
            if isinstance(cp, dict):
                e = cp.get("email")
                p = cp.get("telephone")
                if isinstance(e, str) and e not in out["emails"]:
                    out["emails"].append(e)
                if isinstance(p, str) and p not in out["phones"]:
                    out["phones"].append(p)

    # 4. Heuristic person + nearby mailto pairing
    # Find <h2>/<h3>/<h4>/<strong> elements; look at the next sibling text for emails.
    for header in soup.find_all(["h1", "h2", "h3", "h4", "strong"]):
        text = header.get_text(strip=True)
        if not _looks_like_name(text):
            continue
        # Look in the next 400 chars of text for an email or phone
        scope = ""
        node = header
        for _ in range(4):
            node = getattr(node, "find_next", lambda: None)()
            if node is None:
                break
            scope += " " + (node.get_text(separator=" ", strip=True) or "")
            if len(scope) > 600:
                break
        emails = EMAIL_RE.findall(scope)
        phones = PHONE_RE.findall(scope)
        person = {"name": text}
        if emails:
            person["email"] = emails[0]
        if phones:
            person["phone"] = phones[0]
        if "email" in person or "phone" in person:
            out["persons"].append(person)

    # de-dupe persons by (name, email)
    seen = set()
    deduped = []
    for p in out["persons"]:
        key = (p.get("name", ""), p.get("email", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    out["persons"] = deduped
    out["socials"] = list({(s["kind"], s["url"]): s for s in out["socials"]}.values())
    return dict(out)
