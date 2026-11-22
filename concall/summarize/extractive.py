from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime

from concall.models import ConcallSummary, KeyMetric, QAItem, SegmentNote, ToneScore

SPEAKER = re.compile(r"^([A-Z][A-Za-z.&'\- /]{1,70}):\s*(.+)$")
NUMBERY = re.compile(
    r"(\d+(?:\.\d+)?\s*%|\b\d{1,3}(?:,\d{2,3})+(?:\.\d+)?\b|\b\d+(?:\.\d+)?\s*(?:crore|cr|bps|basis points|million|billion)\b)",
    re.I,
)
GUIDANCE = re.compile(
    r"\b(guid(e|ance)|outlook|expect|target|aim to|we will|going forward|next year|fy2[0-9])\b",
    re.I,
)
RISK = re.compile(
    r"\b(risk|headwind|concern|challeng|uncertain|pressure|slowdown|inflation|competition)\b",
    re.I,
)
SEGMENT = re.compile(
    r"\b(segment|vertical|retail|wholesale|domestic|export|india|us |europe|api|formulation|nbfc)\b",
    re.I,
)
QUARTER = re.compile(r"\b(Q[1-4]\s*FY\s*2[0-9]{1,3}|FY\s*2[0-9]{1,3})\b", re.I)
DATE = re.compile(
    r"\b(\d{1,2}\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+20\d{2})\b",
    re.I,
)


def _turns(text: str) -> list[tuple[str, str]]:
    turns: list[tuple[str, str]] = []
    speaker = "Unknown"
    buf: list[str] = []
    for line in text.splitlines():
        m = SPEAKER.match(line.strip())
        if m:
            if buf:
                turns.append((speaker, " ".join(buf).strip()))
            speaker, first = m.group(1).strip(), m.group(2).strip()
            buf = [first] if first else []
        else:
            if line.strip():
                buf.append(line.strip())
    if buf:
        turns.append((speaker, " ".join(buf).strip()))
    return turns


def _sentences(blob: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", blob)
    return [p.strip() for p in parts if 40 <= len(p.strip()) <= 400]


def _pick(sentences: list[str], pattern: re.Pattern, limit: int) -> list[str]:
    out = []
    seen = set()
    for s in sentences:
        key = s.lower()
        if key in seen:
            continue
        if pattern.search(s):
            seen.add(key)
            out.append(s)
        if len(out) >= limit:
            break
    return out


def extractive_summary(text: str, meta: dict) -> ConcallSummary:
    turns = _turns(text)
    all_text = text
    sentences = _sentences(all_text)

    mgmt_names = {"moderator", "operator", "host"}
    mgmt_sents = []
    qa: list[QAItem] = []
    in_qa = False
    last_q: tuple[str, str] | None = None

    for speaker, body in turns:
        low = speaker.lower()
        if "question" in body.lower()[:80] or low.startswith("q ") or "analyst" in low:
            in_qa = True
        if re.search(r"\b(question[- ]and[- ]answer|open (the )?floor)\b", body, re.I):
            in_qa = True
        if in_qa:
            if "?" in body or low not in {"moderator", "operator"}:
                if "?" in body:
                    last_q = (speaker, body)
                elif last_q and low not in {"moderator", "operator"}:
                    qa.append(
                        QAItem(
                            question=last_q[1][:500],
                            answer=body[:600],
                            asked_by=last_q[0],
                        )
                    )
                    last_q = None
        elif low not in mgmt_names:
            mgmt_sents.extend(_sentences(body)[:3])

    metrics = []
    for s in sentences:
        nums = NUMBERY.findall(s)
        if nums:
            metrics.append(
                KeyMetric(
                    metric="Figure cited on call",
                    value=nums[0] if isinstance(nums[0], str) else str(nums[0]),
                    context=s[:280],
                )
            )
        if len(metrics) >= 8:
            break

    segs: dict[str, str] = defaultdict(str)
    for s in _pick(sentences, SEGMENT, 8):
        word = SEGMENT.search(s)
        key = word.group(0) if word else "Other"
        if key.lower() not in segs:
            segs[key] = s

    qmatch = QUARTER.search(all_text)
    dmatch = DATE.search(all_text[:8000])
    call_date = ""
    if dmatch:
        try:
            call_date = datetime.strptime(dmatch.group(1), "%d %B %Y").date().isoformat()
        except ValueError:
            call_date = dmatch.group(1)

    tldr = []
    for s in sentences:
        if NUMBERY.search(s) or GUIDANCE.search(s):
            tldr.append(s)
        if len(tldr) >= 5:
            break
    if not tldr:
        tldr = sentences[:4]

    pos = len(re.findall(r"\b(strong|growth|improve|resilient|healthy|confident)\b", all_text, re.I))
    neg = len(re.findall(r"\b(weak|decline|pressure|challeng|headwind|soft)\b", all_text, re.I))
    total = pos + neg or 1
    score = (pos - neg) / total
    if score > 0.25:
        label = "constructive"
    elif score < -0.25:
        label = "defensive"
    else:
        label = "mixed"

    return ConcallSummary(
        company=meta.get("company", ""),
        scrip_code=meta.get("scrip_code", ""),
        nse_symbol=meta.get("nse_symbol", ""),
        quarter=(qmatch.group(1).upper().replace("  ", " ") if qmatch else ""),
        call_date=call_date or (meta.get("call_date") or ""),
        source=meta.get("source", ""),
        source_url=meta.get("source_url", ""),
        tldr=tldr[:5],
        management_commentary=(mgmt_sents or sentences)[:6],
        guidance=_pick(sentences, GUIDANCE, 6),
        segment_performance=[SegmentNote(segment=k, notes=v) for k, v in list(segs.items())[:6]],
        key_metrics=metrics,
        notable_qa=qa[:6],
        risks=_pick(sentences, RISK, 6),
        tone=ToneScore(
            label=label,  # type: ignore[arg-type]
            score=round(score, 2),
            evidence=["Heuristic word counts on the extracted text; not an LLM judgment."],
        ),
        model="extractive-fallback",
        limitations=[
            "No LLM API key configured — this is a heuristic extractive summary, not a faithful analyst write-up.",
            "Set OPENAI_API_KEY, GEMINI_API_KEY, or ANTHROPIC_API_KEY for the map-reduce LLM path.",
        ],
    )
