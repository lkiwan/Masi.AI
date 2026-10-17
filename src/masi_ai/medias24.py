"""Client for the Medias24 bourse JSON API.

Endpoints (reverse-engineered from medias24.com leboursier JS bundles):
    /content/api?method=getBidAsk&ISIN=<isin>&format=json        -> carnet d'ordres
    /content/api?method=getStockInfo&ISIN=<isin>&format=json     -> quote snapshot
    /content/api?method=getTransactions&ISIN=<isin>&format=json  -> day trades
    /content/api?method=getStockHistory&ISIN=<isin>&periode=1m|3m|1y|...&format=json
                                                                -> close-price history
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

from .http import build_session, get_with_backoff

log = logging.getLogger(__name__)

API_ROOT = "https://medias24.com/content/api"
FICHE_URL = "https://medias24.com/leboursier/fiche-action"
TOKEN_RE = re.compile(r"token=([0-9a-f-]{36})")
ISIN_RE = re.compile(r"MA000001\d{4}")


class Medias24Client:
    """Thread-safe-ish client. Token is fetched ONCE per instance and reused
    for every API call (it is not slug-bound), halving request volume."""

    def __init__(self, token_slug: str = "attijariwafa-bank") -> None:
        self.session = build_session({"Referer": FICHE_URL})
        self._token_slug = token_slug
        self._token: str | None = None
        self._token_lock = threading.Lock()

    # -- token -----------------------------------------------------------------
    def ensure_token(self) -> str | None:
        if self._token is None:
            with self._token_lock:
                if self._token is None:
                    r = get_with_backoff(self.session, f"{FICHE_URL}?action={self._token_slug}", timeout=30)
                    m = TOKEN_RE.search(r.text)
                    self._token = m.group(1) if m else None
                    if not self._token:
                        log.warning("no medias24 token found (slug=%s)", self._token_slug)
        return self._token

    # -- low level --------------------------------------------------------------
    def api(self, method: str, isin: str, **extra: Any) -> Any:
        params = {
            "method": method,
            "ISIN": isin,
            "format": "json",
            "t": int(time.time() * 1000),
        }
        params.update(extra)
        token = self.ensure_token()
        if token:
            params["token"] = token
        r = get_with_backoff(self.session, API_ROOT, params=params, timeout=30)
        payload = r.json()
        if not isinstance(payload, dict) or "result" not in payload:
            raise RuntimeError(f"{method}: unexpected payload shape: {str(payload)[:200]}")
        return payload["result"]

    # -- public methods ----------------------------------------------------------
    def get_bid_ask(self, isin: str) -> list[dict]:
        """Carnet d'ordres: list of {bidValue,bidQte,bidOrder,askValue,askQte,askOrder}."""
        res = self.api("getBidAsk", isin)
        if isinstance(res, dict):
            return res.get("orderBook", []) or []
        return []

    def get_stock_info(self, isin: str) -> dict:
        res = self.api("getStockInfo", isin)
        if isinstance(res, list):
            res = next((x for x in res if isinstance(x, dict)), {})
        return res if isinstance(res, dict) else {}

    def get_transactions(self, isin: str) -> list[dict]:
        res = self.api("getTransactions", isin)
        return res if isinstance(res, list) else []

    def get_stock_history(self, isin: str, periode: str = "1y") -> list[tuple[int, float]]:
        """Close-price history: list of (unix_ts, close)."""
        res = self.api("getStockHistory", isin, periode=periode)
        labels = res.get("labels", []) if isinstance(res, dict) else []
        prices = res.get("prices", []) if isinstance(res, dict) else []
        out = []
        for ts, px in zip(labels, prices):
            try:
                px = float(px)
            except (TypeError, ValueError):
                continue
            if px > 0:  # exactness: drop null/zero/negative closes
                out.append((int(ts), px))
        return out

    def get_index_history(self, index: str = "masi", periode: str = "1y") -> list[tuple[int, float]]:
        """Index history (masi, msi20): list of (unix_ts, value)."""
        res = self.api("getMasiHistory" if index == "masi" else "getIndexHistory",
                       index, periode=periode)
        labels = res.get("labels", []) if isinstance(res, dict) else []
        prices = res.get("prices", []) if isinstance(res, dict) else []
        out = []
        for ts, px in zip(labels, prices):
            try:
                px = float(px)
            except (TypeError, ValueError):
                continue
            if px > 0:
                out.append((int(ts), px))
        return out

    # -- universe ----------------------------------------------------------------
    def list_values(self) -> list[dict]:
        """All listed values from the fiche-action dropdown: [{label, slug}]."""
        from bs4 import BeautifulSoup

        r = get_with_backoff(self.session, f"{FICHE_URL}?action=attijariwafa-bank", timeout=30)
        soup = BeautifulSoup(r.text, "lxml")
        out: list[dict] = []
        for sel in soup.find_all("select"):
            opts = sel.find_all("option")
            if len(opts) < 20:
                continue
            for o in opts:
                val = (o.get("value") or "").strip()
                m = re.search(r"action=([a-z0-9-]+)", val)
                if m:
                    out.append({"label": o.get_text(strip=True), "slug": m.group(1)})
            break
        return out

    def isin_for_slug(self, slug: str) -> str | None:
        """ISIN embedded in a fiche-action page."""
        r = get_with_backoff(self.session, f"{FICHE_URL}?action={slug}", timeout=30)
        m = ISIN_RE.search(r.text)
        return m.group(0) if m else None
