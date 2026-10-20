"""高镍层状 R-3m 指数化回归测试(high_ni.indexing)。

用**合成谱**做断言(位置由晶胞反算,期望值可解析验证),而不是把某台
仪器的实测谱写进仓库 —— 实测数据属于用户,且不同样品的晶胞本就不同。
合成谱的构造方式与真实层状正极一致:允许反射 + 随角度展宽的峰形 +
低角非晶包 + 泊松噪声。
"""

from __future__ import annotations

import numpy as np

from xrd_engine.high_ni.indexing import (
    allowed_hkl,
    allowed_reflections,
    assign_peaks,
    d_spacing,
    doublet_order_ok,
    ka_components,
    refine_cell,
    two_theta_of,
)

LAM = 1.540593


def _tth(h: int, k: int, l: int, a: float, c: float, lam: float = LAM) -> float:
    return two_theta_of(h, k, l, a, c, lam)  # type: ignore[return-value]


def _pv(x, x0, fwhm, eta=0.6):
    sigma = fwhm / 2.3548200450309493
    return eta / (1 + ((x - x0) / (fwhm / 2)) ** 2) + (1 - eta) * np.exp(
        -0.5 * ((x - x0) / sigma) ** 2
    )


def _synth(
    a: float,
    c: float,
    amps: dict,
    *,
    fwhm_0: float = 0.11,
    slope: float = 0.003,
    lo: float = 10.0,
    hi: float = 100.0,
    seed: int = 11,
    amorphous: float = 400.0,
):
    """按真实晶胞反算位置合成层状正极谱(含 Kα1+Kα2)。"""
    x = np.arange(lo, hi + 0.0001, 0.02)
    y = np.full_like(x, 95.0)
    for (h, k, l), amp in amps.items():
        for center, weight in ka_components(_tth(h, k, l, a, c), LAM):
            y = y + amp * weight * _pv(x, center, fwhm_0 + slope * center)
    y = y + amorphous * np.exp(-(x - lo) / 15.0)
    rng = np.random.default_rng(seed)
    return x, np.maximum(rng.poisson(np.maximum(y, 1.0)).astype(float), 0.0)


# 层状正极典型强度分布:(003) 最强,高角依次衰减。
TYPICAL = {
    (0, 0, 3): 6000, (1, 0, 1): 2000, (0, 0, 6): 300, (0, 1, 2): 900,
    (1, 0, 4): 3000, (0, 1, 5): 700, (1, 0, 7): 600, (0, 1, 8): 700,
    (1, 1, 0): 1500, (1, 1, 3): 600, (1, 1, 6): 400, (2, 0, 2): 300,
}


class _Peak:
    """最小峰对象:indexing.assign_peaks 只要求 .index 与 .position。"""

    def __init__(self, index: int, position: float):
        self.index = index
        self.position = position


# ---------- 晶体学基础 ----------


def test_extinction_rule_matches_r3m():
    """R-3m 阳离子 3a 位点:-h+k+l ≡ 0 (mod 3)。

    这是**物理**判据,不随显示名变化。文献写 (102) 时指的是同一物理
    反射的另一套指数(d 相同),对外 label 用文献名见 _LITERATURE_HKL。
    """
    forbidden = [(0, 0, 1), (0, 0, 2), (0, 0, 4), (1, 0, 0), (0, 1, 1),
                 (1, 1, 1), (2, 0, 0), (0, 1, 4)]
    for hkl in forbidden:
        assert not allowed_hkl(*hkl), f"{hkl} 应为禁阻"

    permitted = [(0, 0, 3), (0, 0, 6), (0, 0, 9), (1, 0, 1), (0, 1, 2),
                 (1, 0, 4), (0, 1, 5), (1, 0, 7), (0, 1, 8), (1, 1, 0),
                 (1, 1, 3), (0, 2, 1), (1, 1, 6), (2, 0, 2)]
    for hkl in permitted:
        assert allowed_hkl(*hkl), f"{hkl} 应为允许"


def test_d_spacing_known_values():
    """六方晶系面间距公式与 NIST 级已知值交叉校验。"""
    # (003): d = c/3
    assert abs(d_spacing(0, 0, 3, 2.878, 14.216) - 14.216 / 3.0) < 1e-9
    # (110): d = a/2
    assert abs(d_spacing(1, 1, 0, 2.878, 14.216) - 2.878 / 2.0) < 1e-9


def test_allowed_reflections_are_range_driven():
    """反射集合由数据范围决定,不是写死的 7 条。"""
    refs = allowed_reflections(2.884, 14.28, LAM, (10.0, 100.0))
    labels = {r.label for r in refs}
    assert len(refs) >= 18
    # 双峰第二个分量对外用文献名 (102)/(108)(d 与 (012)/(018) 相同)
    for expected in ["(003)", "(102)", "(015)", "(107)", "(108)", "(110)",
                     "(113)", "(116)", "(202)", "(024)", "(027)", "(208)"]:
        assert expected in labels, f"缺少 {expected}"
    # 升序
    positions = [r.two_theta for r in refs]
    assert positions == sorted(positions)


