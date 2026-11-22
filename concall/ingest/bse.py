from __future__ import annotations

import logging
from datetime import datetime

from concall.ingest.http import ExchangeClient
from concall.models import Filing

log = logging.getLogger("concall.bse")

API = "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
ATTACH = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"
NEW_ATTACH = "https://www.bseindia.com/xml-data/corpfiling/AttachHis/"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    for fmt in (
        "%Y%m%d%H%M%S",
        "%d/%m/%Y %H:%M:%S",
        "%d-%m-%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%d %b %Y %H:%M:%S",
    ):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _pdf_url(row: dict) -> str:
    name = (row.get("ATTACHMENTNAME") or "").strip()
    if not name:
        return ""
    # Live vs historical folders; try live first, downloader follows redirects.
    return ATTACH + name


def _row_to_filing(row: dict) -> Filing:
    newsid = str(row.get("NEWSID") or row.get("NEWS_DT") or row.get("SLONGNAME") or "")
    scrip = str(row.get("SCRIP_CD") or "").strip()
    subject = row.get("NEWSSUB") or row.get("HEADLINE") or ""
    body = row.get("MORE") or row.get("HEADLINE") or ""
    return Filing(
        source="BSE",
        source_id=newsid or f"{scrip}-{subject[:40]}",
        company=row.get("SLONGNAME") or row.get("SHORT_NAME") or scrip,
        nse_symbol=(row.get("NSURL") or "").split("/")[-1] if row.get("NSURL") else "",
        scrip_code=scrip,
        isin=row.get("ISIN") or "",
        title=subject,
        summary_text=body,
        announced_at=_parse_dt(
            str(row.get("NEWS_DT") or row.get("DissemDT") or row.get("DT_TM") or "")
        ),
        source_url="https://www.bseindia.com/corporates/ann.html",
        pdf_url=_pdf_url(row),
        category=row.get("CATEGORYNAME") or row.get("SUBCATNAME") or "",
    )


async def fetch_announcements(
    client: ExchangeClient,
    *,
    scrip: str | None = None,
    from_yyyymmdd: str,
    to_yyyymmdd: str,
    max_pages: int = 8,
) -> list[Filing]:
    out: list[Filing] = []
    page = 1
    while page <= max_pages:
        params = {
            "pageno": page,
            "strCat": "-1",
            "strPrevDate": from_yyyymmdd,
            "strScrip": scrip or "",
            "strSearch": "P",
            "strToDate": to_yyyymmdd,
            "strType": "C",
        }
        try:
            data = await client.bse_json(API, params)
        except Exception as exc:
            log.warning("BSE fetch failed scrip=%s page=%s: %s", scrip, page, exc)
            break
        if not isinstance(data, dict):
            break
        rows = data.get("Table") or []
        if not rows:
            break
        for row in rows:
            if isinstance(row, dict):
                try:
                    out.append(_row_to_filing(row))
                except Exception as exc:
                    log.debug("skip BSE row: %s", exc)
        total_pages = 1
        try:
            total_pages = int(rows[0].get("TotalPageCnt") or 1)
        except (IndexError, TypeError, ValueError):
            total_pages = 1
        if page >= total_pages:
            break
        page += 1
    return out
