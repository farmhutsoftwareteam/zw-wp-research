"""Environment-driven config loader."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv  # python-dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_args, **_kwargs):  # type: ignore[no-redef]
        return False


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_env() -> None:
    """Load .env from repo root if present. Idempotent."""
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)


@dataclass(frozen=True)
class Config:
    user_agent: str
    rps_per_host: float
    timeout: float
    tranco_csv_path: Path
    cf_radar_token: str | None
    google_cloud_project: str | None
    claude_rps: float
    anthropic_model: str
    repo_root: Path = REPO_ROOT

    @classmethod
    def from_env(cls) -> "Config":
        load_env()
        return cls(
            user_agent=os.getenv(
                "HTTP_USER_AGENT",
                "zw-wp-research/0.1 (research; +https://github.com/farmhutsoftwareteam/zw-wp-research)",
            ),
            rps_per_host=float(os.getenv("HTTP_RPS_PER_HOST", "1")),
            timeout=float(os.getenv("HTTP_TIMEOUT_SECONDS", "15")),
            tranco_csv_path=Path(
                os.getenv("TRANCO_CSV_PATH", str(REPO_ROOT / "data" / "tranco_latest.csv"))
            ),
            cf_radar_token=os.getenv("CLOUDFLARE_API_TOKEN") or None,
            google_cloud_project=os.getenv("GOOGLE_CLOUD_PROJECT") or None,
            claude_rps=float(os.getenv("CLAUDE_RPS", "0.5")),
            anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5"),
        )


def data_dir() -> Path:
    d = REPO_ROOT / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def reports_dir() -> Path:
    d = REPO_ROOT / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d
