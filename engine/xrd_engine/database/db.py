"""SQLite 数据库访问层(§8.2)。

引擎独占访问数据库(§3)。所有表使用软删除(deleted_at)。
schema 与 README §8.2 一致,使用 WAL 模式提升并发读性能。
"""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 11

# 从 v2 → v3 的增量迁移(向后兼容,已存在的库只加列不重建表)。
# (schema_version, table, column, ddl) —— ddl 执行前会先判列是否存在。
_MIGRATIONS: tuple[tuple[int, str, str, str], ...] = (
    (10, "samples", "storage_folder", "ALTER TABLE samples ADD COLUMN storage_folder TEXT"),
    (9, "projects", "storage_folder", "ALTER TABLE projects ADD COLUMN storage_folder TEXT"),
    (8, "analyses", "peaks_pipeline_hash", "ALTER TABLE analyses ADD COLUMN peaks_pipeline_hash TEXT"),
    (8, "datasets", "x_column", "ALTER TABLE datasets ADD COLUMN x_column INTEGER"),
    (8, "datasets", "y_column", "ALTER TABLE datasets ADD COLUMN y_column INTEGER"),
    (8, "datasets", "acquisition_wavelength", "ALTER TABLE datasets ADD COLUMN acquisition_wavelength REAL"),
    (3, "peaks", "hkl", "ALTER TABLE peaks ADD COLUMN hkl TEXT"),
    (3, "peaks", "hkl_confidence", "ALTER TABLE peaks ADD COLUMN hkl_confidence TEXT"),
    # v4:把归属依据持久化。此前 dominance 只在内存里算出来,写 peaks 表时
    # 被丢掉 —— 重新加载数据集后前端只能显示"贡献 0%"。
    (4, "peaks", "dominance", "ALTER TABLE peaks ADD COLUMN dominance REAL"),
    (
        4, "peaks", "hkl_candidates",
        "ALTER TABLE peaks ADD COLUMN hkl_candidates TEXT",
    ),
    # v5:批次分组。同一次制样/同一批送样的多个数据集归一组。
    # 用可空列而不是独立关联表:一条数据集至多属于一个批次,多对多
    # 只会让"这条数据属于哪批"变成需要 JOIN 才能回答的问题。
    (
        5, "datasets", "batch_id",
        "ALTER TABLE datasets ADD COLUMN batch_id INTEGER REFERENCES batches(id)",
    ),
    # v5:库外原始目录。用户的数据往往按文件夹组织(一个文件夹一次实验),
    # 记下来才能在重新导入时认出"这个文件夹已经导过了"。
    (
        5, "samples", "links_json",
        "ALTER TABLE samples ADD COLUMN links_json TEXT DEFAULT '[]'",
    ),
    # v6:晶面标注持久化。逐峰 hkl 早已在 peaks 表,但全谱精修上下文
    # (a/c/zeroShift/rwp) 与 labels 快照此前只存内存/组件 state,重启即丢;
    # 而 peaks 行会被 analysis.commit 在参数变更时删除,context 无处可存。
    (
        6, "analyses", "annotation_json",
        "ALTER TABLE analyses ADD COLUMN annotation_json TEXT",
    ),
)

# 允许软删除的表。**不要用 assert 做这个校验**:
# `python -O` 会移除 assert,那时非法表名会直接拼进 SQL,变成注入面;
# 而且 assert 抛的 AssertionError 会被 api 层兜底成"引擎内部错误",
# 用户看不到人话。这里用真异常,由调用方翻译成中文提示。
ALLOWED_TABLES = ("projects", "samples", "datasets", "batches", "ec_cells")

