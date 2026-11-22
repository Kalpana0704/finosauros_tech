from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

from concall.config import load_watchlist, settings
from concall.db import Store
from concall.extract.pdf import extract_pdf_bytes, looks_like_transcript
from concall.ingest.bse import fetch_announcements as bse_fetch
from concall.ingest.classify import classify_filing, in_watchlist
from concall.ingest.http import ExchangeClient
from concall.ingest.nse import fetch_announcements as nse_fetch
from concall.models import ConcallSummary, Filing
from concall.summarize.extractive import extractive_summary
from concall.summarize.llm import LLMError, provider_name, summarize_transcript

log = logging.getLogger("concall.pipeline")

Broadcast = Callable[[dict[str, Any]], Awaitable[None]]


def _dmy(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _meta(filing: Filing, extra: dict | None = None) -> dict[str, Any]:
    announced = filing.announced_at.date().isoformat() if filing.announced_at else ""
    data = {
        "company": filing.company,
        "nse_symbol": filing.nse_symbol,
        "scrip_code": filing.scrip_code,
        "source": filing.source,
        "source_url": filing.pdf_url or filing.source_url,
        "call_date": announced,
    }
    if extra:
        data.update(extra)
    return data


async def collect_filings(
    client: ExchangeClient,
    companies: list[dict],
    lookback_days: int | None = None,
) -> list[Filing]:
    days = lookback_days if lookback_days is not None else settings.lookback_days
    end = date.today()
    start = end - timedelta(days=days)
    found: list[Filing] = []

    for c in companies:
        nse_rows = await nse_fetch(
            client,
            symbol=c["nse_symbol"],
            from_date=_dmy(start),
            to_date=_dmy(end),
        )
        bse_rows = await bse_fetch(
            client,
            scrip=str(c["bse_scrip"]),
            from_yyyymmdd=_ymd(start),
            to_yyyymmdd=_ymd(end),
        )
        found.extend(nse_rows)
        found.extend(bse_rows)

    interesting: list[Filing] = []
    for f in found:
        classify_filing(f)
        match = in_watchlist(f.nse_symbol, f.scrip_code, companies)
        if not match:
            continue
        if not f.nse_symbol:
            f.nse_symbol = match["nse_symbol"]
        if not f.scrip_code:
            f.scrip_code = str(match["bse_scrip"])
        if f.company in {f.nse_symbol, f.scrip_code}:
            f.company = match["name"]
        if f.kind in {"transcript", "audio_only"}:
            interesting.append(f)
    return interesting


async def process_filing(
    client: ExchangeClient,
    store: Store,
    filing: Filing,
    broadcast: Broadcast | None = None,
) -> ConcallSummary | None:
    existing = store.filing_status(filing.source, filing.source_id)
    if existing and existing[1] not in {"failed", "queued"}:
        return None

    fid = existing[0] if existing else store.insert_filing(filing.model_dump(mode="json"))
    log.info("queued %s %s %s [%s]", filing.source, filing.nse_symbol, filing.kind, filing.title[:80])

    if filing.kind == "audio_only":
        summary = ConcallSummary(
            company=filing.company,
            scrip_code=filing.scrip_code,
            nse_symbol=filing.nse_symbol,
            source=filing.source,
            source_url=filing.pdf_url or filing.source_url,
            call_date=filing.announced_at.date().isoformat() if filing.announced_at else "",
            tldr=[
                "Exchange filing is an audio/video recording, not a transcript.",
                "Pipeline is waiting for the written transcript (typically 5–24 hours later).",
            ],
            audio_only=True,
            model="none",
            limitations=["No transcript PDF in this filing."],
        )
        store.update_filing(fid, status="done", kind="audio_only")
        store.save_summary(fid, summary.model_dump(), summary.to_markdown(), summary.model)
        if broadcast:
            await broadcast(_event(fid, summary, "audio_only"))
        return summary

    if not filing.pdf_url:
        store.update_filing(fid, status="failed", error="no attachment url")
        return None

    try:
        blob = await client.download_bytes(
            filing.pdf_url,
            referer="https://www.nseindia.com/" if filing.source == "NSE" else "https://www.bseindia.com/",
        )
    except Exception as exc:
        log.warning("download failed %s: %s", filing.pdf_url, exc)
        store.update_filing(fid, status="failed", error=f"download: {exc}")
        return None

    digest = hashlib.sha256(blob).hexdigest()
    dup = store.find_duplicate_hash(digest)
    if dup:
        store.update_filing(
            fid, status="duplicate", pdf_hash=digest, error=f"same pdf as filing {dup}"
        )
        log.info("dedup hash %s -> %s", filing.source_id, dup)
        return None

    pdf_path = settings.data_dir / "pdfs" / f"{fid}_{digest[:12]}.pdf"
    pdf_path.write_bytes(blob)
    store.update_filing(fid, pdf_path=str(pdf_path), pdf_hash=digest, status="extracted")

    extracted = extract_pdf_bytes(blob)
    text_path = settings.data_dir / "transcripts" / f"{fid}.txt"
    text_path.write_text(extracted.text, encoding="utf-8")
    store.save_transcript(fid, extracted.text, extracted.page_count, extracted.method)

    if not looks_like_transcript(extracted.text):
        store.update_filing(
            fid,
            status="skipped",
            error="attachment does not look like a concall transcript",
        )
        log.info("skip non-transcript attachment for %s", filing.nse_symbol)
        return None

    meta = _meta(filing)
    summary: ConcallSummary
    try:
        if provider_name() == "extractive-fallback":
            summary = extractive_summary(extracted.text, meta)
        else:
            try:
                summary = await summarize_transcript(extracted.text, meta)
            except (LLMError, Exception) as exc:
                log.warning("LLM failed, falling back to extractive: %s", exc)
                summary = extractive_summary(extracted.text, meta)
                summary.limitations.append(f"LLM error: {exc}")
    except Exception as exc:
        store.update_filing(fid, status="failed", error=f"summarize: {exc}")
        return None

    summary.limitations.extend(extracted.warnings)
    md = summary.to_markdown()
    store.save_summary(fid, summary.model_dump(), md, summary.model)
    store.update_filing(fid, status="done")
    if broadcast:
        await broadcast(_event(fid, summary, "summary"))
    log.info("summarized %s %s via %s", filing.nse_symbol, summary.quarter, summary.model)
    return summary


def _event(fid: int, summary: ConcallSummary, kind: str) -> dict[str, Any]:
    return {
        "type": kind,
        "filing_id": fid,
        "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "summary": summary.model_dump(),
        "markdown": summary.to_markdown(),
    }


async def run_once(
    store: Store,
    broadcast: Broadcast | None = None,
    lookback_days: int | None = None,
) -> int:
    companies = load_watchlist(settings.watchlist)
    processed = 0
    async with _client() as client:
        filings = await collect_filings(client, companies, lookback_days)
        log.info("watchlist hits: %s transcript/audio filings", len(filings))
        for filing in filings:
            result = await process_filing(client, store, filing, broadcast)
            if result:
                processed += 1
    return processed


class _client:
    def __init__(self) -> None:
        self.client = ExchangeClient()

    async def __aenter__(self) -> ExchangeClient:
        return self.client

    async def __aexit__(self, *exc: object) -> None:
        await self.client.aclose()


async def watch_forever(store: Store, broadcast: Broadcast | None = None) -> None:
    import asyncio

    # Startup backfill so the UI is not empty.
    try:
        await run_once(store, broadcast, lookback_days=settings.lookback_days)
    except Exception:
        log.exception("initial backfill failed")

    while True:
        try:
            await run_once(store, broadcast, lookback_days=2)
        except Exception:
            log.exception("watch cycle failed")
        await asyncio.sleep(settings.poll_seconds)


def export_samples(store: Store, n: int = 3) -> list[Path]:
    rows = [
        r
        for r in store.list_summaries()
        if r.get("status") == "done" and r.get("summary") and r.get("kind") == "transcript"
    ]
    written: list[Path] = []
    for row in rows[: max(n, 5)]:
        fid = row["id"]
        full = store.get_summary(fid)
        if not full:
            continue
        symbol = (row.get("nse_symbol") or f"id{fid}").replace(" ", "")
        dest = settings.sample_dir / symbol
        dest.mkdir(parents=True, exist_ok=True)
        transcript = full.get("transcript") or ""
        (dest / "transcript.txt").write_text(transcript, encoding="utf-8")
        if full.get("payload_json"):
            (dest / "summary.json").write_text(full["payload_json"], encoding="utf-8")
        if full.get("markdown"):
            (dest / "summary.md").write_text(full["markdown"], encoding="utf-8")
        written.append(dest)
    return written
