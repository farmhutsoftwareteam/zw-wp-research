#!/usr/bin/env python3
"""Stage 08 — Indexer.

Reads the JSONL files in data/ and builds a single SQLite database at
reports/zwwp.db that the static site (stage 09) and ad-hoc SQL queries can use.

The DB is a derived artifact: dropped and recreated on every run.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import data_dir, reports_dir  # noqa: E402
from lib.jsonl import iter_records  # noqa: E402

SEEDS_PATH = data_dir() / "seeds.jsonl"
LIVE_PATH = data_dir() / "live.jsonl"
DETECTIONS_PATH = data_dir() / "detections.jsonl"
ENRICHED_PATH = data_dir() / "enriched.jsonl"
CLASSIFIED_PATH = data_dir() / "classified.jsonl"
VERIFIED_PATH = data_dir() / "verified.jsonl"
PANELS_PATH = data_dir() / "panels.jsonl"
DEFAULT_DB = reports_dir() / "zwwp.db"


SCHEMA = """
DROP TABLE IF EXISTS domains;
DROP TABLE IF EXISTS signals;
DROP TABLE IF EXISTS plugins;
DROP TABLE IF EXISTS seeds;
DROP TABLE IF EXISTS sector_tags;
DROP TABLE IF EXISTS domains_fts;

CREATE TABLE domains (
    domain TEXT PRIMARY KEY,
    ip TEXT,
    cdn TEXT,
    score INTEGER,
    wp_version TEXT,
    homepage_status INTEGER,
    homepage_kb INTEGER,
    tranco_rank INTEGER,
    cf_radar_bucket TEXT,
    category TEXT,
    category_confidence REAL,
    theme TEXT,
    plugin_count INTEGER,
    screenshot TEXT,
    scheme_used TEXT,
    host_panel TEXT,
    server_header TEXT,
    cert_issuer TEXT,
    reverse_ptr TEXT,
    panel_evidence TEXT,
    verified_at TEXT,
    classified_at TEXT,
    enriched_at TEXT,
    checked_at TEXT,
    resolved_at TEXT
);
CREATE INDEX idx_domains_score ON domains(score);
CREATE INDEX idx_domains_rank ON domains(tranco_rank);
CREATE INDEX idx_domains_category ON domains(category);
CREATE INDEX idx_domains_theme ON domains(theme);
CREATE INDEX idx_domains_cdn ON domains(cdn);
CREATE INDEX idx_domains_panel ON domains(host_panel);

CREATE TABLE signals (
    domain TEXT,
    signal TEXT,
    weight INTEGER,
    PRIMARY KEY (domain, signal)
);

CREATE TABLE plugins (
    domain TEXT,
    plugin TEXT,
    PRIMARY KEY (domain, plugin)
);
CREATE INDEX idx_plugins_plugin ON plugins(plugin);

CREATE TABLE sector_tags (
    domain TEXT,
    tag TEXT,
    PRIMARY KEY (domain, tag)
);
CREATE INDEX idx_sector_tag ON sector_tags(tag);

CREATE TABLE seeds (
    domain TEXT,
    source TEXT,
    hint TEXT,
    PRIMARY KEY (domain, source)
);
CREATE INDEX idx_seeds_source ON seeds(source);

CREATE VIRTUAL TABLE domains_fts USING fts5(
    domain, category, sector_tags, theme, content=''
);

CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


SIGNAL_WEIGHTS = {
    "meta_generator_wp": 30,
    "wp_json_valid": 25,
    "link_header_wp_api": 20,
    "wp_content_path": 15,
    "wp_includes_path": 15,
    "rss_wp_generator": 10,
    "wp_login_200": 10,
    "theme_path": 5,
    "plugin_path": 5,
    "readme_html_wp": 8,
    "xmlrpc_present": 5,
    "wp_body_class": 5,
}


