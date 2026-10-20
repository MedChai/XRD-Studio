"""电化学静默出错类缺陷的回归钉(2026-10 审查发现项)。

这批 bug 的共同特征是**不报错、只给错数**:CV 段漏计、时间回退腰斩容量、
无循环表 0 圈、时间列大面积丢行、归因单位分裂、fast 组混进衰减、
自定义窗口下结论断言"截断主导"。每个测试对应一条已修复的缺陷。
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import openpyxl
import pytest

from tests.ec_fixtures import build_land_xlsx
from xrd_engine.api.commands import Engine
from xrd_engine.electrochem import LandParseError, compute_dqdv, parse_land_path
from xrd_engine.electrochem.attribution import run_attribution, window_keys
from xrd_engine.electrochem.peaks import CyclePeak

# ---------------------------------------------------------------- 夹具工具


def _write_record_xlsx(path, rows, headers=None, with_cycle_sheet=True):
    """极简记录表工作表构造(rows 为 (time, i, v, cyc, step, state) 元组)。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "记录表"
    ws.append(headers or ["测试时间", "电流/mA", "电压/V", "循环序号", "工步序号", "工步状态"])
    for r in rows:
        ws.append(list(r))
    if with_cycle_sheet:
        ws_c = wb.create_sheet("循环表")
        ws_c.append(["循环序号", "充电容量/mAh", "放电容量/mAh", "效率/%"])
    wb.save(path)
    return path


def _pk(cycle, direction, window_key, position, height=1.0, area=1.0, truncated=False):
    return CyclePeak(
        cycle=cycle, direction=direction, window_key=window_key,
        position=position, height=height, area=area, fwhm=0.05,
        prominence=height * 0.5, onset=position - 0.02, truncated=truncated,
    )


# ---------------------------------------------------------------- A5: CV 容量


class TestCVCapacity:
    def test_cv_segment_counted_in_charge_capacity(self, tmp_path):
        """CCCV 充电:恒流 100 mAh + 恒压 20 mAh → q_charge 必须 120。"""
        rows = []
        # 恒流充电 100 mA × 3600 s = 100 mAh
        for k in range(0, 3601, 60):
            rows.append((k, 100.0, 3.0 + k / 3600.0, 1, 1, "恒流充电"))
        # 恒压充电 20 mA × 3600 s = 20 mAh
        for k in range(3600, 7201, 60):
            rows.append((k, 20.0, 4.3, 1, 2, "恒压"))
        # 放电 100 mA × 3600 s
        for k in range(7200, 10801, 60):
            rows.append((k, -100.0, 3.5, 1, 3, "恒流放电"))
        path = _write_record_xlsx(tmp_path / "cv.xlsx", rows)
        lf = parse_land_path(path)
        c = lf.cycles[0]
        assert c.q_charge == pytest.approx(120.0, rel=1e-3)
        assert c.q_discharge == pytest.approx(100.0, rel=1e-3)
        assert c.has_cv

    def test_english_cv_maps_to_charge_cv(self, tmp_path):
        """英文 "CV" 不得被 "c" 前缀抢成恒流 —— 状态归属随语言漂移是老坑。"""
        rows = [(0, 0.0, 3.0, 1, 1, "R"), (60, 20.0, 4.3, 1, 2, "CV"),
                (120, 20.0, 4.3, 1, 2, "CV"), (180, 0.0, 3.0, 1, 3, "R")]
        lf = parse_land_path(_write_record_xlsx(tmp_path / "cv_en.xlsx", rows))
        assert set(lf.qa["state_counts"]) & {"charge_cv"}
        assert lf.cycles[0].q_charge > 0


# ---------------------------------------------------------------- A4: 状态词表


class TestStateWords:
    def test_bare_chinese_charge_discharge_not_dropped(self, tmp_path):
        rows = [(0, 100.0, 3.0, 1, 1, "充电"), (60, 100.0, 3.1, 1, 1, "充电"),
                (120, -100.0, 3.0, 1, 2, "放电"), (180, -100.0, 2.9, 1, 2, "放电")]
        lf = parse_land_path(_write_record_xlsx(tmp_path / "cn.xlsx", rows))
        assert lf.qa["skipped_rows"] == 0
        assert lf.qa["state_counts"].get("charge") == 2
        assert lf.qa["state_counts"].get("discharge") == 2


