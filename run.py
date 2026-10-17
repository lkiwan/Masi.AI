#!/usr/bin/env python3
"""Masi.AI one-file runner — makes everything work.

Usage:
    python run.py                  # bootstrap (first run) + start the scheduler
    python run.py --api            # bootstrap + start the API server
    python run.py --bootstrap-only  # just do the first-time data load, then exit
    python run.py --full-history    # also pull FULL OHLCV history from BVC (slow, run once)

What it does:
    1. Checks Python >= 3.10 and installs requirements if missing.
    2. Waits for Postgres (from .env, defaults to localhost:5432).
    3. Creates all tables.
    4. First-time bootstrap: companies universe -> macro -> price history
       -> news -> order book snapshot.
    5. Starts the scheduler (or the API with --api).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
VENV = ROOT / ".venv"
VENV_PY = VENV / "bin" / "python"


def ensure_venv() -> None:
    """Re-exec inside a project-local venv so installs never touch system python."""
    in_venv = Path(sys.prefix).resolve() == VENV.resolve()
    if in_venv or os.environ.get("MASI_AI_VENV"):
        return
    if not VENV_PY.exists():
        step("Creating project virtualenv (.venv)")
        run([sys.executable, "-m", "venv", str(VENV)])
        run([str(VENV_PY), "-m", "pip", "install", "--upgrade", "pip", "-q"])
        run([str(VENV_PY), "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")])
    os.environ["MASI_AI_VENV"] = "1"
    os.execv(str(VENV_PY), [str(VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])

MIN_PYTHON = (3, 10)


def step(msg: str) -> None:
    print(f"\n==> {msg}", flush=True)


def run(cmd: list[str], **kwargs) -> None:
    print("    $", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, **kwargs)


def ensure_deps() -> None:
    step("Checking dependencies")
    try:
        import fastapi, sqlalchemy, psycopg2, apscheduler, bs4  # noqa: F401
        print("    all dependencies present", flush=True)
    except ImportError:
        print("    installing requirements", flush=True)
        run([sys.executable, "-m", "pip", "install", "-q",
             "-r", str(ROOT / "requirements.txt")])


def wait_for_postgres(timeout: int = 60) -> None:
    step("Waiting for Postgres")
    from masi_ai.config import settings

    import psycopg2
    deadline = time.time() + timeout
    while True:
        try:
            conn = psycopg2.connect(
                host=settings.postgres_host, port=settings.postgres_port,
                user=settings.postgres_user, password=settings.postgres_password,
                dbname=settings.postgres_db, connect_timeout=5,
            )
            conn.close()
            print(f"    Postgres up at {settings.postgres_host}:{settings.postgres_port}",
                  flush=True)
            return
        except Exception as e:  # noqa: BLE001
            if time.time() > deadline:
                print("\n    ERROR: cannot reach Postgres.", flush=True)
                print(f"    Tried {settings.postgres_host}:{settings.postgres_port} "
                      f"as {settings.postgres_user}.", flush=True)
                print("    Fix: start it with `docker compose up -d postgres`, "
                      "or set POSTGRES_HOST/PORT/USER/PASSWORD/DB in .env", flush=True)
                raise SystemExit(1) from e
            time.sleep(2)


def bootstrap(full_history: bool = False) -> None:
    from masi_ai.db import init_db
    from masi_ai.scrapers.universe import sync_universe
    from masi_ai.scrapers.macro import collect_macro
    from masi_ai.scrapers.history import medias24_history_sync, bvc_eod_sync
    from masi_ai.scrapers.news_medias24 import collect_medias24_news
    from masi_ai.scrapers.news_leconomiste import collect_leconomiste_news
    from masi_ai.scrapers.orderbook import snapshot_orderbooks

    step("Creating tables")
    init_db()
    print("    tables ready", flush=True)

    step("1/5 companies universe (auto-adds new listings)")
    print("   ", sync_universe(), flush=True)

    step("2/5 macro indicators (World Bank + IMF + MASI)")
    print("   ", collect_macro(), flush=True)

    step("3/5 price history (Medias24 closes)")
    print("   ", medias24_history_sync(), flush=True)

    if full_history:
        step("3b/5 FULL OHLCV history from BVC official API (slow, one-time)")
        print("   ", bvc_eod_sync(), flush=True)

    step("4/5 news (Medias24 + L'Economiste)")
    print("   ", collect_medias24_news(), flush=True)
    print("   ", collect_leconomiste_news(), flush=True)

    step("5/5 order book snapshot")
    print("   ", snapshot_orderbooks(), flush=True)

    step("Bootstrap complete — database is loaded")


def main() -> None:
    ap = argparse.ArgumentParser(description="Masi.AI one-file runner")
    ap.add_argument("--api", action="store_true", help="start the API server instead of the scheduler")
    ap.add_argument("--bootstrap-only", action="store_true", help="only load data, then exit")
    ap.add_argument("--full-history", action="store_true",
                    help="also pull full OHLCV history from BVC (slow)")
    ap.add_argument("--skip-bootstrap", action="store_true", help="skip the data load")
    args = ap.parse_args()

    if sys.version_info < MIN_PYTHON:
        raise SystemExit(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required")

    ensure_venv()  # re-exec inside .venv if needed
    sys.path.insert(0, str(SRC))

    if not (ROOT / ".env").exists() and (ROOT / ".env.example").exists():
        print("No .env found — copying .env.example (edit it to set your password)",
              flush=True)
        (ROOT / ".env").write_text((ROOT / ".env.example").read_text())

    ensure_deps()
    wait_for_postgres()

    if not args.skip_bootstrap:
        bootstrap(full_history=args.full_history)
    if args.bootstrap_only:
        return

    if args.api:
        step("Starting API on http://0.0.0.0:8000")
        import uvicorn
        uvicorn.run("masi_ai.api:app", host="0.0.0.0", port=8000)
    else:
        step("Starting scheduler (Ctrl+C to stop)")
        from masi_ai.scheduler import main as sched_main
        sched_main()


if __name__ == "__main__":
    main()
