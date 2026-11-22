from __future__ import annotations

import argparse
import asyncio
import logging

from concall.config import settings
from concall.db import Store
from concall.pipeline import export_samples, run_once


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="NSE/BSE concall pipeline")
    parser.add_argument(
        "command",
        nargs="?",
        default="serve",
        choices=["serve", "backfill", "export-samples"],
    )
    args = parser.parse_args()
    store = Store(settings.data_dir / "concall.db")

    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "concall.serve.app:app",
            host=settings.host,
            port=settings.port,
            log_level="info",
        )
        return
    if args.command == "backfill":
        n = asyncio.run(run_once(store))
        print(f"processed {n} filings")
        return
    paths = export_samples(store)
    for p in paths:
        print(p)


if __name__ == "__main__":
    main()
