"""SQLite storage: users (language, bot/human mode, form state), dedupe tables, leads, outbound log."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  igsid TEXT PRIMARY KEY,
  username TEXT,
  lang TEXT,
  mode TEXT NOT NULL DEFAULT 'bot',      -- bot | human
  human_since TEXT,
  greeted INTEGER NOT NULL DEFAULT 0,
  state TEXT,                            -- lead form step
  form TEXT NOT NULL DEFAULT '{}',
  last_in TEXT                           -- last message FROM the user (opens the 24 h window)
);
CREATE TABLE IF NOT EXISTS seen_mids (mid TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS our_mids (mid TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS comments (
  comment_id TEXT PRIMARY KEY, igsid TEXT, media_id TEXT, text TEXT, status TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, igsid TEXT, username TEXT, name TEXT,
  item TEXT, contact TEXT, lang TEXT, source TEXT
);
CREATE TABLE IF NOT EXISTS handovers (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, igsid TEXT, username TEXT, reason TEXT, text TEXT
);
CREATE TABLE IF NOT EXISTS private_reply_log (at TEXT);
"""


class DB:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def _x(self, sql: str, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    # ---- users ----
    def user(self, igsid: str) -> dict:
        row = self._x("SELECT * FROM users WHERE igsid=?", (igsid,)).fetchone()
        if not row:
            self._x("INSERT INTO users (igsid) VALUES (?)", (igsid,))
            row = self._x("SELECT * FROM users WHERE igsid=?", (igsid,)).fetchone()
        d = dict(row)
        d["form"] = json.loads(d["form"] or "{}")
        return d

    def update_user(self, igsid: str, **kw) -> None:
        if "form" in kw:
            kw["form"] = json.dumps(kw["form"], ensure_ascii=False)
        cols = ", ".join(f"{k}=?" for k in kw)
        self._x(f"UPDATE users SET {cols} WHERE igsid=?", (*kw.values(), igsid))

    # ---- dedupe ----
    def first_time(self, mid: str) -> bool:
        """True once per message id (Meta retries webhooks for up to 36 h)."""
        try:
            self._x("INSERT INTO seen_mids (mid) VALUES (?)", (mid,))
            return True
        except sqlite3.IntegrityError:
            return False

    def remember_ours(self, mid: str) -> None:
        if mid:
            self._x("INSERT OR IGNORE INTO our_mids (mid) VALUES (?)", (mid,))

    def is_ours(self, mid: str) -> bool:
        return self._x("SELECT 1 FROM our_mids WHERE mid=?", (mid,)).fetchone() is not None

    # ---- comments ----
    def claim_comment(self, comment_id: str, igsid: str, media_id: str, text: str, at: str) -> bool:
        try:
            self._x("INSERT INTO comments VALUES (?,?,?,?,?,?)", (comment_id, igsid, media_id, text, "new", at))
            return True
        except sqlite3.IntegrityError:
            return False

    def set_comment_status(self, comment_id: str, status: str) -> None:
        self._x("UPDATE comments SET status=? WHERE comment_id=?", (status, comment_id))

    def replied_to_user_on_media(self, igsid: str, media_id: str) -> bool:
        return self._x("SELECT 1 FROM comments WHERE igsid=? AND media_id=? AND status='sent'",
                       (igsid, media_id)).fetchone() is not None

    def private_replies_since(self, since_iso: str) -> int:
        return self._x("SELECT COUNT(*) FROM private_reply_log WHERE at>=?", (since_iso,)).fetchone()[0]

    def log_private_reply(self, at: str) -> None:
        self._x("INSERT INTO private_reply_log VALUES (?)", (at,))

    # ---- leads / handovers ----
    def add_lead(self, **kw) -> int:
        cols = ", ".join(kw)
        q = ", ".join("?" for _ in kw)
        return self._x(f"INSERT INTO leads ({cols}) VALUES ({q})", tuple(kw.values())).lastrowid

    def leads(self) -> list[dict]:
        return [dict(r) for r in self._x("SELECT * FROM leads ORDER BY id").fetchall()]

    def add_handover(self, **kw) -> int:
        cols = ", ".join(kw)
        q = ", ".join("?" for _ in kw)
        return self._x(f"INSERT INTO handovers ({cols}) VALUES ({q})", tuple(kw.values())).lastrowid

    def handovers(self) -> list[dict]:
        return [dict(r) for r in self._x("SELECT * FROM handovers ORDER BY id").fetchall()]
