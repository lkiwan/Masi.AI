"""CLI: python -m masi_ai.cli <command> [options]

Commands:
  universe            sync the listed-companies universe (auto-adds new listings)
  news                collect news from Medias24 + L'Economiste
  orderbook           snapshot the order book for all instruments
  history-medias24    refresh close history from Medias24
  history-bvc         full OHLCV history from the BVC official API
  macro               collect macro indicators (World Bank, IMF, MASI)
  api                 run the FastAPI server
  scheduler           run the APScheduler worker
"""
from __future__ import annotations

import argparse
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    ap = argparse.ArgumentParser(prog="masi-ai")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("universe")
    sub.add_parser("news")
    sub.add_parser("orderbook")
    sub.add_parser("history-medias24")
    p = sub.add_parser("history-bvc")
    p.add_argument("--tickers", nargs="*", default=None)
    p.add_argument("--no-discover", action="store_true")
    sub.add_parser("macro")
    sub.add_parser("api")
    sub.add_parser("scheduler")

    args = ap.parse_args()

    if args.cmd == "universe":
        from .db import init_db
        from .scrapers.universe import sync_universe
        init_db()
        print(sync_universe())
    elif args.cmd == "news":
        from .db import init_db
        from .scrapers.news_leconomiste import collect_leconomiste_news
        from .scrapers.news_medias24 import collect_medias24_news
        init_db()
        print(collect_medias24_news())
        print(collect_leconomiste_news())
    elif args.cmd == "orderbook":
        from .db import init_db
        from .scrapers.orderbook import snapshot_orderbooks
        init_db()
        print(snapshot_orderbooks())
    elif args.cmd == "history-medias24":
        from .db import init_db
        from .scrapers.history import medias24_history_sync
        init_db()
        print(medias24_history_sync())
    elif args.cmd == "history-bvc":
        from .db import init_db
        from .scrapers.history import bvc_eod_sync
        init_db()
        print(bvc_eod_sync(tickers=args.tickers, discover_ids=not args.no_discover))
    elif args.cmd == "macro":
        from .db import init_db
        from .scrapers.macro import collect_macro
        init_db()
        print(collect_macro())
    elif args.cmd == "api":
        import uvicorn
        uvicorn.run("masi_ai.api:app", host="0.0.0.0", port=8000)
    elif args.cmd == "scheduler":
        from .scheduler import main as sched_main
        sched_main()


if __name__ == "__main__":
    main()