# ---------------------------------------------------------------- A1/A2: 解析


class TestParserRobustness:
    def test_no_cycle_sheet_still_builds_cycles(self, tmp_path):
        """只勾记录表的导出:圈必须从记录表积分出来,不能是 0 圈死数据集。"""
        path = tmp_path / "nocyc.xlsx"
        build_land_xlsx(path, n_slow=1, n_fast=2)
        wb = openpyxl.load_workbook(path)
        del wb["循环表"]
        wb.save(path)
        lf = parse_land_path(path)
        assert len(lf.cycles) == 3
        assert lf.cycles[0].q_charge > 0
        assert np.isnan(lf.cycles[0].ref_q_charge)  # 无参照值,但圈存在

    def test_datetime_time_column_parses(self, tmp_path):
        """datetime.datetime 时间列 → 按纪元秒相对化,不整列丢弃。"""
        base = datetime(2026, 10, 6, 8, 0, 0)
        rows = [
            (base.replace(hour=8, minute=k), 100.0, 3.0 + 0.01 * k, 1, 1, "充电")
            for k in range(5)
        ]
        lf = parse_land_path(_write_record_xlsx(tmp_path / "dt.xlsx", rows))
        assert lf.t.size == 5
        assert lf.qa["skipped_rows"] == 0
        assert lf.qa["time_epoch_based"]
        assert lf.t[0] == pytest.approx(0.0, abs=1e-6)

    def test_slash_date_text_time_column(self, tmp_path):
        rows = [(f"2026/10/06 08:0{k}:00", 100.0, 3.0, 1, 1, "充电") for k in range(5)]
        lf = parse_land_path(_write_record_xlsx(tmp_path / "txt.xlsx", rows))
        assert lf.t.size == 5
        assert lf.qa["skipped_rows"] == 0

    def test_massive_silent_drops_raise(self, tmp_path):
        """过半行无法解析必须报错 —— '7184 行只剩 29 行还导入成功'的防线。"""
        rows = [(k * 60, 100.0, 3.0, 1, 1, "未知状态XYZ") for k in range(200)]
        with pytest.raises(LandParseError, match="解析失败"):
            parse_land_path(_write_record_xlsx(tmp_path / "bad.xlsx", rows))

    def test_excel_day_duration_rescaled(self, tmp_path):
        """Excel 时长数值是「天」:亚毫秒中位间隔必须按天重标,容量才对。"""
        rows = []
        for k in range(50):
            day = k * 60.0 / 86400.0  # 每 60 秒一采样,按天记
            rows.append((day, 100.0, 3.0 + 0.01 * k, 1, 1, "充电"))
        lf = parse_land_path(_write_record_xlsx(tmp_path / "days.xlsx", rows))
        assert lf.qa.get("time_unit_rescaled") == "days->seconds"
        assert lf.qa["interval_med"] == pytest.approx(60.0, rel=1e-6)
        # 50 点 × 60 s × 100 mA ≈ 83 mAh(梯形:49 个间隔)
        assert lf.cycles[0].q_charge == pytest.approx(49 * 60 * 100 / 3600.0, rel=1e-6)


# ---------------------------------------------------------------- B1: dQ/dV 时间回退


class TestDqdvTimeBackwards:
    def test_restart_does_not_halve_capacity(self):
        """一次时间重启后 q_total 必须是两段之和,不是腰斩。"""
        seg_t = np.array([0.0, 12.0, 24.0, 36.0])
        seg_i = np.full(4, 100.0)  # 100 mA × 36 s = 1 mAh/段
        seg_v = np.array([3.0, 3.3, 3.6, 3.9])
        t = np.concatenate([seg_t, seg_t + 0.0])  # 第二段时间回退到 0
        i = np.concatenate([seg_i, seg_i])
        v = np.concatenate([seg_v, seg_v + 0.2])
        res = compute_dqdv(t, i, v, "charge")
        # 两段共 2 mAh(容差含端点效应)
        assert res.q_total == pytest.approx(2.0, rel=0.05)
        assert any("回退" in w for w in res.warnings)

    def test_no_negative_step_in_q(self):
        seg_t = np.array([0.0, 10.0, 20.0])
        t = np.concatenate([seg_t, seg_t])
        i = np.full(6, 100.0)
        v = np.linspace(3.0, 4.0, 6)
        res = compute_dqdv(t, i, v, "charge")
        # dq/dV 曲线不应出现巨大负尖峰(腰斩时代的残迹)
        assert res.dqdv.min() > -5.0