def build(db_path: Path) -> dict[str, int]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    counts = {
        "seeds": 0,
        "domains": 0,
        "signals": 0,
        "plugins": 0,
        "sector_tags": 0,
    }

    # seeds — full provenance
    with conn:
        for rec in iter_records(SEEDS_PATH):
            d = rec.get("domain")
            src = rec.get("source")
            if not isinstance(d, str) or not isinstance(src, str):
                continue
            conn.execute(
                "INSERT OR IGNORE INTO seeds(domain, source, hint) VALUES (?, ?, ?)",
                (d, src, rec.get("hint")),
            )
            counts["seeds"] += 1

    # Build by-domain merged view from each pipeline file.
    live = {r["domain"]: r for r in iter_records(LIVE_PATH) if r.get("domain")}
    detections = {r["domain"]: r for r in iter_records(DETECTIONS_PATH) if r.get("domain")}
    enriched = {r["domain"]: r for r in iter_records(ENRICHED_PATH) if r.get("domain")}
    classified = {r["domain"]: r for r in iter_records(CLASSIFIED_PATH) if r.get("domain")}
    verified = {r["domain"]: r for r in iter_records(VERIFIED_PATH) if r.get("domain")}
    panels = {r["domain"]: r for r in iter_records(PANELS_PATH) if r.get("domain")}

    all_domains = set()
    all_domains.update(detections.keys())  # only WP-relevant set; live is too broad
    all_domains.update(verified.keys())

    with conn:
        for d in sorted(all_domains):
            lv = live.get(d, {})
            de = detections.get(d, {})
            en = enriched.get(d, {})
            cl = classified.get(d, {})
            vf = verified.get(d, {})
            pn = panels.get(d, {})
            plugins_list = vf.get("plugins") or []
            sector_tags = cl.get("sector_tags") or []
            evidence = pn.get("evidence") or {}
            evidence_str = json.dumps(evidence) if evidence else None
            conn.execute(
                """INSERT INTO domains(
                    domain, ip, cdn, score, wp_version, homepage_status, homepage_kb,
                    tranco_rank, cf_radar_bucket, category, category_confidence, theme,
                    plugin_count, screenshot, scheme_used, host_panel, server_header,
                    cert_issuer, reverse_ptr, panel_evidence, verified_at,
                    classified_at, enriched_at, checked_at, resolved_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    d,
                    lv.get("ip"),
                    lv.get("cdn"),
                    de.get("score"),
                    de.get("wp_version"),
                    de.get("homepage_status"),
                    vf.get("homepage_kb"),
                    en.get("tranco_rank"),
                    en.get("cf_radar_bucket"),
                    cl.get("category"),
                    cl.get("category_confidence"),
                    vf.get("theme"),
                    len(plugins_list),
                    vf.get("screenshot"),
                    vf.get("scheme_used") or de.get("scheme"),
                    pn.get("host_panel"),
                    pn.get("server_header"),
                    pn.get("cert_issuer"),
                    pn.get("reverse_ptr"),
                    evidence_str,
                    vf.get("verified_at"),
                    cl.get("classified_at"),
                    en.get("enriched_at"),
                    de.get("checked_at"),
                    lv.get("resolved_at"),
                ),
            )
            counts["domains"] += 1

            signals = de.get("signals") or {}
            for name, present in signals.items():
                if present:
                    conn.execute(
                        "INSERT OR IGNORE INTO signals(domain, signal, weight) VALUES (?, ?, ?)",
                        (d, name, SIGNAL_WEIGHTS.get(name, 0)),
                    )
                    counts["signals"] += 1

            for pl in plugins_list:
                conn.execute(
                    "INSERT OR IGNORE INTO plugins(domain, plugin) VALUES (?, ?)",
                    (d, pl),
                )
                counts["plugins"] += 1

            for tag in sector_tags:
                if isinstance(tag, str):
                    conn.execute(
                        "INSERT OR IGNORE INTO sector_tags(domain, tag) VALUES (?, ?)",
                        (d, tag),
                    )
                    counts["sector_tags"] += 1

            conn.execute(
                "INSERT INTO domains_fts(rowid, domain, category, sector_tags, theme) "
                "VALUES ((SELECT rowid FROM domains WHERE domain = ?), ?, ?, ?, ?)",
                (d, d, cl.get("category") or "", " ".join(sector_tags or []), vf.get("theme") or ""),
            )

        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('generated_at', datetime('now'))"
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('counts', ?)",
            (json.dumps(counts),),
        )

    conn.close()
    return counts


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-path", default=str(DEFAULT_DB))
    args = p.parse_args()
    db_path = Path(args.db_path)
    counts = build(db_path)
    print(f"[index] built {db_path}", file=sys.stderr)
    for k, v in counts.items():
        print(f"  {k}: {v}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
