"""Order-book (carnet d'ordres) snapshots from Medias24.

Fast by design: ONE token fetch per run, then all tickers in parallel
(ThreadPoolExecutor). One row per (ticker, captured_at, side, level).
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from ..db import session_scope
from ..medias24 import Medias24Client
from ..models import Instrument, OrderBookLevel

log = logging.getLogger(__name__)

MAX_LEVELS = 10  # medias24 publishes 10 levels per side
WORKERS = 10


def _clean_price(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None  # exactness: prices must be positive


def _clean_qty(v) -> int | None:
    try:
        q = int(float(v))
    except (TypeError, ValueError):
        return None
    return q if q >= 0 else None


def _fetch_one(client: Medias24Client, ticker: str, isin: str):
    """Returns (ticker, rows) or (ticker, None) on failure."""
    try:
        book = client.get_bid_ask(isin)
    except Exception as e:  # noqa: BLE001
        log.warning("orderbook failed for %s: %s", ticker, e)
        return ticker, None
    now = datetime.now(timezone.utc)
    rows: list[OrderBookLevel] = []
    prev_bid, prev_ask = float("inf"), 0.0
    for i, lvl in enumerate(book[:MAX_LEVELS]):
        for side, pfx in (("bid", "bid"), ("ask", "ask")):
            price = _clean_price(lvl.get(f"{pfx}Value"))
            qty = _clean_qty(lvl.get(f"{pfx}Qte"))
            if price is None or qty is None:
                continue
            rows.append(
                OrderBookLevel(
                    ticker=ticker,
                    captured_at=now,
                    side=side,
                    level=i + 1,
                    price=price,
                    qty=qty,
                    orders=int(lvl.get(f"{pfx}Order") or 0),
                )
            )
        # exactness: sanity-check book ordering, log anomalies
        if rows:
            b = next((r.price for r in rows if r.side == "bid" and r.level == i + 1), None)
            a = next((r.price for r in rows if r.side == "ask" and r.level == i + 1), None)
            if b is not None:
                if b > prev_bid:
                    log.warning("%s: bid levels out of order at level %d", ticker, i + 1)
                prev_bid = b
            if a is not None:
                if a < prev_ask:
                    log.warning("%s: ask levels out of order at level %d", ticker, i + 1)
                prev_ask = a
    return ticker, rows


def snapshot_orderbooks(workers: int = WORKERS) -> dict:
    client = Medias24Client()
    client.ensure_token()  # single token fetch up front

    with session_scope() as db:
        instruments = (
            db.query(Instrument.ticker, Instrument.isin)
            .filter(Instrument.active.is_(True), Instrument.isin.isnot(None))
            .all()
        )
    targets = [(t, i) for t, i in instruments]

    total_levels = 0
    tickers_ok: list[str] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_one, client, t, i): t for t, i in targets}
        results: dict[str, list | None] = {}
        for fut in as_completed(futures):
            ticker, rows = fut.result()
            results[ticker] = rows

    with session_scope() as db:
        for ticker, rows in results.items():
            if rows is None:
                errors.append(ticker)
            elif rows:
                db.add_all(rows)
                total_levels += len(rows)
                tickers_ok.append(ticker)
            else:
                # empty book: transient, skip so the API keeps serving the last real one
                log.debug("%s: empty order book, skipped", ticker)

    stats = {"tickers": len(tickers_ok), "levels": total_levels, "errors": errors}
    log.info("orderbook snapshot done: %s", stats)
    return stats
