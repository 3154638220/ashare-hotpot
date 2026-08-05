from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import SHANGHAI_TZ
from .models import OfficialPopularitySnapshot, ParsedArticle, Snapshot


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

CREATE TABLE IF NOT EXISTS guba_stock_catalog (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    updated_ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS guba_posts (
    post_id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    published_ts INTEGER NOT NULL,
    author TEXT NOT NULL,
    comment_count INTEGER NOT NULL,
    fetched_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_guba_posts_published ON guba_posts(published_ts);
CREATE INDEX IF NOT EXISTS idx_guba_posts_code_published ON guba_posts(code, published_ts DESC);

CREATE TABLE IF NOT EXISTS guba_scan_state (
    code TEXT PRIMARY KEY,
    scanned_ts INTEGER NOT NULL,
    pages_scanned INTEGER NOT NULL,
    reached_cutoff INTEGER NOT NULL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_ts INTEGER NOT NULL
);
"""

POPULARITY_STATE_KEY = "popularity"


@dataclass(frozen=True, slots=True)
class RefreshRunSummary:
    run_id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    message: str


@dataclass(frozen=True, slots=True)
class StorageStats:
    database_bytes: int
    article_count: int
    snapshot_count: int
    latest_run: RefreshRunSummary | None


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
        self._migrate_legacy_guba()

    def _migrate_legacy_guba(self) -> None:
        """Clear the old per-stock-bar scan data and drop old self-computed guba
        results from historical snapshots while keeping the news snapshot."""

        with self._connect() as connection:
            connection.execute("DELETE FROM guba_posts")
            connection.execute("DELETE FROM guba_scan_state")
            connection.execute("DELETE FROM guba_stock_catalog")
            rows = connection.execute("SELECT id, payload_json FROM snapshots").fetchall()
            for row in rows:
                try:
                    payload = json.loads(row["payload_json"])
                except (json.JSONDecodeError, TypeError):
                    continue
                if "guba" not in payload:
                    continue
                payload.pop("guba")
                connection.execute(
                    "UPDATE snapshots SET payload_json=? WHERE id=?",
                    (json.dumps(payload, ensure_ascii=False), row["id"]),
                )

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

    def get_latest_refresh_run(self) -> RefreshRunSummary | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, started_ts, finished_ts, status, message "
                "FROM refresh_runs ORDER BY started_ts DESC, id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return RefreshRunSummary(
            run_id=int(row["id"]),
            started_at=datetime.fromtimestamp(row["started_ts"], tz=SHANGHAI_TZ),
            finished_at=(
                datetime.fromtimestamp(row["finished_ts"], tz=SHANGHAI_TZ)
                if row["finished_ts"] is not None
                else None
            ),
            status=str(row["status"]),
            message=str(row["message"] or ""),
        )

    def get_storage_stats(self) -> StorageStats:
        with self._connect() as connection:
            article_count = int(connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
            snapshot_count = int(connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0])
        try:
            database_bytes = self.database_path.stat().st_size
        except OSError:
            database_bytes = 0
        return StorageStats(
            database_bytes=database_bytes,
            article_count=article_count,
            snapshot_count=snapshot_count,
            latest_run=self.get_latest_refresh_run(),
        )

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

    def set_popularity_state(self, snapshot: OfficialPopularitySnapshot, updated_at: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO app_state(key, value_json, updated_ts) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json=excluded.value_json,
                    updated_ts=excluded.updated_ts
                """,
                (
                    POPULARITY_STATE_KEY,
                    json.dumps(snapshot.to_dict(), ensure_ascii=False),
                    int(updated_at.timestamp()),
                ),
            )

    def get_popularity_state(self) -> OfficialPopularitySnapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_state WHERE key=?",
                (POPULARITY_STATE_KEY,),
            ).fetchone()
        if row is None:
            return None
        try:
            return OfficialPopularitySnapshot.from_dict(json.loads(row["value_json"]))
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

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
            connection.execute("DELETE FROM guba_posts")
            connection.execute("DELETE FROM guba_stock_catalog")
            connection.execute("DELETE FROM guba_scan_state")
            connection.execute("DELETE FROM app_state")
        with self._connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
