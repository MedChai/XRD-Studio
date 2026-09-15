"""SQLite 数据库访问层(§8.2)。

引擎独占访问数据库(§3)。所有表使用软删除(deleted_at)。
schema 与 README §8.2 一致,使用 WAL 模式提升并发读性能。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    sample_code TEXT DEFAULT '',
    batch TEXT DEFAULT '',
    material TEXT DEFAULT '',
    temperature TEXT DEFAULT '',
    duration TEXT DEFAULT '',
    preparation_date TEXT DEFAULT '',
    radiation_target TEXT DEFAULT 'Cu',
    wavelength REAL DEFAULT 1.5418,
    notes TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS datasets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id INTEGER NOT NULL REFERENCES samples(id),
    name TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    source_path TEXT DEFAULT '',
    stored_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    x_axis_type TEXT NOT NULL DEFAULT 'two_theta',
    x_min REAL,
    x_max REAL,
    step REAL,
    point_count INTEGER,
    grid TEXT DEFAULT 'uniform',
    imported_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id INTEGER NOT NULL REFERENCES datasets(id),
    preset_id INTEGER REFERENCES analysis_presets(id),
    pipeline_json TEXT NOT NULL DEFAULT '{}',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS peaks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL REFERENCES analyses(id),
    position REAL NOT NULL,
    intensity REAL NOT NULL,
    fwhm REAL,
    area REAL,
    left_bound REAL,
    right_bound REAL,
    manual_override INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS analysis_presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    pipeline_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_samples_project ON samples(project_id);
CREATE INDEX IF NOT EXISTS idx_datasets_sample ON datasets(sample_id);
CREATE INDEX IF NOT EXISTS idx_datasets_hash ON datasets(file_hash);
CREATE INDEX IF NOT EXISTS idx_analyses_dataset ON analyses(dataset_id);
CREATE INDEX IF NOT EXISTS idx_peaks_analysis ON peaks(analysis_id);
CREATE INDEX IF NOT EXISTS idx_presets_project ON analysis_presets(project_id);
"""


class Database:
    """线程安全的 SQLite 封装。WAL 模式,检查外键。"""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def _init_schema(self) -> None:
        conn = self._conn()
        with conn:
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        conn = self._conn()
        with conn:
            return conn.execute(sql, params)

    def query_all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self._conn().execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self._conn().execute(sql, params).fetchone()

    def insert(self, sql: str, params: tuple = ()) -> int:
        """执行 INSERT 并返回 lastrowid。"""
        conn = self._conn()
        with conn:
            cur = conn.execute(sql, params)
            return int(cur.lastrowid)

    # --- 常用查询 ---

    def find_duplicate_by_hash(self, file_hash: str) -> sqlite3.Row | None:
        """SHA-256 查重(§8.4)。"""
        return self.query_one(
            """SELECT d.id, d.name, d.original_filename,
                      s.name AS sample_name, p.name AS project_name
               FROM datasets d
               JOIN samples s ON d.sample_id = s.id
               JOIN projects p ON s.project_id = p.id
               WHERE d.file_hash = ? AND d.deleted_at IS NULL
               LIMIT 1""",
            (file_hash,),
        )

    def get_library_tree(self) -> list[sqlite3.Row]:
        """返回未删除的 library 树(Sample 视图,含所属 Project 名)。"""
        return self.query_all(
            """SELECT s.id, s.project_id, s.name, s.sample_code,
                      s.temperature, s.duration, p.name AS project_name,
                      (SELECT COUNT(*) FROM datasets d
                       WHERE d.sample_id = s.id AND d.deleted_at IS NULL
                      ) AS dataset_count
               FROM samples s
               JOIN projects p ON s.project_id = p.id
               WHERE s.deleted_at IS NULL AND p.deleted_at IS NULL
               ORDER BY p.name, s.name"""
        )

    def soft_delete(self, table: str, row_id: int) -> None:
        assert table in ("projects", "samples", "datasets")
        self.execute(
            f"UPDATE {table} SET deleted_at = datetime('now') WHERE id = ?",
            (row_id,),
        )

    def restore(self, table: str, row_id: int) -> None:
        assert table in ("projects", "samples", "datasets")
        self.execute(
            f"UPDATE {table} SET deleted_at = NULL WHERE id = ?", (row_id,)
        )

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
