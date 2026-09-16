"""数据库与 Data Store 集成测试(§8.2 / §8.3 / §8.4)。"""

from __future__ import annotations

import json

import numpy as np
import pytest

from xrd_engine.database.db import Database
from xrd_engine.services.data_store import (
    DataStore,
    generate_ulid,
    sha256_of_file,
)


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "xrdstudio.db")
    yield database
    database.close()


@pytest.fixture
def store(tmp_path):
    return DataStore(tmp_path / "store")


@pytest.fixture
def populated_db(db):
    """一个 Project → Sample → Dataset 的最小填充库。"""
    project_id = db.insert(
        "INSERT INTO projects (name) VALUES (?)", ("LFP Study",)
    )
    sample_id = db.insert(
        "INSERT INTO samples (project_id, name, temperature) VALUES (?,?,?)",
        (project_id, "LFP-500C", "500"),
    )
    dataset_id = db.insert(
        """INSERT INTO datasets
           (sample_id, name, original_filename, stored_path, file_hash,
            x_axis_type, x_min, x_max, step, point_count, grid)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            sample_id, "scan-01.xy", "scan-01.xy", "data/01JABC/original.xy",
            "deadbeef" * 8, "two_theta", 10.0, 80.0, 0.02, 3501, "uniform",
        ),
    )
    return db, project_id, sample_id, dataset_id


class TestSchema:
    def test_all_tables_exist(self, db):
        rows = db.query_all(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        names = {r["name"] for r in rows}
        assert {"projects", "samples", "datasets", "analyses",
                "peaks", "analysis_presets"} <= names

    def test_user_version(self, db):
        row = db.query_one("PRAGMA user_version")
        assert row[0] == 1

    def test_wal_mode(self, db):
        row = db.query_one("PRAGMA journal_mode")
        assert row[0].lower() == "wal"


class TestCRUD:
    def test_insert_and_query(self, populated_db):
        db, _, _, dataset_id = populated_db
        row = db.query_one("SELECT * FROM datasets WHERE id = ?", (dataset_id,))
        assert row["name"] == "scan-01.xy"
        assert row["point_count"] == 3501

    def test_soft_delete_and_restore(self, populated_db):
        db, _, sample_id, _ = populated_db
        db.soft_delete("samples", sample_id)
        row = db.query_one("SELECT deleted_at FROM samples WHERE id = ?", (sample_id,))
        assert row["deleted_at"] is not None
        db.restore("samples", sample_id)
        row = db.query_one("SELECT deleted_at FROM samples WHERE id = ?", (sample_id,))
        assert row["deleted_at"] is None

    def test_invalid_table_rejected(self, db):
        # 用 ValueError 而非 assert:python -O 会移除 assert,那时非法表名
        # 会直接拼进 SQL,变成注入面
        with pytest.raises(ValueError):
            db.soft_delete("peaks", 1)
        with pytest.raises(ValueError):
            db.restore("peaks", 1)

    def test_soft_delete_returns_rowcount(self, populated_db):
        db, _, sample_id, _ = populated_db
        assert db.soft_delete("samples", sample_id) is True
        # 重复删除不再改动行
        assert db.soft_delete("samples", sample_id) is False
        assert db.restore("samples", sample_id) is True
        assert db.restore("samples", sample_id) is False

    def test_soft_delete_updates_updated_at(self, populated_db):
        db, _, sample_id, _ = populated_db
        # 先把 updated_at 拨回一天前,再软删除 —— 否则同一秒内的两次
        # datetime('now') 相等,断言会假通过
        db.execute(
            "UPDATE samples SET updated_at = datetime('now', '-1 day') WHERE id = ?",
            (sample_id,),
        )
        before = db.query_one(
            "SELECT updated_at FROM samples WHERE id = ?", (sample_id,)
        )["updated_at"]
        db.soft_delete("samples", sample_id)
        after = db.query_one(
            "SELECT updated_at FROM samples WHERE id = ?", (sample_id,)
        )["updated_at"]
        assert after != before


class TestQueries:
    def test_find_duplicate_by_hash(self, populated_db):
        db, _, _, dataset_id = populated_db
        hit = db.find_duplicate_by_hash("deadbeef" * 8)
        assert hit is not None
        assert hit["id"] == dataset_id
        assert hit["project_name"] == "LFP Study"
        assert db.find_duplicate_by_hash("ffffff" * 8) is None

    def test_duplicate_ignores_deleted(self, populated_db):
        db, _, _, dataset_id = populated_db
        db.soft_delete("datasets", dataset_id)
        assert db.find_duplicate_by_hash("deadbeef" * 8) is None

    def test_library_tree(self, populated_db):
        db, _, _, _ = populated_db
        tree = db.get_library_tree()
        assert len(tree) == 1
        assert tree[0]["name"] == "LFP-500C"
        assert tree[0]["project_name"] == "LFP Study"
        assert tree[0]["dataset_count"] == 1

    def test_analysis_and_peaks(self, populated_db):
        db, _, _, dataset_id = populated_db
        snapshot = {"background": {"method": "asls", "lambda": 1e5}}
        analysis_id = db.insert(
            "INSERT INTO analyses (dataset_id, pipeline_json) VALUES (?,?)",
            (dataset_id, json.dumps(snapshot)),
        )
        peak_id = db.insert(
            """INSERT INTO peaks
               (analysis_id, position, intensity, fwhm, area,
                left_bound, right_bound)
               VALUES (?,?,?,?,?,?,?)""",
            (analysis_id, 34.39, 31542.0, 0.18, 4200.0, 33.9, 34.9),
        )
        row = db.query_one("SELECT * FROM peaks WHERE id = ?", (peak_id,))
        assert row["position"] == pytest.approx(34.39)
        assert row["manual_override"] == 0

    def test_preset(self, populated_db):
        db, project_id, _, _ = populated_db
        preset_id = db.insert(
            "INSERT INTO analysis_presets (project_id, name, pipeline_json) VALUES (?,?,?)",
            (project_id, "标准处理", "{}"),
        )
        assert db.query_one(
            "SELECT * FROM analysis_presets WHERE id = ?", (preset_id,)
        )["name"] == "标准处理"


class TestDataStore:
    def test_directories_created(self, store, tmp_path):
        for sub in ("database", "data", "cache", "exports", "logs"):
            assert (tmp_path / "store" / sub).is_dir()

    def test_generate_ulid_unique(self):
        ids = {generate_ulid() for _ in range(50)}
        assert len(ids) == 50

    def test_sha256_stable(self, tmp_path):
        p = tmp_path / "a.xy"
        p.write_bytes(b"hello xrd")
        h1 = sha256_of_file(p)
        h2 = sha256_of_file(p)
        assert h1 == h2
        assert len(h1) == 64

    def test_import_copies_file(self, store, tmp_path):
        src = tmp_path / "scan.xy"
        src.write_bytes(b"10.0 100\n10.02 120\n")
        stored, file_hash = store.import_file(src, {"project": "LFP Study"})
        assert stored.startswith("data/")
        assert stored.endswith("original.xy")
        abs_p = store.abs_path(stored)
        assert abs_p.is_file()
        assert abs_p.read_bytes() == src.read_bytes()
        assert len(file_hash) == 64
        meta_file = abs_p.parent / "metadata.json"
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        assert meta["original_filename"] == "scan.xy"
        assert "source_path" in meta

    def test_import_missing_raises(self, store, tmp_path):
        with pytest.raises(FileNotFoundError):
            store.import_file(tmp_path / "nope.xy", {})

    def test_abs_path_blocks_escape(self, store):
        with pytest.raises(ValueError):
            store.abs_path("../../etc/passwd")

    def test_curve_cache_roundtrip(self, store):
        x = np.linspace(10.0, 80.0, 100)
        y = np.sin(x)
        store.save_curve_cache(7, x, y, kind="processed")
        got = store.load_curve_cache(7, kind="processed")
        assert got is not None
        np.testing.assert_allclose(got[0], x)
        np.testing.assert_allclose(got[1], y)
        assert store.load_curve_cache(99) is None

    def test_export_path_no_overwrite(self, store):
        p1 = store.export_path("peaks.csv")
        p1.write_text("a,b")
        p2 = store.export_path("peaks.csv")
        assert p1 != p2
        assert p2.name == "peaks_1.csv"
