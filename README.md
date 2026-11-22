# Concall wire

Real-time pipeline that watches **NSE + BSE** corporate announcements for earnings **concall transcripts**, extracts the PDF, summarizes it, and streams the result to a small UI and a **WebSocket** feed.

Built as the Finosauras Tech intern take-home.

```
new filing (NSE/BSE)
        │
        ▼
 [watch & ingest]  classify transcript vs audio vs noise
        │          download PDF, hash-dedup across exchanges
        ▼
    [extract]      PyMuPDF, strip running headers/footers
        │
        ▼
   [summarize]     map-reduce LLM  (or extractive fallback)
        │
        ▼
  [serve/stream]   FastAPI UI + /ws
```

## One command

```bash
cp .env.example .env   # optional: add an LLM key
docker compose up --build
```

Then open [http://localhost:8000](http://localhost:8000).

Without Docker:

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env
python -m concall serve
```

On startup the watcher **backfills** the watchlist (default 120 days), then polls every 45 seconds for new filings.

| Command | What it does |
|---|---|
| `python -m concall serve` | watcher + UI + WebSocket (the assignment “one command”) |
| `python -m concall backfill` | one-shot historical pull, no server |
| `python -m concall export-samples` | copy ≥3 companies into `sample_outputs/` |
| `POST /api/backfill` | same as backfill, from the UI button |

WebSocket: `ws://localhost:8000/ws`. Each new summary is a JSON event `{ type, filing_id, ts, summary, markdown }`.

## Watchlist

Eight names, one per sector-ish, in `watchlist.yaml`:

HDFCBANK (bank), MARUTI (auto), SUNPHARMA (pharma), INFY (IT), RELIANCE (energy), HINDUNILVR (FMCG), TATASTEEL (metals), TITAN (consumer).

Edit the YAML; no code change needed.

## LLM

Set **one** of:

- `OPENAI_API_KEY` (+ optional `OPENAI_MODEL`, `OPENAI_BASE_URL` for compatible proxies)
- `GEMINI_API_KEY`
- `ANTHROPIC_API_KEY`

If none are set the pipeline still runs end-to-end using an **extractive fallback**. That path is honest about being a heuristic (see `limitations` on every payload). It exists so the watcher/UI/WebSocket can be demoed without burning API credits.

Long transcripts (15–40 pages) are split into ~12k-character chunks with overlap, summarized per chunk (map), then merged (reduce). The model is instructed to omit anything that is not in the excerpt.

## Schema vs the brief

The assignment schema is implemented as-is, plus:

| Extra field | Why |
|---|---|
| `tone` `{label, score, evidence}` | bonus: management tone, with evidence strings so it is not a naked number |
| `audio_only` | bonus: audio recording filed, transcript not up yet |
| `model` | which summarizer produced the payload |
| `limitations` | missing data / OCR / LLM errors — better than silent holes |

I did **not** add hallucinated “sentiment of the stock”. Tone is about wording on the call.

## Design decisions

**Classify, don’t scrape HTML.** NSE’s filings page is filled from `GET /api/corporate-announcements`. BSE’s is `GET api.bseindia.com/BseIndiaAPI/api/AnnGetData/w`. Both want a browser User-Agent; NSE is warmed with a session cookie when it 403s.

**Per-symbol queries.** A full-market daily dump is huge and easy to rate-limit. The watcher asks each watchlist symbol / scrip for a date window. That is enough for the assignment and is kinder to the exchanges.

**Transcript vs the rest.** Subject lines that contain `transcript` win. `audio recording` without `transcript` is stored as a placeholder event. `intimation` / dial-in / schedule lines are ignored so we don’t summarize a one-page invite.

**Dedup.** `(source, source_id)` is the primary key. PDF `sha256` catches the same file re-filed. NSE vs BSE copies of a transcript are often *different PDFs* (different covering letters), so hash will not always collapse them; the UI will then show two rows for the same call. I would add a fuzzy “same symbol + same calendar day + similar length” merge with more time — doing it blindly false-positives an intimation and a transcript on the same morning.

**No secrets in git.** Keys only via `.env`.

**SQLite as the spool.** One file in `data/`. Fine for a single-box intern assignment; not a bus.

## Limitations / known issues

- Exchange endpoints are unofficial and flaky. 403s, empty bodies, and slow PDFs happen. Retries + backoff are in place; they are not magic.
- OCR is stubbed: image-only PDFs are flagged in `limitations`, not run through Tesseract (extra system packages, weak ROI for this set).
- XBRL metadata is not parsed. Announcement JSON already has company, scrip, time; XBRL would help quarter tagging on odd PDFs.
- Extractive fallback will over-capture number-bearing sentences and under-capture narrative. Use a real LLM for anything you would send to an analyst.
- Cross-exchange duplicates are imperfect (see above).
- Failed downloads are retried on the next cycle; “skipped / not a transcript” attachments are not.
- This is **not** a licensed corporate-actions feed. It reads the same public JSON the websites use.

## What I would do with more time

- OCR fallback (Tesseract) and a “audio only → poll until transcript appears” join on symbol+date.
- Proper speaker diarization from labels, then retrieve-then-summarize over Q&A only for `notable_qa`.
- On-demand historical backfill per company from the UI.
- A tiny eval set: 10 hand-checked claims per summary, fail the job if the model invents a number.
- Push to Slack / Telegram from the same WebSocket payload.

## Sample outputs

After a successful backfill:

```bash
python -m concall export-samples
```

writes `sample_outputs/<SYMBOL>/{transcript.txt,summary.json,summary.md}` for at least three names.

## Tests

```bash
pip install pytest
pytest -q
```
