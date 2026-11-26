# Bronx — Cloudflare web app

Web edition of ICE Detention Pathway. It runs on **Cloudflare Workers Free + R2 Free**: no container, no paid plan, no always-on server, no public API.

- Production: <https://bronx.remypicciano.me> — untouched two-column workbench.
- Staging: <https://bronx-staging.remypicciano.me> — one centered bar that reveals a flat film ledger after lookup.

## How it works

The free Workers plan cannot run Python or DuckDB, so lookup data is prepared locally.

1. `website/offline/sources.py` reads the root `data-sources.json`, the same URL file the desktop app uses for **Download / Update Data…**, and refreshes the three required Parquet files when they are missing, older than seven days, or their URLs changed since the last build.
2. `website/offline/build_index.py` streams each release through the unchanged desktop engine and writes gzipped JSON shards, one per 3-hex identifier prefix.
3. `website/offline/upload_index.py` uploads those shards to R2.
4. `website/frontend/worker/index.ts` hashes an identifier to one shard, reads that one object from R2, and renders the output with `website/frontend/worker/format.ts`.

The Worker never touches Parquet files. `manifest.json` holds release names and watermarks; `demo.json` holds the fabricated demo. Bulk runs are temporary R2 objects at `bulk/<token>.json`.

Arrest-location entry stays off the web form. Every other desktop behavior is present: identifier / `stay_ID` / `stint_ID` search, manual entries with `[MANUAL_EDIT original=…]` tagging, bulk search, copy, source-only JSON export, browser-local history, update check, demo, and watermarks.

## Repository layout

| Path | Purpose |
| --- | --- |
| `offline/sources.py` | Fetches current Parquet files from `data-sources.json` |
| `offline/datasets.py` | Release discovery, watermarks, demo, web field policy |
| `offline/build_index.py` | Builds gzipped R2 shards and `manifest.json` |
| `offline/upload_index.py` | Uploads a build to an R2 bucket |
| `frontend/worker/index.ts` | Routes, form parsing, responses |
| `frontend/worker/store.ts` | R2 reads, manifest cache, shard lookup |
| `frontend/worker/pages.ts` | Server-rendered HTML pages |
| `frontend/worker/format.ts` | Byte-identical TypeScript port of the Python formatter |
| `frontend/static/` | CSS and browser JS |
| `wrangler.toml` | Worker entry, assets, routes, R2 bindings, staging env |
| `requirements-build.txt` | Python packages for build/upload |

Run Python commands from the project root with `python -m website.offline.<module>`.

## Staging and production views

Staging opens on one centered bar on a near-black hairline grid. No header, navigation, Table tab, heading, release selector, pathway, or footer appears in that first view. The bar accepts a `unique_identifier`, `stay_ID`, or `stint_ID`; its placeholder names all three.

A lookup reveals the ledger: top bar with **Search**, **Table**, Bulk search, History, Datasets, and Help; the editable pathway on hairline rules; a collapsed **Dataset** disclosure; and the compact action row. The reveal is stored in browser `localStorage` (`bronx.staging.revealed.v1`), and a return link with `?q=` or a server-rendered result also opens in the revealed state. Clearing browser data restores the single bar.

There is one staging direction: flat film ledger. Grey and near-black carry the field; cyan marks focus only. No style switcher, no bezel or glow, no cards, no rounded surfaces. Legacy `?style=` values and retired aliases resolve to the same direction; the parameter is no longer written into links or forms. Production ignores it and keeps the original Record workbench.

Manual entries, copy, export, update check, demo, and bulk search remain available after reveal. `location` and `landmark` stay out of the manual-entry form.

Staging lookup is in place. `POST /api/lookup` returns the rendered pathway as JSON; the page updates status, pathway, history, and form state without reloading. Repeated queries reuse a browser session cache keyed by dataset build, release, identifier, and manual overrides. The Worker keeps recently read shards warm for five minutes. Production keeps full-page search.

The staging-only `GET /table?q=…` route renders one section per stay: arrest, entry, release, and summary facts followed by a row per stint with stint ID, book-in, facility, and book-out. Edited fields keep their `[MANUAL_EDIT original=…]` tag; a missing book-out is `UNKNOWN`, not a claim of current detention. The Table tab is a normal server-rendered page; it does not replace or recache the in-place Search result.

Search results are copied to the clipboard automatically; **Copy** also sits in the output head and the action row. Favicon comes from the desktop app's existing ice-cube icon.

`GET /datasets` shows the release table on production and the status plus rebuild runbook on staging. Retired staging prototype paths (`/guided`, `/workbench`, `/dataset`) redirect to the one-panel search or `/datasets`.

## One-time Cloudflare setup

```bash
npx wrangler login
npx wrangler r2 bucket create bronx-data
npx wrangler r2 bucket create bronx-data-staging
cf auth login
export CLOUDFLARE_ACCOUNT_ID='a430aaf38399e167fe94b8659f4c1b8a'
```

