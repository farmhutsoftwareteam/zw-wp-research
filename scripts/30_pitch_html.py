#!/usr/bin/env python3
"""Stage 30 — Render pitch cards to HTML for local browsing.

Converts reports/pitch_cards/*.md → reports/pitch_html/*.html. Uses the
`markdown` library (with tables/fenced-code/sane-lists extensions). Adds a
small CSS so the cards look good in a browser, plus an index page sortable
by lead score.

This output is LOCAL ONLY — `.gitignore` keeps it out of the public site.
"""
from __future__ import annotations

import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import reports_dir  # noqa: E402

import markdown  # type: ignore

SRC_DIR = reports_dir() / "pitch_cards"
OUT_DIR = reports_dir() / "pitch_html"

CSS = """
*{box-sizing:border-box}
body{margin:0;font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;color:#1a1a1a;background:#fafafa}
.banner{background:#fff3e0;border-bottom:2px solid #f57c00;padding:8px 24px;font-size:12px}
.banner strong{color:#bf360c}
.wrap{max-width:880px;margin:0 auto;padding:24px}
h1{font-size:24px;margin:0 0 6px;font-weight:700}
h2{font-size:17px;margin:28px 0 10px;border-bottom:1px solid #e0e0e0;padding-bottom:4px}
h3{font-size:15px;margin:24px 0 8px}
p{margin:0 0 12px}
a{color:#21759b;text-decoration:none}
a:hover{text-decoration:underline}
code{background:#f0f0f0;padding:1px 6px;border-radius:3px;font-size:12.5px}
blockquote{margin:0;padding:10px 16px;background:#fff8e1;border-left:3px solid #f57c00;border-radius:0 4px 4px 0;color:#3e2723}
table{border-collapse:collapse;width:100%;margin:12px 0;background:#fff;border:1px solid #e0e0e0;border-radius:6px;overflow:hidden}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid #f0f0f0;font-size:13px}
th{background:#fafafa;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.03em;color:#666}
tbody tr:last-child td{border-bottom:none}
ul{margin:0 0 12px;padding-left:22px}
li{margin:3px 0}
hr{border:none;border-top:1px solid #e0e0e0;margin:28px 0}
.nav{display:flex;gap:14px;font-size:13px;margin-bottom:18px}
.nav a{padding:6px 12px;background:#fff;border:1px solid #e0e0e0;border-radius:5px}
.nav a:hover{background:#f5f5f5}
"""


import re as _re
_MD_LINK_RE = _re.compile(r'(href=")(\./)?([^"]+?)\.md(")')


def convert_one(md_path: Path, out_path: Path) -> None:
    md_text = md_path.read_text(encoding="utf-8")
    body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
    )
    # Rewrite cross-card links: ./csi.co.zw.md → csi.co.zw.html
    body = _MD_LINK_RE.sub(r'\1\3.html\4', body)
    title = md_path.stem
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)} — pitch card</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<link rel="stylesheet" href="style.css">
</head>
<body>
<div class="banner"><strong>PRIVATE — internal pitch card.</strong> Contains personal contact data; do not share.</div>
<div class="wrap">
<div class="nav"><a href="index.html">← back to index</a></div>
{body}
</div>
</body></html>
"""
    out_path.write_text(page, encoding="utf-8")


def build() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "style.css").write_text(CSS, encoding="utf-8")
    n = 0
    for src in sorted(SRC_DIR.glob("*.md")):
        out = OUT_DIR / f"{src.stem}.html"
        convert_one(src, out)
        n += 1
    print(f"[pitch-html] wrote {n} pages to {OUT_DIR}", file=sys.stderr)
    return n


def main() -> int:
    if not SRC_DIR.exists():
        print(f"[pitch-html] source missing: {SRC_DIR}", file=sys.stderr)
        print("[pitch-html] run scripts/29_pitch_cards.py first", file=sys.stderr)
        return 1
    build()
    return 0


if __name__ == "__main__":
    sys.exit(main())
