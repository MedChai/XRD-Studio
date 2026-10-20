"""land_parser 解析测试(合成夹具,M0 验收)。"""


import openpyxl
import pytest

from tests.ec_fixtures import build_land_xlsx
from xrd_engine.electrochem import LandParseError, cycle_dicts, parse_land_path


@pytest.fixture(scope="module")
def fixture_xlsx(tmp_path_factory):
    path = tmp_path_factory.mktemp("land") / "fixture.xlsx"
    info = build_land_xlsx(path, n_slow=2, n_fast=4)
    return path, info


def test_parse_basic(fixture_xlsx):
    path, info = fixture_xlsx
    lf = parse_land_path(path)
    assert len(lf.cycles) == info["n_cycles"]
    assert lf.qa["n_records"] > 0
    assert set(lf.qa["state_counts"]) >= {"rest", "charge", "discharge"}
    assert lf.session.test_name == "fixture-倍率循环"
    assert any("0.1*C" in s for s in lf.session.program)
    assert any("结束" in s for s in lf.session.program)


def test_cycle_capacity_matches_reference(fixture_xlsx):
    """积分容量与循环表参照逐圈一致(恒流恒定 → 梯形积分为精确值)。"""
    path, info = fixture_xlsx
    lf = parse_land_path(path)
    for c in lf.cycles:
        exp = info["expected"][c.index]
        assert c.q_charge == pytest.approx(exp["q_charge"], rel=1e-3)
        assert c.q_discharge == pytest.approx(exp["q_discharge"], rel=1e-3)
    assert lf.qa["capacity_dev_med"] < 1e-3


def test_rate_groups_slow_vs_fast(fixture_xlsx):
    """前 n_slow 圈低倍率识别为 slow 组(电流随容量重标定场景)。"""
    path, info = fixture_xlsx
    lf = parse_land_path(path)
    groups = lf.qa["rate_groups"]
    assert groups["slow"] == list(range(1, info["n_slow"] + 1))
    assert len(groups["main"]) == info["n_cycles"] - info["n_slow"]


def test_mass_estimate_from_cycle_table(fixture_xlsx):
    """活性物质质量从循环表比容量反推(夹具按 1.77mg 构造)。"""
    path, _ = fixture_xlsx
    lf = parse_land_path(path)
    assert lf.qa["mass_mg_estimate"] == pytest.approx(1.77, rel=2e-3)


def test_incomplete_last_cycle_flagged(tmp_path):
    """末圈放电段被砍(测试中途停止)应标记 incomplete。"""
    path = tmp_path / "incomplete.xlsx"
    build_land_xlsx(path, n_slow=1, n_fast=3)
    wb = openpyxl.load_workbook(path)
    ws = wb["记录表"]
    # 保留末圈(圈4)放电段的前 30 条记录,其余删除 → 放电容量骤降
    rows = list(ws.iter_rows(values_only=True))
    header, body = rows[0], rows[1:]
    kept, seen = [], 0
    for r in body:
        if r[6] == "RateD" and r[4] == 4:
            seen += 1
            if seen > 30:
                continue
        kept.append(r)
    ws.delete_rows(1, ws.max_row)
    ws.append(list(header))
    for r in kept:
        ws.append(list(r))
    # 循环表参照同步改,避免交叉校验报警干扰
    ws_c = wb["循环表"]
    ws_c.cell(row=5, column=3, value=0.05)
    ws_c.cell(row=5, column=5, value=0.05 / 1.77 * 1000)
    wb.save(path)
    lf = parse_land_path(path)
    assert lf.cycles[-1].incomplete is True
    assert lf.qa["incomplete_last_cycle"] == lf.cycles[-1].index
    assert all(not c.incomplete for c in lf.cycles[:-1])


def test_time_backwards_detected(tmp_path):
    """时间回退(疑似重启)计入 QA 而非静默吞掉。"""
    path = tmp_path / "backwards.xlsx"
    build_land_xlsx(path, n_slow=1, n_fast=1)
    wb = openpyxl.load_workbook(path)
    ws = wb["记录表"]
    ws.insert_rows(30)
    ws.cell(row=30, column=1, value="00:00:01")
    ws.cell(row=30, column=2, value="00:00:01")
    ws.cell(row=30, column=3, value=0.032)
    ws.cell(row=30, column=4, value=3.5)
    ws.cell(row=30, column=5, value=1)
    ws.cell(row=30, column=6, value=3)
    ws.cell(row=30, column=7, value="R")
    wb.save(path)
    lf = parse_land_path(path)
    assert lf.qa["backwards"] >= 1


