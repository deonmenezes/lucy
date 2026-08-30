"""Local persistent memory for Lucy.

Everything lives in a single SQLite file (default ~/.lucy/memory.db), so memory
survives restarts and needs no server, VM, or third-party vector database.

Layout:
    callers  one row per phone number, with a display name
    calls    one row per call, with an end-of-call summary
    turns    every utterance, written live so nothing is lost on a crash
    facts    durable extracted facts about the caller
    search   FTS5 index over turns, facts, and summaries

All writes go through one connection guarded by a lock. Guava runs each call on
its own thread, so this must be thread-safe.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("LUCY_DB", Path.home() / ".lucy" / "memory.db"))

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS callers (
    phone       TEXT PRIMARY KEY,
    name        TEXT,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    call_count  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS calls (
    call_id     TEXT PRIMARY KEY,
    phone       TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    summary     TEXT,
    reason      TEXT
);
CREATE TABLE IF NOT EXISTS turns (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id  TEXT NOT NULL,
    phone    TEXT NOT NULL,
    speaker  TEXT NOT NULL,
    text     TEXT NOT NULL,
    ts       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS turns_phone_idx ON turns (phone, id);
CREATE TABLE IF NOT EXISTS facts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    phone      TEXT NOT NULL,
    fact       TEXT NOT NULL,
    category   TEXT,
    call_id    TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (phone, fact)
);
CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    phone      TEXT NOT NULL,
    task       TEXT NOT NULL,
    done       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    done_at    TEXT
);
CREATE INDEX IF NOT EXISTS tasks_phone_idx ON tasks (phone, done, id);
CREATE VIRTUAL TABLE IF NOT EXISTS search
    USING fts5(phone UNINDEXED, kind UNINDEXED, ref UNINDEXED, text);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
        _conn.commit()
    return _conn


def _index(phone: str, kind: str, ref: str, text: str) -> None:
    connect().execute(
        "INSERT INTO search (phone, kind, ref, text) VALUES (?, ?, ?, ?)",
        (phone, kind, ref, text),
    )


def ensure_caller(phone: str) -> None:
    with _lock:
        c = connect()
        c.execute(
            "INSERT INTO callers (phone, first_seen, last_seen, call_count) "
            "VALUES (?, ?, ?, 0) ON CONFLICT(phone) DO UPDATE SET last_seen=excluded.last_seen",
            (phone, _now(), _now()),
        )
        c.commit()


def set_name(phone: str, name: str) -> None:
    with _lock:
        c = connect()
        c.execute("UPDATE callers SET name=? WHERE phone=?", (name.strip(), phone))
        c.commit()


def start_call(call_id: str, phone: str) -> None:
    with _lock:
        c = connect()
        c.execute(
            "INSERT OR IGNORE INTO calls (call_id, phone, started_at) VALUES (?, ?, ?)",
            (call_id, phone, _now()),
        )
        c.execute(
            "UPDATE callers SET call_count = call_count + 1, last_seen = ? WHERE phone = ?",
            (_now(), phone),
        )
        c.commit()


def record_turn(call_id: str, phone: str, speaker: str, text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    with _lock:
        c = connect()
        c.execute(
            "INSERT INTO turns (call_id, phone, speaker, text, ts) VALUES (?, ?, ?, ?, ?)",
            (call_id, phone, speaker, text, _now()),
        )
        if speaker == "caller":
            _index(phone, "turn", call_id, text)
        c.commit()


def end_call(call_id: str, summary: str | None, reason: str | None) -> None:
    with _lock:
        c = connect()
        c.execute(
            "UPDATE calls SET ended_at=?, summary=?, reason=? WHERE call_id=?",
            (_now(), summary, reason, call_id),
        )
        if summary:
            row = c.execute("SELECT phone FROM calls WHERE call_id=?", (call_id,)).fetchone()
            if row:
                _index(row["phone"], "summary", call_id, summary)
        c.commit()


def add_facts(phone: str, facts: list[str], call_id: str | None = None) -> int:
    added = 0
    with _lock:
        c = connect()
        for fact in facts:
            fact = (fact or "").strip()
            if not fact:
                continue
            cur = c.execute(
                "INSERT OR IGNORE INTO facts (phone, fact, call_id, created_at) VALUES (?, ?, ?, ?)",
                (phone, fact, call_id, _now()),
            )
            if cur.rowcount:
                _index(phone, "fact", call_id or "", fact)
                added += 1
        c.commit()
    return added


def _norm(text: str) -> str:
    return " ".join(sorted(w for w in re.findall(r"[a-z0-9']+", text.lower()) if len(w) > 2))


def add_task(phone: str, task: str) -> str:
    """Add one thing to this caller's list, ignoring a repeat of one already open.

    The agent can fire the same action twice for a single spoken request, so
    without this the list fills up with duplicates.
    """
    task = (task or "").strip()
    if not task:
        return ""
    key = _norm(task)
    with _lock:
        c = connect()
        existing = c.execute(
            "SELECT task FROM tasks WHERE phone=? AND done=0", (phone,)
        ).fetchall()
        for r in existing:
            if _norm(r["task"]) == key:
                return r["task"]
        c.execute(
            "INSERT INTO tasks (phone, task, created_at) VALUES (?, ?, ?)",
            (phone, task, _now()),
        )
        _index(phone, "task", "", task)
        c.commit()
    return task


def open_tasks(phone: str, limit: int = 25) -> list[str]:
    rows = connect().execute(
        "SELECT task FROM tasks WHERE phone=? AND done=0 ORDER BY id", (phone, )
    ).fetchall()
    return [r["task"] for r in rows][:limit]


def complete_task(phone: str, query: str) -> str | None:
    """Mark the best keyword match among this caller's open tasks as done."""
    rows = connect().execute(
        "SELECT id, task FROM tasks WHERE phone=? AND done=0 ORDER BY id", (phone,)
    ).fetchall()
    if not rows:
        return None
    words = {w for w in re.findall(r"[a-z0-9']+", query.lower()) if len(w) > 2}
    best, best_score = None, 0
    for r in rows:
        task_words = {w for w in re.findall(r"[a-z0-9']+", r["task"].lower()) if len(w) > 2}
        score = len(words & task_words)
        if score > best_score:
            best, best_score = r, score
    if best is None:
        return None
    with _lock:
        c = connect()
        c.execute("UPDATE tasks SET done=1, done_at=? WHERE id=?", (_now(), best["id"]))
        c.commit()
    return best["task"]


