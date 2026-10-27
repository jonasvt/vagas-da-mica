"""Banco de dados SQLite: esquema, conexão e operações básicas."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .util import now_iso

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    id                   TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    type                 TEXT,
    method               TEXT,
    url                  TEXT,
    enabled              INTEGER NOT NULL DEFAULT 1,
    interval_min         INTEGER NOT NULL DEFAULT 60,
    last_check_at        TEXT,
    last_success_at      TEXT,
    next_check_at        TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error           TEXT,
    first_sync_done      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS jobs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id         TEXT NOT NULL,
    source_item_id    TEXT NOT NULL,
    source_name       TEXT,
    community         TEXT,           -- subreddit, canal do Discord, categoria do site
    source_url        TEXT,           -- link do post/vaga original
    canonical_url     TEXT,
    apply_url         TEXT,
    title             TEXT,
    company           TEXT,           -- empresa ou canal, só se informado
    author            TEXT,           -- quem postou
    description       TEXT,           -- texto original preservado
    published_at      TEXT,           -- data de publicação original (se a fonte informa)
    discovered_at     TEXT NOT NULL,  -- quando entrou no MEU banco
    last_seen_at      TEXT,
    updated_at        TEXT,
    discovery_kind    TEXT,           -- live | backfill | manual
    post_kind         TEXT,           -- hiring | for_hire | unclear
    comp_min          REAL,
    comp_max          REAL,
    comp_currency     TEXT,
    comp_unit         TEXT,           -- video | hour | month | year | project | minute | short | unknown
    comp_text         TEXT,           -- trecho exato de onde o valor foi lido
    payment_model     TEXT,           -- per_video | hourly | fixed | monthly_retainer | salary | revenue_share | unspecified
    employment_type   TEXT,
    remote_scope      TEXT,           -- worldwide | restricted | onsite | unknown
    location_text     TEXT,
    software          TEXT,           -- JSON
    specializations   TEXT,           -- JSON
    experience        TEXT,
    languages         TEXT,           -- JSON
    legit_confidence  REAL,
    legit_flags       TEXT,           -- JSON
    score             INTEGER,
    score_reasons     TEXT,           -- JSON
    duplicate_of      INTEGER REFERENCES jobs(id),
    dup_reason        TEXT,
    title_norm        TEXT,
    fingerprint       TEXT,
    content_hash      TEXT,
    job_status        TEXT DEFAULT 'open',   -- open | closed (estado da vaga na fonte)
    user_status       TEXT NOT NULL DEFAULT 'new',  -- new | interested | applied | interviewing | rejected | closed
    saved             INTEGER NOT NULL DEFAULT 0,
    hidden            INTEGER NOT NULL DEFAULT 0,
    notes             TEXT,
    follow_up_at      TEXT,
    applied_at        TEXT,
    status_changed_at TEXT,
    notified_at       TEXT,
    notified_hash     TEXT,
    raw               TEXT,
    UNIQUE (source_id, source_item_id)
);
CREATE INDEX IF NOT EXISTS ix_jobs_canonical  ON jobs(canonical_url);
CREATE INDEX IF NOT EXISTS ix_jobs_fp         ON jobs(fingerprint);
CREATE INDEX IF NOT EXISTS ix_jobs_discovered ON jobs(discovered_at);
CREATE INDEX IF NOT EXISTS ix_jobs_published  ON jobs(published_at);
CREATE INDEX IF NOT EXISTS ix_jobs_score      ON jobs(score);
CREATE INDEX IF NOT EXISTS ix_jobs_dup        ON jobs(duplicate_of);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id     TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT,       -- ok | error | rate_limited
    http_status   INTEGER,
    items_seen    INTEGER DEFAULT 0,
    items_new     INTEGER DEFAULT 0,
    items_updated INTEGER DEFAULT 0,
    items_skipped INTEGER DEFAULT 0,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS ix_runs_source ON runs(source_id, started_at);

-- Bot do Discord: alertas de cada pessoa e o que já foi enviado
CREATE TABLE IF NOT EXISTS alertas (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   TEXT NOT NULL,
    termos    TEXT NOT NULL,   -- "python, backend, cientista de dados"
    excluir   TEXT,            -- "senior, estágio"
    criado_em TEXT NOT NULL,
    ativo     INTEGER NOT NULL DEFAULT 1,
    canal_id  TEXT             -- canal do servidor onde as vagas são postadas
);
CREATE TABLE IF NOT EXISTS enviadas (
    user_id TEXT NOT NULL,     -- destino: id do canal
    job_id  INTEGER NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (user_id, job_id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id   INTEGER,
    kind     TEXT,     -- immediate | digest
    channel  TEXT,
    sent_at  TEXT,
    status   TEXT,     -- sent | failed | logged
    error    TEXT,
    payload  TEXT
);
"""

JSON_FIELDS = ("software", "specializations", "languages", "legit_flags", "score_reasons", "raw")

_lock = threading.RLock()


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    init(conn)
    return conn


def init(conn: sqlite3.Connection) -> None:
    with _lock:
        conn.executescript(SCHEMA)
        # bancos criados antes dos alertas por canal
        if "canal_id" not in {r[1] for r in conn.execute("PRAGMA table_info(alertas)")}:
            conn.execute("ALTER TABLE alertas ADD COLUMN canal_id TEXT")
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
        )
        conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('created_at', ?)", (now_iso(),))
        conn.commit()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for f in JSON_FIELDS:
        if f in d and isinstance(d[f], str):
            try:
                d[f] = json.loads(d[f])
            except ValueError:
                pass
    return d


def dumps(v) -> str | None:
    if v is None:
        return None
    return json.dumps(v, ensure_ascii=False)


def sync_sources(conn: sqlite3.Connection, sources: list[dict]) -> None:
    """Garante que cada fonte do config exista na tabela (sem apagar o histórico)."""
    with _lock:
        for s in sources:
            conn.execute(
                """INSERT INTO sources(id, name, type, method, url, enabled, interval_min)
                   VALUES (:id, :name, :type, :method, :url, :enabled, :interval_min)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name, type=excluded.type,
                       method=excluded.method, url=excluded.url, interval_min=excluded.interval_min""",
                {
                    "id": s["id"],
                    "name": s["name"],
                    "type": s.get("type"),
                    "method": s.get("method"),
                    "url": s.get("url"),
                    "enabled": 1 if s.get("enabled", True) else 0,
                    "interval_min": int(s.get("interval_min", 60)),
                },
            )
        conn.commit()


def get_meta(conn, key, default=None):
    r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_meta(conn, key, value):
    with _lock:
        conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        conn.commit()