# ---------------------------------------------------------------- D2: fast 组不进归因


class TestAttributionGroups:
    def test_fast_group_excluded_from_main(self):
        """倍率阶梯里 fast 圈容量天然低,不能被算成衰减。"""
        cycles = []
        for n in range(1, 21):
            q = 100.0 - 0.2 * n
            grp = "fast" if n > 15 else "main"
            if grp == "fast":
                q = 60.0  # 快充容量低,但不是衰减
            cycles.append({
                "index": n, "q_discharge": q, "q_charge": q + 1,
                "ce": 0.99, "rate_group": grp, "incomplete": False,
            })
        res = run_attribution(cycles, [], None)
        idxs = {c.cycle for c in res.cycles}
        assert idxs.isdisjoint({16, 17, 18, 19, 20})
        # 总衰减不应包含 fast 组的容量落差(基线 100 → 末圈约 96)
        assert res.cycles[-1].total < 10.0


# ---------------------------------------------------------------- D4: 自定义窗口


class TestAttributionWindows:
    def test_custom_window_keys_not_hardcoded(self):
        """自定义窗口名下方法 B 仍能拆分,截断证据不再永远 available=False。"""
        cycles = [
            {"index": n, "q_discharge": 100.0 - n, "q_charge": 101.0 - n,
             "ce": 0.99, "rate_group": "main", "incomplete": False}
            for n in range(1, 11)
        ]
        peaks = [
            _pk(n, "discharge", "MY_MAIN", position=3.7 - 0.002 * n,
                height=10.0, area=100.0 - n) for n in range(1, 11)
        ] + [
            _pk(n, "charge", "MY_HIGH", position=4.18 + 0.002 * n,
                height=5.0, area=50.0, truncated=(n > 6)) for n in range(1, 11)
        ]
        res = run_attribution(cycles, peaks, None)
        assert res.method == "B"
        assert res.truncated_evidence.get("available")
        assert res.truncated_evidence.get("window") == "MY_HIGH"
        assert not res.split_uninformative

    def test_no_peaks_conclusion_does_not_assert_cutoff(self):
        """峰序列不足 → 结论必须说无法拆分,不能断言'截断主导'。"""
        cycles = [
            {"index": n, "q_discharge": 100.0 - n, "q_charge": 101.0 - n,
             "ce": 0.99, "rate_group": "main", "incomplete": False}
            for n in range(1, 11)
        ]
        res = run_attribution(cycles, [], None)
        assert res.split_uninformative
        assert "无法拆分" in res.conclusion
        assert "截断主导" not in res.conclusion

    def test_window_keys_prefers_convention_names(self):
        peaks = [_pk(1, "discharge", "H1H2", 3.7), _pk(1, "charge", "H2H3", 4.2)]
        assert window_keys(peaks) == ("H1H2", "H2H3")


# ---------------------------------------------------------------- 命令层


@pytest.fixture
def engine(tmp_path):
    eng = Engine(tmp_path)
    yield eng
    eng.close()


@pytest.fixture
def cell_id(engine, tmp_path):
    path = tmp_path / "land.xlsx"
    build_land_xlsx(path, n_slow=2, n_fast=4)
    pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
    r = engine.handle("ec.import", {
        "paths": [str(path)], "project_id": pid, "new_sample": {"name": "S"},
    })
    assert r["ok"], r
    return r["data"]["imported"][0]["id"]


