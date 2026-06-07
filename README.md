# CDMX Doctores Daily Listing Tracker

Automated pipeline that scrapes apartment-for-sale listings across CDMX real-estate portals, deduplicates across sources and days, estimates rental yield via a hedonic model, and produces daily reports with time-series data for a dashboard.

## Known Limitations

1. **Hard-blocker coverage gaps.** `inmuebles24.com` (DataDome), `easybroker.com`, and `propiedades.com` actively block automated access. The pipeline runs with whatever sources succeed and clearly reports which failed. On a datacenter IP (e.g., GitHub Actions), expect only `icasas`, `lamudi`, and `vivanuncios` to work reliably.

2. **Static-IP fragility.** GitHub Actions runners use datacenter IPs that are fingerprinted quickly. Set the `PROXY_URL` secret to a residential proxy for better coverage. Without it, blocked sources return empty and the report reflects reduced data.

3. **Rent-model uncertainty.** Yields depend on a hedonic rent estimate fitted on whatever rental comps are available. The model reports n, R², and prediction intervals; listings where the colonia has <15 comps fall back to median rent/m² and are flagged `[F]` in the report. Do not treat yield numbers as precise.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Run for today
python -m tracker.run

# Run for a specific date
python -m tracker.run --date 2024-06-01
```

## Configuration

All parameters live in [`tracker/config.yaml`](tracker/config.yaml):
- Price band, area range, bedroom minimum
- Target colonias (tier 1 = primary, tier 2 = comparables)
- Financial assumptions (vacancy, maintenance, mortgage rate, etc.)
- Scoring weights for the composite value score
- Source enable/disable list
- Quarantine bounds

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PROXY_URL` | No | HTTP proxy for hard-blocker sources |
| `TELEGRAM_TOKEN` | No | Telegram bot token for alerts |
| `TELEGRAM_CHAT_ID` | No | Telegram chat ID for alerts |
| `GOOGLE_MAPS_KEY` | No | Google Geocoding API key (falls back to Nominatim) |
| `SMTP_HOST` | No | SMTP server for email alerts |
| `SMTP_PORT` | No | SMTP port (default 587) |
| `SMTP_USER` | No | SMTP username |
| `SMTP_PASS` | No | SMTP password |
| `ALERT_EMAIL` | No | Recipient for email alerts |

## Outputs

- `data/snapshot_YYYY-MM-DD.csv` — Full deduped property table with indicators
- `data/market_timeseries.json` — Daily time series for dashboard charting
- `reports/daily_YYYY-MM-DD.md` — Human-readable report with top opportunities, movers, recovery watch
- `tracker.sqlite` — Persistent state (listings, history, properties, timeseries)
- `models/rent_hedonic_YYYY-MM-DD.json` — Hedonic model coefficients (refitted weekly)

## GitHub Actions

The workflow runs daily at 07:00 CDMX (13:00 UTC). On success, it commits `data/`, `reports/`, and `models/` to the repo. On failure, it opens/updates a GitHub issue with the failure log.

Set secrets in your repo settings:
- `PROXY_URL` (recommended)
- `TELEGRAM_TOKEN` + `TELEGRAM_CHAT_ID` (for alerts)

## Tests

```bash
pytest tests/ -v
```

The test suite covers:
1. **Idempotency** — double-run produces identical state
2. **Source failure** — partial sources produce valid report
3. **Empty guard** — all-empty aborts without corrupting state
4. **Deduplication** — cross-portal matching collapses to one UID
5. **Recovery isolation** — remates excluded from main ranking, no mortgage metrics
6. **Rent fallback** — low-comp colonias flagged
7. **Quarantine** — out-of-range listings excluded from aggregates

## Architecture

```
tracker/
  config.yaml          # all thresholds, no magic numbers
  run.py               # pipeline orchestrator
  adapters/            # one per portal, common SourceAdapter interface
    base.py            # abstract adapter + RawListing schema
    icasas.py          # server-rendered, primary source
    lamudi.py          # JSON-LD preferred
    vivanuncios.py     # rich descriptions
    inmuebles24.py     # DataDome — expect blocks
    propiedades.py     # robots-disallowed, proxy-only
    mudafy.py          # SPA, JSON API preferred
  normalize.py         # schema mapping, recovery detection, feature extraction
  geocode.py           # Nominatim + cache
  dedup.py             # geo + fuzzy matching → stable property_uid
  rent_model.py        # OLS hedonic with prediction intervals
  indicators.py        # yield, cap rate, GRM, break-even, spread
  state.py             # SQLite persistence, change detection, atomic swap
  analyze.py           # scoring, ranking, market aggregates
  report.py            # markdown report + CSV snapshot + JSON timeseries
  alert.py             # Telegram / email push (env-gated)
```

## Data Freshness & ToS

Portal terms of service may prohibit scraping. This tool is for **private research only**. Rate-limited to 1 request per 2–5 seconds per domain. Raw HTML/JSON is cached locally for audit and replay.
