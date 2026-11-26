"""Regressions for the historic numerical analysis primitives."""

import numpy as np
import pytest

from xrd_engine.electrochem import compute_dqdv
from xrd_engine.electrochem.attribution import run_attribution, window_keys
from xrd_engine.electrochem.peaks import CyclePeak


def _pk(cycle, direction, window_key, position, height=1.0, area=1.0, truncated=False):
    return CyclePeak(
        cycle=cycle, direction=direction, window_key=window_key,
        position=position, height=height, area=area, fwhm=0.05,
        prominence=height * 0.5, onset=position - 0.02, truncated=truncated,
    )


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
