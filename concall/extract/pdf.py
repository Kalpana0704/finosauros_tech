from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass

import fitz

log = logging.getLogger("concall.extract")

SPEAKER_RE = re.compile(
    r"^(?P<name>[A-Z][A-Za-z.&'\- ]{1,60}):\s+(?P<text>.+)$"
)
HEADERISH = re.compile(
    r"page\s+\d+|confidential|earnings conference call|investor.*call",
    re.I,
)


@dataclass
class ExtractResult:
    text: str
    page_count: int
    method: str
    warnings: list[str]


def _strip_repeated_lines(pages: list[str]) -> list[str]:
    """Drop headers/footers that repeat on most pages."""
    counts: Counter[str] = Counter()
    for page in pages:
        seen = set()
        for raw in page.splitlines():
            line = raw.strip()
            if 4 <= len(line) <= 80 and line not in seen:
                counts[line] += 1
                seen.add(line)
    n = max(len(pages), 1)
    boilerplate = {
        line
        for line, c in counts.items()
        if c >= max(3, int(n * 0.45)) or HEADERISH.search(line)
    }
    cleaned = []
    for page in pages:
        kept = []
        for raw in page.splitlines():
            line = raw.strip()
            if not line:
                kept.append("")
                continue
            if line in boilerplate:
                continue
            if re.fullmatch(r"\d{1,4}", line):
                continue
            kept.append(line)
        cleaned.append("\n".join(kept))
    return cleaned


def _normalize_speakers(text: str) -> str:
    # "Moderator :" / "Q – Analyst Name" variants
    text = re.sub(r"^([A-Za-z][A-Za-z.&'\- /]{1,70})\s*:\s*", r"\1: ", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pdf_bytes(data: bytes) -> ExtractResult:
    warnings: list[str] = []
    doc = fitz.open(stream=data, filetype="pdf")
    pages: list[str] = []
    image_only = 0
    for page in doc:
        text = page.get_text("text") or ""
        if len(text.strip()) < 40:
            image_only += 1
        pages.append(text)
    page_count = len(doc)
    doc.close()

    pages = _strip_repeated_lines(pages)
    text = _normalize_speakers("\n\n".join(pages))
    method = "pymupdf"

    if page_count and image_only / page_count >= 0.7:
        warnings.append("PDF looks scanned / image-heavy; OCR not installed in this build.")
        method = "pymupdf-sparse"

    if len(text) < 400:
        warnings.append("Extracted text is very short; attachment may not be a transcript.")

    return ExtractResult(text=text, page_count=page_count, method=method, warnings=warnings)


def looks_like_transcript(text: str) -> bool:
    if len(text) < 600:
        return False
    speakers = sum(1 for line in text.splitlines() if SPEAKER_RE.match(line.strip()))
    keys = (
        "moderator",
        "conference call",
        "earnings",
        "operator",
        "questions and answers",
        "ladies and gentlemen",
    )
    hits = sum(1 for k in keys if k in text.lower())
    return speakers >= 3 or hits >= 2 or (len(text) > 8000 and "transcript" in text.lower())
