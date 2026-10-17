"""News from Medias24 (leboursier categories)."""
from __future__ import annotations

import logging
import re
from datetime import datetime

from ..db import session_scope
from ..http import build_session, get_with_backoff
from ..models import NewsArticle

log = logging.getLogger(__name__)

CATEGORIES = [
    "https://medias24.com/categorie/leboursier/actus/",
    "https://medias24.com/categorie/leboursier/la-seance-du-jour/",
    "https://medias24.com/categorie/leboursier/communication-financiere/",
]
ARTICLE_RE = re.compile(r"https://medias24\.com/20\d\d/\d\d/\d\d/[^/]+/?$")


def _parse_article(session, url: str) -> dict | None:
    from bs4 import BeautifulSoup

    try:
        r = get_with_backoff(session, url, timeout=30)
    except Exception as e:  # noqa: BLE001
        log.warning("article fetch failed %s: %s", url, e)
        return None
    soup = BeautifulSoup(r.text, "lxml")
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else ""
    meta = soup.find("meta", {"property": "article:published_time"})
    published = None
    if meta and meta.get("content"):
        try:
            published = datetime.fromisoformat(meta["content"])
        except ValueError:
            published = None
    body_el = soup.select_one(".article-content")
    body = body_el.get_text("\n", strip=True) if body_el else None
    if not title:
        return None
    return {"source": "medias24", "url": url, "title": title,
            "published_at": published, "body": body}


def collect_medias24_news(max_articles: int = 60, workers: int = 8) -> dict:
    from bs4 import BeautifulSoup
    from concurrent.futures import ThreadPoolExecutor, as_completed

    session = build_session({"Referer": "https://medias24.com/"})
    urls: list[str] = []
    for cat in CATEGORIES:
        try:
            r = get_with_backoff(session, cat, timeout=30)
        except Exception as e:  # noqa: BLE001
            log.warning("category fetch failed %s: %s", cat, e)
            continue
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.find_all("a", href=True):
            href = a["href"].split("#")[0]
            if ARTICLE_RE.match(href) and href not in urls:
                urls.append(href)
        if len(urls) >= max_articles:
            break

    with session_scope() as db:
        existing = {u for (u,) in db.query(NewsArticle.url).filter(
            NewsArticle.source == "medias24").all()}
    fresh = [u for u in urls[:max_articles] if u not in existing]

    arts: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_parse_article, session, u): u for u in fresh}
        for fut in as_completed(futures):
            art = fut.result()
            if art:
                arts.append(art)

    inserted = 0
    with session_scope() as db:
        for art in arts:
            db.add(NewsArticle(**art))
            inserted += 1
    stats = {"found": len(urls), "inserted": inserted, "skipped": len(urls) - len(fresh)}
    log.info("medias24 news done: %s", stats)
    return stats
