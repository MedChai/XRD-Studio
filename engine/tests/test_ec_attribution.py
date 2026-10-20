"""attribution 模块测试:已知真值分量的恢复精度(M3 验收)。"""

import pytest

from xrd_engine.electrochem.attribution import AttributionParams, run_attribution
from xrd_engine.electrochem.peaks import CyclePeak


def _mk_peak(cycle, pos, height=100.0, area=6.0, fwhm=0.06, direction="discharge",
             window="H1H2", truncated=False):
    return CyclePeak(cycle=cycle, direction=direction, window_key=window,
                     position=pos, height=height, area=area, fwhm=fwhm,
                     prominence=height, onset=pos - fwhm, truncated=truncated)


def _cycles(q_series: dict[int, float], rate_groups: dict[int, str] | None = None,
            incomplete: set[int] | None = None):
    """构造归因输入:q_series = {圈: 放电容量 mAh/g}。"""
    rate_groups = rate_groups or {}
    incomplete = incomplete or set()
    out = []
    for n, q in sorted(q_series.items()):
        out.append({
            "index": n, "q_discharge": q, "q_charge": q / 0.98, "ce": 0.98,
            "rate_group": rate_groups.get(n, "main"),
            "incomplete": n in incomplete,
        })
    return out


class TestMethodB:
    """方法 B(无回访圈):峰位移/峰面积近似。"""

    def test_pure_polarization(self):
        """纯极化场景:峰位下移 × 峰高 = 总衰减。"""
        baseline = 100.0
        shift = 0.06      # 60 mV
        height = 500.0    # mAh/(g·V)
        q = {n: baseline for n in range(1, 101)}
        q[100] = baseline - shift * height   # 70 mAh/g 衰减,全由极化造成
        peaks = [_mk_peak(n, 3.70) for n in range(1, 101)]
        peaks[-1] = _mk_peak(100, 3.70 - shift, height, 6.0)
        res = run_attribution(_cycles(q), peaks, AttributionParams(method="B"))
        last = res.cycles[-1]
        assert res.method == "B" and res.confidence == "low"
        assert last.total == pytest.approx(shift * height, rel=1e-3)
        assert last.polarization == pytest.approx(shift * height, rel=0.15)
        assert last.material == pytest.approx(0.0, abs=1.0)

    def test_pure_material_loss(self):
        """纯活性损失:峰面积等比萎缩,峰位不动。"""
        baseline = 100.0
        q = {n: baseline for n in range(1, 51)}
        q[50] = 60.0   # 40 mAh/g 衰减
        peaks = [_mk_peak(n, 3.70, 500.0, 30.0) for n in range(1, 51)]
        peaks[-1] = _mk_peak(50, 3.70, 500.0, 30.0 * 0.6)
        res = run_attribution(_cycles(q), peaks, AttributionParams(method="B"))
        last = res.cycles[-1]
        assert last.total == pytest.approx(40.0)
        # 等比例假设:material = baseline × (1 - 0.6) = 40
        assert last.material == pytest.approx(40.0, rel=0.05)
        assert last.polarization == pytest.approx(0.0, abs=1.0)

    def test_components_close_exactly(self):
        """三分量之和 = 总衰减(按构造闭合)。"""
        baseline = 120.0
        q = {n: baseline for n in range(1, 81)}
        q[80] = 70.0
        peaks = [_mk_peak(n, 3.70, 400.0, 30.0) for n in range(1, 81)]
        peaks[-1] = _mk_peak(80, 3.62, 400.0, 18.0)   # 位移 + 萎缩混合
        res = run_attribution(_cycles(q), peaks, AttributionParams(method="B"))
        for c in res.cycles:
            assert c.polarization + c.material + c.cutoff == pytest.approx(
                c.total, abs=0.51
            )

    def test_incomplete_cycles_excluded(self):
        baseline = 100.0
        q = {n: baseline for n in range(1, 31)}
        q[30] = 20.0     # 末圈骤降:不完整,应被剔除
        peaks = [_mk_peak(n, 3.70) for n in range(1, 30)]
        res = run_attribution(_cycles(q, incomplete={30}), peaks,
                              AttributionParams(method="B"))
        assert all(c.cycle != 30 for c in res.cycles)
        assert res.cycles[-1].cycle == 29


class TestMethodA:
    """方法 A(有低倍率回访圈):回访插值法。"""

    def test_checkup_split(self):
        """已知极化/活性构造:主倍率容量 = 回访插值容量 − 极化损失。"""
        q, groups = {}, {}
        # 回访圈(圈1/50/100,slow):100 → 90 → 80(活性损失累计 20)
        q[1], groups[1] = 100.0, "slow"
        q[50], groups[50] = 90.0, "slow"
        q[100], groups[100] = 80.0, "slow"
        # 基线主倍率圈 2-4(容量 99.8 → baseline=99.8)
        for n in (2, 3, 4):
            q[n] = 99.8
        # 主倍率圈 49:回访插值 90.2,极化 5 → 容量 85.2
        q[49] = 90.2 - 5.0
        # 主倍率圈 99:回访插值 80.2,极化 9.8 → 容量 70.4
        q[99] = 80.2 - 9.8
        res = run_attribution(_cycles(q, groups), [],
                              AttributionParams(method="A"))
        assert res.method == "A" and res.confidence == "high"
        c99 = next(c for c in res.cycles if c.cycle == 99)
        assert c99.total == pytest.approx(99.8 - 70.4, rel=1e-6)
        assert c99.polarization == pytest.approx(9.8, rel=0.05)
        assert c99.material == pytest.approx(19.8, rel=0.05)

    def test_method_a_requires_checkups(self):
        q = {n: 100.0 for n in range(1, 21)}
        with pytest.raises(ValueError, match="回访圈不足"):
            run_attribution(_cycles(q), [], AttributionParams(method="A"))


def test_conclusion_text_rules():
    """结论包含主导分量与方向建议。"""
    baseline = 100.0
    q = {n: baseline for n in range(1, 61)}
    q[60] = 40.0
    peaks = [_mk_peak(n, 3.70, 500.0, 30.0) for n in range(1, 61)]
    peaks[-1] = _mk_peak(60, 3.64, 500.0, 29.0)
    res = run_attribution(_cycles(q), peaks, AttributionParams())
    assert "极化型" in res.conclusion or "窗口截断" in res.conclusion
    assert "方法学局限" in res.conclusion
