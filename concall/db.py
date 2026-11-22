from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS filings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    company TEXT NOT NULL,
    nse_symbol TEXT,
    scrip_code TEXT,
    isin TEXT,
    title TEXT,
    announced_at TEXT,
    source_url TEXT,
    pdf_url TEXT,
    pdf_path TEXT,
    pdf_hash TEXT,
    kind TEXT,
    status TEXT NOT NULL DEFAULT 'seen',
    error TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS transcripts (
    filing_id INTEGER PRIMARY KEY,
    text TEXT,
    page_count INTEGER,
    extract_method TEXT,
    FOREIGN KEY(filing_id) REFERENCES filings(id)
);

CREATE TABLE IF NOT EXISTS summaries (
    filing_id INTEGER PRIMARY KEY,
    payload_json TEXT NOT NULL,
    markdown TEXT NOT NULL,
    model TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(filing_id) REFERENCES filings(id)
);

CREATE INDEX IF NOT EXISTS idx_filings_hash ON filings(pdf_hash);
CREATE INDEX IF NOT EXISTS idx_filings_symbol ON filings(nse_symbol);
"""


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def filing_status(self, source: str, source_id: str) -> tuple[int, str] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, status FROM filings WHERE source=? AND source_id=?",
                (source, source_id),
            ).fetchone()
        if not row:
            return None
        return int(row["id"]), str(row["status"])

    def find_duplicate_hash(self, pdf_hash: str) -> int | None:
        if not pdf_hash:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM filings WHERE pdf_hash=? AND status='done' LIMIT 1",
                (pdf_hash,),
            ).fetchone()
        return int(row["id"]) if row else None

    def find_cross_exchange(
        self, nse_symbol: str, scrip_code: str, announced_at: str | None
    ) -> int | None:
        """Same transcript often hits NSE and BSE within hours."""
        if not announced_at:
            return None
        day = announced_at[:10]
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id FROM filings
                WHERE status='done'
                  AND date(announced_at)=?
                  AND (
                    (nse_symbol != '' AND nse_symbol=?)
                    OR (scrip_code != '' AND scrip_code=?)
                  )
                LIMIT 1
                """,
                (day, nse_symbol, scrip_code),
            ).fetchone()
        return int(row["id"]) if row else None

    def insert_filing(self, data: dict[str, Any]) -> int:
        now = datetime.utcnow().isoformat(timespec="seconds")
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT OR IGNORE INTO filings
                (source, source_id, company, nse_symbol, scrip_code, isin, title,
                 announced_at, source_url, pdf_url, kind, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["source"],
                    data["source_id"],
                    data["company"],
                    data.get("nse_symbol", ""),
                    data.get("scrip_code", ""),
                    data.get("isin", ""),
                    data.get("title", ""),
                    data.get("announced_at"),
                    data.get("source_url", ""),
                    data.get("pdf_url", ""),
                    data.get("kind", "other"),
                    data.get("status", "queued"),
                    now,
                ),
            )
            self._conn.commit()
            if cur.lastrowid:
                return int(cur.lastrowid)
            row = self._conn.execute(
                "SELECT id FROM filings WHERE source=? AND source_id=?",
                (data["source"], data["source_id"]),
            ).fetchone()
            return int(row["id"])

    def update_filing(self, filing_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = list(fields.values()) + [filing_id]
        with self._lock:
            self._conn.execute(f"UPDATE filings SET {cols} WHERE id=?", vals)
            self._conn.commit()

    def save_transcript(
        self, filing_id: int, text: str, page_count: int, method: str
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO transcripts (filing_id, text, page_count, extract_method)
                VALUES (?, ?, ?, ?)
                """,
                (filing_id, text, page_count, method),
            )
            self._conn.commit()

    def save_summary(
        self, filing_id: int, payload: dict[str, Any], markdown: str, model: str
    ) -> None:
        now = datetime.utcnow().isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO summaries (filing_id, payload_json, markdown, model, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (filing_id, json.dumps(payload, ensure_ascii=False, indent=2), markdown, model, now),
            )
            self._conn.commit()

    def list_summaries(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT f.id, f.company, f.nse_symbol, f.scrip_code, f.source, f.title,
                       f.announced_at, f.status, f.kind, s.payload_json, s.markdown, s.model, s.created_at
                FROM filings f
                LEFT JOIN summaries s ON s.filing_id = f.id
                WHERE f.kind IN ('transcript', 'audio_only')
                ORDER BY COALESCE(f.announced_at, f.created_at) DESC
                """
            ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            if item.get("payload_json"):
                item["summary"] = json.loads(item["payload_json"])
            out.append(item)
        return out

    def get_summary(self, filing_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT f.*, t.text AS transcript, t.page_count, t.extract_method,
                       s.payload_json, s.markdown, s.model AS summary_model
                FROM filings f
                LEFT JOIN transcripts t ON t.filing_id = f.id
                LEFT JOIN summaries s ON s.filing_id = f.id
                WHERE f.id=?
                """,
                (filing_id,),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        if item.get("payload_json"):
            item["summary"] = json.loads(item["payload_json"])
        return item

    def stats(self) -> dict[str, int]:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) c FROM filings").fetchone()["c"]
            done = self._conn.execute(
                "SELECT COUNT(*) c FROM filings WHERE status='done'"
            ).fetchone()["c"]
            failed = self._conn.execute(
                "SELECT COUNT(*) c FROM filings WHERE status='failed'"
            ).fetchone()["c"]
            audio = self._conn.execute(
                "SELECT COUNT(*) c FROM filings WHERE kind='audio_only'"
            ).fetchone()["c"]
        return {
            "filings": int(total),
            "summaries": int(done),
            "failed": int(failed),
            "audio_only": int(audio),
        }
