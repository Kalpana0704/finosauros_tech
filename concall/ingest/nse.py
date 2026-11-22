from __future__ import annotations

import logging
from datetime import datetime

from concall.ingest.http import ExchangeClient
from concall.models import Filing

log = logging.getLogger("concall.nse")

API = "https://www.nseindia.com/api/corporate-announcements"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def _row_to_filing(row: dict) -> Filing:
    pdf = (row.get("attchmntFile") or "").strip()
    symbol = (row.get("symbol") or "").strip()
    seq = str(row.get("seq_id") or row.get("exchdisstime") or pdf or symbol)
    subject = (row.get("attchmntText") or "").strip()
    category = (row.get("desc") or "").strip()
    return Filing(
        source="NSE",
        source_id=seq,
        company=row.get("sm_name") or symbol,
        nse_symbol=symbol,
        isin=row.get("sm_isin") or "",
        title=subject or category,
        summary_text=subject,
        announced_at=_parse_dt(row.get("an_dt") or row.get("sort_date")),
        source_url="https://www.nseindia.com/companies-listing/corporate-filings-announcements",
        pdf_url=pdf,
        category=category,
    )


async def fetch_announcements(
    client: ExchangeClient,
    *,
    symbol: str | None = None,
    from_date: str,
    to_date: str,
) -> list[Filing]:
    params: dict[str, str] = {
        "index": "equities",
        "from_date": from_date,
        "to_date": to_date,
    }
    if symbol:
        params["symbol"] = symbol
    try:
        data = await client.nse_json(API, params)
    except Exception as exc:
        log.warning("NSE fetch failed symbol=%s: %s", symbol, exc)
        return []

    rows: list[dict]
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("data") or data.get("announcements") or []
        if not isinstance(rows, list):
            rows = []
    else:
        rows = []

    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            out.append(_row_to_filing(row))
        except Exception as exc:
            log.debug("skip NSE row: %s", exc)
    return out