# 可删除对象的层级(浅 → 深),purge 必须按此顺序深度优先处理
# ec_cells 与 datasets 同层:都直接挂在 sample 下,互不为父子。
_TABLE_DEPTH = {"projects": 0, "samples": 1, "batches": 2, "datasets": 3, "ec_cells": 3}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    storage_folder TEXT,
    description TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    storage_folder TEXT,
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
    links_json TEXT DEFAULT '[]',
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
    acquisition_wavelength REAL,
    x_column INTEGER,
    y_column INTEGER,
    x_min REAL,
    x_max REAL,
    step REAL,
    point_count INTEGER,
    grid TEXT DEFAULT 'uniform',
    batch_id INTEGER REFERENCES batches(id),
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
    annotation_json TEXT,
    peaks_pipeline_hash TEXT,
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
    hkl TEXT,
    hkl_confidence TEXT,
    dominance REAL,
    hkl_candidates TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id INTEGER NOT NULL REFERENCES samples(id),
    name TEXT NOT NULL,
    source_path TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS analysis_presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    pipeline_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS material_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    family TEXT NOT NULL DEFAULT 'high_ni_layered',
    formula TEXT NOT NULL DEFAULT '',
    ni_fraction REAL,
    li_tm_ratio REAL,
    dopants_json TEXT NOT NULL DEFAULT '{}',
    wavelength REAL,
    instrument_name TEXT NOT NULL DEFAULT '',
    reference_dataset_id INTEGER REFERENCES datasets(id),
    thresholds_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS domain_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL REFERENCES analyses(id),
    profile_id INTEGER REFERENCES material_profiles(id),
    domain TEXT NOT NULL,
    result_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    source_hash TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_samples_project ON samples(project_id);
CREATE INDEX IF NOT EXISTS idx_datasets_sample ON datasets(sample_id);
CREATE INDEX IF NOT EXISTS idx_datasets_hash ON datasets(file_hash);
CREATE INDEX IF NOT EXISTS idx_analyses_dataset ON analyses(dataset_id);
CREATE INDEX IF NOT EXISTS idx_peaks_analysis ON peaks(analysis_id);
CREATE INDEX IF NOT EXISTS idx_batches_sample ON batches(sample_id);
CREATE INDEX IF NOT EXISTS idx_presets_project ON analysis_presets(project_id);
CREATE INDEX IF NOT EXISTS idx_profiles_project ON material_profiles(project_id);
CREATE INDEX IF NOT EXISTS idx_domain_results_analysis
    ON domain_results(analysis_id, domain);

-- 蓝电 CEX 归档与历史电化学数据。ec_cells 与 datasets 同属样品。
-- 旧表格的循环统计、分析记录和磁盘缓存供历史备份读取。
CREATE TABLE IF NOT EXISTS ec_cells (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id INTEGER NOT NULL REFERENCES samples(id),
    name TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    source_path TEXT DEFAULT '',
    stored_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    format TEXT DEFAULT '',
    active_mass_mg REAL,
    meta_json TEXT NOT NULL DEFAULT '{}',
    imported_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS ec_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cell_id INTEGER NOT NULL REFERENCES ec_cells(id),
    cycle_index INTEGER NOT NULL,
    q_charge REAL,
    q_discharge REAL,
    ce REAL,
    i_med_charge REAL,
    i_med_discharge REAL,
    v_min REAL,
    v_max REAL,
    rate_group TEXT NOT NULL DEFAULT 'main',
    incomplete INTEGER NOT NULL DEFAULT 0,
    UNIQUE(cell_id, cycle_index)
);

CREATE TABLE IF NOT EXISTS ec_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cell_id INTEGER NOT NULL REFERENCES ec_cells(id),
    kind TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    params_hash TEXT NOT NULL DEFAULT '',
    result_json TEXT NOT NULL DEFAULT '{}',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ec_cells_sample ON ec_cells(sample_id);
CREATE INDEX IF NOT EXISTS idx_ec_cycles_cell ON ec_cycles(cell_id, cycle_index);
CREATE INDEX IF NOT EXISTS idx_ec_analyses_cell ON ec_analyses(cell_id, kind);

