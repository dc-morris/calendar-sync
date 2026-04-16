import sqlite3
from datetime import datetime, timezone
from calendar_sync.models import SyncPair

SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_pairs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    icloud_uid      TEXT NOT NULL UNIQUE,
    google_event_id TEXT NOT NULL UNIQUE,
    icloud_etag     TEXT,
    google_etag     TEXT,
    content_hash    TEXT NOT NULL,
    last_modified   TEXT NOT NULL,
    last_synced_at  TEXT NOT NULL,
    source_origin   TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    status          TEXT NOT NULL DEFAULT 'running',
    events_created  INTEGER DEFAULT 0,
    events_updated  INTEGER DEFAULT 0,
    events_deleted  INTEGER DEFAULT 0,
    error_message   TEXT
);

CREATE TABLE IF NOT EXISTS pending_changes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL,
    target_side     TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_id         INTEGER REFERENCES sync_pairs(id),
    summary         TEXT NOT NULL,
    description     TEXT,
    location        TEXT,
    start_time      TEXT NOT NULL,
    end_time        TEXT NOT NULL,
    is_all_day      INTEGER NOT NULL DEFAULT 0,
    recurrence_rule TEXT,
    status          TEXT NOT NULL DEFAULT 'CONFIRMED',
    source_origin   TEXT,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sync_pairs_icloud ON sync_pairs(icloud_uid);
CREATE INDEX IF NOT EXISTS idx_sync_pairs_google ON sync_pairs(google_event_id);
CREATE INDEX IF NOT EXISTS idx_pending_target ON pending_changes(target_side, event_id);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_time);
CREATE INDEX IF NOT EXISTS idx_events_pair ON events(pair_id);
"""


class SyncDB:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def get_all_pairs(self) -> list[SyncPair]:
        rows = self.conn.execute("SELECT * FROM sync_pairs").fetchall()
        return [
            SyncPair(
                id=r["id"],
                icloud_uid=r["icloud_uid"],
                google_event_id=r["google_event_id"],
                icloud_etag=r["icloud_etag"],
                google_etag=r["google_etag"],
                content_hash=r["content_hash"],
                last_modified=r["last_modified"],
                last_synced_at=r["last_synced_at"],
                source_origin=r["source_origin"],
            )
            for r in rows
        ]

    def create_pair(
        self,
        icloud_uid: str,
        google_event_id: str,
        content_hash: str,
        source_origin: str,
        icloud_etag: str | None = None,
        google_etag: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO sync_pairs
               (icloud_uid, google_event_id, content_hash, source_origin,
                icloud_etag, google_etag, last_modified, last_synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (icloud_uid, google_event_id, content_hash, source_origin,
             icloud_etag, google_etag, now, now),
        )
        self.conn.commit()

    def update_pair(
        self,
        pair_id: int,
        content_hash: str,
        icloud_etag: str | None = None,
        google_etag: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """UPDATE sync_pairs
               SET content_hash = ?, icloud_etag = ?, google_etag = ?,
                   last_modified = ?, last_synced_at = ?
               WHERE id = ?""",
            (content_hash, icloud_etag, google_etag, now, now, pair_id),
        )
        self.conn.commit()

    def delete_pair(self, pair_id: int) -> None:
        self.conn.execute("DELETE FROM sync_pairs WHERE id = ?", (pair_id,))
        self.conn.commit()

    def record_pending_change(
        self, event_id: str, target_side: str, content_hash: str, ttl_seconds: int = 900
    ) -> None:
        now = datetime.now(timezone.utc)
        expires = datetime.fromtimestamp(now.timestamp() + ttl_seconds, tz=timezone.utc)
        self.conn.execute(
            """INSERT INTO pending_changes (event_id, target_side, content_hash, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (event_id, target_side, content_hash, now.isoformat(), expires.isoformat()),
        )
        self.conn.commit()

    def is_our_pending_change(self, target_side: str, event_id: str) -> bool:
        row = self.conn.execute(
            """SELECT 1 FROM pending_changes
               WHERE target_side = ? AND event_id = ? AND expires_at > datetime('now')
               LIMIT 1""",
            (target_side, event_id),
        ).fetchone()
        return row is not None

    def expire_pending_changes(self) -> None:
        self.conn.execute("DELETE FROM pending_changes WHERE expires_at <= datetime('now')")
        self.conn.commit()

    def start_sync_run(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.execute(
            "INSERT INTO sync_runs (started_at, status) VALUES (?, 'running')",
            (now,),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def complete_sync_run(
        self,
        run_id: int,
        status: str,
        created: int = 0,
        updated: int = 0,
        deleted: int = 0,
        error: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """UPDATE sync_runs
               SET completed_at = ?, status = ?, events_created = ?,
                   events_updated = ?, events_deleted = ?, error_message = ?
               WHERE id = ?""",
            (now, status, created, updated, deleted, error, run_id),
        )
        self.conn.commit()

    def last_sync_run(self) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def recent_sync_runs(self, limit: int = 10) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def pair_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM sync_pairs").fetchone()
        return row["cnt"] if row else 0

    def upsert_event(
        self,
        pair_id: int,
        summary: str,
        start_time: str,
        end_time: str,
        is_all_day: bool,
        source_origin: str | None = None,
        description: str | None = None,
        location: str | None = None,
        recurrence_rule: str | None = None,
        status: str = "CONFIRMED",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        existing = self.conn.execute(
            "SELECT id FROM events WHERE pair_id = ?", (pair_id,)
        ).fetchone()
        if existing:
            self.conn.execute(
                """UPDATE events SET summary = ?, description = ?, location = ?,
                   start_time = ?, end_time = ?, is_all_day = ?, recurrence_rule = ?,
                   status = ?, source_origin = ?, updated_at = ?
                   WHERE pair_id = ?""",
                (summary, description, location, start_time, end_time,
                 int(is_all_day), recurrence_rule, status, source_origin, now, pair_id),
            )
        else:
            self.conn.execute(
                """INSERT INTO events (pair_id, summary, description, location,
                   start_time, end_time, is_all_day, recurrence_rule, status,
                   source_origin, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (pair_id, summary, description, location, start_time, end_time,
                 int(is_all_day), recurrence_rule, status, source_origin, now),
            )
        self.conn.commit()

    def delete_event_by_pair(self, pair_id: int) -> None:
        self.conn.execute("DELETE FROM events WHERE pair_id = ?", (pair_id,))
        self.conn.commit()

    def get_events(self, from_date: str | None = None, to_date: str | None = None) -> list[dict]:
        query = "SELECT * FROM events WHERE 1=1"
        params: list = []
        if from_date:
            query += " AND end_time >= ?"
            params.append(from_date)
        if to_date:
            query += " AND start_time <= ?"
            params.append(to_date)
        query += " ORDER BY start_time ASC"
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
