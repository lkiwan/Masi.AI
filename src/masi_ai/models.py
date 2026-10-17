"""SQLAlchemy models for Masi.AI."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Instrument(Base):
    """Every company listed on the Casablanca Stock Exchange.

    New listings are picked up automatically by the universe-sync job.
    """

    __tablename__ = "instruments"

    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    isin: Mapped[str | None] = mapped_column(String(16), nullable=True)
    medias24_slug: Mapped[str | None] = mapped_column(String(128), nullable=True)
    bvc_instrument_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DailyBar(Base):
    """End-of-day OHLCV bars, one row per (ticker, session date)."""

    __tablename__ = "daily_bars"
    __table_args__ = (UniqueConstraint("ticker", "session_date", name="uq_daily_ticker_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), ForeignKey("instruments.ticker"), index=True)
    session_date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    volume_titles: Mapped[int | None] = mapped_column(BigInteger)
    volume_mad: Mapped[float | None] = mapped_column(Float)
    trades: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32), default="medias24", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrderBookLevel(Base):
    """Order-book depth snapshots from Medias24 (carnet d'ordres)."""

    __tablename__ = "orderbook_levels"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), ForeignKey("instruments.ticker"), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    side: Mapped[str] = mapped_column(String(4), nullable=False)  # bid | ask
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    qty: Mapped[int] = mapped_column(BigInteger, nullable=False)
    orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class NewsArticle(Base):
    """News from Medias24 and L'Economiste."""

    __tablename__ = "news"
    __table_args__ = (UniqueConstraint("url", name="uq_news_url"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # medias24 | leconomiste
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    body: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MacroData(Base):
    """Macroeconomic indicators (World Bank, IMF) + MASI index history."""

    __tablename__ = "macro_data"
    __table_args__ = (
        UniqueConstraint("indicator", "ref_date", "source", name="uq_macro_indicator_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    indicator: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ref_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # worldbank | imf | medias24
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
