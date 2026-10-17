"""APScheduler jobs: universe sync, news, order book, EOD history."""
from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .config import settings
from .db import init_db
from .scrapers.history import bvc_eod_sync, medias24_history_sync
from .scrapers.macro import collect_macro
from .scrapers.news_leconomiste import collect_leconomiste_news
from .scrapers.news_medias24 import collect_medias24_news
from .scrapers.orderbook import snapshot_orderbooks
from .scrapers.universe import sync_universe

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def job_universe():
    log.info("=== job: universe sync ===")
    sync_universe()


def job_news():
    log.info("=== job: news ===")
    collect_medias24_news()
    collect_leconomiste_news()


def job_orderbook():
    log.info("=== job: orderbook ===")
    snapshot_orderbooks()


def job_eod():
    log.info("=== job: eod ===")
    medias24_history_sync()   # daily close refresh (lightweight)
    bvc_eod_sync()            # full OHLCV from BVC official API


def job_macro():
    log.info("=== job: macro ===")
    collect_macro()


def main() -> None:
    init_db()
    sched = BlockingScheduler(timezone=settings.tz)

    # New listings are picked up automatically every morning
    sched.add_job(job_universe, CronTrigger(hour=7, minute=30), id="universe", max_instances=1)
    # News every 30 minutes
    sched.add_job(job_news, IntervalTrigger(minutes=settings.news_interval_min),
                  id="news", max_instances=1)
    # Order book every 5 min during market hours (Mon-Fri 09:00-15:45)
    sched.add_job(job_orderbook, CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5"),
                  id="orderbook", max_instances=1)
    # EOD after close (Mon-Fri 16:30)
    sched.add_job(job_eod, CronTrigger(day_of_week="mon-fri", hour=16, minute=30),
                  id="eod", max_instances=1)
    # Macro (World Bank / IMF / MASI) weekly — annual series barely move
    sched.add_job(job_macro, CronTrigger(day_of_week="sun", hour=6, minute=0),
                  id="macro", max_instances=1)

    log.info("scheduler started (tz=%s)", settings.tz)
    sched.start()


if __name__ == "__main__":
    main()
