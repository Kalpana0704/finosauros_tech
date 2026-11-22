SCHEMA_HINT = """
Return ONLY valid JSON matching this schema (no markdown fences):
{
  "company": "string",
  "scrip_code": "string",
  "nse_symbol": "string",
  "quarter": "e.g. Q2 FY26",
  "call_date": "YYYY-MM-DD or empty",
  "source": "NSE or BSE",
  "source_url": "string",
  "tldr": ["3 to 5 short bullets a busy analyst can scan"],
  "management_commentary": ["grounded highlights and tone"],
  "guidance": ["forward-looking statements only if spoken on the call"],
  "segment_performance": [{"segment": "...", "notes": "..."}],
  "key_metrics": [{"metric": "...", "value": "...", "context": "..."}],
  "notable_qa": [{"question": "...", "answer": "...", "asked_by": "..."}],
  "risks": ["concerns raised on the call"],
  "tone": {
    "label": "constructive|cautious|mixed|defensive|unknown",
    "score": 0.0,
    "evidence": ["short quotes or paraphrases from the call"]
  },
  "limitations": ["what was missing or unreadable"]
}
"""

SYSTEM = """You are a sell-side equity analyst summarizing Indian listed-company earnings conference calls.
Hard rules:
- Every claim MUST be grounded in the provided transcript excerpt. If it is not in the text, omit it.
- Never invent numbers, guidance, segment names, or analyst identities.
- Prefer the company's own wording for targets (e.g. "mid-teens", "₹X crore").
- If a field has no support, use an empty list / unknown — do not guess.
- Distinguish management prepared remarks from Q&A.
- Indian fiscal year: Q1 = Apr-Jun, Q2 = Jul-Sep, Q3 = Oct-Dec, Q4 = Jan-Mar.
"""

MAP_PROMPT = """Extract grounded notes from this TRANSCRIPT CHUNK.
{schema}

Company hint: {company}
Symbol: {symbol}  Scrip: {scrip}  Source: {source}

CHUNK {index}/{total}:
\"\"\"
{chunk}
\"\"\"
"""

REDUCE_PROMPT = """Merge the following chunk-level JSON notes into ONE faithful concall summary.
Drop duplicates. Keep only facts that appear in the notes. Do not add new facts.
If chunk notes conflict, prefer the more specific wording and record the conflict under limitations.

Metadata to copy:
company={company}
nse_symbol={symbol}
scrip_code={scrip}
source={source}
source_url={url}

{schema}

CHUNK NOTES:
{notes}
"""
