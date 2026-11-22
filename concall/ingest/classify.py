from __future__ import annotations

import re

from concall.models import Filing

TRANSCRIPT = [
    r"\btranscript\b",
    r"concall\s+transcript",
    r"earnings\s+(?:conference\s+)?call\s+transcript",
    r"conference\s+call\s+transcript",
    r"analyst\s+(?:\/|and|&)?\s*investor.{0,40}transcript",
]
AUDIO = [
    r"audio\s+recording",
    r"audio[/\-\s]?video",
    r"recording of (?:the )?(?:earnings|conference) call",
    r"\bwebcast\b",
]
INTIMATION = [
    r"\bintimation\b",
    r"schedule of (?:analyst|investor)",
    r"upcoming (?:earnings|conference) call",
    r"invitation",
    r"dial[\-\s]?in",
]
NOISE = [
    r"investor presentation",
    r"outcome of board",
    r"financial results",
    r"press release",
    r"newspaper publication",
    r"regulation 30 \(lodr\)-analyst",
]


def _hit(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def classify_text(title: str, body: str = "", category: str = "") -> str:
    blob = f"{title}\n{body}\n{category}".lower()
    if _hit(AUDIO, blob) and not _hit(TRANSCRIPT, blob):
        return "audio_only"
    if _hit(TRANSCRIPT, blob):
        return "transcript"
    if _hit(INTIMATION, blob):
        return "intimation"
    # Category-only: analyst meet filings that are not presentations/results.
    cat = category.lower()
    if "analyst" in cat or "institutional investor" in cat:
        if _hit(INTIMATION, blob):
            return "intimation"
        if _hit(NOISE, blob):
            return "other"
    return "other"


def classify_filing(filing: Filing) -> Filing:
    filing.kind = classify_text(filing.title, filing.summary_text, filing.category)  # type: ignore[assignment]
    return filing


def in_watchlist(
    symbol: str,
    scrip: str,
    companies: list[dict],
) -> dict | None:
    symbol = (symbol or "").upper().strip()
    scrip = str(scrip or "").strip()
    for c in companies:
        if symbol and c["nse_symbol"].upper() == symbol:
            return c
        if scrip and str(c["bse_scrip"]) == scrip:
            return c
    return None