def test_ka2_splitting_grows_with_angle():
    """Kα2 位移必须按 Bragg 重算,不能固定 —— 它随角度增大。"""
    t_low = ka_components(_tth(0, 0, 3, 2.884, 14.28), LAM)
    t_high = ka_components(_tth(1, 1, 0, 2.884, 14.28), LAM)
    assert len(t_low) == 2 and len(t_high) == 2
    split_low = t_low[1][0] - t_low[0][0]
    split_high = t_high[1][0] - t_high[0][0]
    assert split_low < 0.06          # (003) 附近约 0.047
    assert split_high > 0.15         # (110) 附近约 0.18
    assert split_high > 2 * split_low


# ---------- 晶胞精修 ----------


def test_cell_refinement_recovers_truth():
    """在 ±0.01 Å (a) / ±0.02 Å (c) 内恢复真实晶胞。

    写死晶胞会让 (006)/(102) 的归属翻转 —— 两者的理论间距对 c 高度敏感。
    """
    for true_a, true_c in [(2.860, 14.19), (2.874, 14.24), (2.890, 14.31)]:
        x, y = _synth(true_a, true_c, TYPICAL)
        fit = refine_cell(x, y, wavelength=LAM)
        assert fit is not None
        assert abs(fit.a - true_a) < 0.01, f"a: 得 {fit.a:.4f} 真 {true_a}"
        assert abs(fit.c - true_c) < 0.02, f"c: 得 {fit.c:.4f} 真 {true_c}"
        assert fit.rwp < 0.20, f"Rwp 过高 {fit.rwp:.3f}"


def test_cell_refinement_is_not_fooled_by_amorphous_hump():
    """低角非晶包(导电碳/粘结剂)不得把晶胞带偏。

    背景多项式必须能吸收它;否则拟合会去扭曲峰形与晶胞来迁就它。
    """
    x, y = _synth(2.874, 14.24, TYPICAL, amorphous=4000.0)
    fit = refine_cell(x, y, wavelength=LAM)
    assert fit is not None
    assert abs(fit.a - 2.874) < 0.012
    assert abs(fit.c - 14.24) < 0.025


def test_doublet_order_rejects_inverted_cell():
    """c² ≤ 24a² 时 (006)/(102) 与 (018)/(110) 理论角对调,必须拒绝。

    反序盆地下理论 (018) 反超 (110),观测高角峰会被标成 (108)
    (用户反馈「110 也显示 108」)。层状正极 c/a ≈ 4.95–5.0,永远满足
    c² > 24a²;网格搜索与 assign_peaks 都靠这条判据兜底。
    """
    assert doublet_order_ok(2.884, 14.28)
    assert doublet_order_ok(2.878, 14.216)
    assert not doublet_order_ok(2.917, 14.192)  # 用户库里出现过的反序胞
    assert not doublet_order_ok(2.90, 14.207)   # c/a = √24 边界(含等号)
    # 理论角同步对调
    assert _tth(0, 0, 6, 2.884, 14.28) < _tth(0, 1, 2, 2.884, 14.28)
    assert _tth(0, 1, 8, 2.884, 14.28) < _tth(1, 1, 0, 2.884, 14.28)
    assert _tth(0, 1, 8, 2.917, 14.192) > _tth(1, 1, 0, 2.917, 14.192)


def test_refine_cell_never_returns_inverted_cell():
    """精修不得落在反序盆地 —— 即便真值胞就在搜索框边界附近。"""
    for true_a, true_c in [(2.860, 14.19), (2.890, 14.31), (2.910, 14.35)]:
        x, y = _synth(true_a, true_c, TYPICAL)
        fit = refine_cell(x, y, wavelength=LAM)
        assert fit is not None
        assert doublet_order_ok(fit.a, fit.c), (
            f"精修给出反序胞 a={fit.a} c={fit.c}"
        )


def test_inverted_cell_fit_never_labels_peaks():
    """手工构造的反序 CellFit 不得输出 reliable —— 宁可 none 也不反标。"""
    from xrd_engine.high_ni.indexing import CellFit

    a, c = 2.917, 14.192  # 反序
    refs = allowed_reflections(a, c, LAM, (10.0, 100.0))
    fit = CellFit(
        a=a, c=c, zero_shift=0.0, fwhm_0=0.12, fwhm_slope=0.0032,
        reflections=refs,
    )
    fit.amplitudes = {r.hkl: 100.0 for r in refs}
    x = np.arange(10.0, 100.0, 0.02)
    # 观测峰放在正常胞的 (108)/(110) 理论位置上
    peaks = [
        _Peak(0, _tth(0, 1, 8, 2.884, 14.28)),
        _Peak(1, _tth(1, 1, 0, 2.884, 14.28)),
    ]
    result = assign_peaks(x, peaks, fit)
    assert all(r.confidence == "none" and r.label is None for r in result), (
        f"反序胞不得给标签:{[(r.position, r.label) for r in result]}"
    )


