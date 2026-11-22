from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Company(BaseModel):
    name: str
    nse_symbol: str
    bse_scrip: str
    sector: str = ""


class Filing(BaseModel):
    source: Literal["NSE", "BSE"]
    source_id: str
    company: str
    nse_symbol: str = ""
    scrip_code: str = ""
    isin: str = ""
    title: str
    summary_text: str = ""
    announced_at: datetime | None = None
    source_url: str = ""
    pdf_url: str = ""
    category: str = ""
    kind: Literal["transcript", "audio_only", "intimation", "other"] = "other"


class SegmentNote(BaseModel):
    segment: str
    notes: str


class KeyMetric(BaseModel):
    metric: str
    value: str
    context: str = ""


class QAItem(BaseModel):
    question: str
    answer: str
    asked_by: str = ""


class ToneScore(BaseModel):
    label: Literal["constructive", "cautious", "mixed", "defensive", "unknown"] = "unknown"
    score: float = Field(
        default=0.0,
        description="[-1, 1] from defensive/negative to constructive/positive, grounded in wording on the call.",
    )
    evidence: list[str] = Field(default_factory=list)


class ConcallSummary(BaseModel):
    company: str
    scrip_code: str = ""
    nse_symbol: str = ""
    quarter: str = ""
    call_date: str = ""
    source: str
    source_url: str = ""
    tldr: list[str] = Field(default_factory=list)
    management_commentary: list[str] = Field(default_factory=list)
    guidance: list[str] = Field(default_factory=list)
    segment_performance: list[SegmentNote] = Field(default_factory=list)
    key_metrics: list[KeyMetric] = Field(default_factory=list)
    notable_qa: list[QAItem] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    tone: ToneScore = Field(default_factory=ToneScore)
    audio_only: bool = False
    model: str = ""
    limitations: list[str] = Field(default_factory=list)

    def to_markdown(self) -> str:
        lines = [
            f"# {self.company} — {self.quarter or 'Earnings call'}",
            "",
            f"- **Symbol:** {self.nse_symbol or '—'}  |  **Scrip:** {self.scrip_code or '—'}",
            f"- **Call date:** {self.call_date or '—'}  |  **Source:** {self.source}",
        ]
        if self.source_url:
            lines.append(f"- **Filing:** {self.source_url}")
        if self.tone.label != "unknown":
            lines.append(
                f"- **Management tone:** {self.tone.label} ({self.tone.score:+.2f})"
            )
        lines += ["", "## TL;DR", ""]
        for item in self.tldr:
            lines.append(f"- {item}")
        lines += ["", "## Management commentary", ""]
        for item in self.management_commentary:
            lines.append(f"- {item}")
        lines += ["", "## Guidance / outlook", ""]
        if self.guidance:
            for item in self.guidance:
                lines.append(f"- {item}")
        else:
            lines.append("_No forward-looking statements extracted from the transcript._")
        lines += ["", "## Segment / vertical performance", ""]
        if self.segment_performance:
            for seg in self.segment_performance:
                lines.append(f"- **{seg.segment}:** {seg.notes}")
        else:
            lines.append("_Not broken out on this call, or not recoverable from the text._")
        lines += ["", "## Key numbers", ""]
        if self.key_metrics:
            for m in self.key_metrics:
                ctx = f" — {m.context}" if m.context else ""
                lines.append(f"- **{m.metric}:** {m.value}{ctx}")
        else:
            lines.append("_No numerical KPIs recovered._")
        lines += ["", "## Notable Q&A", ""]
        if self.notable_qa:
            for qa in self.notable_qa:
                who = f" ({qa.asked_by})" if qa.asked_by else ""
                lines.append(f"- **Q{who}:** {qa.question}")
                lines.append(f"  - **A:** {qa.answer}")
        else:
            lines.append("_Q&A not identified._")
        lines += ["", "## Risks / concerns", ""]
        if self.risks:
            for item in self.risks:
                lines.append(f"- {item}")
        else:
            lines.append("_None flagged in the extracted text._")
        if self.limitations:
            lines += ["", "## Limitations", ""]
            for item in self.limitations:
                lines.append(f"- {item}")
        lines.append("")
        return "\n".join(lines)
