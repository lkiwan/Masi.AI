"""Macroeconomic data for Morocco.

Sources (all free, no key, JSON):
  - World Bank API  : GDP growth, inflation, unemployment, ...
  - IMF DataMapper  : real GDP, inflation, unemployment, public debt, ...
  - Medias24        : MASI index history (daily)

Annual series are refreshed weekly (they barely move); MASI is daily.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..db import session_scope
from ..http import build_session, get_with_backoff
from ..medias24 import Medias24Client
from ..models import MacroData

log = logging.getLogger(__name__)

WB_BASE = "https://api.worldbank.org/v2/country/MA/indicator"
IMF_BASE = "https://www.imf.org/external/datamapper/api/v1"

# indicator_name -> (source, source_code)
INDICATORS: dict[str, tuple[str, str]] = {
    "gdp_growth_pct": ("worldbank", "NY.GDP.MKTP.KD.ZG"),
    "inflation_cpi_pct": ("worldbank", "FP.CPI.TOTL.ZG"),
    "unemployment_pct": ("worldbank", "SL.UEM.TOTL.ZS"),
    "gdp_per_capita_usd": ("worldbank", "NY.GDP.PCAP.CD"),
    "population": ("worldbank", "SP.POP.TOTL"),
    "imf_gdp_real_pct": ("imf", "NGDP_RPCH"),
    "imf_inflation_pct": ("imf", "PCPIPCH"),
    "imf_unemployment_pct": ("imf", "LUR"),
    "imf_debt_pct_gdp": ("imf", "GGXWDG_NGDP"),
    "imf_current_account_pct_gdp": ("imf", "BCA_NGDPD"),
}


def _fetch_wb(session, code: str) -> list[tuple[date, float]]:
    r = get_with_backoff(session, f"{WB_BASE}/{code}",
                         params={"format": "json", "mrv": 30, "per_page": 40}, timeout=30)
    payload = r.json()
    rows = payload[1] if isinstance(payload, list) and len(payload) > 1 else []
    out = []
    for row in rows:
        try:
            v = float(row["value"])
            d = date(int(row["date"]), 12, 31)
        except (TypeError, ValueError, KeyError):
            continue
        out.append((d, v))
    return out


def _fetch_imf(session, code: str) -> list[tuple[date, float]]:
    # IMF's WAF blocks browser-like User-Agents; use a neutral one
    headers = {"User-Agent": "masi-ai/1.0 (+https://github.com/lkiwan/Masi.AI)"}
    r = get_with_backoff(session, f"{IMF_BASE}/{code}/MAR", timeout=30, headers=headers)
    values = r.json().get("values", {}).get(code, {}).get("MAR", {})
    out = []
    for year, v in values.items():
        try:
            out.append((date(int(year), 12, 31), float(v)))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def _fetch_masi() -> list[tuple[date, float]]:
    client = Medias24Client()
    series = client.get_index_history("masi", periode="5y")
    return [(datetime.fromtimestamp(ts, tz=timezone.utc).date(), px) for ts, px in series]


def collect_macro(workers: int = 6) -> dict:
    session = build_session()

    def _one(name: str, source: str, code: str):
        try:
            if source == "worldbank":
                rows = _fetch_wb(session, code)
            else:
                rows = _fetch_imf(session, code)
        except Exception as e:  # noqa: BLE001
            log.warning("macro %s failed: %s", name, e)
            return name, source, None
        return name, source, rows

    results: dict[str, tuple[str, list | None]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_one, n, s, c): n for n, (s, c) in INDICATORS.items()}
        for fut in as_completed(futures):
            name, source, rows = fut.result()
            results[name] = (source, rows)

    # MASI index (daily)
    try:
        masi_rows = _fetch_masi()
        results["masi_index"] = ("medias24", masi_rows)
    except Exception as e:  # noqa: BLE001
        log.warning("masi index history failed: %s", e)
        results["masi_index"] = ("medias24", None)

    inserted, failed = 0, []
    with session_scope() as db:
        for name, (source, rows) in results.items():
            if not rows:
                failed.append(name)
                continue
            for d, v in rows:
                stmt = pg_insert(MacroData).values(
                    indicator=name, ref_date=d, value=v, source=source)
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_macro_indicator_date",
                    set_={"value": stmt.excluded.value},
                )
                db.execute(stmt)
                inserted += 1
    stats = {"indicators": len(results), "rows": inserted, "failed": failed}
    log.info("macro done: %s", stats)
    return stats