`wrangler.toml` declares both routes. Both hostnames also need proxied DNS records in zone `remypicciano.me`: `A 192.0.2.1`, proxied, for `bronx` and `bronx-staging`.

Optional S3 upload instead of the signed-in `cf` token:

```bash
export R2_ACCOUNT_ID='…'
export R2_ACCESS_KEY_ID='…'
export R2_SECRET_ACCESS_KEY='…'
.venv/bin/python -m website.offline.upload_index --auth s3 --bucket bronx-data
```

## Build and upload the index

Requires Python 3.12+ and `data-sources.json` in the project root. The build downloads the datasets itself.

```bash
python -m venv .venv
.venv/bin/python -m pip install -r website/requirements-build.txt

# Smoke test: 500 identifiers per release.
.venv/bin/python -m website.offline.build_index --limit 500 --out website/.build/r2

# Full build. This is the one to deploy; refresh first so a same-URL
# publisher update cannot be missed by the local mtime check.
.venv/bin/python -m website.offline.build_index --refresh-data --out website/.build/r2
```

Useful flags:

| Flag | Meaning |
| --- | --- |
| `--refresh-data` | Download all three required files even when recent |
| `--no-download` | Use only files already on disk; works offline |
| `--data-max-age-days N` | Refresh a present file older than `N` days; default 7 |
| `--limit N` | Build only `N` identifiers per release |
| `--releases key …` | Build selected release keys only |

Upload the same build to staging first, then production:

```bash
.venv/bin/python -m website.offline.upload_index \
  --build-dir website/.build/r2 \
  --bucket bronx-data-staging

.venv/bin/python -m website.offline.upload_index \
  --build-dir website/.build/r2 \
  --bucket bronx-data
```

Always upload the full build when refreshing data. `--skip-existing` compares keys only, so it skips rebuilt shards and `manifest.json` under unchanged paths; use it only to resume a first-time seed. `--dry-run` lists work without writing. OAuth uploads default to 4 workers and 3.5 PUT/s; `--workers` and `--rate` adjust that. `Retry-After` is honored on HTTP 429.

## Deploy

Always pass `--env` explicitly; the config defines two environments.

```bash
# Staging
npx wrangler deploy --config website/wrangler.toml --env staging

# Production
npx wrangler deploy --config website/wrangler.toml --env=""

# Syntax check only
npx wrangler deploy --dry-run --config website/wrangler.toml --env staging
npx wrangler deploy --dry-run --config website/wrangler.toml --env=""
```

## Updating the dataset

Update the URLs in `data-sources.json` if the publisher changes its links, then rebuild with `--refresh-data` and upload the full build to both buckets. The build also refreshes files when missing, older than seven days, or when a URL changes; `--refresh-data` removes the seven-day window because the publisher uses stable `*-latest.parquet` URLs that can change in place. Do not use `--skip-existing` for a data refresh. R2 data is live immediately, but the Worker caches the manifest and shards for five minutes, so redeploy if a refresh must be visible sooner. The Datasets page shows each release's watermark, key count, and build time from `manifest.json`.

## Routes

| Route | Function |
| --- | --- |
| `GET /` | Centered bar on staging; workbench on production |
| `GET /?q=…` | Staging lookup opened in the revealed ledger state |
| `GET /table?q=…` | Staging stays/stints table for the same lookup result |
| `POST /api/lookup` | JSON lookup for in-place staging search |
| `GET /demo` | Demo pathway |
| `POST /search` | Identifier / stay / stint lookup |
| `POST /manual` | Re-render with manual entries |
| `POST /export-json` | Source-only pathway JSON download |
| `POST /update-check` | GitHub latest-release check |
| `GET /bulk` | Bulk form |
| `POST /bulk` | Run batch, store results in R2 |
| `GET /bulk/<token>/export.json` | Bulk JSON bundle |
| `GET /bulk/<token>/export.txt` | Bulk text report |
| `GET /history` | Browser-local history view |
| `GET /help` | Usage and interpretation notes |
| `GET /datasets` | Release list; runbook on staging |
| `GET /health` | `ok` |

## Cost and limits

- Workers Free: 100,000 requests/day, 10 ms CPU per invocation.
- R2 Free: 10 GB-month storage, 1M Class A / 10M Class B operations per month. A shard read is one Class B op.
- Bulk batches cap at 200 identifiers; search and bulk are rate-limited per IP.
- History lives in `localStorage` (`bronx.history.v1`), never on the server.
- Bulk exports are temporary; rerun the batch after the object expires.

## Verification

- `format.ts` matched Python `format_pathway()` byte-for-byte on 400 real pathway documents.
- Full offline build produced 10,586,760 indexed keys in 8,192 gzipped shards across two releases.
- `wrangler deploy --dry-run` passes for both environments with the correct `DATA` bucket and `ASSETS` binding.
- Staging one-panel search, Datasets tab, manual entries, copy, export, demo, bulk, and update check were exercised against live R2 data.
