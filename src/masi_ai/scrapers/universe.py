"""Instrument universe sync.

Sources:
  - casabourse.ma homepage: canonical ticker list (e.g. ATW, IAM) + company slugs.
  - medias24.com fiche-action dropdown: medias24 slugs for every listed value.

Matching is done on normalized company names. New tickers are inserted as
active automatically; tickers that disappear are marked inactive (never deleted).
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timezone

from ..db import session_scope
from ..http import build_session, get_with_backoff
from ..medias24 import Medias24Client
from ..models import Instrument

log = logging.getLogger(__name__)

CASABOURSE_HOME = "https://casabourse.ma/"

# medias24 labels don't always match company names (abbreviations, typos).
# Maps ticker -> medias24 dropdown label for the tricky ones.
TICKER_ALIASES = {
    "MSA": ["SODEP P"],            # Marsa Maroc = SODEP
    "WAA": ["Wafa Assur P"],
    "AKT": ["Aktidal"],            # medias24 typo for Akdital
    "SAH": ["Saham Assurance N"],  # Sanlam Maroc ex-Saham
    "STR": ["STROC Indus. P"],
    "S2M": ["S2M P"],
    "AFI": ["Afric Indus."],
    "IBC": ["IBMaroc.com P"],
    "LBV": ["LABEL VIE P"],
    "ATL": ["ATLANTA P"],
    "SBM": ["Ste Boissons P"],
    "SNA": ["SNA P"],
    "SRM": ["SRM P"],
    "IMO": ["Immr Invest P"],
    "CMT": ["CMT"],
    "TQM": ["TAQA Morocco P"],
    "GTM": ["SGTM P"],
    "TGC": ["TGCC"],
    "LHM": ["LafargeHolcim P"],
    "CMA": ["Ciments Maroc"],
    "CFG": ["CFG Bank"],
    "CIH": ["CIH P"],
    "CDM": ["CDM P"],
    "DHO": ["Addoha P"],
    "LES": ["Lesieur Cristal"],
    "MAB": ["Maghrebail P"],
    "MLE": ["Maroc Leasing N"],
    "CTM": ["CTM P"],
    "ALM": ["Aluminium Maroc P"],
    "SOT": ["SOTHEMA"],
    "SNP": ["SNEP P"],
    "FBR": ["FENIE BROSSETTE P"],
    "DWY": ["DISWAY P"],
    "JET": ["Jet Contractors P"],
    "RDS": ["Resid Dar Saada P"],
    "DRI": ["Dari Couspate"],
    "COL": ["Colorado P"],
    "BAL": ["BALIMA P"],
    "CRS": ["Cartier Saada P"],
    "ZDJ": ["Zellidja P"],
    "MDP": ["Med Paper P"],
    "INV": ["INVOLYS P"],
    "REB": ["Rebab Company P"],
    "NEJ": ["Auto Nejma P"],
    "ARD": ["Aradei Capital"],
    "CAP": ["Cash Plus P"],
    "T2S": ["T2S Group Holding"],
    "CMG": ["CMGP GROUP"],
    "HPS": ["HPS P"],
    "VCN": ["Vicenne P"],
    "ATH": ["ATLANTA P"],
    "EQD": ["EQDOM P"],
    "OUL": ["Oulmes P"],
    "MUT": ["Mutandis Br"],
    "UMR": ["Unimer P"],
    "PRO": ["PROMOPHARM"],
    "NKL": ["Ennakl N"],
    "AGM": ["Agma P"],
    "SLF": ["SALAFIN P"],
    "MIC": ["Microdata N"],
    "AFM": ["AFMA P"],
    "DYT": ["Disty Technologies"],
    "MOX": ["Maghreb Oxygene P"],
    "M2M": ["M2M Group P"],
    "GAZ": ["Afriquia Gaz P"],
    "TMA": ["Total Maroc P"],
    "SID": ["Sonasid P"],
    "CSR": ["COSUMAR"],
    "SMI": ["SMI P"],
    "ADI": ["Alliances P"],
    "DIS": ["Diac Salaf P"],
    "BCI": ["BMCI P"],
    "BOA": ["Bank Of Africa", "BMCE Bank P"],
    "BCP": ["BCP P"],
    "IAM": ["Maroc Telecom"],
    "ATW": ["Attijariwafa Bank P"],
    "MNG": ["Managem P"],
}


def normalize_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    tokens = [t for t in s.split() if t not in {"p", "br", "n", "sa", "s", "sca", "sca."}]
    return " ".join(tokens).strip()


def fetch_casabourse_tickers() -> list[dict]:
    """[{ticker, name, slug}] from the casabourse.ma homepage ticker tape."""
    from bs4 import BeautifulSoup

    s = build_session()
    r = get_with_backoff(s, CASABOURSE_HOME, timeout=30)
    soup = BeautifulSoup(r.text, "lxml")
    out: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        m = re.match(r"https://casabourse\.ma/entreprise/([a-z0-9-]+)/$", a["href"])
        if not m:
            continue
        txt = a.get_text(" ", strip=True)
        tm = re.match(r"([A-Z0-9]{2,6})\s", txt)
        if not tm:
            continue
        slug = m.group(1)
        if slug not in out:
            out[slug] = {
                "ticker": tm.group(1),
                "name": slug.replace("-", " ").title(),
                "slug": slug,
            }
    return list(out.values())


def sync_universe() -> dict:
    """Sync the full BVC instrument universe. Returns stats."""
    client = Medias24Client()
    m24_values = client.list_values()
    log.info("medias24 values: %d", len(m24_values))
    cb = fetch_casabourse_tickers()
    log.info("casabourse.ma tickers: %d", len(cb))

    # index medias24 values by normalized label
    m24_by_name: dict[str, dict] = {}
    for v in m24_values:
        key = normalize_name(v["label"])
        m24_by_name.setdefault(key, v)
    # plus explicit ticker aliases (medias24 uses abbreviations / old names)
    alias_index: dict[str, dict] = {}
    for ticker, labels in TICKER_ALIASES.items():
        for lbl in labels:
            v = m24_by_name.get(normalize_name(lbl))
            if v:
                alias_index[ticker] = v
                break

    added, updated, matched = 0, 0, 0
    seen: set[str] = set()
    now = datetime.now(timezone.utc)
    new_isins: dict[str, str | None] = {}

    # discover ISINs for new tickers in parallel (only ones missing it)
    with session_scope() as db:
        need_isin = []
        for row in cb:
            inst = db.get(Instrument, row["ticker"])
            norm = normalize_name(row["name"])
            m24 = alias_index.get(row["ticker"]) or m24_by_name.get(norm)
            if not m24:
                row_tokens = set(norm.split())
                best, best_score = None, 0.0
                for key, v in m24_by_name.items():
                    key_tokens = set(key.split())
                    if not key_tokens:
                        continue
                    score = len(row_tokens & key_tokens) / len(row_tokens | key_tokens)
                    if score > best_score:
                        best, best_score = v, score
                if best_score >= 0.5:
                    m24 = best
            if (inst is None or not inst.isin) and m24:
                need_isin.append((row["ticker"], m24["slug"]))

    if need_isin:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _isin(ticker: str, slug: str):
            try:
                return ticker, client.isin_for_slug(slug)
            except Exception as e:  # noqa: BLE001
                log.warning("isin discovery failed for %s: %s", ticker, e)
                return ticker, None

        with ThreadPoolExecutor(max_workers=8) as pool:
            for fut in as_completed(pool.submit(_isin, t, s) for t, s in need_isin):
                t, isin = fut.result()
                new_isins[t] = isin

    with session_scope() as db:
        for row in cb:
            ticker = row["ticker"]
            seen.add(ticker)
            norm = normalize_name(row["name"])
            m24 = alias_index.get(ticker) or m24_by_name.get(norm)
            if not m24:
                row_tokens = set(norm.split())
                best, best_score = None, 0.0
                for key, v in m24_by_name.items():
                    key_tokens = set(key.split())
                    if not key_tokens:
                        continue
                    score = len(row_tokens & key_tokens) / len(row_tokens | key_tokens)
                    if score > best_score:
                        best, best_score = v, score
                if best_score >= 0.5:
                    m24 = best

            inst = db.get(Instrument, ticker)
            if inst is None:
                isin = new_isins.get(ticker)
                inst = Instrument(
                    ticker=ticker,
                    name=row["name"],
                    isin=isin,
                    medias24_slug=m24["slug"] if m24 else None,
                    active=True,
                )
                db.add(inst)
                added += 1
                log.info("NEW listing: %s (%s) isin=%s", ticker, row["name"], isin)
            else:
                changed = False
                if m24 and inst.medias24_slug != m24["slug"]:
                    inst.medias24_slug = m24["slug"]
                    changed = True
                if not inst.isin and ticker in new_isins and new_isins[ticker]:
                    inst.isin = new_isins[ticker]
                    changed = True
                if not inst.active:
                    inst.active = True
                    changed = True
                inst.last_seen = now
                if changed:
                    updated += 1
            if m24:
                matched += 1

        # mark disappeared tickers inactive
        for inst in db.query(Instrument).filter(Instrument.active.is_(True)).all():
            if inst.ticker not in seen:
                inst.active = False
                log.info("delisted (marked inactive): %s", inst.ticker)

    stats = {"casabourse_tickers": len(cb), "medias24_values": len(m24_values),
             "matched": matched, "added": added, "updated": updated}
    log.info("universe sync done: %s", stats)
    return stats
