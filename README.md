# Masi.AI

Data platform for the **Casablanca Stock Exchange**: news, listed companies,
historical prices and live order books — all in one Postgres database,
collected automatically and served over a REST API.

## What it collects

| Data | Source | How |
|---|---|---|
| News | Medias24 (leboursier) | category pages → article pages |
| News | L'Économiste | XML sitemaps → article pages |
| Listed companies | casabourse.ma + Medias24 | daily universe sync — **new listings are added automatically** |
| Order book (carnet d'ordres) | Medias24 `getBidAsk` API | every 5 min, Mon–Fri 09:00–15:45 |
| Price history (closes) | Medias24 `getStockHistory` API | daily after close |
| Price history (full OHLCV) | Bourse de Casablanca official JSON:API | daily after close |
| Macro (GDP, inflation, unemployment, debt, ...) | World Bank + IMF DataMapper APIs | weekly |
| MASI index history | Medias24 `getMasiHistory` API | weekly |

## Quick start (Docker)

```bash
cp .env.example .env        # then set a real POSTGRES_PASSWORD
docker compose up -d --build
```

- API: http://localhost:8000 (`/health`, `/api/v1/...`)
- **Dashboard: http://localhost:8000** — market terminal with all companies,
  price charts, live order books, news feed and macro indicators
- Postgres: localhost:5432

The `worker` container runs the scheduler; the `api` container serves the REST API.

## Quick start (one file, no Docker)

```bash
python3 run.py                  # creates .venv, installs deps, waits for
                                # Postgres, loads all data, starts scheduler
python3 run.py --api            # same, but starts the API server instead
python3 run.py --bootstrap-only # only load the data, then exit
python3 run.py --full-history   # also pull FULL OHLCV history from BVC
```

`run.py` does everything: Python version check, project virtualenv,
dependency install, Postgres wait, table creation, then the first data load
(companies → macro → price history → news → order books). Needs Postgres
running — either `docker compose up -d postgres` or your own server
(configured in `.env`).

## Manual runs

```bash
# without docker (needs Postgres running + .env)
pip install -r requirements.txt
export PYTHONPATH=src

python -m masi_ai.cli universe            # sync companies (auto-adds new ones)
python -m masi_ai.cli news                # collect news now
python -m masi_ai.cli orderbook           # snapshot order books now
python -m masi_ai.cli history-medias24    # refresh close history
python -m masi_ai.cli history-bvc         # full OHLCV from BVC official API
python -m masi_ai.cli scheduler           # run all scheduled jobs
python -m masi_ai.cli api                 # run the API on :8000
```

## API

- `GET /health`
- `GET /api/v1/instruments` — all listed companies
- `GET /api/v1/instruments/{ticker}`
- `GET /api/v1/history/{ticker}?from=2026-01-01&to=2026-10-17`
- `GET /api/v1/orderbook/{ticker}` — latest carnet d'ordres snapshot
- `GET /api/v1/news?source=medias24&limit=50`
- `GET /api/v1/news/{id}` — full article body
- `GET /api/v1/macro/indicators` — available macro indicators
- `GET /api/v1/macro/{indicator}` — e.g. `gdp_growth_pct`, `masi_index`

## Notes

- The official Bourse de Casablanca site is WAF-protected; its scrapers use
  `cloudscraper` (same approach as the proven PFE.ATW pipeline). Medias24 and
  L'Économiste work with plain requests.
- Companies that get delisted are marked `active=false`, never deleted.
- All times Africa/Casablanca.
