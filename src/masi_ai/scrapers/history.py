"""Historical price data.

Two layers:
  1. Medias24 ``getStockHistory`` — close prices per ISIN (works everywhere).
  2. Casablanca Bourse official JSON:API ``instrument_history`` — full OHLCV
     (needs the BVC ``instrument_id``; the site is WAF-protected so this uses
     cloudscraper exactly like the proven PFE.ATW pipeline).

Bars are upserted on (ticker, session_date); the two layers complement each
other in the same rows.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timezone
from urllib.parse import urlencode

from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..db import session_scope
from ..http import build_cloudscraper_session
from ..medias24 import Medias24Client
from ..models import DailyBar, Instrument

log = logging.getLogger(__name__)

BVC_API = "https://www.casablanca-bourse.com/api/proxy/fr/api/bourse_data/instrument_history"
BVC_INSTRUMENT_PAGE = "https://www.casablanca-bourse.com/fr/live-market/instruments/{ticker}"
BVC_REFERER = "https://www.casablanca-bourse.com/fr/live-market/actions"
PAGE_SIZE = 100
ID_RE = re.compile(r"drupal_internal__target_id[\"']?\s*:\s*(\d+)")


# ---------------------------------------------------------------------------
# BVC official EOD (full OHLCV)
# ---------------------------------------------------------------------------

def discover_instrument_id(ticker: str) -> str | None:
    """Find the BVC drupal instrument_id for a ticker from its instrument page."""
    s = build_cloudscraper_session({"Referer": BVC_REFERER})
    try:
        r = s.get(BVC_INSTRUMENT_PAGE.format(ticker=ticker), timeout=40)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("BVC instrument page failed for %s: %s", ticker, e)
        return None
    m = ID_RE.search(r.text)
    # the page embeds the *current* instrument's target id near its symbol
    if m:
        return m.group(1)
    return None


def _bvc_fetch_page(session, instrument_id: str, offset: int) -> list[dict]:
    params = {
        "fields[instrument_history]": (
            "symbol,created,field_seance_date,"
            "openingPrice,coursCourant,highPrice,lowPrice,"
            "cumulTitresEchanges,cumulVolumeEchange,totalTrades,"
            "capitalisation,closingPrice,staticReferencePrice"
        ),
        "include": "symbol",
        "sort[date-seance][path]": "created",
        "sort[date-seance][direction]": "ASC",
        "filter[filter-historique-instrument-emetteur][condition][path]":
            "symbol.meta.drupal_internal__target_id",
        "filter[filter-historique-instrument-emetteur][condition][operator]": "=",
        "filter[filter-historique-instrument-emetteur][condition][value]": instrument_id,
        "filter[instrument-history-class][condition][path]": "symbol.codeClasse.field_code",
        "filter[instrument-history-class][condition][value]": "1",
        "filter[instrument-history-class][condition][operator]": "=",
        "filter[published]": "1",
        "page[offset]": str(offset),
        "page[limit]": str(PAGE_SIZE),
    }
    r = session.get(f"{BVC_API}?{urlencode(params)}", timeout=40)
    r.raise_for_status()
    return r.json().get("data", [])


def bvc_full_history(instrument_id: str) -> list[dict]:
    s = build_cloudscraper_session(
        {
            "Accept": "application/vnd.api+json",
            "Content-Type": "application/vnd.api+json",
            "Referer": BVC_REFERER,
        }
    )
    out: list[dict] = []
    offset = 0
    while True:
        items = _bvc_fetch_page(s, instrument_id, offset)
        if not items:
            break
        out.extend(i.get("attributes", {}) for i in items)
        if len(items) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        time.sleep(1)
    return out


def _parse_seance(attrs: dict) -> date | None:
    for key in ("field_seance_date", "created"):
        v = attrs.get(key)
        if v:
            try:
                return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()
            except ValueError:
                continue
    return None


def _f(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _i(v) -> int | None:
    try:
        return int(float(v)) if v is not None else None
    except (TypeError, ValueError):
        return None


def bvc_eod_sync(tickers: list[str] | None = None, discover_ids: bool = True) -> dict:
    """Pull full OHLCV history from the BVC official API for given (or all) tickers."""
    synced, skipped = 0, []
    with session_scope() as db:
        q = db.query(Instrument).filter(Instrument.active.is_(True))
        if tickers:
            q = q.filter(Instrument.ticker.in_(tickers))
        instruments = q.all()
        for inst in instruments:
            iid = inst.bvc_instrument_id
            if not iid and discover_ids:
                iid = discover_instrument_id(inst.ticker)
                if iid:
                    inst.bvc_instrument_id = iid
                    log.info("%s: discovered bvc_instrument_id=%s", inst.ticker, iid)
            if not iid:
                skipped.append(inst.ticker)
                continue
            try:
                attrs_list = bvc_full_history(iid)
            except Exception as e:  # noqa: BLE001
                log.warning("BVC history failed for %s: %s", inst.ticker, e)
                skipped.append(inst.ticker)
                continue
            rows = []
            for attrs in attrs_list:
                seance = _parse_seance(attrs)
                if not seance:
                    continue
                rows.append(
                    {
                        "ticker": inst.ticker,
                        "session_date": seance,
                        "open": _f(attrs.get("openingPrice")),
                        "high": _f(attrs.get("highPrice")),
                        "low": _f(attrs.get("lowPrice")),
                        "close": _f(attrs.get("closingPrice")) or _f(attrs.get("coursCourant")),
                        "volume_titles": _i(attrs.get("cumulTitresEchanges")),
                        "volume_mad": _f(attrs.get("cumulVolumeEchange")),
                        "trades": _i(attrs.get("totalTrades")),
                        "source": "bvc",
                    }
                )
            if rows:
                upsert_bars(db, rows)
                synced += 1
                log.info("%s: upserted %d BVC bars", inst.ticker, len(rows))
            time.sleep(1)
    return {"synced": synced, "skipped": skipped}


# ---------------------------------------------------------------------------
# Medias24 close history (lightweight daily refresh)
# ---------------------------------------------------------------------------

def medias24_history_sync(tickers: list[str] | None = None, periode: str = "1y",
                          workers: int = 10) -> dict:
    """Refresh close history from Medias24, tickers in parallel."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    client = Medias24Client()
    client.ensure_token()  # single token fetch up front

    with session_scope() as db:
        q = db.query(Instrument.ticker, Instrument.isin).filter(
            Instrument.active.is_(True),
            Instrument.isin.isnot(None),
        )
        if tickers:
            q = q.filter(Instrument.ticker.in_(tickers))
        targets = [(t, i) for t, i in q.all()]

    def _one(ticker: str, isin: str):
        try:
            series = client.get_stock_history(isin, periode=periode)
        except Exception as e:  # noqa: BLE001
            log.warning("medias24 history failed for %s: %s", ticker, e)
            return ticker, None
        # exactness: dedupe by date, keep last
        by_date: dict[date, float] = {}
        for ts, px in series:
            by_date[datetime.fromtimestamp(ts, tz=timezone.utc).date()] = px
        rows = [
            {"ticker": ticker, "session_date": d, "close": px, "source": "medias24"}
            for d, px in sorted(by_date.items())
        ]
        return ticker, rows

    synced, skipped = 0, []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_one, t, i): t for t, i in targets}
        with session_scope() as db:
            for fut in as_completed(futures):
                ticker, rows = fut.result()
                if rows is None:
                    skipped.append(ticker)
                elif rows:
                    upsert_bars(db, rows)
                    synced += 1
    return {"synced": synced, "skipped": skipped}


# ---------------------------------------------------------------------------
def upsert_bars(db, rows: list[dict]) -> int:
    """Upsert daily bars; existing rows keep their data, new fields fill in."""
    n = 0
    for r in rows:
        stmt = pg_insert(DailyBar).values(**r)
        update_cols = {
            c: stmt.excluded[c]
            for c in ("open", "high", "low", "close", "volume_titles",
                      "volume_mad", "trades", "source")
            if c in r and r[c] is not None
        }
        stmt = stmt.on_conflict_do_update(
            constraint="uq_daily_ticker_date", set_=update_cols
        )
        db.execute(stmt)
        n += 1
    return n
