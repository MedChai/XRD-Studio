"""High-Ni layered cathode screening tests."""

from __future__ import annotations

import numpy as np

from xrd_engine.high_ni import analyze_high_ni, default_profile
from xrd_engine.models.types import Curve

# 合成谱用的晶胞与波长 —— 峰位由晶体学反算,不手写数字。
# 手写位置的旧版本把 (006)/(102) 放在 38.25/39.05(间距 0.80°),
# 而真实 R-3m 高镍只有约 0.29°;(108)/(110) 同理(旧 0.90° vs 真实 0.49°)。
# 那种"把双峰拉得很开"的合成谱会让双峰拟合看起来很容易,
# 掩盖了真实数据上分辨不出的问题。
SYNTH_A, SYNTH_C, SYNTH_LAM = 2.878, 14.240, 1.5418


def _tth(h: int, k: int, l: int) -> float:
    import math

    inv = (4.0 / 3.0) * (h * h + h * k + k * k) / (SYNTH_A * SYNTH_A) + (
        l * l
    ) / (SYNTH_C * SYNTH_C)
    d = 1.0 / math.sqrt(inv)
    return math.degrees(2.0 * math.asin(SYNTH_LAM / (2.0 * d)))


def synthetic_layered_curve(reduce_003: float = 1.0, split: bool = True) -> Curve:
    x = np.arange(10.0, 80.0, 0.01)
    y = 120.0 + 0.5 * (x - 10.0)

    def add(center: float, amplitude: float, width: float = 0.18) -> None:
        sigma = width / 2.354820045
        nonlocal y
        y = y + amplitude * np.exp(-0.5 * ((x - center) / sigma) ** 2)

    add(18.60, 5200.0 * reduce_003, 0.24)
    add(_tth(1, 0, 1), 2500.0, 0.25)
    add(_tth(1, 0, 4), 3900.0, 0.28)
    if split:
        # (006) 弱、102 分量强 —— 与层状正极实验一致
        add(_tth(0, 0, 6), 300.0, 0.16)
        add(_tth(0, 1, 2), 900.0, 0.16)
        add(_tth(0, 1, 8), 720.0, 0.20)
        add(_tth(1, 1, 0), 900.0, 0.20)
    else:
        # 合并成一个包:中心取两分量中点,宽度盖过间距
        add(0.5 * (_tth(0, 0, 6) + _tth(0, 1, 2)), 1500.0, 0.55)
        add(0.5 * (_tth(0, 1, 8) + _tth(1, 1, 0)), 1400.0, 0.65)
    return Curve(x=x, y=y)


def test_lower_003_intensity_increases_mixing_risk():
    profile = default_profile()
    normal = analyze_high_ni(synthetic_layered_curve(), profile)
    reduced = analyze_high_ni(synthetic_layered_curve(0.55), profile)
    normal_ratio = normal["mixing"]["i003_i104_area_ratio"]
    reduced_ratio = reduced["mixing"]["i003_i104_area_ratio"]
    assert normal_ratio > reduced_ratio
    assert reduced["mixing"]["risk"] in {"medium", "high"}


def test_clear_and_unresolved_split_are_distinguished():
    profile = default_profile()
    clear = analyze_high_ni(synthetic_layered_curve(split=True), profile)
    unresolved = analyze_high_ni(synthetic_layered_curve(split=False), profile)
    assert clear["layered"]["split006_102"]["splitStatus"] in {"clear", "weak"}
    assert unresolved["layered"]["split006_102"]["splitStatus"] in {
        "weak", "unresolved"
    }
    assert clear["layered"]["split006_102"]["fitImprovement"] is not None


def test_lattice_fit_is_reproducible_and_has_traceability():
    curve = synthetic_layered_curve()
    first = analyze_high_ni(
        curve,
        default_profile(),
        dataset_id=12,
        profile_id=3,
        source_hash="abc123",
    )
    second = analyze_high_ni(curve, default_profile())
    assert first["analysisVersion"] == "high-ni-0.1"
    assert first["datasetId"] == 12
    assert first["profileId"] == 3
    assert first["sourceHash"] == "abc123"
    assert first["lattice"]["a"] == second["lattice"]["a"]
    assert first["lattice"]["c"] == second["lattice"]["c"]
    assert first["lattice"]["a"] is not None
    assert first["lattice"]["c"] is not None