def forget(phone: str, query: str) -> list[str]:
    """Delete this caller's facts matching a description. Returns what went."""
    words = {w for w in re.findall(r"[a-z0-9']+", query.lower()) if len(w) > 2}
    if not words:
        return []
    rows = connect().execute("SELECT id, fact FROM facts WHERE phone=?", (phone,)).fetchall()
    hits = [
        r for r in rows
        if words & {w for w in re.findall(r"[a-z0-9']+", r["fact"].lower()) if len(w) > 2}
    ]
    if not hits:
        return []
    with _lock:
        c = connect()
        for r in hits:
            c.execute("DELETE FROM facts WHERE id=?", (r["id"],))
            c.execute("DELETE FROM search WHERE phone=? AND kind='fact' AND text=?",
                      (phone, r["fact"]))
        c.commit()
    return [r["fact"] for r in hits]


def profile(phone: str) -> dict:
    """Everything Lucy knows about one caller, for injection at call start."""
    c = connect()
    row = c.execute("SELECT * FROM callers WHERE phone=?", (phone,)).fetchone()
    facts = [
        r["fact"]
        for r in c.execute(
            "SELECT fact FROM facts WHERE phone=? ORDER BY id DESC LIMIT 40", (phone,)
        )
    ]
    summaries = [
        {"when": r["started_at"], "summary": r["summary"]}
        for r in c.execute(
            "SELECT started_at, summary FROM calls "
            "WHERE phone=? AND summary IS NOT NULL ORDER BY started_at DESC LIMIT 5",
            (phone,),
        )
    ]
    return {
        "known": row is not None and (row["call_count"] or 0) > 0,
        "name": row["name"] if row else None,
        "call_count": row["call_count"] if row else 0,
        "first_seen": row["first_seen"] if row else None,
        "last_seen": row["last_seen"] if row else None,
        "facts": facts,
        "open_tasks": open_tasks(phone),
        "recent_calls": summaries,
    }


def _fts_query(text: str) -> str:
    """Turn free-form speech into a safe FTS5 OR query.

    Raw user text breaks FTS5 (bare punctuation, reserved words like NEAR), so
    each word is extracted and quoted.
    """
    words = re.findall(r"[A-Za-z0-9']+", text.lower())
    words = [w for w in words if len(w) > 2]
    return " OR ".join(f'"{w}"' for w in words[:12])


def search(phone: str, query: str, k: int = 8) -> list[str]:
    """Keyword-rank past turns, facts, and summaries for one caller."""
    q = _fts_query(query)
    if not q:
        return []
    rows = connect().execute(
        "SELECT kind, text FROM search WHERE phone=? AND search MATCH ? "
        "ORDER BY bm25(search) LIMIT ?",
        (phone, q, k),
    ).fetchall()
    return [f"[{r['kind']}] {r['text']}" for r in rows]


def transcript(call_id: str) -> list[tuple[str, str]]:
    rows = connect().execute(
        "SELECT speaker, text FROM turns WHERE call_id=? ORDER BY id", (call_id,)
    ).fetchall()
    return [(r["speaker"], r["text"]) for r in rows]


def stats() -> dict:
    c = connect()
    one = lambda sql: c.execute(sql).fetchone()[0]
    return {
        "db": str(DB_PATH),
        "callers": one("SELECT COUNT(*) FROM callers"),
        "calls": one("SELECT COUNT(*) FROM calls"),
        "turns": one("SELECT COUNT(*) FROM turns"),
        "facts": one("SELECT COUNT(*) FROM facts"),
        "open_tasks": one("SELECT COUNT(*) FROM tasks WHERE done=0"),
    }
