"""meta.file_path:右键"在资源管理器中显示"的取路径命令。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from xrd_engine.api.commands import Engine


@pytest.fixture
def engine(tmp_path):
    eng = Engine(tmp_path / "store")
    yield eng
    eng.close()


def _import_dataset(engine, fixtures_dir, tmp_path):
    """把 fixture 拷进 tmp 再导入 —— 不能直接删会话级 fixture 文件。"""
    src = fixtures_dir / "lfp_500c_scan01.xy"
    if not src.exists():
        pytest.skip("fixture 未生成")
    local = tmp_path / "scan01.xy"
    shutil.copy(src, local)
    pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
    r = engine.handle("datasets.import", {
        "paths": [str(local)], "project_id": pid,
        "new_sample": {"name": "S"},
    })
    assert r["ok"], r
    return local, r["data"]["imported"][0]["id"]


def test_dataset_prefers_source(engine, fixtures_dir, tmp_path):
    """原文件还在 → 给原路径(kind=source)。"""
    local, ds_id = _import_dataset(engine, fixtures_dir, tmp_path)
    r = engine.handle("meta.file_path", {"table": "datasets", "id": ds_id})
    assert r["ok"], r
    assert r["data"]["kind"] == "source"
    assert r["data"]["path"] == str(local.resolve())


def test_explicit_locations_stay_separate(engine, fixtures_dir, tmp_path):
    local, ds_id = _import_dataset(engine, fixtures_dir, tmp_path)
    args = {"table": "datasets", "id": ds_id}
    original = engine.handle("meta.file_path", {**args, "kind": "source"})["data"]
    copy = engine.handle("meta.file_path", {**args, "kind": "stored"})["data"]
    saved = Path(copy["path"])
    assert copy["kind"] == "stored" and saved.name == local.name
    assert saved.relative_to(engine.store.root).parts[:2] == ("projects", "1-P")
    assert original == {"path": str(local.resolve()), "kind": "source"}
    assert saved.read_bytes() == local.read_bytes()
    saved.rename(saved.with_suffix(".hidden"))
    assert not engine.handle("meta.file_path", {**args, "kind": "stored"})["ok"]
    saved.with_suffix(".hidden").rename(saved)
    local.rename(local.with_suffix(".moved"))
    missing = engine.handle("meta.file_path", {**args, "kind": "source"})
    assert not missing["ok"] and str(local) in missing["error"]
    assert engine.handle("datasets.curve", {"id": ds_id})["ok"]
    assert engine.handle("meta.file_path", {**args, "kind": "stored"})["data"] == copy


def test_project_rename_and_same_named_scans(engine, fixtures_dir, tmp_path):
    local, ds_id = _import_dataset(engine, fixtures_dir, tmp_path)
    scan = tmp_path / "another" / local.name
    scan.parent.mkdir()
    shutil.copyfile(fixtures_dir / "ncm811_01.xy", scan)
    def import_into(pid):
        result = engine.handle("datasets.import", {"paths": [str(scan)], "project_id": pid,
                               "new_sample": {"name": "sample"}, "import_anyway": True})
        return result["data"]["imported"][0]["id"]
    def archive(dataset):
        return Path(engine.handle("meta.file_path", {"table": "datasets", "id": dataset, "kind": "stored"})["data"]["path"])
    old = archive(ds_id)
    assert engine.handle("meta.rename", {"table": "projects", "id": 1, "name": "重命名"})["ok"]
    new = archive(import_into(1))
    other = engine.handle("projects.create", {"name": "重命名/A"})["data"]["projectId"]
    another = archive(import_into(other))
    assert old != new and old.parents[1] == new.parents[1]
    assert another.parents[1] != new.parents[1] and another.parents[1].name.endswith("重命名_A")
    assert old.name == new.name == another.name
    assert engine.handle("meta.soft_delete", {"table": "datasets", "id": ds_id})["ok"]
    assert engine.handle("meta.purge", {"table": "datasets", "id": ds_id})["ok"]
    assert not old.exists() and new.exists() and another.exists() and local.exists()


def test_upgrade_archives_old_copy_with_wal_and_missing_source(engine, fixtures_dir, tmp_path, monkeypatch):
    from xrd_engine.services.library_upgrade import prepare_library

    local, ds_id = _import_dataset(engine, fixtures_dir, tmp_path)
    oldpath, _ = engine.store.import_file(local, {})
    current = engine.db.query_one("SELECT stored_path FROM datasets WHERE id=?", (ds_id,))[0]
    engine.store.remove_stored(current)
    engine.db.execute("UPDATE datasets SET stored_path=? WHERE id=?", (oldpath, ds_id))
    local.unlink()
    target = tmp_path / "软件" / "XRDStudio" / "library"
    # A failed copy must leave the old DB usable and allow the next launch to retry.
    with monkeypatch.context() as patch:
        def fail_copy(*args, **kwargs):
            raise OSError("磁盘空间不足")
        patch.setattr(shutil, "copytree", fail_copy)
        with pytest.raises(OSError, match="磁盘空间不足"):
            prepare_library(target, engine.store.root)
    assert not target.exists()
    prepare_library(target, engine.store.root)
    other = Engine(target)
    try:
        args = {"table": "datasets", "id": ds_id}
        saved = Path(other.handle("meta.file_path", {**args, "kind": "stored"})["data"]["path"])
        assert saved.is_relative_to(target / "projects") and saved.name == local.name
        assert other.handle("datasets.curve", {"id": ds_id})["ok"]
        assert str(local) in other.handle("meta.file_path", {**args, "kind": "source"})["error"]
        assert engine.store.abs_path(oldpath).read_bytes() == saved.read_bytes()
        assert other.handle("projects.create", {"name": "新增项目"})["ok"]
        prepare_library(target, engine.store.root)
        assert other.db.query_one("SELECT name FROM projects WHERE name='新增项目'")
    finally:
        other.close()


def test_dataset_falls_back_to_stored(engine, fixtures_dir, tmp_path):
    """原文件被挪走 → 回落库内副本(kind=stored,且文件真实存在)。"""
    local, ds_id = _import_dataset(engine, fixtures_dir, tmp_path)
    local.unlink()
    r = engine.handle("meta.file_path", {"table": "datasets", "id": ds_id})
    assert r["ok"], r
    assert r["data"]["kind"] == "stored"
    assert Path(r["data"]["path"]).exists()


def test_ec_cell_file_path(engine, tmp_path):
    """CEX 归档优先定位原件，原件不存在时定位库内副本。"""
    src = tmp_path / "land.cex"
    src.write_bytes(b"CEX test snapshot")
    pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
    sample_id = engine.db.insert("INSERT INTO samples(project_id, name) VALUES (?, 'S')", (pid,))
    stored = "data/archive.cex"
    shutil.copyfile(src, engine.store.abs_path(stored))
    cell_id = engine.db.insert(
        """INSERT INTO ec_cells(sample_id, name, original_filename, source_path, stored_path, file_hash, format)
           VALUES (?, 'cell', 'land.cex', ?, ?, 'snapshot', 'cex')""",
        (sample_id, str(src), stored),
    )
    got = engine.handle("meta.file_path", {"table": "ec_cells", "id": cell_id})
    assert got["ok"], got
    assert got["data"] == {"path": str(src.resolve()), "kind": "source"}
    src.unlink()
    got2 = engine.handle("meta.file_path", {"table": "ec_cells", "id": cell_id})
    assert got2["ok"], got2
    assert got2["data"]["kind"] == "stored"
    assert Path(got2["data"]["path"]).exists()


def test_file_path_rejects_bad_input(engine, fixtures_dir, tmp_path):
    """非文件表 / 不存在 id / 两处文件都没了 → 人话错误,不返回假路径。"""
    # 表白名单
    r = engine.handle("meta.file_path", {"table": "samples", "id": 1})
    assert not r["ok"] and "文件位置" in r["error"]
    # id 不存在
    r = engine.handle("meta.file_path", {"table": "datasets", "id": 999999})
    assert not r["ok"] and "不存在" in r["error"]
    # 原文件与库内副本都没了
    local, ds_id = _import_dataset(engine, fixtures_dir, tmp_path)
    local.unlink()
    row = engine.db.query_one("SELECT stored_path FROM datasets WHERE id = ?", (ds_id,))
    engine.store.abs_path(row["stored_path"]).unlink()
    r = engine.handle("meta.file_path", {"table": "datasets", "id": ds_id})
    assert not r["ok"] and "找不到" in r["error"]
