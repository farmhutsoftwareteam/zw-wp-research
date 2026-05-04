# zw-wp-research

> Find and rank the top WordPress sites in Zimbabwe — multi-day, multi-agent research pipeline.

## The idea

There is no single source for "which Zimbabwean websites run WordPress, and which are the most important." This project builds one. The output is a ranked, categorized, verified list — useful for market research, security/audit work, agency lead-gen, or just curiosity about the local web.

The project is **not** a one-script crawl. It's a pipeline of small, single-purpose stages that each write to disk, so any stage can be re-run without redoing the others, and stages can fan out across parallel workers (or Claude subagents) without coordination.

## What we're solving

Two problems chained together:

1. **Discovery** — finding Zimbabwean domains at all. The `.zw` registry is partial (zone files aren't fully public), and many local sites use `.com` / `.africa` instead. We have to combine several sources.
2. **Detection** — deciding which of those domains run WordPress. Cloudflare hides server fingerprints, so detection has to work from rendered HTML and a handful of probe paths, not headers alone.

Once both problems are solved per-domain, we enrich (traffic rank, category) and verify (visual + plugin/theme fingerprint) the top results.

## The pipeline

```
seeds → live → detections → enriched → classified → verified → report.md → zwwp.db → site/
 01     02       03            04         05           06         07          08         09
```

Each arrow is a script. Each `*.jsonl` between stages 01–06 is the contract between stages — append-only, one record per line, idempotent. Stages 07–09 produce *derived* artifacts (markdown, SQLite, static site); they're rebuilt from scratch every run.

### Stages

| # | Script | Job | Input | Output |
|---|---|---|---|---|
| 01 | `01_seed_harvester.py` | Pull candidate domains from every free source | (sources) | `data/seeds.jsonl` |
| 02 | `02_dns_resolver.py` | DNS-resolve each candidate, keep only live ones, capture IP/CDN | `seeds.jsonl` | `data/live.jsonl` |
| 03 | `03_wp_detector.py` | Fetch homepage + 4 probe paths, score WordPress signals (0–100) | `live.jsonl` | `data/detections.jsonl` |
| 04 | `04_traffic_enricher.py` | Tranco rank + (optional) Cloudflare Radar bucket | `detections.jsonl` (score ≥ 70) | `data/enriched.jsonl` |
| 05 | `05_categorizer.py` | Claude Haiku (via `claude -p`) classifies into 9 categories | `enriched.jsonl` | `data/classified.jsonl` |
| 06 | `06_verifier.py` | Playwright renders top N, screenshots, fingerprints theme/plugins | `classified.jsonl` top N | `data/verified.jsonl` |
| 07 | `07_reporter.py` | Final ranked markdown + CSV (+ optional Claude prose) | `verified.jsonl` | `reports/report.md`, `reports/top_zw_wordpress.csv` |
| 08 | `08_indexer.py` | Build SQLite query layer from all JSONL files | `data/*.jsonl` | `reports/zwwp.db` |
| 09 | `09_site_builder.py` | Generate static HTML+JS site from SQLite | `reports/zwwp.db` | `reports/site/` |

A small Makefile chains them: `make all` runs 01→09; `make detect` runs through stage 03; `make site` builds the browseable directory; `make serve` opens it locally; `make clean` wipes generated data.

## Free path (Mac + Claude Max)

This pipeline is designed to run end-to-end at **zero out-of-pocket cost** on a Mac with an existing Claude Max subscription:

- **Compute & egress** — your Mac and home internet. No cloud VM, no AWS egress.
- **LLM (stages 05, 07)** — `lib/claude_cli.py` shells out to the `claude` CLI with `-p` headless mode. It uses your Max-plan auth (no `ANTHROPIC_API_KEY`). Default model: Claude Haiku 4.5 — lightest on Max usage caps and plenty good for 9-bucket categorization.
- **Pacing** — `CLAUDE_RPS` env var (default `0.5`, one call every 2 seconds) keeps stage 05 well under Max plan rate limits. Resumable: if you hit a cap, Ctrl-C and re-run; it skips already-classified domains.
- **Sleep prevention** — long stages should be wrapped: `make detect-awake`, `make all-awake`, etc. The pattern rule `%-awake` runs the underlying target via `caffeinate -i` so the laptop doesn't sleep.
- **DNS** — points at `1.1.1.1` and `8.8.8.8` directly (in `lib/dns_utils.py`) so the resolver doesn't get rate-limited by the ISP at high concurrency.
- **Idempotency** — every JSONL stage skips records already present in its output. Safe to Ctrl-C and re-run any stage at any time.

## Storage layout

| Layer | Where | What | Built by |
|---|---|---|---|
| Pipeline (source of truth) | `data/*.jsonl` | Append-only, one record per line | Stages 01–06 |
| Query layer | `reports/zwwp.db` | SQLite — `domains`, `signals`, `plugins`, `seeds`, FTS5 | Stage 08 |
| Binary assets | `reports/screenshots/` | One PNG per verified site | Stage 06 |
| Markdown report | `reports/report.md` | Narrative summary + per-category top-10s | Stage 07 |
| Static site | `reports/site/` | HTML + vanilla JS, browseable directory | Stage 09 |

`reports/zwwp.db` is single-file SQLite — point Datasette at it for ad-hoc SQL exploration if you want.

## Browsing the results

After `make all` (or `make site` if earlier stages already ran):

```bash
make serve     # python -m http.server on reports/site/
open http://localhost:8000
```

The site is fully static — same files deploy to Cloudflare Pages, GitHub Pages, or Netlify with no extra build step.

## Discovery sources (stage 01)

We gather domains from as many independent sources as possible, then dedupe. No single source covers everything.

- **`.zw` second-level domains** — `.co.zw`, `.org.zw`, `.ac.zw`, `.gov.zw`, `.web.zw`. Sources: ZICTA registry where available; scraping registrar listing pages (Webdev, ZISPA-affiliated registrars).
- **Public top-site lists** — Tranco (top 1M, filterable by TLD), Cloudflare Radar (top sites by country), Majestic Million.
- **HTTP Archive (BigQuery)** — already runs WP detection on the top millions of sites; just `WHERE country_code = "ZW"`. This may give us the bulk of stage 03 for free if it's recent.
- **BuiltWith / Wappalyzer (paid APIs)** — direct CMS info; rate-limited; budget-dependent.
- **Common Crawl CDX** — bulk index, filterable by host suffix.
- **Curated scrapes** — `techzim.co.zw` link directories, `pindula.co.zw` business listings, government department pages on `.gov.zw` (which often link out to other ministries).
- **Geo-clue sweep** — domains under `.com` / `.africa` that look Zimbabwean (mention ZWL/ZAR pricing, Zim phone numbers `+263`, `addressCountry: ZW` in schema.org).

## Detection signals (stage 03)

Confidence score from these, weighted highest first:

1. `<meta name="generator" content="WordPress X.Y">` — definitive
2. `/wp-json/` returns JSON with WP API shape
3. `Link: <…/wp-json/>; rel="https://api.w.org/"` HTTP header
4. `/wp-content/`, `/wp-includes/` paths in any asset URL
5. `/feed/` returns WP-flavored RSS (specific generator)
6. `/wp-login.php` or `/xmlrpc.php` reachable
7. Theme/plugin paths in `<link>` and `<script>` tags
8. Common WP body classes (`wp-singular`, `wp-block-*`)

Score ≥ 70 = strong WP signal. Score 30–70 = ambiguous (heavily customized WP, headless WP, or wp-clone CMS). Score < 30 = not WordPress.

## Enrichment & ranking (stages 04–05)

For each detected WP site:

- **Traffic rank** — Tranco rank, Cloudflare Radar bucket, similarweb estimate (use whichever is cheapest to obtain).
- **Category** — news, government, business, blog, NGO, e-commerce, education, religious, other. LLM-classified from title + meta description + visible nav links.
- **Sector tags** — finance, telecom, real estate, etc. (optional, only for top tier).

## Verification (stage 06)

Top 100 by traffic rank get a deeper pass:

- Playwright render at 1440×900, screenshot to `data/screenshots/`.
- Plugin/theme fingerprinting: parse all asset URLs, identify named plugins (`/wp-content/plugins/<name>/…`) and active theme.
- Confirmation that the homepage actually loaded (status 200, body > 5KB) — eliminates parked domains.

## Output (stage 07)

- `reports/report.md` — narrative writeup with top sites by category, screenshots, plugin trends.
- `reports/top_zw_wordpress.csv` — flat data file: domain, score, rank, category, plugins, theme.
- (Day 5+ stretch) static site or web view — see Future work.

## Day-by-day plan

| Day | Goal | Output |
|---|---|---|
| 1 | Seed harvest from all sources | ~5–20k candidate `.zw` + Zim-clue domains |
| 2 | DNS pass + WP detection (parallel shards) | Ranked detection scores for all live domains |
| 3 | Traffic enrichment + LLM categorization | Top 200 with metadata |
| 4 | Playwright verification of top 100 (screenshots, plugin/theme fingerprint) | `verified.jsonl` |
| 5 | Final report.md + CSV | Shareable artifact |

Stretch (later):
- Static site at `zwwp.report` or similar
- Periodic re-runs (monthly cron) so the list stays fresh
- Compare-over-time view (which sites churned off WP, which migrated onto it)

## Multi-agent orchestration

Inside Claude Code, stages can fan out:

- **Stage 01 (seed harvest)** — one subagent per source (registry, BuiltWith, Tranco, Common Crawl, scrapes). Run in parallel, merge outputs.
- **Stage 03 (WP detection)** — shard the input file (`--shard 0/8`, `--shard 1/8`, …) and run 8 detector workers in parallel. Each is a Bash subprocess, not a Claude subagent — pure HTTP.
- **Stage 05 (categorization)** — batched LLM calls, but the orchestration agent verifies categories by spot-checking ~10% manually.
- **Stage 06 (verification)** — Playwright in parallel up to ~5 workers (browser memory cost).
- **Stage 07 (reporter)** — one synthesizing agent reads `verified.jsonl` and writes the markdown.

Each stage is **resumable** because every stage's output is a JSONL file. Re-running a stage skips records already present in the output (look up by domain).

## Gotchas to design for upfront

1. **Cloudflare hides headers.** Detection must work from rendered HTML alone. Don't trust `Server:` or `X-Powered-By:` — they're stripped.
2. **Many Zimbabwean sites aren't on `.zw`.** They use `.com` / `.africa` for SEO. The geo-clue sweep is essential.
3. **Some WP sites are heavily customized** and don't expose `/wp-json/`. Hence the ≥ 7 weighted signals — no single signal is required.
4. **Robots.txt + rate limiting.** Respect `robots.txt`. Budget: max 1 req/sec/host, 4 probe paths/host. Total 5 requests per detected site is fine; cap on per-IP queries-per-second to be a good citizen.
5. **Parked / for-sale domains** show up in registries with placeholder pages. Strip these in stage 02 (status, body length, content hash against known parking templates).
6. **WP-clones (Drupal, Ghost, Joomla, headless)** can match a few signals but not all. Multi-signal scoring with a clear threshold prevents false positives.

## Open questions (decide before/while building)

- Budget for paid sources? BuiltWith ~$295/mo for the API tier we'd need; similarweb usage-priced; Cloudflare Radar is free for our scale; HTTP Archive is free via BigQuery (BQ usage cost only).
- Self-host LLM categorization with Ollama, or pay Claude API for stage 05? Volume is small (~200–2000 calls), so Claude is cheap and gives better category coverage.
- Storage backend for the final report — flat files only (this repo), or write to a Supabase/Postgres so the "ZW WP top sites" data is queryable and updatable?
- Public release of the dataset? Some sites (especially `.gov.zw`) may not appreciate being inventoried. Consider redacting tier 4+ from any public output.

## Repo layout

```
zw-wp-research/
├── README.md                 # this file — single source of truth
├── .env.example              # required API keys
├── .gitignore
├── Makefile                  # `make all` runs the whole pipeline
├── requirements.txt          # Python deps (httpx, dnspython, playwright, anthropic)
├── scripts/
│   ├── 01_seed_harvester.py
│   ├── 02_dns_resolver.py
│   ├── 03_wp_detector.py
│   ├── 04_traffic_enricher.py
│   ├── 05_categorizer.py
│   ├── 06_verifier.py
│   └── 07_reporter.py
├── data/                     # generated, gitignored
│   ├── seeds.jsonl
│   ├── live.jsonl
│   ├── detections.jsonl
│   ├── enriched.jsonl
│   ├── classified.jsonl
│   └── verified.jsonl
└── reports/                  # generated, gitignored except final report
    ├── report.md
    ├── top_zw_wordpress.csv
    └── screenshots/
```

## License

MIT (assumed, to be confirmed before publishing the dataset).
