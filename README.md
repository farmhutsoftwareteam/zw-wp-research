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
seeds.jsonl ──► live.jsonl ──► detections.jsonl ──► enriched.jsonl ──► classified.jsonl ──► verified.jsonl ──► report.md
   01              02                03                  04                   05                   06              07
```

Each arrow is a script. Each `*.jsonl` is the contract between stages — append-only, one record per line, idempotent.

### Stages

| # | Script | Job | Input | Output |
|---|---|---|---|---|
| 01 | `seed_harvester.py` | Pull candidate domains from every source we have | (sources) | `data/seeds.jsonl` |
| 02 | `dns_resolver.py` | DNS-resolve each candidate, keep only live ones, capture IP/CDN | `seeds.jsonl` | `data/live.jsonl` |
| 03 | `wp_detector.py` | Fetch homepage + 4 probe paths, score WordPress signals (0–100) | `live.jsonl` | `data/detections.jsonl` |
| 04 | `traffic_enricher.py` | Cross-reference Tranco / Cloudflare Radar / similarweb estimates | `detections.jsonl` (score ≥ 70) | `data/enriched.jsonl` |
| 05 | `categorizer.py` | LLM-classify (news, gov, business, blog, NGO, e-commerce) | `enriched.jsonl` | `data/classified.jsonl` |
| 06 | `verifier.py` | Playwright render top N, screenshot, fingerprint themes/plugins | `classified.jsonl` top N | `data/verified.jsonl` |
| 07 | `reporter.py` | Build final ranked markdown + CSV + (later) static site | `verified.jsonl` | `reports/report.md`, `reports/top_zw_wordpress.csv` |

A small Makefile chains them: `make all` runs the whole pipeline; `make detect` runs from stage 03 onward; `make clean` wipes `data/`.

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
