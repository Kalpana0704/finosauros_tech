from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from concall.config import load_watchlist, settings
from concall.db import Store
from concall.pipeline import export_samples, run_once, watch_forever
from concall.serve.hub import Hub
from concall.summarize.llm import provider_name

log = logging.getLogger("concall.serve")
STATIC = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    store = Store(settings.data_dir / "concall.db")
    hub = Hub()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        app.state.store = store
        app.state.hub = hub
        task = asyncio.create_task(watch_forever(store, hub.publish))
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    app = FastAPI(title="Finosauras Concall Pipeline", lifespan=lifespan)

    @app.get("/api/health")
    async def health():
        return {
            "ok": True,
            "provider": provider_name(),
            "stats": store.stats(),
        }

    @app.get("/api/companies")
    async def companies():
        return load_watchlist(settings.watchlist)

    @app.get("/api/summaries")
    async def summaries():
        rows = store.list_summaries()
        slim = []
        for r in rows:
            s = r.get("summary") or {}
            slim.append(
                {
                    "id": r["id"],
                    "company": r["company"],
                    "nse_symbol": r["nse_symbol"],
                    "scrip_code": r["scrip_code"],
                    "source": r["source"],
                    "title": r["title"],
                    "announced_at": r["announced_at"],
                    "status": r["status"],
                    "kind": r["kind"],
                    "quarter": s.get("quarter", ""),
                    "tldr": s.get("tldr") or [],
                    "model": r.get("model"),
                }
            )
        return slim

    @app.get("/api/summaries/{filing_id}")
    async def summary(filing_id: int):
        row = store.get_summary(filing_id)
        if not row:
            return JSONResponse({"error": "not found"}, status_code=404)
        return {
            "id": row["id"],
            "company": row["company"],
            "nse_symbol": row["nse_symbol"],
            "scrip_code": row["scrip_code"],
            "source": row["source"],
            "title": row["title"],
            "announced_at": row["announced_at"],
            "status": row["status"],
            "kind": row["kind"],
            "source_url": row.get("pdf_url") or row.get("source_url"),
            "page_count": row.get("page_count"),
            "extract_method": row.get("extract_method"),
            "summary": row.get("summary"),
            "markdown": row.get("markdown"),
            "transcript_preview": (row.get("transcript") or "")[:4000],
        }

    @app.post("/api/backfill")
    async def backfill():
        n = await run_once(store, hub.publish, lookback_days=settings.lookback_days)
        return {"processed": n, "stats": store.stats()}

    @app.post("/api/export-samples")
    async def samples():
        paths = export_samples(store)
        return {"written": [str(p) for p in paths]}

    @app.websocket("/ws")
    async def ws_feed(ws: WebSocket):
        await hub.connect(ws)
        try:
            await ws.send_json({"type": "hello", "recent": hub.recent[-10:]})
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            await hub.disconnect(ws)
        except Exception:
            await hub.disconnect(ws)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    return app


app = create_app()