def test_mixing_area_uses_peak_body_bounds():
    """缺陷 1:Area(003)/Area(104) 必须用峰本体边界,不是整窗积分。

    合成曲线里 003 在 18.60°,峰本体边界应在 FWHM 附近(约 ±0.3°),
    而不是退化到整窗(16.4–20.8°)。
    """
    result = analyze_high_ni(synthetic_layered_curve(), default_profile())
    obs003 = result["peaks"]["003"]
    obs104 = result["peaks"]["104"]
    assert obs003["left_bound"] is not None
    assert obs003["right_bound"] is not None
    body_width = obs003["right_bound"] - obs003["left_bound"]
    # 峰本体宽度应为 FWHM 量级(合成 003 FWHM≈0.26°),远小于整窗 4.4°
    assert body_width < 2.0
    assert obs104["left_bound"] is not None
    # 面积比量级应与峰高比量级一致(不因宽度差而严重偏离)
    m = result["mixing"]
    assert m["i003_i104_area_ratio"] is not None
    assert m["i003_i104_height_ratio"] is not None
    # 面积比不应比高度比小一半(整窗积分会引入不可解释的压制)
    assert m["i003_i104_area_ratio"] > 0.5 * m["i003_i104_height_ratio"]


def test_mixing_risk_grades_on_height_ratio():
    """缺陷 1(分级):清晰分层(reduce_003=1.0,高度比≈1.33)不应判成 high。

    经典约定 I(003)/I(104)>1.2 表示良好层状;若按整窗面积分级,
    宽度差会把 1.33 压到 1.14 而误判 high。
    """
    result = analyze_high_ni(synthetic_layered_curve(1.0), default_profile())
    m = result["mixing"]
    assert m["i003_i104_height_ratio"] > 1.20
    assert m["risk"] in {"low", "medium"}  # 不再误判 high


def test_mixing_risk_rises_when_003_suppressed():
    """003 强度被压(reduce_003=0.55)时风险应升高到 medium/high。"""
    result = analyze_high_ni(synthetic_layered_curve(0.55), default_profile())
    m = result["mixing"]
    assert m["i003_i104_height_ratio"] < 0.9
    assert m["risk"] in {"medium", "high"}


def test_r_order_reported_only_when_006_102_split():
    """缺陷 2:R_order 只应在 006/102 真正分裂时报告;未分裂返回 None。"""
    prof = default_profile()
    split = analyze_high_ni(synthetic_layered_curve(split=True), prof)
    unresolved = analyze_high_ni(synthetic_layered_curve(split=False), prof)
    # split=True 时 006/102 明确分裂,splitStatus 不应为 unresolved
    assert split["peaks"]["006_102"]["splitStatus"] not in {"unresolved", "not_found"}
    assert split["mixing"]["r_order"] is not None
    # split=False 时 006/102 合并为单峰,splitStatus 为 unresolved → r_order 应为 None
    assert unresolved["peaks"]["006_102"]["splitStatus"] in {"unresolved", "not_found"}
    assert unresolved["mixing"]["r_order"] is None


def test_mixing_exposes_area_bounds():
    """UI 需要 003/104/101 的积分边界来在图上叠加范围框。"""
    result = analyze_high_ni(synthetic_layered_curve(), default_profile())
    bounds = result["mixing"]["peaks_area_bounds"]
    assert set(bounds) == {"003", "104", "101"}
    assert bounds["003"] is not None
    assert bounds["104"] is not None
    assert len(bounds["003"]) == 2
    assert bounds["003"][0] < bounds["003"][1]


def test_pair_naming_uses_refined_cell_not_typical():
    """双峰配名必须接受精修胞;写死胞在无钴端元上会偏约 0.1°。

    用 102 分量理论位置做判据:精修胞 2.884/14.278 与典型 2.878/14.216
    算出的位置相差 >0.05°,观测峰落在两者之间时配名应跟随精修值。
    """
    from xrd_engine.high_ni.analysis import (
        _pair_positions_by_label,
        _pair_theoretical_positions,
    )

    typical = _pair_theoretical_positions(("006", "102"), 1.540593)
    refined = _pair_theoretical_positions(
        ("006", "102"), 1.540593, cell=(2.884, 14.278)
    )
    assert abs(refined["102"] - typical["102"]) > 0.05, (
        "两套晶胞的 102 位置应可区分,否则该测试没有分辨力"
    )
    # 观测峰放在精修胞的 102 上:必须配到 102
    named = _pair_positions_by_label(
        ("006", "102"), [refined["102"]], 1.540593, cell=(2.884, 14.278)
    )
    assert "102" in named
    # 同一观测峰用典型胞配:仍按典型理论位置就近 —— 行为由 cell 参数决定
    named_typical = _pair_positions_by_label(
        ("006", "102"), [typical["102"]], 1.540593
    )
    assert "102" in named_typical


def test_analyze_high_ni_accepts_cell_argument():
    """analyze_high_ni 新增 cell 参数不破坏默认路径(无 cell 回退典型胞)。"""
    result = analyze_high_ni(synthetic_layered_curve(), default_profile())
    assert result["domain"] == "high_ni_layered"
    with_cell = analyze_high_ni(
        synthetic_layered_curve(), default_profile(), cell=(2.878, 14.240)
    )
    assert with_cell["domain"] == "high_ni_layered"
