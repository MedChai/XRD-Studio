"""ec.* 命令层测试:Engine 上的完整往返(导入→计算→归因→导出→生命周期)。"""

import json
from pathlib import Path

import pytest

from tests.ec_fixtures import build_land_xlsx
from xrd_engine.api.commands import Engine


@pytest.fixture
def engine(tmp_path):
    eng = Engine(tmp_path)
    yield eng
    eng.close()


@pytest.fixture
def imported_cell(engine, tmp_path):
    path = tmp_path / "land.xlsx"
    build_land_xlsx(path, n_slow=2, n_fast=4)
    pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
    r = engine.handle("ec.import", {
        "paths": [str(path)], "project_id": pid,
        "new_sample": {"name": "S1"},
    })
    assert r["ok"], r
    cell = r["data"]["imported"][0]
    return cell["id"], r["data"]["sampleId"]


def test_import_and_overview(engine, imported_cell):
    cell_id, sample_id = imported_cell
    cells = engine.handle("ec.cells", {"sample_id": sample_id})["data"]["cells"]
    assert len(cells) == 1
    assert cells[0]["cycleCount"] == 6
    ov = engine.handle("ec.cell_overview", {"cell_id": cell_id})["data"]
    assert len(ov["cycles"]) == 6
    assert ov["session"]["test_name"] == "fixture-倍率循环"
    assert ov["session"]["program"] and ov["qa"]["interval_med"] > 0
    # 质量从循环表比容量反推
    assert ov["massMg"] == pytest.approx(1.77, rel=2e-3)


def test_dqdv_compute_and_curves(engine, imported_cell):
    cell_id, _ = imported_cell
    # 夹具的 dQ/dV 峰比默认窗口宽:传自定义窗口(顺带验证窗口参数链路)
    r = engine.handle("ec.dqdv_compute", {"cell_id": cell_id, "params": {
        "windows": [{"key": "H1H2", "label": "H1→H2", "v_lo": 3.3, "v_hi": 4.0}],
    }})
    assert r["ok"], r
    data = r["data"]
    assert data["nCurves"] >= 10          # 6 圈 × 充放
    assert data["nPeaks"] > 0
    assert len(data["tracks"]) >= 2       # 充放两方向至少各一条
    state = engine.handle("ec.dqdv_state", {"cell_id": cell_id})["data"]
    assert state["computed"] and state["paramsHash"]
    # 单圈曲线
    c = engine.handle("ec.dqdv_curve", {
        "cell_id": cell_id, "cycle": 1, "direction": "charge",
    })["data"]
    assert c["pointCount"] > 50
    assert c["__bin__"]["layout"] == ["v", "dqdv"]
    # 瀑布
    wf = engine.handle("ec.dqdv_waterfall", {
        "cell_id": cell_id, "direction": "charge", "step": 2,
    })["data"]
    assert len(wf["shape"]) == 2 and len(wf["v"]) == wf["shape"][1]


def test_dqdv_params_change_updates_hash(engine, imported_cell):
    """参数变更 → 重新计算后 params_hash 更新(前端的"已过期"依据)。"""
    cell_id, _ = imported_cell
    h1 = engine.handle("ec.dqdv_compute", {"cell_id": cell_id})["data"]["paramsHash"]
    h2 = engine.handle("ec.dqdv_compute", {
        "cell_id": cell_id, "params": {"dv_mv": 5.0},
    })["data"]["paramsHash"]
    assert h1 != h2


def test_dqdv_result_readback(engine, imported_cell, tmp_path):
    """ec.dqdv_result 读回峰/轨迹/参数;重启(新 Engine)后依然可读。"""
    cell_id, _ = imported_cell
    r = engine.handle("ec.dqdv_compute", {"cell_id": cell_id, "params": {
        "windows": [{"key": "H1H2", "label": "H1→H2", "v_lo": 3.3, "v_hi": 4.0}],
    }})
    assert r["ok"], r
    got = engine.handle("ec.dqdv_result", {"cell_id": cell_id})["data"]
    assert got["computed"] is True
    assert got["nPeaks"] == r["data"]["nPeaks"]
    assert got["peaks"] == r["data"]["peaks"]
    assert got["tracks"] == r["data"]["tracks"]
    assert got["paramsHash"] == r["data"]["paramsHash"]
    assert got["params"]["dv_mv"] == 2.0

    # 模拟重启:同一个 data_root 上新建 Engine(内存缓存全空)
    engine.close()
    eng2 = Engine(tmp_path)
    try:
        got2 = eng2.handle("ec.dqdv_result", {"cell_id": cell_id})["data"]
        assert got2["computed"] is True
        assert got2["nPeaks"] == r["data"]["nPeaks"]
        assert got2["peaks"][0]["position"] == pytest.approx(
            r["data"]["peaks"][0]["position"]
        )
    finally:
        eng2.close()


