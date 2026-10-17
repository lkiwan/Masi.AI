"""Masi.AI market dashboard (Streamlit).

Run:  streamlit run src/masi_ai/dashboard.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from masi_ai.db import SessionLocal  # noqa: E402
from masi_ai.models import DailyBar, Instrument, MacroData, NewsArticle, OrderBookLevel  # noqa: E402
from sqlalchemy import desc, func  # noqa: E402

# ------------------------------------------------------------------ styling
st.set_page_config(page_title="Masi.AI — Casablanca Stock Exchange",
                   page_icon="◼", layout="wide")

st.markdown("""
<style>
  .block-container { padding-top: 1.2rem; max-width: 1400px; }
  [data-testid="stMetricValue"] { font-family: "IBM Plex Mono", monospace; font-size: 1.5rem; }
  h1, h2, h3 { font-weight: 600; letter-spacing: -0.01em; }
  .brand { font-weight: 700; font-size: 1.15rem; letter-spacing: 0.03em; }
  .brand small { color: #8a94a6; font-weight: 400; font-size: 0.8rem; }
  a { color: #d9a441; }
</style>
""", unsafe_allow_html=True)

PLOT_TEMPLATE = dict(
    layout=go.Layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#8a94a6", size=11),
        margin=dict(l=50, r=20, t=20, b=30),
        xaxis=dict(gridcolor="#1d2534", zeroline=False),
        yaxis=dict(gridcolor="#1d2534", zeroline=False),
    )
)

# ------------------------------------------------------------------- data
@st.cache_data(ttl=300)
def get_overview() -> pd.DataFrame:
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
                    order_by=desc(DailyBar.session_date)).label("rn"),
            ).subquery()
        )
        latest = db.query(ranked).filter(ranked.c.rn == 1).subquery()
        prev = db.query(ranked).filter(ranked.c.rn == 2).subquery()
        rows = (
            db.query(Instrument.ticker, Instrument.name, latest.c.close,
                     latest.c.session_date, latest.c.volume_titles,
                     latest.c.volume_mad, prev.c.close.label("prev_close"))
            .outerjoin(latest, latest.c.ticker == Instrument.ticker)
            .outerjoin(prev, prev.c.ticker == Instrument.ticker)
            .filter(Instrument.active.is_(True))
            .order_by(Instrument.ticker).all()
        )
    df = pd.DataFrame(rows, columns=["ticker", "name", "close", "date",
                                     "volume", "turnover", "prev_close"])
    df["change_pct"] = (df["close"] - df["prev_close"]) / df["prev_close"] * 100
    return df


@st.cache_data(ttl=300)
def get_history(ticker: str) -> pd.DataFrame:
    with SessionLocal() as db:
        rows = (db.query(DailyBar.session_date, DailyBar.open, DailyBar.high,
                         DailyBar.low, DailyBar.close, DailyBar.volume_titles)
                .filter(DailyBar.ticker == ticker)
                .order_by(DailyBar.session_date).all())
    return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])


@st.cache_data(ttl=120)
def get_orderbook(ticker: str) -> pd.DataFrame:
    with SessionLocal() as db:
        latest = (db.query(func.max(OrderBookLevel.captured_at))
                  .filter(OrderBookLevel.ticker == ticker).scalar())
        if not latest:
            return pd.DataFrame()
        rows = (db.query(OrderBookLevel.side, OrderBookLevel.level,
                         OrderBookLevel.price, OrderBookLevel.qty,
                         OrderBookLevel.orders)
                .filter(OrderBookLevel.ticker == ticker,
                        OrderBookLevel.captured_at == latest)
                .order_by(OrderBookLevel.side.desc(), OrderBookLevel.level).all())
    return pd.DataFrame(rows, columns=["side", "level", "price", "qty", "orders"])


@st.cache_data(ttl=600)
def get_news(limit: int = 40) -> pd.DataFrame:
    with SessionLocal() as db:
        rows = (db.query(NewsArticle.source, NewsArticle.title, NewsArticle.url,
                         NewsArticle.published_at)
                .order_by(desc(NewsArticle.published_at), desc(NewsArticle.id))
                .limit(limit).all())
    return pd.DataFrame(rows, columns=["source", "title", "url", "published_at"])


@st.cache_data(ttl=3600)
def get_macro(indicator: str) -> pd.DataFrame:
    with SessionLocal() as db:
        rows = (db.query(MacroData.ref_date, MacroData.value, MacroData.source)
                .filter(MacroData.indicator == indicator)
                .order_by(MacroData.ref_date).all())
    return pd.DataFrame(rows, columns=["date", "value", "source"])


# ------------------------------------------------------------------ header
df = get_overview()

c1, c2, c3, c4 = st.columns([2.2, 1, 1, 1])
with c1:
    st.markdown('<div class="brand">MASI.AI <small>Casablanca Stock Exchange</small></div>',
                unsafe_allow_html=True)
try:
    masi = get_macro("masi_index")
    if len(masi) > 1:
        last, prevv = masi["value"].iloc[-1], masi["value"].iloc[-2]
        with c2:
            st.metric("MASI index", f"{last:,.2f}",
                      f"{(last - prevv) / prevv * 100:+.2f}%")
except Exception:
    pass
with c3:
    up = df["change_pct"].gt(0).sum()
    down = df["change_pct"].lt(0).sum()
    st.metric("Advancers / decliners", f"{int(up)} / {int(down)}")
with c4:
    st.metric("Last session", str(df["date"].max()))

st.divider()

# --------------------------------------------------------------------- tabs
tab_market, tab_company, tab_news, tab_macro = st.tabs(
    ["Market", "Company", "News", "Macro"])

with tab_market:
    show = df[["ticker", "name", "close", "change_pct", "volume", "turnover"]].copy()
    st.dataframe(
        show, use_container_width=True, hide_index=True,
        column_config={
            "ticker": st.column_config.TextColumn("Ticker", width="small"),
            "name": st.column_config.TextColumn("Company", width="large"),
            "close": st.column_config.NumberColumn("Last (MAD)", format="%.2f"),
            "change_pct": st.column_config.NumberColumn("Chg %", format="%+.2f%%"),
            "volume": st.column_config.NumberColumn("Volume", format="%d"),
            "turnover": st.column_config.NumberColumn("Turnover (MAD)", format="%.0f"),
        },
    )

with tab_company:
    tickers = df["ticker"].tolist()
    default = tickers.index("ATW") if "ATW" in tickers else 0
    ticker = st.selectbox("Company", tickers, index=default,
                          format_func=lambda t: f"{t} — {df.set_index('ticker').loc[t, 'name']}")

    row = df.set_index("ticker").loc[ticker]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Last (MAD)", f"{row['close']:,.2f}", f"{row['change_pct']:+.2f}%")
    m2.metric("Volume", f"{int(row['volume']):,}" if pd.notna(row["volume"]) else "—")
    m3.metric("Turnover (MAD)",
              f"{row['turnover']:,.0f}" if pd.notna(row["turnover"]) else "—")
    m4.metric("Session", str(row["date"]))

    hist = get_history(ticker)
    if not hist.empty:
        rng = st.radio("Range", ["1M", "3M", "1Y", "Max"], horizontal=True,
                       label_visibility="collapsed")
        n = {"1M": 22, "3M": 66, "1Y": 252, "Max": len(hist)}[rng]
        h = hist.tail(n)
        up_day = h["close"].iloc[-1] >= h["close"].iloc[0]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=h["date"], y=h["close"], mode="lines",
                                 line=dict(color="#3cb56e" if up_day else "#e05252",
                                           width=1.5),
                                 name="Close"))
        fig.update_layout(**PLOT_TEMPLATE["layout"],
                          title=f"{ticker} — close price (MAD)",
                          xaxis_title=None, yaxis_title=None, height=380)
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Order book")
    ob = get_orderbook(ticker)
    if ob.empty:
        st.info("No order book snapshot for this ticker yet.")
    else:
        bids = ob[ob["side"] == "bid"].sort_values("price", ascending=False)
        asks = ob[ob["side"] == "ask"].sort_values("price", ascending=True)
        bcol, acol = st.columns(2)
        with bcol:
            st.markdown("**Bids**")
            st.dataframe(bids[["price", "qty", "orders"]], use_container_width=True,
                         hide_index=True,
                         column_config={
                             "price": st.column_config.NumberColumn("Price", format="%.2f"),
                             "qty": st.column_config.NumberColumn("Qty", format="%d"),
                             "orders": st.column_config.NumberColumn("Orders", format="%d")})
        with acol:
            st.markdown("**Asks**")
            st.dataframe(asks[["price", "qty", "orders"]], use_container_width=True,
                         hide_index=True,
                         column_config={
                             "price": st.column_config.NumberColumn("Price", format="%.2f"),
                             "qty": st.column_config.NumberColumn("Qty", format="%d"),
                             "orders": st.column_config.NumberColumn("Orders", format="%d")})
        if not bids.empty and not asks.empty:
            spread = asks["price"].iloc[0] - bids["price"].iloc[0]
            st.caption(f"Spread: {spread:.2f} MAD "
                       f"({spread / bids['price'].iloc[0] * 100:.2f}%)")

with tab_news:
    news = get_news()
    for _, a in news.iterrows():
        src = "Medias24" if a["source"] == "medias24" else "L'Économiste"
        date = a["published_at"].strftime("%Y-%m-%d") if pd.notna(a["published_at"]) else ""
        st.markdown(f"**[{a['title']}]({a['url']})**  \n"
                    f"<small style='color:#8a94a6'>{src} · {date}</small>",
                    unsafe_allow_html=True)
        st.divider()

with tab_macro:
    indicators = {
        "gdp_growth_pct": "GDP growth (%)",
        "inflation_cpi_pct": "Inflation — CPI (%)",
        "unemployment_pct": "Unemployment (%)",
        "imf_debt_pct_gdp": "Public debt / GDP (%)",
        "imf_current_account_pct_gdp": "Current account / GDP (%)",
        "gdp_per_capita_usd": "GDP per capita (USD)",
    }
    cols = st.columns(3)
    for i, (ind, label) in enumerate(indicators.items()):
        s = get_macro(ind)
        if s.empty:
            continue
        with cols[i % 3]:
            last, first = s["value"].iloc[-1], s["value"].iloc[0]
            st.metric(label, f"{last:,.2f}",
                      f"{last - s['value'].iloc[-2]:+.2f} pts" if len(s) > 1 else None)
            fig = go.Figure(go.Scatter(x=s["date"], y=s["value"], mode="lines",
                                       line=dict(color="#d9a441", width=1.5)))
            fig.update_layout(**PLOT_TEMPLATE["layout"], height=160,
                              margin=dict(l=40, r=10, t=10, b=20))
            st.plotly_chart(fig, use_container_width=True)

st.caption(f"Data: Medias24 · L'Économiste · World Bank · IMF · Bourse de Casablanca — "
           f"refreshed {datetime.now().strftime('%Y-%m-%d %H:%M')}")
