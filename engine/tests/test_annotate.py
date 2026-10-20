"""晶面(hkl)标注单测(peaks.annotate)。

覆盖两条路径:
  - 高镍层状路径 annotate_high_ni:全谱晶胞精修 + 强度贡献归属
  - 通用兜底路径 annotate_peaks(reference_table=...):最近邻匹配
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from xrd_engine.models.types import Peak
from xrd_engine.peaks.annotate import (
    annotate_high_ni,
    annotate_peaks,
)

LAM = 1.540593


def _peaks(positions: list[float]) -> list[Peak]:
    return [
        Peak(index=i, position=pos, intensity=1000.0 - i * 10, fwhm=0.2)
        for i, pos in enumerate(positions)
    ]


def _tth(h: int, k: int, l: int, a: float, c: float, lam: float = LAM) -> float:
    inv = (4.0 / 3.0) * (h * h + h * k + k * k) / (a * a) + (l * l) / (c * c)
    d = 1.0 / math.sqrt(inv)
    return math.degrees(2.0 * math.asin(lam / (2.0 * d)))


def _pv(x, x0, fwhm, eta=0.6):
    sigma = fwhm / 2.3548200450309493
    return eta / (1 + ((x - x0) / (fwhm / 2)) ** 2) + (1 - eta) * np.exp(
        -0.5 * ((x - x0) / sigma) ** 2
    )


def _synth(a: float, c: float, amps: dict, fwhm: float = 0.18, seed: int = 7):
    """按真实 (a, c) 反算位置合成层状谱。位置来自晶体学,不是写死的数字。"""
    x = np.arange(10.0, 80.0001, 0.02)
    y = np.full_like(x, 95.0) + 300.0 * np.exp(-(x - 10.0) / 15.0)
    for (h, k, l), amp in amps.items():
        y = y + amp * _pv(x, _tth(h, k, l, a, c), fwhm)
    rng = np.random.default_rng(seed)
    return x, np.maximum(rng.poisson(np.maximum(y, 1.0)).astype(float), 0.0)


# ---------- 禁阻规则(本次修复的第一性问题) ----------


def test_forbidden_reflections_are_excluded():
    """R-3m 阳离子 3a 位点:只有 -h+k+l ≡ 0 (mod 3) 的反射允许出现。

    这是**物理**判据。对外 label 对双峰用文献名 (102)/(108)——同一
    物理反射的另一套指数(d 相同),见 indexing._LITERATURE_HKL。
    """
    from xrd_engine.high_ni.indexing import allowed_hkl

    for hkl in [(0, 0, 1), (1, 0, 0), (0, 1, 1), (1, 1, 1), (0, 0, 4)]:
        assert not allowed_hkl(*hkl), f"{hkl} 应为禁阻反射"

    for hkl in [
        (0, 0, 3), (0, 0, 6), (0, 0, 9), (1, 0, 1), (0, 1, 2), (1, 0, 4),
        (0, 1, 5), (1, 0, 7), (0, 1, 8), (1, 1, 0), (1, 1, 3), (0, 2, 1),
    ]:
        assert allowed_hkl(*hkl), f"{hkl} 应为允许反射"


def test_allowed_reflections_cover_the_observable_range():
    """枚举应覆盖数据范围内**全部**允许反射,而不是写死 7 条。"""
    from xrd_engine.high_ni.indexing import allowed_reflections

    refs = allowed_reflections(2.884, 14.28, LAM, (10.0, 80.0))
    labels = {r.label for r in refs}
    for expected in ["(003)", "(101)", "(006)", "(102)", "(104)", "(015)", "(107)", "(108)", "(110)", "(113)"]:
        assert expected in labels, f"缺少 {expected}"
    # 真正不存在的反射不得出现
    assert "(001)" not in labels
    assert len(refs) >= 14


# ---------- 高镍路径:双峰归属 ----------


def test_006_and_102_are_not_confused():
    """(006)/(102) 双峰:强度大的那条应判给 (102)。

    层状正极实验上 (006) 弱、~38.3° 强分量对外标 (102)。
    """
    a, c = 2.884, 14.28
    amps = {(0, 0, 3): 6000, (1, 0, 1): 2000, (1, 0, 4): 3000,
            (0, 0, 6): 300, (0, 1, 2): 900, (0, 1, 5): 700,
            (1, 0, 7): 600, (0, 1, 8): 800, (1, 1, 0): 900, (1, 1, 3): 700}
    x, y = _synth(a, c, amps)

    pos_102 = _tth(0, 1, 2, a, c)
    pos_006 = _tth(0, 0, 6, a, c)
    assert pos_006 < pos_102

    # 观测峰落在强分量上
    result, ctx = annotate_high_ni(x, y, _peaks([pos_102]), wavelength=LAM)
    assert ctx is not None
    assert result[0].hkl_label == "(102)"
    assert result[0].hkl_confidence == "reliable"


def test_006_dominant_case_is_labelled_006():
    """反向用例:(006) 强、102 分量弱时不得对调。

    混排严重的样品里 (006) 会长起来 —— 归属必须跟着强度走,而不是
    固定按"低角 = (006)"的顺序 zip(旧 _fit_pair 的做法)。
    """
    a, c = 2.884, 14.28
    amps = {(0, 0, 3): 6000, (1, 0, 1): 2000, (1, 0, 4): 3000,
            (0, 0, 6): 1200, (0, 1, 2): 250, (0, 1, 5): 700,
            (1, 0, 7): 600, (0, 1, 8): 800, (1, 1, 0): 900, (1, 1, 3): 700}
    x, y = _synth(a, c, amps)

    pos_006 = _tth(0, 0, 6, a, c)
    result, ctx = annotate_high_ni(x, y, _peaks([pos_006]), wavelength=LAM)
    assert ctx is not None
    assert result[0].hkl_label == "(006)"


def test_108_and_110_doublet_is_resolved():
    """(108)/(110) 间距约 0.49°:两者都应各自拿到正确的晶面。

    旧逻辑用固定 ±0.60° 窗口算贡献时,(110) 有约 65% 面积落进 (108) 的
    窗口,(108) 被永久判成 (110)。归属窗口必须随峰宽缩放。
    """
    a, c = 2.884, 14.28
    amps = {(0, 0, 3): 6000, (1, 0, 1): 2000, (1, 0, 4): 3000,
            (0, 1, 8): 700, (1, 1, 0): 1500, (1, 1, 3): 600}
    x, y = _synth(a, c, amps, fwhm=0.16)

    pos_108 = _tth(0, 1, 8, a, c)
    pos_110 = _tth(1, 1, 0, a, c)
    result, ctx = annotate_high_ni(
        x, y, _peaks([pos_108, pos_110]), wavelength=LAM
    )
    assert ctx is not None
    labels = {round(r.position, 3): r.hkl_label for r in result}
    assert labels[round(pos_108, 3)] == "(108)"
    assert labels[round(pos_110, 3)] == "(110)"


def test_cell_parameters_are_refined_per_pattern():
    """晶胞必须每张谱精修 —— 写死 (a,c) 会让 (006)/(102) 的归属翻转。"""
    for true_a, true_c in [(2.860, 14.19), (2.890, 14.30)]:
        amps = {(0, 0, 3): 6000, (1, 0, 1): 2000, (1, 0, 4): 3000,
                (0, 1, 2): 900, (0, 1, 5): 700, (1, 0, 7): 600,
                (0, 1, 8): 800, (1, 1, 0): 900, (1, 1, 3): 700}
        x, y = _synth(true_a, true_c, amps)
        _result, ctx = annotate_high_ni(x, y, _peaks([18.6]), wavelength=LAM)
        assert ctx is not None
        assert abs(ctx.cell[0] - true_a) < 0.01, f"a 精修偏差过大:{ctx.cell}"
        assert abs(ctx.cell[1] - true_c) < 0.02, f"c 精修偏差过大:{ctx.cell}"


def test_ambiguous_is_reported_not_guessed():
    """双峰物理上未分开时,应返回 ambiguous + 候选,而不是硬给一个 reliable。"""
    a, c = 2.884, 14.28
    # 很宽的峰:006/102 合并成一个包
    amps = {(0, 0, 3): 6000, (1, 0, 1): 2000, (1, 0, 4): 3000,
            (0, 0, 6): 500, (0, 1, 2): 500}
    x, y = _synth(a, c, amps, fwhm=0.55)

    mid = 0.5 * (_tth(0, 0, 6, a, c) + _tth(0, 1, 2, a, c))
    result, ctx = annotate_high_ni(x, y, _peaks([mid]), wavelength=LAM)
    assert ctx is not None
    assert result[0].hkl_confidence in {"ambiguous", "none"}
    if result[0].hkl_confidence == "ambiguous":
        assert len(result[0].candidates) >= 2


def test_wavelength_is_respected():
    """非 Cu 靶数据必须按给定波长算位置。固定 Cu Kα 会让所有反射整体错位。"""
    a, c = 2.884, 14.28
    lam_co = 1.788965
    amps = {(0, 0, 3): 6000, (1, 0, 1): 2000, (1, 0, 4): 3000,
            (0, 1, 2): 900, (0, 1, 5): 700, (1, 0, 7): 600,
            (0, 1, 8): 800, (1, 1, 0): 900, (1, 1, 3): 700}
    x, y = _synth(a, c, amps)
    # 合成谱用的是 Cu 位置;按 Co 波长标注时 (003) 应在 21.7° 而非 18.6°
    pos_003_co = _tth(0, 0, 3, a, c, lam_co)
    assert pos_003_co > 21.0
    result, ctx = annotate_high_ni(x, y, _peaks([pos_003_co]), wavelength=lam_co)
    assert ctx is not None
    # 用 Co 波长时 (003) 位置确实在 21.7 附近,但合成谱里那里没有峰 ——
    # 归属应当失败,而不是拿 Cu 位置硬套
    assert result[0].hkl_label in {None, "(003)"}


def test_no_peaks_returns_empty():
    x, y = _synth(2.884, 14.28, {(0, 0, 3): 5000})
    result, _ctx = annotate_high_ni(x, y, [], wavelength=LAM)
    assert result == []


# ---------- 通用兜底路径 ----------


def test_generic_path_requires_explicit_table():
    """不传参考表时不再静默回退到含禁阻反射的高镍表。"""
    with pytest.raises(ValueError, match="reference_table"):
        annotate_peaks(_peaks([38.1]))


def test_generic_path_matches_custom_table():
    refs = [{"label": "(111)", "center_2theta": 40.0, "hkl": (1, 1, 1)}]
    result = annotate_peaks(_peaks([39.95]), refs, tolerance=0.1)
    assert result[0].hkl_label == "(111)"
    assert result[0].hkl == (1, 1, 1)
    assert result[0].offset_deg is not None


def test_generic_path_out_of_tolerance_is_none():
    refs = [{"label": "(111)", "center_2theta": 40.0, "hkl": (1, 1, 1)}]
    result = annotate_peaks(_peaks([50.0, 55.0]), refs, tolerance=0.1)
    assert all(r.hkl_label is None for r in result)
    assert all(r.hkl_confidence == "none" for r in result)


def test_generic_path_near_tie_is_ambiguous():
    """两个参考几乎等距时应报 ambiguous 并保留候选,而不是任选一个。"""
    refs = [
        {"label": "(006)", "center_2theta": 37.98, "hkl": (0, 0, 6)},
        {"label": "(102)", "center_2theta": 38.20, "hkl": (1, 0, 2)},
    ]
    result = annotate_peaks(_peaks([38.09]), refs, tolerance=0.3)
    assert result[0].hkl_confidence == "ambiguous"
    assert len(result[0].candidates) == 2


# ---------- 无钴基准谱(cobalt_free_01)回归 ----------


class TestCobaltFreeFixture:
    """无钴高镍(a=2.884/c=14.278)上的标注回归。

    与 ncm811(含钴)分开:无钴端元晶胞偏离写死的 2.878/14.216 约
    0.06 Å,102 分量理论位置偏约 0.1° —— 正是历史上配名翻转的量级。
    谱里还刻意埋了一个 R-3m 允许表之外的杂相峰(39.5°)。
    """

    def _load(self):
        from pathlib import Path

        from xrd_engine.importers.loader import load_curve

        path = Path(__file__).resolve().parent.parent / "fixtures" / "cobalt_free_01.xy"
        if not path.exists():
            pytest.skip("cobalt_free fixture 未生成")
        return load_curve(path)

    def test_refine_cell_recovers_cobalt_free_cell(self):
        from xrd_engine.high_ni.indexing import refine_cell

        curve = self._load()
        fit = refine_cell(curve.x, curve.y, wavelength=1.540593)
        assert fit is not None
        # 真值 2.884 / 14.278:允许与写死表(2.878/14.216)可区分的精度
        assert abs(fit.a - 2.884) < 0.01, f"a={fit.a}"
        assert abs(fit.c - 14.278) < 0.03, f"c={fit.c}"

    def test_006_102_pair_labelled_on_cobalt_free(self):
        """(006)/(102) 双峰必须分别归属,不得互换。"""
        from xrd_engine.models.types import PeakDetectionParams
        from xrd_engine.peaks.detection import (
            compute_fwhm_and_bounds,
            detect_peaks,
        )

        curve = self._load()
        peaks, _diag = detect_peaks(
            curve.x, curve.y, PeakDetectionParams(sensitivity="medium")
        )
        peaks = compute_fwhm_and_bounds(curve.x, curve.y, peaks)
        annotated, context = annotate_high_ni(
            curve.x, curve.y, peaks, wavelength=1.540593, dataset_id=None
        )
        assert context is not None
        a_tth = _tth(0, 0, 6, 2.884, 14.278, 1.540593)
        c_tth = _tth(0, 1, 2, 2.884, 14.278, 1.540593)
        near_006 = [a for a in annotated if abs(a.position - a_tth) < 0.25]
        near_102 = [a for a in annotated if abs(a.position - c_tth) < 0.25]
        assert near_006, "006 位置应有检出峰"
        # 006 处:reliable 只能标 (006);重叠未分开时 ambiguous 且候选必须含 (006)
        a006 = near_006[0]
        if a006.hkl_confidence == "reliable":
            assert a006.hkl_label == "(006)", f"006 位置被可靠地标成 {a006.hkl_label}"
        else:
            labels = [c.get("label") for c in a006.candidates]
            assert "(006)" in labels, f"ambiguous 候选必须含 (006): {labels}"
            assert a006.hkl_label is None or a006.hkl_label == "(006)"
        # 102 处(强分量,文献显示名):至少不得被标成 (006) 或其他晶面
        assert near_102, "102 位置应有检出峰"
        a102 = near_102[0]
        if a102.hkl_confidence == "reliable":
            assert a102.hkl_label == "(102)", f"102 位置被可靠地标成 {a102.hkl_label}"
        # 两个位置都不得出现"可靠但配对互换"
        for a in (a006, a102):
            if a.hkl_confidence == "reliable":
                expect = "(006)" if abs(a.position - a_tth) < abs(a.position - c_tth) else "(102)"
                assert a.hkl_label == expect, f"配名互换: {a.position} -> {a.hkl_label}"

    def test_impurity_peak_reports_none(self):
        """39.5° 杂相峰不在 R-3m 允许表内 → none,不许硬套晶面。"""
        from xrd_engine.models.types import PeakDetectionParams
        from xrd_engine.peaks.detection import (
            compute_fwhm_and_bounds,
            detect_peaks,
        )

        curve = self._load()
        peaks, _diag = detect_peaks(
            curve.x, curve.y, PeakDetectionParams(sensitivity="medium")
        )
        peaks = compute_fwhm_and_bounds(curve.x, curve.y, peaks)
        annotated, _ = annotate_high_ni(
            curve.x, curve.y, peaks, wavelength=1.540593, dataset_id=None
        )
        impurity = [a for a in annotated if abs(a.position - 39.50) < 0.15]
        assert impurity, "杂相峰应被检出"
        for a in impurity:
            assert a.hkl_label is None, f"杂相峰不得标晶面: {a.hkl_label}"
            assert a.hkl_confidence in {"none", "ambiguous"}
            # candidates 保留(近邻弱匹配),供 UI 提示"疑似杂相"
            assert a.dominance is not None
