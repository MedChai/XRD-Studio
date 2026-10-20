"""meta.file_path:右键"在资源管理器中显示"的取路径命令。"""

from __future__ import annotations

import shutil

import pytest

from tests.ec_fixtures import build_land_xlsx
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


def test_dataset_falls_back_to_stored(engine, fixtures_dir, tmp_path):
    """原文件被挪走 → 回落库内副本(kind=stored,且文件真实存在)。"""
    local, ds_id = _import_dataset(engine, fixtures_dir, tmp_path)
    local.unlink()
    r = engine.handle("meta.file_path", {"table": "datasets", "id": ds_id})
    assert r["ok"], r
    assert r["data"]["kind"] == "stored"
    from pathlib import Path
    assert Path(r["data"]["path"]).exists()


def test_ec_cell_file_path(engine, tmp_path):
    """电池同样:原 xlsx 优先,删掉后回落库内副本。"""
    src = tmp_path / "land.xlsx"
    build_land_xlsx(src, n_slow=1, n_fast=1)
    pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
    r = engine.handle("ec.import", {
        "paths": [str(src)], "project_id": pid, "new_sample": {"name": "S"},
    })
    assert r["ok"], r
    cell_id = r["data"]["imported"][0]["id"]
    got = engine.handle("meta.file_path", {"table": "ec_cells", "id": cell_id})
    assert got["ok"], got
    assert got["data"] == {"path": str(src.resolve()), "kind": "source"}
    src.unlink()
    got2 = engine.handle("meta.file_path", {"table": "ec_cells", "id": cell_id})
    assert got2["ok"], got2
    assert got2["data"]["kind"] == "stored"
    from pathlib import Path
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