CREATE TABLE IF NOT EXISTS chi_datasets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id INTEGER REFERENCES samples(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('CV', 'EIS')),
    name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    point_count INTEGER NOT NULL,
    fit_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_chi_sample_hash ON chi_datasets(sample_id, sha256);
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
            # 增量迁移:对已有库只加列,不重建表。逐列判存在,幂等。
            #
            # version 字段是**真正的门控**:库里已经到 v3 时不该再跑 v3 的
            # 迁移。此前这个字段被 `for _version, ...` 丢掉,靠"逐列判存在"
            # 兜底 —— 结果正确但版本号形同虚设,新增迁移时无法靠它跳过,
            # 也无法判断某个 DDL 该不该执行。这里读一次 user_version 固定
            # 本次迁移的目标,后续比较才有意义。
            current = int(conn.execute("PRAGMA user_version").fetchone()[0])
            for version, table, col, ddl in _MIGRATIONS:
                if current >= version:
                    continue
                cols = {
                    row["name"].lower()
                    for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                if col not in cols:
                    conn.execute(ddl)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._conn()
        if conn.in_transaction:
            yield conn
            return
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            yield conn

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self.transaction() as conn:
            return conn.execute(sql, params)

    def query_all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self._conn().execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self._conn().execute(sql, params).fetchone()

    def insert(self, sql: str, params: tuple = ()) -> int:
        """执行 INSERT 并返回 lastrowid。"""
        return int(self.execute(sql, params).lastrowid)

    # --- 常用查询 ---

    def project_storage_folder(self, project_id: int) -> str:
        """项目首次归档时固定目录名；显示名变更不拆分已有文件。"""
        return self._storage_folder("projects", project_id, "项目")

    def sample_storage_folder(self, sample_id: int) -> str:
        """样品首次归档蓝电数据时固定存储目录。"""
        return self._storage_folder("samples", sample_id, "样品")

    def _storage_folder(self, table: str, row_id: int, default_label: str) -> str:
        with self.transaction() as conn:
            row = conn.execute(f"SELECT name, storage_folder FROM {table} WHERE id=?", (row_id,)).fetchone()
            if row is None:
                raise ValueError(f"{default_label}不存在")
            if row["storage_folder"]:
                return row["storage_folder"]
            label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", row["name"])[:48].strip().rstrip(". ")
            folder = f"{int(row_id)}-{label or default_label}"
            conn.execute(f"UPDATE {table} SET storage_folder=? WHERE id=?", (folder, row_id))
            return folder

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
        """返回未删除的 library 树(Sample 视图,含所属 Project 名)。

        **必须带全样品元数据**:温度/批次/材料这些字段是结构趋势图的自变量
        (docs/DEVELOPMENT_HIGH_NI.md §11.4)。此前只返回 id/name/temperature/
        duration,前端拿不到 material 与 wavelength —— 用户填了也看不到,
        趋势图更无从按温度分组。
        """
        return self.query_all(
            """SELECT s.id, s.project_id, s.name, s.sample_code,
                      s.temperature, s.duration, s.material, s.batch,
                      s.wavelength, s.notes, s.links_json,
                      p.name AS project_name,
                      (SELECT COUNT(*) FROM datasets d
                       WHERE d.sample_id = s.id AND d.deleted_at IS NULL
                      ) AS dataset_count,
                      (SELECT COUNT(*) FROM ec_cells e
                       WHERE e.sample_id = s.id AND e.deleted_at IS NULL AND e.format = 'cex'
                      ) AS electrochem_count
               FROM samples s
               JOIN projects p ON s.project_id = p.id
               WHERE s.deleted_at IS NULL AND p.deleted_at IS NULL
               ORDER BY p.name, s.name"""
        )

    def get_batches(self) -> list[sqlite3.Row]:
        """未删除的批次,含所属 sample / project 名,供 library.tree 挂到样品下。"""
        return self.query_all(
            """SELECT b.id, b.sample_id, b.name, b.source_path, b.notes,
                      s.name AS sample_name, p.name AS project_name,
                      (SELECT COUNT(*) FROM datasets d
                       WHERE d.batch_id = b.id AND d.deleted_at IS NULL
                      ) AS dataset_count
               FROM batches b
               JOIN samples s ON b.sample_id = s.id
               JOIN projects p ON s.project_id = p.id
               WHERE b.deleted_at IS NULL
                 AND s.deleted_at IS NULL AND p.deleted_at IS NULL
               ORDER BY b.name"""
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
        if table == "batches":
            sql = """SELECT COUNT(*) AS n FROM datasets
                     WHERE batch_id = ? AND deleted_at IS NULL"""
            row = self.query_one(sql, (row_id,))
            return int(row["n"]) if row else 0
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
               SELECT 'ec_cells' AS kind, c.id AS id, c.name AS name,
                      c.deleted_at AS deleted_at,
                      p.name AS project_name, s.name AS sample_name,
                      c.stored_path AS stored_path, 0 AS child_count
               FROM ec_cells c
               JOIN samples s ON c.sample_id = s.id
               JOIN projects p ON s.project_id = p.id
               WHERE c.deleted_at IS NOT NULL
               UNION ALL
               SELECT 'batches' AS kind, b.id AS id, b.name AS name,
                      b.deleted_at AS deleted_at,
                      p.name AS project_name, s.name AS sample_name,
                      NULL AS stored_path,
                      (SELECT COUNT(*) FROM datasets d3
                       WHERE d3.batch_id = b.id AND d3.deleted_at IS NULL
                      ) AS child_count
               FROM batches b
               JOIN samples s ON b.sample_id = s.id
               JOIN projects p ON s.project_id = p.id
               WHERE b.deleted_at IS NOT NULL
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
        """列出某对象下所有可清磁盘对象的 (id, stored_path),供 purge。

        必须在删 DB 行**之前**调用 —— 行删了就查不到 stored_path 了。
        """
        self._check_table(table)
        if table == "datasets":
            return self.query_all(
                "SELECT id, stored_path FROM datasets WHERE id = ?", (row_id,)
            )
        if table == "ec_cells":
            return self.query_all(
                "SELECT id, stored_path FROM ec_cells WHERE id = ?", (row_id,)
            )
        if table == "batches":
            return self.query_all(
                "SELECT id, stored_path FROM datasets WHERE batch_id = ?", (row_id,)
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

    def purge_ec_cell(self, cell_id: int) -> list[str]:
        """永久删除一个 ec_cell 及其分析结果,返回 stored_path 列表。"""
        row = self.query_one(
            "SELECT stored_path FROM ec_cells WHERE id = ?", (cell_id,)
        )
        if row is None:
            return []
        self.execute("DELETE FROM ec_analyses WHERE cell_id = ?", (cell_id,))
        self.execute("DELETE FROM ec_cycles WHERE cell_id = ?", (cell_id,))
        self.execute("DELETE FROM ec_cells WHERE id = ?", (cell_id,))
        return [row["stored_path"]]

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
        # domain_results → peaks → analyses → datasets,由深到浅(外键约束已开启)
        self.execute(
            "DELETE FROM domain_results WHERE analysis_id IN "
            "(SELECT id FROM analyses WHERE dataset_id = ?)",
            (dataset_id,),
        )
        self.execute(
            """DELETE FROM peaks WHERE analysis_id IN
                 (SELECT id FROM analyses WHERE dataset_id = ?)""",
            (dataset_id,),
        )
        # material profiles may point at a reference dataset; clear that
        # optional link before removing the dataset.
        self.execute(
            "UPDATE material_profiles SET reference_dataset_id = NULL "
            "WHERE reference_dataset_id = ?",
            (dataset_id,),
        )
        self.execute("DELETE FROM analyses WHERE dataset_id = ?", (dataset_id,))
        self.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
        return stored

    def purge_sample(self, sample_id: int) -> list[str]:
        """永久删除一个 sample 及其全部 dataset / ec_cell,返回要清理的 stored_path 列表。"""
        paths = []
        for ds_id in [
            r["id"]
            for r in self.query_all(
                "SELECT id FROM datasets WHERE sample_id = ?", (sample_id,)
            )
        ]:
            path = self.purge_dataset(ds_id)
            if path is not None:
                paths.append(path)
        # 电化学 cell 同属 sample:ec_analyses → ec_cycles → ec_cells(外键序)
        for cell_id in [
            r["id"]
            for r in self.query_all(
                "SELECT id FROM ec_cells WHERE sample_id = ?", (sample_id,)
            )
        ]:
            paths.extend(self.purge_ec_cell(cell_id))
        paths.extend(r["stored_path"] for r in self.query_all(
            "SELECT stored_path FROM chi_datasets WHERE sample_id = ?", (sample_id,)
        ))
        # datasets 清完后 batches 已无引用者,但外键仍在,必须先删
        self.execute("DELETE FROM batches WHERE sample_id = ?", (sample_id,))
        self.execute("DELETE FROM samples WHERE id = ?", (sample_id,))
        return paths

    def purge_project(self, project_id: int) -> list[str]:
        """永久删除一个 project 及其全部 sample / dataset / ec_cell / preset。"""
        paths = []
        for sample_id in [
            r["id"]
            for r in self.query_all(
                "SELECT id FROM samples WHERE project_id = ?", (project_id,)
            )
        ]:
            # purge_sample 的返回值含 ec 文件的 stored_path,不能丢
            paths.extend(self.purge_sample(sample_id))
        self.execute(
            "DELETE FROM material_profiles WHERE project_id = ?", (project_id,)
        )
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
