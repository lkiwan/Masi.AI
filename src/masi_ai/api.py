"""FastAPI read API over the collected market data."""
from __future__ import annotations

from datetime import date, datetime

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func

from .db import SessionLocal, init_db
from .models import DailyBar, Instrument, MacroData, NewsArticle, OrderBookLevel

app = FastAPI(title="Masi.AI API", version="1.0.0")


@app.on_event("startup")
def _startup():
    init_db()


@app.get("/health")
def health():
    return {"ok": True}


# ---------------------------------------------------------------- instruments
class InstrumentOut(BaseModel):
    ticker: str
    name: str
    isin: str | None
    active: bool


@app.get("/api/v1/instruments", response_model=list[InstrumentOut])
def list_instruments(active: bool = True):
    with SessionLocal() as db:
        q = db.query(Instrument)
        if active:
            q = q.filter(Instrument.active.is_(True))
        return [InstrumentOut(ticker=i.ticker, name=i.name, isin=i.isin, active=i.active)
                for i in q.order_by(Instrument.ticker).all()]


@app.get("/api/v1/instruments/{ticker}", response_model=InstrumentOut)
def get_instrument(ticker: str):
    with SessionLocal() as db:
        i = db.get(Instrument, ticker.upper())
        if not i:
            raise HTTPException(404, "unknown ticker")
        return InstrumentOut(ticker=i.ticker, name=i.name, isin=i.isin, active=i.active)


# ------------------------------------------------------------------- history
class BarOut(BaseModel):
    date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume_titles: int | None
    volume_mad: float | None
    trades: int | None


@app.get("/api/v1/history/{ticker}", response_model=list[BarOut])
def history(ticker: str, from_: date | None = Query(None, alias="from"),
            to: date | None = Query(None, alias="to"), limit: int = 500):
    with SessionLocal() as db:
        q = db.query(DailyBar).filter(DailyBar.ticker == ticker.upper())
        if from_:
            q = q.filter(DailyBar.session_date >= from_)
        if to:
            q = q.filter(DailyBar.session_date <= to)
        bars = q.order_by(desc(DailyBar.session_date)).limit(limit).all()
        return [BarOut(date=b.session_date, open=b.open, high=b.high, low=b.low,
                       close=b.close, volume_titles=b.volume_titles,
                       volume_mad=b.volume_mad, trades=b.trades) for b in bars]


# ------------------------------------------------------------------ orderbook
class LevelOut(BaseModel):
    side: str
    level: int
    price: float
    qty: int
    orders: int


@app.get("/api/v1/orderbook/{ticker}", response_model=list[LevelOut])
def orderbook(ticker: str):
    with SessionLocal() as db:
        latest = db.query(func.max(OrderBookLevel.captured_at)).filter(
            OrderBookLevel.ticker == ticker.upper()).scalar()
        if not latest:
            raise HTTPException(404, "no order book data for this ticker")
        lvls = (db.query(OrderBookLevel)
                .filter(OrderBookLevel.ticker == ticker.upper(),
                        OrderBookLevel.captured_at == latest)
                .order_by(OrderBookLevel.side.desc(), OrderBookLevel.level).all())
        return [LevelOut(side=l.side, level=l.level, price=l.price, qty=l.qty,
                         orders=l.orders) for l in lvls]


# ----------------------------------------------------------------------- news
class NewsOut(BaseModel):
    source: str
    title: str
    url: str
    published_at: datetime | None


@app.get("/api/v1/news", response_model=list[NewsOut])
def news(source: str | None = None, limit: int = Query(50, le=200)):
    with SessionLocal() as db:
        q = db.query(NewsArticle)
        if source:
            q = q.filter(NewsArticle.source == source)
        arts = q.order_by(desc(NewsArticle.published_at),
                         desc(NewsArticle.id)).limit(limit).all()
        return [NewsOut(source=a.source, title=a.title, url=a.url,
                        published_at=a.published_at) for a in arts]


@app.get("/api/v1/news/{article_id}")
def news_detail(article_id: int):
    with SessionLocal() as db:
        a = db.get(NewsArticle, article_id)
        if not a:
            raise HTTPException(404, "unknown article")
        return {"source": a.source, "title": a.title, "url": a.url,
                "published_at": a.published_at, "body": a.body}


# --------------------------------------------------------------------- market
class MarketRow(BaseModel):
    ticker: str
    name: str
    price: float | None
    prev_close: float | None
    change_pct: float | None
    volume_titles: int | None
    volume_mad: float | None
    session_date: date | None


@app.get("/api/v1/market/overview", response_model=list[MarketRow])
def market_overview():
    """Latest quote per ticker with day change vs previous close."""
    with SessionLocal() as db:
        ranked = (
            db.query(
                DailyBar.ticker.label("ticker"),
                DailyBar.session_date.label("session_date"),
                DailyBar.close.label("close"),
                DailyBar.volume_titles.label("volume_titles"),
                DailyBar.volume_mad.label("volume_mad"),
                func.row_number().over(
                    partition_by=DailyBar.ticker,
                    order_by=desc(DailyBar.session_date),
                ).label("rn"),
            ).subquery()
        )
        latest = db.query(ranked).filter(ranked.c.rn == 1).subquery()
        prev = db.query(ranked).filter(ranked.c.rn == 2).subquery()
        rows = (
            db.query(Instrument.ticker, Instrument.name,
                     latest.c.close, latest.c.session_date,
                     latest.c.volume_titles, latest.c.volume_mad,
                     prev.c.close.label("prev_close"))
            .outerjoin(latest, latest.c.ticker == Instrument.ticker)
            .outerjoin(prev, prev.c.ticker == Instrument.ticker)
            .filter(Instrument.active.is_(True))
            .order_by(Instrument.ticker).all()
        )
        out = []
        for r in rows:
            chg = None
            if r.close and r.prev_close:
                chg = round((r.close - r.prev_close) / r.prev_close * 100, 2)
            out.append(MarketRow(
                ticker=r.ticker, name=r.name,
                price=r.close, prev_close=r.prev_close, change_pct=chg,
                volume_titles=r.volume_titles, volume_mad=r.volume_mad,
                session_date=r.session_date))
        return out


# ------------------------------------------------------------- dashboard app
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

_STATIC_DIR = _Path(__file__).resolve().parent / "static"
if _STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="dashboard")
class MacroOut(BaseModel):
    indicator: str
    date: date
    value: float
    source: str


@app.get("/api/v1/macro/indicators", response_model=list[str])
def macro_indicators():
    with SessionLocal() as db:
        rows = db.query(MacroData.indicator).distinct().order_by(MacroData.indicator).all()
        return [r[0] for r in rows]


@app.get("/api/v1/macro/{indicator}", response_model=list[MacroOut])
def macro_series(indicator: str, limit: int = Query(100, le=1000)):
    with SessionLocal() as db:
        rows = (db.query(MacroData)
                .filter(MacroData.indicator == indicator)
                .order_by(desc(MacroData.ref_date)).limit(limit).all())
        if not rows:
            raise HTTPException(404, "unknown indicator")
        return [MacroOut(indicator=r.indicator, date=r.ref_date,
                         value=r.value, source=r.source) for r in rows]
