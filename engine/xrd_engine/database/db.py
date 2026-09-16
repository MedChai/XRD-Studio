"""SQLite 数据库访问层(§8.2)。

引擎独占访问数据库(§3)。所有表使用软删除(deleted_at)。
schema 与 README §8.2 一致,使用 WAL 模式提升并发读性能。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA_VERSION = 1

# 允许软删除的表。**不要用 assert 做这个校验**:
# `python -O` 会移除 assert,那时非法表名会直接拼进 SQL,变成注入面;
# 而且 assert 抛的 AssertionError 会被 api 层兜底成"引擎内部错误",
# 用户看不到人话。这里用真异常,由调用方翻译成中文提示。
ALLOWED_TABLES = ("projects", "samples", "datasets")

# 三类可删除对象的层级(浅 → 深),purge 必须按此顺序深度优先处理
_TABLE_DEPTH = {"projects": 0, "samples": 1, "datasets": 2}

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
        """SHA-256 查重(§8.4)。

        **必须一并过滤父级的删除状态**:否则把一个 Project 移入回收站后,
        重新导入同一个文件仍会被判为"重复",并且提示的是一个已经不在
        Library 里的路径 —— 用户既看不到那条记录,也没法处理。
        """
        return self.query_one(
            """SELECT d.id, d.name, d.original_filename,
                      s.name AS sample_name, p.name AS project_name
               FROM datasets d
               JOIN samples s ON d.sample_id = s.id
               JOIN projects p ON s.project_id = p.id
               WHERE d.file_hash = ?
                 AND d.deleted_at IS NULL
                 AND s.deleted_at IS NULL
                 AND p.deleted_at IS NULL
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

    def soft_delete(self, table: str, row_id: int) -> bool:
        """把一行移入回收站(软删除)。

        **只标记被直接删除的那一行,不级联写子级的 deleted_at** —— 这是
        「隐式级联」:子对象的可见性由父级决定,查询时一并判父级即可
        (见 get_library_tree / find_duplicate_by_hash)。

        为什么不级联写:
          级联写会让「删项目 → 恢复项目」无法区分「这些 sample 是删项目时
          一起删的」还是「用户本来就单独删过」。要正确恢复就得再加一张
          操作日志表,成本远大于收益。隐式级联则天然正确 —— 恢复项目时
          里面的样品与数据集自动全部回来。

        返回是否真的改动了行(已删或不存在时为 False)。
        """
        self._check_table(table)
        cur = self.execute(
            f"""UPDATE {table} SET deleted_at = datetime('now'),
                       updated_at = datetime('now')
               WHERE id = ? AND deleted_at IS NULL""",
            (row_id,),
        )
        return cur.rowcount > 0

    def restore(self, table: str, row_id: int) -> bool:
        """从回收站恢复一行。返回是否真的改动了行。"""
        self._check_table(table)
        cur = self.execute(
            f"""UPDATE {table} SET deleted_at = NULL, updated_at = datetime('now')
               WHERE id = ? AND deleted_at IS NOT NULL""",
            (row_id,),
        )
        return cur.rowcount > 0

    @staticmethod
    def _check_table(table: str) -> str:
        if table not in ALLOWED_TABLES:
            raise ValueError(f"unsupported table: {table!r}")
        return table

    # --- 回收站 ---

    def descendant_dataset_count(self, table: str, row_id: int) -> int:
        """隐式随该行一起下线的 dataset 数量,用于给用户一个量级提示。"""
        self._check_table(table)
        if table == "datasets":
            return 0
        if table == "samples":
            sql = """SELECT COUNT(*) AS n FROM datasets
                     WHERE sample_id = ? AND deleted_at IS NULL"""
        else:  # projects
            sql = """SELECT COUNT(*) AS n FROM datasets d
                     JOIN samples s ON d.sample_id = s.id
                     JOIN projects p ON s.project_id = p.id
                     WHERE s.project_id = ? AND d.deleted_at IS NULL
                       AND s.deleted_at IS NULL AND p.deleted_at IS NULL"""
        row = self.query_one(sql, (row_id,))
        return int(row["n"]) if row else 0

    def trash_rows(self, limit: int = 500) -> list[sqlite3.Row]:
        """回收站列表:项目 / 样品 / 数据集三类扁平混排。

        每条都带完整的层级路径(project_name / sample_name),因为回收站的
        核心任务是「找到那条被误删的记录」,一条路径字符串比展开一棵树快。

        `kind` 用的是**表名**(projects/samples/datasets),不是单数名词 ——
        这样前端可以把 item.kind 直接透传给 meta.restore / meta.purge,
        不必再维护一张"单数 ↔ 表名"的映射表。

        排序键必须是 `deleted_at DESC, kind, id`:datetime('now') 只有秒级
        精度,同一批删除会大量并列,只按时间排会让两次打开回收站的顺序不同。
        """
        return self.query_all(
            """SELECT 'datasets' AS kind, d.id AS id, d.name AS name,
                      d.deleted_at AS deleted_at,
                      p.name AS project_name, s.name AS sample_name,
                      d.stored_path AS stored_path, 0 AS child_count
               FROM datasets d
               JOIN samples s ON d.sample_id = s.id
               JOIN projects p ON s.project_id = p.id
               WHERE d.deleted_at IS NOT NULL
               UNION ALL
               SELECT 'samples' AS kind, s.id AS id, s.name AS name,
                      s.deleted_at AS deleted_at,
                      p.name AS project_name, NULL AS sample_name,
                      NULL AS stored_path,
                      (SELECT COUNT(*) FROM datasets d2
                       WHERE d2.sample_id = s.id AND d2.deleted_at IS NULL
                      ) AS child_count
               FROM samples s
               JOIN projects p ON s.project_id = p.id
               WHERE s.deleted_at IS NOT NULL
               UNION ALL
               SELECT 'projects' AS kind, p.id AS id, p.name AS name,
                      p.deleted_at AS deleted_at,
                      NULL AS project_name, NULL AS sample_name,
                      NULL AS stored_path,
                      (SELECT COUNT(*) FROM samples s2
                       WHERE s2.project_id = p.id AND s2.deleted_at IS NULL
                      ) AS child_count
               FROM projects p
               WHERE p.deleted_at IS NOT NULL
               ORDER BY deleted_at DESC, kind, id
               LIMIT ?""",
            (limit,),
        )

    def dataset_ids_of(self, table: str, row_id: int) -> list[sqlite3.Row]:
        """列出某对象下所有 dataset 的 (id, stored_path),供 purge 删磁盘。

        必须在删 DB 行**之前**调用 —— 行删了就查不到 stored_path 了。
        """
        self._check_table(table)
        if table == "datasets":
            return self.query_all(
                "SELECT id, stored_path FROM datasets WHERE id = ?", (row_id,)
            )
        if table == "samples":
            return self.query_all(
                "SELECT id, stored_path FROM datasets WHERE sample_id = ?", (row_id,)
            )
        return self.query_all(
            """SELECT d.id, d.stored_path FROM datasets d
               JOIN samples s ON d.sample_id = s.id
               WHERE s.project_id = ?""",
            (row_id,),
        )

    def purge_dataset(self, dataset_id: int) -> str | None:
        """**永久删除**一个 dataset 及其分析结果,返回它的 stored_path。

        只删数据库行;磁盘文件由调用方在事务成功后再删 —— SQL 先删、
        文件后删,文件删失败只记日志,避免出现"文件没了但行还在"的
        自相矛盾状态。
        """
        row = self.query_one(
            "SELECT stored_path FROM datasets WHERE id = ?", (dataset_id,)
        )
        if row is None:
            return None
        stored = row["stored_path"]
        # peaks → analyses → datasets,由深到浅(外键约束已开启)
        self.execute(
            """DELETE FROM peaks WHERE analysis_id IN
                 (SELECT id FROM analyses WHERE dataset_id = ?)""",
            (dataset_id,),
        )
        self.execute("DELETE FROM analyses WHERE dataset_id = ?", (dataset_id,))
        self.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
        return stored

    def purge_sample(self, sample_id: int) -> list[str]:
        """永久删除一个 sample 及其全部 dataset,返回要清理的 stored_path 列表。"""
        paths = [
            r["stored_path"]
            for r in self.query_all(
                "SELECT stored_path FROM datasets WHERE sample_id = ?", (sample_id,)
            )
        ]
        for ds_id in [
            r["id"]
            for r in self.query_all(
                "SELECT id FROM datasets WHERE sample_id = ?", (sample_id,)
            )
        ]:
            self.purge_dataset(ds_id)
        self.execute("DELETE FROM samples WHERE id = ?", (sample_id,))
        return paths

    def purge_project(self, project_id: int) -> list[str]:
        """永久删除一个 project 及其全部 sample / dataset / preset。"""
        paths = [
            r["stored_path"]
            for r in self.query_all(
                """SELECT d.stored_path FROM datasets d
                   JOIN samples s ON d.sample_id = s.id
                   WHERE s.project_id = ?""",
                (project_id,),
            )
        ]
        for sample_id in [
            r["id"]
            for r in self.query_all(
                "SELECT id FROM samples WHERE project_id = ?", (project_id,)
            )
        ]:
            self.purge_sample(sample_id)
        # presets 有 project_id 外键,不清会留下孤儿行,且删 project 时
        # 会被 PRAGMA foreign_keys=ON 拦下
        self.execute("DELETE FROM analysis_presets WHERE project_id = ?", (project_id,))
        self.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return paths

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
