from __future__ import annotations

import json
import logging
import re
from typing import Any

from concall.config import settings
from concall.models import ConcallSummary, KeyMetric, QAItem, SegmentNote, ToneScore
from concall.summarize.prompts import MAP_PROMPT, REDUCE_PROMPT, SCHEMA_HINT, SYSTEM

log = logging.getLogger("concall.llm")

CHUNK_CHARS = 12000
CHUNK_OVERLAP = 800


class LLMError(RuntimeError):
    pass


def provider_name() -> str:
    if settings.openai_api_key:
        return f"openai:{settings.openai_model}"
    if settings.gemini_api_key:
        return f"gemini:{settings.gemini_model}"
    if settings.anthropic_api_key:
        return f"anthropic:{settings.anthropic_model}"
    return "extractive-fallback"


async def complete(prompt: str) -> str:
    if settings.openai_api_key:
        return await _openai(prompt)
    if settings.gemini_api_key:
        return await _gemini(prompt)
    if settings.anthropic_api_key:
        return await _anthropic(prompt)
    raise LLMError("no provider configured")


async def _openai(prompt: str) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=settings.openai_api_key, base_url=settings.openai_base_url
    )
    resp = await client.chat.completions.create(
        model=settings.openai_model,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
    )
    return resp.choices[0].message.content or "{}"


async def _gemini(prompt: str) -> str:
    import google.generativeai as genai

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(
        settings.gemini_model,
        system_instruction=SYSTEM,
        generation_config={"temperature": 0.1, "response_mime_type": "application/json"},
    )
    resp = await model.generate_content_async(prompt)
    return resp.text or "{}"


async def _anthropic(prompt: str) -> str:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    resp = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=4000,
        temperature=0.1,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def parse_json(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.S)
    if match:
        return json.loads(match.group(0))
    raise LLMError("model did not return JSON")


def chunk_text(text: str) -> list[str]:
    if len(text) <= CHUNK_CHARS:
        return [text]
    chunks = []
    i = 0
    while i < len(text):
        end = min(len(text), i + CHUNK_CHARS)
        if end < len(text):
            cut = text.rfind("\n", i + CHUNK_CHARS // 2, end)
            if cut > i:
                end = cut
        chunks.append(text[i:end].strip())
        i = max(end - CHUNK_OVERLAP, i + 1)
    return [c for c in chunks if c]


def _summary_from_dict(data: dict[str, Any], fallback: dict[str, Any]) -> ConcallSummary:
    segs = []
    for item in data.get("segment_performance") or []:
        if isinstance(item, dict) and item.get("segment"):
            segs.append(SegmentNote(segment=str(item["segment"]), notes=str(item.get("notes") or "")))
    metrics = []
    for item in data.get("key_metrics") or []:
        if isinstance(item, dict) and item.get("metric"):
            metrics.append(
                KeyMetric(
                    metric=str(item["metric"]),
                    value=str(item.get("value") or ""),
                    context=str(item.get("context") or ""),
                )
            )
    qa = []
    for item in data.get("notable_qa") or []:
        if isinstance(item, dict) and (item.get("question") or item.get("answer")):
            qa.append(
                QAItem(
                    question=str(item.get("question") or ""),
                    answer=str(item.get("answer") or ""),
                    asked_by=str(item.get("asked_by") or ""),
                )
            )
    tone_raw = data.get("tone") or {}
    if not isinstance(tone_raw, dict):
        tone_raw = {}
    label = tone_raw.get("label") or "unknown"
    if label not in {"constructive", "cautious", "mixed", "defensive", "unknown"}:
        label = "unknown"
    try:
        score = float(tone_raw.get("score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    return ConcallSummary(
        company=str(data.get("company") or fallback.get("company") or ""),
        scrip_code=str(data.get("scrip_code") or fallback.get("scrip_code") or ""),
        nse_symbol=str(data.get("nse_symbol") or fallback.get("nse_symbol") or ""),
        quarter=str(data.get("quarter") or ""),
        call_date=str(data.get("call_date") or fallback.get("call_date") or ""),
        source=str(data.get("source") or fallback.get("source") or ""),
        source_url=str(data.get("source_url") or fallback.get("source_url") or ""),
        tldr=[str(x) for x in (data.get("tldr") or []) if str(x).strip()][:6],
        management_commentary=[str(x) for x in (data.get("management_commentary") or []) if str(x).strip()],
        guidance=[str(x) for x in (data.get("guidance") or []) if str(x).strip()],
        segment_performance=segs,
        key_metrics=metrics,
        notable_qa=qa,
        risks=[str(x) for x in (data.get("risks") or []) if str(x).strip()],
        tone=ToneScore(
            label=label,  # type: ignore[arg-type]
            score=max(-1.0, min(1.0, score)),
            evidence=[str(x) for x in (tone_raw.get("evidence") or [])][:5],
        ),
        limitations=[str(x) for x in (data.get("limitations") or []) if str(x).strip()],
        model=provider_name(),
    )


async def summarize_transcript(
    text: str,
    meta: dict[str, Any],
) -> ConcallSummary:
    chunks = chunk_text(text)
    notes: list[dict[str, Any]] = []
    for i, chunk in enumerate(chunks, start=1):
        prompt = MAP_PROMPT.format(
            schema=SCHEMA_HINT,
            company=meta.get("company", ""),
            symbol=meta.get("nse_symbol", ""),
            scrip=meta.get("scrip_code", ""),
            source=meta.get("source", ""),
            index=i,
            total=len(chunks),
            chunk=chunk,
        )
        raw = await complete(prompt)
        notes.append(parse_json(raw))

    if len(notes) == 1:
        summary = _summary_from_dict(notes[0], meta)
    else:
        reduce_prompt = REDUCE_PROMPT.format(
            company=meta.get("company", ""),
            symbol=meta.get("nse_symbol", ""),
            scrip=meta.get("scrip_code", ""),
            source=meta.get("source", ""),
            url=meta.get("source_url", ""),
            schema=SCHEMA_HINT,
            notes=json.dumps(notes, ensure_ascii=False)[:80000],
        )
        merged = parse_json(await complete(reduce_prompt))
        summary = _summary_from_dict(merged, meta)

    if len(chunks) > 1:
        summary.limitations.append(
            f"Summarized with map-reduce over {len(chunks)} chunks (~{CHUNK_CHARS} chars)."
        )
    return summary
