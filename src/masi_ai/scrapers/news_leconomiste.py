"""News from L'Economiste via its XML sitemaps.

The WP REST API is restricted, so discovery goes through
/sitemap_index.xml -> post-sitemapN.xml (1000 URLs each, newest first).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from ..db import session_scope
from ..http import build_session, get_with_backoff
from ..models import NewsArticle

log = logging.getLogger(__name__)

SITEMAP_INDEX = "https://www.leconomiste.com/sitemap_index.xml"
LOC_RE = re.compile(r"<loc>([^<]+)</loc>")
LASTMOD_RE = re.compile(r"<lastmod>([^<]+)</lastmod>")


def _sitemap_urls(session, sitemap_url: str, limit: int) -> list[tuple[str, datetime | None]]:
    r = get_with_backoff(session, sitemap_url, timeout=40)
    locs = LOC_RE.findall(r.text)
    mods = LASTMOD_RE.findall(r.text)
    out = []
    for i, loc in enumerate(locs[:limit]):
        pub = None
        if i < len(mods):
            try:
                pub = datetime.fromisoformat(mods[i])
            except ValueError:
                pub = None
        out.append((loc, pub))
    return out


def _fix_bogus_year(dt):
    """L'Economiste's site stamps recent articles with year 1991 in every
    date field (verified: 'Le 17/10/1991' on an article published 17/10/2026
    - day/month are right, only the year is wrong). We only ever collect
    the head of the newest sitemaps, so a 1991-dated entry there is a
    recent article with broken metadata, not a genuine 1991 article.
    Capped at today to avoid future dates."""
    if dt is not None and dt.year == 1991:
        now = datetime.now(timezone.utc)
        return min(dt.replace(year=now.year), now)
    return dt


def _parse_article(session, url: str, fallback_date) -> dict | None:
    from bs4 import BeautifulSoup

    try:
        r = get_with_backoff(session, url, timeout=30)
    except Exception as e:  # noqa: BLE001
        log.warning("article fetch failed %s: %s", url, e)
        return None
    soup = BeautifulSoup(r.text, "lxml")
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else ""
    if not title:
        return None
    meta = soup.find("meta", {"property": "article:published_time"})
    published = fallback_date
    if meta and meta.get("content"):
        try:
            published = datetime.fromisoformat(meta["content"])
        except ValueError:
            pass
    published = _fix_bogus_year(published)
    # Divi theme: article body lives under .et-l--body / et_pb_section
    body_el = soup.select_one(".et-l--body") or soup.select_one("article")
    body = body_el.get_text("\n", strip=True) if body_el else None
    if body and len(body) > 6000:
        body = body[:6000]
    return {"source": "leconomiste", "url": url, "title": title,
            "published_at": published, "body": body}


def collect_leconomiste_news(max_articles: int = 40, workers: int = 8) -> dict:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    session = build_session({"Referer": "https://www.leconomiste.com/"})
    r = get_with_backoff(session, SITEMAP_INDEX, timeout=40)
    sitemaps = [u for u in LOC_RE.findall(r.text) if "post-sitemap" in u]

    # Sitemaps are numbered chronologically (post-sitemap.xml = oldest,
    # highest number = newest). Traverse newest-first so we always get the
    # latest articles. (lastmod dates are unreliable on this site.)
    def _sitemap_num(u: str) -> int:
        m = re.search(r"post-sitemap(\d*)\.xml", u)
        return int(m.group(1) or 1) if m else 0

    sitemaps.sort(key=_sitemap_num, reverse=True)

    # NOTE: lastmod dates in these sitemaps are bogus (e.g. 1991), so we rely
    # on sitemap order instead: post-sitemap.xml holds the newest articles.
    # The same URL can appear in several sitemap pages -> dedupe by URL.
    candidates: list[tuple[str, datetime | None]] = []
    seen_urls: set[str] = set()
    for sm in sitemaps:
        try:
            for url, pub in _sitemap_urls(session, sm, max_articles):
                if url not in seen_urls:
                    seen_urls.add(url)
                    candidates.append((url, pub))
        except Exception as e:  # noqa: BLE001
            log.warning("sitemap failed %s: %s", sm, e)
        if len(candidates) >= max_articles * 2:
            break

    with session_scope() as db:
        existing = {u for (u,) in db.query(NewsArticle.url).filter(
            NewsArticle.source == "leconomiste").all()}
    fresh = [(u, p) for u, p in candidates if u not in existing][:max_articles]

    arts: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_parse_article, session, u, p): u for u, p in fresh}
        for fut in as_completed(futures):
            art = fut.result()
            if art:
                arts.append(art)

    inserted = 0
    with session_scope() as db:
        for art in arts:
            stmt = pg_insert(NewsArticle).values(**art).on_conflict_do_nothing(
                constraint="uq_news_url")
            res = db.execute(stmt)
            inserted += res.rowcount or 0
    stats = {"candidates": len(candidates), "inserted": inserted,
             "skipped": len(candidates) - len(fresh)}
    log.info("leconomiste news done: %s", stats)
    return stats