def test_dqdv_result_before_compute(engine, imported_cell):
    """未计算时返回 computed=false 而非报错。"""
    cell_id, _ = imported_cell
    got = engine.handle("ec.dqdv_result", {"cell_id": cell_id})["data"]
    assert got["computed"] is False


def test_attribution_flow(engine, imported_cell):
    cell_id, _ = imported_cell
    engine.handle("ec.dqdv_compute", {"cell_id": cell_id})
    r = engine.handle("ec.attribution_run", {"cell_id": cell_id})
    assert r["ok"], r
    att = r["data"]
    assert att["method"] == "B"          # 夹具只有圈首 slow,无后续回访
    assert att["cycles"] and att["conclusion"]
    got = engine.handle("ec.attribution_get", {"cell_id": cell_id})["data"]
    assert got["computed"] and got["stale"] is False
    # dqdv 参数变更后归因应标记过期
    engine.handle("ec.dqdv_compute", {
        "cell_id": cell_id, "params": {"dv_mv": 5.0},
    })
    got2 = engine.handle("ec.attribution_get", {"cell_id": cell_id})["data"]
    assert got2["stale"] is True


def test_attribution_requires_dqdv_first(engine, imported_cell):
    cell_id, _ = imported_cell
    r = engine.handle("ec.attribution_run", {"cell_id": cell_id})
    assert not r["ok"]
    assert "dQ/dV" in r["error"]


def test_exports(engine, imported_cell, tmp_path):
    cell_id, _ = imported_cell
    engine.handle("ec.dqdv_compute", {"cell_id": cell_id})
    engine.handle("ec.attribution_run", {"cell_id": cell_id})
    for kind, min_rows in (("cycles", 6), ("peaks", 1), ("attribution", 3)):
        r = engine.handle("ec.export", {
            "cell_id": cell_id, "kind": kind, "path": str(tmp_path / f"{kind}.csv"),
        })
        assert r["ok"], r
        assert r["data"]["rows"] >= min_rows
        assert Path(r["data"]["path"]).exists()


def test_duplicate_detection(engine, imported_cell, tmp_path):
    cell_id, _ = imported_cell
    # 同一文件再导:查重应拦截
    src = tmp_path / "land.xlsx"
    r = engine.handle("ec.import", {
        "paths": [str(src)], "project_id": 1,
        "new_sample": {"name": "S2"},
    })
    assert r["ok"]
    dups = r["data"]["duplicates"]
    assert len(dups) == 1 and dups[0]["cellId"] == cell_id
    assert not r["data"]["imported"]
    # import_anyway 强制导入成功
    r2 = engine.handle("ec.import", {
        "paths": [str(src)], "project_id": 1,
        "new_sample": {"name": "S2"}, "import_anyway": True,
    })
    assert len(r2["data"]["imported"]) == 1


def test_soft_delete_and_restore(engine, imported_cell):
    cell_id, sample_id = imported_cell
    r = engine.handle("meta.soft_delete", {"table": "ec_cells", "id": cell_id})
    assert r["ok"], r
    trash = engine.handle("trash.list", {})["data"]["items"]
    assert any(i["kind"] == "ec_cells" and i["id"] == cell_id for i in trash)
    # 删除后列表不可见
    cells = engine.handle("ec.cells", {"sample_id": sample_id})["data"]["cells"]
    assert cells == []
    assert engine.handle("meta.restore", {"table": "ec_cells", "id": cell_id})["ok"]
    cells2 = engine.handle("ec.cells", {"sample_id": sample_id})["data"]["cells"]
    assert len(cells2) == 1


def test_preview_dry_run(engine, tmp_path):
    path = tmp_path / "land.xlsx"
    build_land_xlsx(path, n_slow=1, n_fast=2)
    r = engine.handle("ec.preview", {"path": str(path)})
    assert r["ok"], r
    data = r["data"]
    assert data["n_cycles"] == 3
    assert data["mass_mg_estimate"] == pytest.approx(1.77, rel=2e-3)
    # 不落库:ec_cells 应为空
    assert engine.db.query_one("SELECT COUNT(*) AS n FROM ec_cells")["n"] == 0


def test_import_failure_reported(engine, tmp_path):
    bad = tmp_path / "bad.xlsx"
    bad.write_text("not an xlsx")
    pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
    r = engine.handle("ec.import", {
        "paths": [str(bad)], "project_id": pid, "new_sample": {"name": "S"},
    })
    assert r["ok"]
    assert len(r["data"]["failures"]) == 1
    assert not r["data"]["imported"]


def test_result_json_serializable(engine, imported_cell):
    """ec_analyses.result_json 必须可 JSON 序列化(错误协议要求)。"""
    cell_id, _ = imported_cell
    engine.handle("ec.dqdv_compute", {"cell_id": cell_id})
    engine.handle("ec.attribution_run", {"cell_id": cell_id})
    for kind in ("dqdv", "attribution"):
        row = engine.db.query_one(
            "SELECT result_json FROM ec_analyses WHERE kind = ?", (kind,)
        )
        data = json.loads(row["result_json"])
        assert isinstance(data, dict)
