from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import SHANGHAI_TZ
from .models import ParsedArticle, Snapshot


SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    url TEXT PRIMARY KEY,
    seq TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    published_ts INTEGER NOT NULL,
    channel_key TEXT NOT NULL,
    channel_name TEXT NOT NULL,
    source_name TEXT NOT NULL,
    stocks_json TEXT NOT NULL,
    filtered_reason TEXT,
    fetch_error TEXT,
    fetched_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_ts);

CREATE TABLE IF NOT EXISTS refresh_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_ts INTEGER NOT NULL,
    finished_ts INTEGER,
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts INTEGER NOT NULL,
    window_start_ts INTEGER NOT NULL,
    window_end_ts INTEGER NOT NULL,
    partial INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_created ON snapshots(created_ts DESC);

CREATE TABLE IF NOT EXISTS stock_industries (
    code TEXT PRIMARY KEY,
    industry TEXT NOT NULL,
    updated_ts INTEGER NOT NULL
);
"""


class Storage:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def create_run(self, started_at: datetime) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO refresh_runs(started_ts, status) VALUES (?, 'running')",
                (int(started_at.timestamp()),),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str, message: str, finished_at: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE refresh_runs SET finished_ts=?, status=?, message=? WHERE id=?",
                (int(finished_at.timestamp()), status, message[:2000], run_id),
            )

    def upsert_article(self, article: ParsedArticle, fetched_at: datetime) -> None:
        payload = json.dumps([stock.to_dict() for stock in article.stocks], ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO articles(
                    url, seq, title, summary, published_ts, channel_key, channel_name,
                    source_name, stocks_json, filtered_reason, fetch_error, fetched_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    seq=excluded.seq,
                    title=excluded.title,
                    summary=excluded.summary,
                    published_ts=excluded.published_ts,
                    channel_key=excluded.channel_key,
                    channel_name=excluded.channel_name,
                    source_name=excluded.source_name,
                    stocks_json=excluded.stocks_json,
                    filtered_reason=excluded.filtered_reason,
                    fetch_error=excluded.fetch_error,
                    fetched_ts=excluded.fetched_ts
                """,
                (
                    article.url,
                    article.seq,
                    article.title,
                    article.summary,
                    int(article.published_at.timestamp()),
                    article.channel_key,
                    article.channel_name,
                    article.source_name,
                    payload,
                    article.filtered_reason,
                    article.fetch_error,
                    int(fetched_at.timestamp()),
                ),
            )

    def get_cached_article(self, url: str) -> ParsedArticle | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM articles WHERE url=?", (url,)).fetchone()
        if row is None or row["fetch_error"]:
            return None
        return self._row_to_article(row)

    def get_articles_between(self, start: datetime, end: datetime) -> list[ParsedArticle]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM articles
                WHERE published_ts >= ? AND published_ts <= ?
                ORDER BY published_ts DESC
                """,
                (int(start.timestamp()), int(end.timestamp())),
            ).fetchall()
        return [self._row_to_article(row) for row in rows]

    @staticmethod
    def _row_to_article(row: sqlite3.Row) -> ParsedArticle:
        payload: dict[str, Any] = {
            "seq": row["seq"],
            "url": row["url"],
            "title": row["title"],
            "summary": row["summary"],
            "published_at": datetime.fromtimestamp(row["published_ts"], tz=SHANGHAI_TZ).isoformat(),
            "channel_key": row["channel_key"],
            "channel_name": row["channel_name"],
            "source_name": row["source_name"],
            "stocks": json.loads(row["stocks_json"]),
            "filtered_reason": row["filtered_reason"],
            "fetch_error": row["fetch_error"],
        }
        return ParsedArticle.from_dict(payload)

    def save_snapshot(self, snapshot: Snapshot) -> Snapshot:
        payload = snapshot.to_dict()
        payload["snapshot_id"] = None
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO snapshots(created_ts, window_start_ts, window_end_ts, partial, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(snapshot.created_at.timestamp()),
                    int(snapshot.window_start.timestamp()),
                    int(snapshot.window_end.timestamp()),
                    int(snapshot.partial),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            snapshot.snapshot_id = int(cursor.lastrowid)
            updated_payload = snapshot.to_dict()
            connection.execute(
                "UPDATE snapshots SET payload_json=? WHERE id=?",
                (json.dumps(updated_payload, ensure_ascii=False), snapshot.snapshot_id),
            )
        return snapshot

    def load_latest_snapshot(self) -> Snapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM snapshots ORDER BY created_ts DESC, id DESC LIMIT 1"
            ).fetchone()
        return Snapshot.from_dict(json.loads(row["payload_json"])) if row else None

    def get_stock_industries(self, codes: set[str]) -> dict[str, str]:
        if not codes:
            return {}
        result: dict[str, str] = {}
        ordered_codes = sorted(codes)
        with self._connect() as connection:
            for start in range(0, len(ordered_codes), 900):
                batch = ordered_codes[start : start + 900]
                placeholders = ", ".join("?" for _ in batch)
                rows = connection.execute(
                    f"SELECT code, industry FROM stock_industries WHERE code IN ({placeholders})",  # noqa: S608
                    batch,
                ).fetchall()
                result.update({str(row["code"]): str(row["industry"]) for row in rows})
        return result

    def upsert_stock_industries(self, industries: dict[str, str], updated_at: datetime) -> None:
        rows = [
            (code, industry, int(updated_at.timestamp()))
            for code, industry in industries.items()
            if code and industry
        ]
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO stock_industries(code, industry, updated_ts) VALUES (?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET industry=excluded.industry, updated_ts=excluded.updated_ts
                """,
                rows,
            )

    def purge_older_than(self, timestamp: datetime) -> None:
        cutoff = int(timestamp.timestamp())
        with self._connect() as connection:
            connection.execute("DELETE FROM articles WHERE published_ts < ?", (cutoff,))

    def clear_all(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM snapshots")
            connection.execute("DELETE FROM refresh_runs")
            connection.execute("DELETE FROM articles")
            connection.execute("DELETE FROM stock_industries")
        with self._connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