# ---------- 峰归属 ----------


def test_doublets_are_assigned_correctly():
    """(006)/(102) 与 (108)/(110) 两对双峰都要判对(文献显示名)。"""
    a, c = 2.884, 14.28
    x, y = _synth(a, c, TYPICAL, fwhm_0=0.10, slope=0.002)
    fit = refine_cell(x, y, wavelength=LAM)
    assert fit is not None

    targets = {
        _tth(0, 0, 6, a, c): "(006)",
        _tth(0, 1, 2, a, c): "(102)",
        _tth(0, 1, 8, a, c): "(108)",
        _tth(1, 1, 0, a, c): "(110)",
    }
    peaks = [_Peak(i, p) for i, p in enumerate(targets)]
    result = assign_peaks(x, peaks, fit)
    got = {round(r.position, 4): r.label for r in result}
    for pos, label in targets.items():
        assert got[round(pos, 4)] == label, f"{pos:.3f} 应为 {label},得 {got[round(pos,4)]}"


def test_006_not_swapped_with_102():
    """~38.3° 强分量对外标 (102);不得被标成 (006)。"""
    a, c = 2.884, 14.28
    x, y = _synth(a, c, TYPICAL)
    fit = refine_cell(x, y, wavelength=LAM)
    assert fit is not None
    pos = _tth(0, 1, 2, a, c)
    result = assign_peaks(x, [_Peak(0, pos)], fit)
    assert result[0].label == "(102)"
    # 内部消光指数仍是 (0,1,2)(拟合强度的键)
    assert result[0].hkl == (0, 1, 2)


def test_high_angle_weak_reflections_are_assigned():
    """高角弱反射 (015)/(107)/(113)/(116) 必须能被归属。

    旧参考表只到 (110),这些峰在构造上就不可能拿到晶面。
    """
    a, c = 2.884, 14.28
    x, y = _synth(a, c, TYPICAL, fwhm_0=0.10, slope=0.002)
    fit = refine_cell(x, y, wavelength=LAM)
    assert fit is not None

    expected = {
        (0, 1, 5): "(015)", (1, 0, 7): "(107)", (1, 1, 3): "(113)",
        (1, 1, 6): "(116)",
    }
    peaks = [_Peak(i, _tth(h, k, l, a, c)) for i, (h, k, l) in enumerate(expected)]
    result = assign_peaks(x, peaks, fit)
    for r, (hkl, label) in zip(result, expected.items(), strict=True):
        assert r.label == label, f"{hkl} 应为 {label},得 {r.label}"


def test_unmodelled_peak_gets_none():
    """不属于 R-3m 的峰(杂相/伪峰)应报 none,不硬套一个晶面。"""
    a, c = 2.884, 14.28
    x, y = _synth(a, c, TYPICAL)
    fit = refine_cell(x, y, wavelength=LAM)
    assert fit is not None
    # 20° 附近在 R-3m 下没有允许反射(低角区只有 (003)@18.6)
    result = assign_peaks(x, [_Peak(0, 25.5)], fit)
    assert result[0].label is None
    assert result[0].confidence == "none"


def test_merged_doublet_is_not_reported_with_high_confidence():
    """双峰合并成一个包时,归属不能给出"高置信 + 零偏移"的假象。

    这里不强制要求 ambiguous —— 一个 0.55° 宽的包在拟合上仍可能偏向
    较强的 (102),这在数学上是可以接受的。真正要防的是**把不确定当成
    确定**:归属若成立,offset 必须显著非零(说明峰位已偏离理论位置),
    或者候选列表里要能看到另一个分量。
    """
    a, c = 2.884, 14.28
    x, y = _synth(a, c, TYPICAL, fwhm_0=0.55, slope=0.0)
    fit = refine_cell(x, y, wavelength=LAM)
    assert fit is not None
    mid = 0.5 * (_tth(0, 0, 6, a, c) + _tth(0, 1, 2, a, c))
    result = assign_peaks(x, [_Peak(0, mid)], fit)

    if result[0].confidence == "reliable":
        # 允许判给 (102),但必须如实报告显著偏移与竞争候选
        assert abs(result[0].offset_deg) > 0.10, (
            "合并峰被判为可靠归属时,偏移量必须如实反映峰位偏离"
        )
        assert result[0].candidates, "应给出竞争候选供用户核对"
    else:
        assert result[0].confidence == "ambiguous"
        assert len(result[0].candidates) >= 2


