"""Free email-finder API rotation.

One Provider class per service; FinderRotator picks the next provider by
remaining-quota. State persisted in `data/finder_quota.json` so re-runs
across days respect the monthly free-tier ceilings.

By design, the entire module no-ops cleanly when no keys are configured.
The downstream stage 20 just gets back empty results.

Free-tier sources:
  - Hunter.io        50 lookups / month     https://hunter.io/api
  - Apollo.io        50 lookups / month     https://docs.apollo.io/reference
  - Snov.io          50 lookups / month     https://app.snov.io/api
  - Tomba.io         variable / month       https://app.tomba.io/api
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .config import Config
from .http import PoliteClient


QUOTA_FILE = Path(__file__).resolve().parents[2] / "data" / "finder_quota.json"


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _load_quota_state() -> dict:
    if not QUOTA_FILE.exists():
        return {}
    try:
        with QUOTA_FILE.open() as f:
            return json.load(f)
    except Exception:
        return {}


def _save_quota_state(state: dict) -> None:
    QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with QUOTA_FILE.open("w") as f:
        json.dump(state, f, indent=2)


class Provider(Protocol):
    name: str
    monthly_quota: int

    def is_configured(self) -> bool: ...
    async def find_by_domain(self, domain: str, client: PoliteClient) -> list[dict]: ...


# ---- Hunter ------------------------------------------------------------
@dataclass
class HunterProvider:
    name: str = "hunter"
    monthly_quota: int = 50

    def is_configured(self) -> bool:
        return bool(os.environ.get("HUNTER_API_KEY"))

    async def find_by_domain(self, domain: str, client: PoliteClient) -> list[dict]:
        key = os.environ.get("HUNTER_API_KEY")
        if not key:
            return []
        url = f"https://api.hunter.io/v2/domain-search?domain={domain}&api_key={key}&limit=10"
        resp = await client.get(url)
        if resp is None or resp.status_code != 200:
            return []
        try:
            data = resp.json()
        except Exception:
            return []
        emails = (data.get("data") or {}).get("emails") or []
        return [
            {
                "email": e.get("value"),
                "first_name": e.get("first_name"),
                "last_name": e.get("last_name"),
                "position": e.get("position"),
                "confidence": float(e.get("confidence") or 50) / 100,
                "source_provider": "hunter",
                "verified": (e.get("verification") or {}).get("status") == "valid",
            }
            for e in emails
            if e.get("value")
        ]


# ---- Apollo ------------------------------------------------------------
@dataclass
class ApolloProvider:
    name: str = "apollo"
    monthly_quota: int = 50

    def is_configured(self) -> bool:
        return bool(os.environ.get("APOLLO_API_KEY"))

    async def find_by_domain(self, domain: str, client: PoliteClient) -> list[dict]:
        key = os.environ.get("APOLLO_API_KEY")
        if not key:
            return []
        url = "https://api.apollo.io/v1/mixed_people/search"
        payload = {"q_organization_domains": domain, "page": 1, "per_page": 10}
        try:
            resp = await client.client.post(
                url,
                headers={"X-Api-Key": key, "Content-Type": "application/json"},
                json=payload,
                timeout=30,
            )
        except Exception:
            return []
        if resp.status_code != 200:
            return []
        try:
            data = resp.json()
        except Exception:
            return []
        people = data.get("people") or []
        out = []
        for p in people:
            email = p.get("email") or p.get("email_status_verified_email")
            if not email:
                continue
            out.append({
                "email": email,
                "first_name": p.get("first_name"),
                "last_name": p.get("last_name"),
                "position": p.get("title"),
                "confidence": 0.6,
                "source_provider": "apollo",
                "verified": False,
            })
        return out


# ---- Snov.io -----------------------------------------------------------
@dataclass
class SnovProvider:
    name: str = "snov"
    monthly_quota: int = 50
    _token: str | None = None

    def is_configured(self) -> bool:
        return bool(os.environ.get("SNOV_CLIENT_ID")
                    and os.environ.get("SNOV_CLIENT_SECRET"))

    async def _get_token(self, client: PoliteClient) -> str | None:
        if self._token:
            return self._token
        cid = os.environ.get("SNOV_CLIENT_ID")
        cs = os.environ.get("SNOV_CLIENT_SECRET")
        if not (cid and cs):
            return None
        try:
            r = await client.client.post(
                "https://api.snov.io/v1/oauth/access_token",
                data={"grant_type": "client_credentials",
                      "client_id": cid, "client_secret": cs},
                timeout=15,
            )
            if r.status_code == 200:
                self._token = r.json().get("access_token")
                return self._token
        except Exception:
            return None
        return None

    async def find_by_domain(self, domain: str, client: PoliteClient) -> list[dict]:
        token = await self._get_token(client)
        if not token:
            return []
        try:
            r = await client.client.post(
                "https://api.snov.io/v1/get-domain-emails-with-info",
                headers={"Authorization": f"Bearer {token}"},
                data={"domain": domain, "type": "all", "limit": 10},
                timeout=30,
            )
        except Exception:
            return []
        if r.status_code != 200:
            return []
        try:
            data = r.json()
        except Exception:
            return []
        emails = data.get("emails") or []
        return [
            {
                "email": e.get("email"),
                "first_name": e.get("firstName"),
                "last_name": e.get("lastName"),
                "position": e.get("position"),
                "confidence": 0.55,
                "source_provider": "snov",
                "verified": e.get("emailStatus") == "valid",
            }
            for e in emails
            if e.get("email")
        ]


# ---- Tomba.io ----------------------------------------------------------
@dataclass
class TombaProvider:
    name: str = "tomba"
    monthly_quota: int = 50

    def is_configured(self) -> bool:
        return bool(os.environ.get("TOMBA_API_KEY")
                    and os.environ.get("TOMBA_API_SECRET"))

    async def find_by_domain(self, domain: str, client: PoliteClient) -> list[dict]:
        key = os.environ.get("TOMBA_API_KEY")
        secret = os.environ.get("TOMBA_API_SECRET")
        if not (key and secret):
            return []
        url = f"https://api.tomba.io/v1/domain-search?domain={domain}"
        try:
            r = await client.client.get(
                url,
                headers={"X-Tomba-Key": key, "X-Tomba-Secret": secret},
                timeout=20,
            )
        except Exception:
            return []
        if r.status_code != 200:
            return []
        try:
            data = r.json()
        except Exception:
            return []
        emails = ((data.get("data") or {}).get("emails")) or []
        return [
            {
                "email": e.get("email"),
                "first_name": e.get("first_name"),
                "last_name": e.get("last_name"),
                "position": e.get("position"),
                "confidence": 0.6,
                "source_provider": "tomba",
                "verified": e.get("verified") is True,
            }
            for e in emails
            if e.get("email")
        ]


# ---- Rotator -----------------------------------------------------------
DEFAULT_PROVIDERS: list[Provider] = [
    HunterProvider(), ApolloProvider(), SnovProvider(), TombaProvider(),
]


class FinderRotator:
    """Try providers in remaining-quota order; persist usage across runs."""

    def __init__(self, providers: list[Provider] | None = None):
        self.providers = providers if providers is not None else list(DEFAULT_PROVIDERS)
        self.state = _load_quota_state()

    def configured(self) -> list[Provider]:
        return [p for p in self.providers if p.is_configured()]

    def _remaining(self, provider: Provider) -> int:
        month = _current_month()
        s = self.state.get(provider.name) or {}
        if s.get("month") != month:
            return provider.monthly_quota
        return max(0, provider.monthly_quota - int(s.get("used", 0)))

    def _consume(self, provider: Provider, n: int = 1) -> None:
        month = _current_month()
        s = self.state.get(provider.name) or {}
        if s.get("month") != month:
            s = {"month": month, "used": 0}
        s["used"] = int(s.get("used", 0)) + n
        self.state[provider.name] = s
        _save_quota_state(self.state)

    async def find(self, domain: str, client: PoliteClient) -> list[dict]:
        candidates = sorted(
            self.configured(),
            key=lambda p: -self._remaining(p),
        )
        for p in candidates:
            if self._remaining(p) <= 0:
                continue
            try:
                results = await p.find_by_domain(domain, client)
            except Exception:
                results = []
            self._consume(p, 1)
            if results:
                return results
        return []
