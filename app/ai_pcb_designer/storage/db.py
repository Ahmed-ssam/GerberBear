"""SQLite metadata store: projects, conversation messages and design revisions.
Design files live in project directories; only paths and small JSON go in the DB."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '', dir TEXT NOT NULL,
  created REAL NOT NULL, modified REAL NOT NULL, state_json TEXT DEFAULT '{}',
  current_revision INTEGER, ai_meta TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  ts REAL NOT NULL, role TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, data_json TEXT);
CREATE TABLE IF NOT EXISTS revisions (
  project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, rev INTEGER NOT NULL,
  ts REAL NOT NULL, description TEXT, spec_json TEXT, circuit_json TEXT, changes_json TEXT,
  dir TEXT NOT NULL, status TEXT, summary_json TEXT, PRIMARY KEY (project_id, rev));
CREATE INDEX IF NOT EXISTS idx_messages_project ON messages(project_id, id);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    def execute(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def query(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self.conn.execute(sql, args))

    @staticmethod
    def now() -> float:
        return time.time()

    @staticmethod
    def j(obj) -> str:
        return json.dumps(obj, default=str)