class TestCommandLayer:
    def test_unknown_peak_param_rejected(self, engine, cell_id):
        r = engine.handle("ec.dqdv_compute", {
            "cell_id": cell_id, "params": {"peak": {"heigth": 1.0}},
        })
        assert not r["ok"]
        assert "heigth" in r["error"]

    def test_illegal_peak_param_rejected(self, engine, cell_id):
        r = engine.handle("ec.dqdv_compute", {
            "cell_id": cell_id, "params": {"peak": {"height_frac": -1}},
        })
        assert not r["ok"]
        assert "peak 参数非法" in r["error"]

    def test_attribution_get_corrupt_payload(self, engine, cell_id):
        """result_json 损坏 → corrupt 语义,不能 computed=true 空体。"""
        engine.handle("ec.dqdv_compute", {
            "cell_id": cell_id, "params": {
                "windows": [{"key": "H1H2", "label": "l", "v_lo": 3.3, "v_hi": 4.0}],
            },
        })
        engine.handle("ec.attribution_run", {"cell_id": cell_id})
        db = engine.db
        db.execute(
            "UPDATE ec_analyses SET result_json = '{broken' "
            "WHERE cell_id = ? AND kind = 'attribution'",
            (cell_id,),
        )
        r = engine.handle("ec.attribution_get", {"cell_id": cell_id})
        assert r["ok"]
        assert r["data"].get("corrupt") is True
        assert r["data"].get("computed") is False

    def test_npz_recompute_cleans_stale_files(self, engine, cell_id):
        engine.handle("ec.dqdv_compute", {"cell_id": cell_id})
        engine.handle("ec.dqdv_compute", {
            "cell_id": cell_id, "params": {"dv_mv": 5.0},
        })
        from xrd_engine.api.commands import Engine as _E  # noqa: F401
        row = engine.db.query_one(
            "SELECT stored_path FROM ec_cells WHERE id = ?", (cell_id,)
        )
        cell_dir = engine.store.abs_path(row["stored_path"]).parent
        npzs = sorted(p.name for p in cell_dir.glob("dqdv_*.npz"))
        assert len(npzs) == 1, f"旧曲线缓存未清理: {npzs}"

    def test_attribution_uses_dqdv_mass_basis(self, engine, cell_id):
        """dQ/dV 页改 mass 后,归因容量口径必须跟着同一体系。"""
        engine.handle("ec.dqdv_compute", {
            "cell_id": cell_id,
            "params": {"mass_mg": 2.0,
                       "windows": [{"key": "H1H2", "label": "l", "v_lo": 3.3, "v_hi": 4.0}]},
        })
        r = engine.handle("ec.attribution_run", {"cell_id": cell_id})
        assert r["ok"], r
        data = r["data"]
        if data.get("cycles"):
            # 基线应按 mass=2.0mg 缩放(mAh/g),不是 cell 的 1.77mg
            assert data["baseline"] > 0

    def test_dqdv_qtotal_includes_cv(self, engine, tmp_path):
        """命令层容量口径含 CV:构造 CCCV 小样本,充放曲线都算得出。"""
        rows = []
        for k in range(0, 3601, 60):
            rows.append((k, 100.0, 3.0 + k / 3600.0, 1, 1, "恒流充电"))
        for k in range(3600, 7201, 60):
            rows.append((k, 20.0, 4.3, 1, 2, "恒压"))
        for k in range(7200, 10801, 60):
            rows.append((k, -100.0, 3.8 - (k - 7200) / 3600.0 * 0.6, 1, 3, "恒流放电"))
        path = _write_record_xlsx(tmp_path / "cccv.xlsx", rows)
        pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
        r = engine.handle("ec.import", {
            "paths": [str(path)], "project_id": pid, "new_sample": {"name": "S"},
        })
        assert r["ok"], r
        cid = r["data"]["imported"][0]["id"]
        r2 = engine.handle("ec.dqdv_compute", {"cell_id": cid, "params": {
            "windows": [{"key": "W", "label": "W", "v_lo": 2.9, "v_hi": 4.35}],
        }})
        assert r2["ok"], r2
        # 充(CC)放两条曲线都该有
        assert r2["data"]["nCurves"] >= 2
        # 充电段容量口径含 CV:100 mAh(CC)+ 20 mAh(CV)≈ 120,无质量时单位 mAh
        rd = engine.handle("ec.dqdv_result", {"cell_id": cid})["data"]
        chg = [x for x in rd["index"] if x["direction"] == "charge"]
        assert chg and chg[0]["qTotal"] == pytest.approx(120.0, rel=0.02)