def test_missing_required_column_raises(tmp_path):
    """缺必需列 → 明确报错而非错数据。"""
    path = tmp_path / "badcols.xlsx"
    build_land_xlsx(path, n_slow=1, n_fast=1)
    wb = openpyxl.load_workbook(path)
    wb["记录表"].cell(row=1, column=7, value="备注")
    wb.save(path)
    with pytest.raises(LandParseError):
        parse_land_path(path)


def test_cycle_dicts_normalization(fixture_xlsx):
    """cycle_dicts 用循环表反推质量归一到 mAh/g。"""
    path, _ = fixture_xlsx
    lf = parse_land_path(path)
    ds = cycle_dicts(lf)
    c1 = next(d for d in ds if d["index"] == 1)
    # 夹具质量 1.77mg:0.24 mAh / 1.77mg = 135.6 mAh/g
    assert c1["q_charge"] == pytest.approx(0.24 / 1.77 * 1000, rel=1e-3)


def test_cycle_table_reordered_with_title(tmp_path):
    """循环表列乱序 + 标题行 + 夹一列中压 → 按表头名取对列,不按位置。"""
    path = tmp_path / "reorder.xlsx"
    info = build_land_xlsx(path, n_slow=1, n_fast=2)
    wb = openpyxl.load_workbook(path)
    ws = wb["循环表"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))  # [n, qc, qd, qcg, qdg, ce]
    ws.delete_rows(1, ws.max_row)
    ws.append(["蓝电循环报表", None, None])  # 标题行在表头之前
    # 表头乱序:效率打头、充放容量互换、中间插"充电中压/V"
    ws.append(["效率/%", "放电容量/mAh", "充电中压/V", "充电容量/mAh",
               "循环序号", "充电比容量/mAh/g", "放电比容量/mAh/g"])
    for r in rows:
        ws.append([r[5], r[2], 3.9, r[1], r[0], r[3], r[4]])
    wb.save(path)

    lf = parse_land_path(path)
    assert len(lf.cycles) == info["n_cycles"]
    for c in lf.cycles:
        exp = info["expected"][c.index]
        assert c.ref_q_charge == pytest.approx(exp["q_charge"], rel=1e-3)
        assert c.ref_q_discharge == pytest.approx(exp["q_discharge"], rel=1e-3)
        assert c.ref_q_charge_g == pytest.approx(exp["q_charge"] / 1.77 * 1000, rel=1e-3)
        assert c.ref_q_discharge_g == pytest.approx(exp["q_discharge"] / 1.77 * 1000, rel=1e-3)
        assert c.ref_ce == pytest.approx(exp["q_discharge"] / exp["q_charge"], rel=1e-3)
    assert lf.qa["capacity_dev_med"] < 1e-3
    assert lf.qa["mass_mg_estimate"] == pytest.approx(1.77, rel=2e-3)


def test_cycle_table_missing_capacity_density(tmp_path):
    """循环表缺比容量列(导出未勾) → ref 比容量 nan、质量不反推,容量/效率照常。"""
    path = tmp_path / "nodensity.xlsx"
    info = build_land_xlsx(path, n_slow=1, n_fast=2)
    wb = openpyxl.load_workbook(path)
    ws = wb["循环表"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    ws.delete_rows(1, ws.max_row)
    # 比容量两列整个没导出,还多了中压列(固定位置读会错位)
    ws.append(["循环序号", "充电容量/mAh", "放电容量/mAh", "效率/%",
               "充电中压/V", "放电中压/V"])
    for r in rows:
        ws.append([r[0], r[1], r[2], r[5], 4.1, 3.6])
    wb.save(path)

    lf = parse_land_path(path)
    for c in lf.cycles:
        exp = info["expected"][c.index]
        assert c.ref_q_charge == pytest.approx(exp["q_charge"], rel=1e-3)
        assert c.ref_q_discharge == pytest.approx(exp["q_discharge"], rel=1e-3)
        assert c.ref_ce == pytest.approx(exp["q_discharge"] / exp["q_charge"], rel=1e-3)
        assert c.ref_q_charge_g != c.ref_q_charge_g  # nan(列缺失)
        assert c.ref_q_discharge_g != c.ref_q_discharge_g
    assert "mass_mg_estimate" not in lf.qa  # 没比容量就不瞎反推质量
    assert lf.qa["capacity_dev_med"] < 1e-3