def test_clean_doublet_has_small_offset():
    """双峰真正分开时,归属应干净:小偏移、无竞争候选。"""
    a, c = 2.884, 14.28
    x, y = _synth(a, c, TYPICAL, fwhm_0=0.10, slope=0.002)
    fit = refine_cell(x, y, wavelength=LAM)
    assert fit is not None
    pos = _tth(0, 1, 2, a, c)
    result = assign_peaks(x, [_Peak(0, pos)], fit)
    assert result[0].label == "(102)"
    assert result[0].confidence == "reliable"
    assert abs(result[0].offset_deg) < 0.06
    assert result[0].dominance > 0.6


def test_assignment_window_scales_with_fwhm():
    """归属窗口必须随峰宽缩放。

    固定 ±0.6° 时 (110) 的约 65% 面积会落进 (108) 的窗口,(108) 被
    永久判成 (110) —— 这正是实测中高角分量丢失的原因。
    """
    a, c = 2.884, 14.28
    x, y = _synth(a, c, TYPICAL, fwhm_0=0.10, slope=0.002)
    fit = refine_cell(x, y, wavelength=LAM)
    assert fit is not None
    pos_108 = _tth(0, 1, 8, a, c)
    result = assign_peaks(x, [_Peak(0, pos_108)], fit)
    assert result[0].label == "(108)"
    assert result[0].dominance > 0.5


def test_peak_position_offset_is_reported():
    """offset_deg 应如实给出观测峰相对理论位置的偏差,供审计。"""
    a, c = 2.884, 14.28
    x, y = _synth(a, c, TYPICAL)
    fit = refine_cell(x, y, wavelength=LAM)
    assert fit is not None
    pos = _tth(0, 0, 3, a, c) + 0.03
    result = assign_peaks(x, [_Peak(0, pos)], fit)
    assert result[0].label == "(003)"
    assert result[0].offset_deg is not None
    assert abs(result[0].offset_deg) < 0.15


# ---------- 精修缓存(切换数据集不卡顿的关键) ----------


def test_fit_cache_reuses_and_invalidates():
    """晶胞精修缓存:同数据集复用,数据变了必须重算。

    这条缓存是"多次切换图卡顿"的修复核心 —— 没有它,每切一次数据集
    就要重跑一次全谱精修(实测 12 秒)。但缓存必须按**数据指纹**失效,
    只按 dataset_id 缓存会在 id 复用 / 数据集被替换时拿到旧晶胞,
    导致标签静默错位。
    """
    import time

    from xrd_engine.peaks.annotate import (
        _FIT_CACHE,
        annotate_high_ni,
        invalidate_fit_cache,
    )

    _FIT_CACHE.clear()
    a, c = 2.884, 14.28
    x1, y1 = _synth(a, c, TYPICAL, seed=21)
    peaks = [_Peak(0, _tth(0, 0, 3, a, c))]

    t0 = time.perf_counter()
    r1, ctx1 = annotate_high_ni(x1, y1, peaks, wavelength=LAM, dataset_id=1)
    first = time.perf_counter() - t0
    assert ctx1 is not None

    t0 = time.perf_counter()
    r2, ctx2 = annotate_high_ni(x1, y1, peaks, wavelength=LAM, dataset_id=1)
    second = time.perf_counter() - t0
    assert ctx2 is not None
    assert ctx2.cell == ctx1.cell
    assert r2[0].hkl_label == r1[0].hkl_label
    # 第二次必须显著快(命中缓存),否则缓存形同虚设
    assert second < first / 5, f"缓存未生效:{first:.2f}s -> {second:.2f}s"

    # 换一条曲线但仍用同一个 dataset_id:指纹不同,必须重算而不是复用
    x2, y2 = _synth(2.860, 14.19, TYPICAL, seed=22)
    _r3, ctx3 = annotate_high_ni(x2, y2, peaks, wavelength=LAM, dataset_id=1)
    assert ctx3 is not None
    assert abs(ctx3.cell[1] - ctx1.cell[1]) > 0.02, "id 复用时应按指纹重算"

    # 删除数据集后显式失效
    invalidate_fit_cache(1)
    assert 1 not in _FIT_CACHE


def test_annotate_without_dataset_id_does_not_cache():
    """不传 dataset_id 时不缓存 —— 避免无归属的调用污染缓存。"""
    from xrd_engine.peaks.annotate import _FIT_CACHE, annotate_high_ni

    _FIT_CACHE.clear()
    a, c = 2.884, 14.28
    x, y = _synth(a, c, TYPICAL, seed=23)
    annotate_high_ni(x, y, [_Peak(0, _tth(0, 0, 3, a, c))], wavelength=LAM)
    assert not _FIT_CACHE
