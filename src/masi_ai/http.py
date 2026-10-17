"""HTTP helpers: plain + Cloudflare-bypassing sessions with sane defaults."""
from __future__ import annotations

import logging
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}


def build_session(headers: dict | None = None, retries: int = 3) -> requests.Session:
    s = requests.Session()
    h = dict(DEFAULT_HEADERS)
    if headers:
        h.update(headers)
    s.headers.update(h)
    retry = Retry(
        total=retries,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD"),
        raise_on_status=False,
    )
    s.mount("http://", HTTPAdapter(max_retries=retry))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def build_cloudscraper_session(headers: dict | None = None):
    """Cloudscraper session for WAF-protected hosts (e.g. casablanca-bourse.com)."""
    import cloudscraper

    s = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    h = dict(DEFAULT_HEADERS)
    if headers:
        h.update(headers)
    s.headers.update(h)
    return s


def get_with_backoff(session, url: str, timeout: int = 30, attempts: int = 3, **kwargs):
    last: Exception | None = None
    for i in range(attempts):
        try:
            r = session.get(url, timeout=timeout, **kwargs)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001 - retry on any transport error
            last = e
            log.warning("GET %s failed (attempt %d/%d): %s", url, i + 1, attempts, e)
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"GET {url} failed after {attempts} attempts: {last}")
