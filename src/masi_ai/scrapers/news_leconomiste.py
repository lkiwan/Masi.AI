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
    # Divi theme: article body lives under .et-l--body / et_pb_section
    body_el = soup.select_one(".et-l--body") or soup.select_one("article")
    body = body_el.get_text("\n", strip=True) if body_el else None
    if body and len(body) > 6000:
        body = body[:6000]
    return {"source": "leconomiste", "url": url, "title": title,
            "published_at": published, "body": body}


def collect_leconomiste_news(max_articles: int = 40, workers: int = 8) -> dict:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    session = build_session({"Referer": "https://www.leconomiste.com/"})
    r = get_with_backoff(session, SITEMAP_INDEX, timeout=40)
    sitemaps = [u for u in LOC_RE.findall(r.text) if "post-sitemap" in u]

    candidates: list[tuple[str, datetime | None]] = []
    for sm in sitemaps:
        try:
            candidates.extend(_sitemap_urls(session, sm, max_articles))
        except Exception as e:  # noqa: BLE001
            log.warning("sitemap failed %s: %s", sm, e)
        if len(candidates) >= max_articles * 2:
            break

    # newest first
    candidates.sort(key=lambda x: x[1] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

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
            db.add(NewsArticle(**art))
            inserted += 1
    stats = {"candidates": len(candidates), "inserted": inserted,
             "skipped": len(candidates) - len(fresh)}
    log.info("leconomiste news done: %s", stats)
    return stats
