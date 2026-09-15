"""坐标换算测试。

§4.1 要求用 NIST Si 标准值校验。这里用两个独立来源交叉验证:

  1. NIST SRM 640 系列 Si 的认证晶格常数 a = 5.4311946 Å,
     由此算出的 (111) 反射 d = a/√3 = 3.135621 Å
  2. 常规 Cu Kα1 下的 2θ 应落在 28.44° 附近

若换算公式写错(例如把 2θ 当 θ 用,或漏掉 4π/2π),这些断言会立刻失败。
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from xrd_engine.coords import (
    DEFAULT_WAVELENGTH,
    axis_display_decimals,
    axis_label,
    convert,
    d_to_q,
    d_to_two_theta,
    q_to_d,
    q_to_two_theta,
    two_theta_to_d,
    two_theta_to_q,
)

CU_KA1 = 1.5405980

# NIST SRM 640f Si 认证晶格常数与由它推出的 (111) 面间距
SI_A = 5.4311946
SI_D_111 = SI_A / math.sqrt(3.0)  # 3.135621 Å


class TestNistSilicon:
    """用 NIST Si 标准物质校验换算链。"""

    def test_d_111_from_lattice_constant(self):
        d = two_theta_to_d(28.4427, CU_KA1)
        assert d == pytest.approx(SI_D_111, abs=2e-4)

    def test_two_theta_from_d(self):
        # Cu Kα1(λ=1.540598)下 d=3.135621 的 (111) 反射 2θ = 28.4410°。
        # 注意 NIST 证书上常见的 28.4427° 是 Cu Kα 加权平均波长(1.5418)
        # 下的值,不要混用 —— 混用会产生 0.0017° 的假失败。
        tt = d_to_two_theta(SI_D_111, CU_KA1)
        assert tt == pytest.approx(28.4410, abs=1e-3)

    def test_q_of_si_111(self):
        # 必须用精确角(由 d 反推),不能喂舍入角 —— 否则与 2π/d 相比
        # 会有 1e-4 量级的假差异。
        tt_exact = d_to_two_theta(SI_D_111, CU_KA1)
        q = two_theta_to_q(tt_exact, CU_KA1)
        assert q == pytest.approx(2.0 * math.pi / SI_D_111, abs=1e-9)

    def test_roundtrip_two_theta_d(self):
        angles = np.linspace(10.0, 120.0, 500)
        back = d_to_two_theta(two_theta_to_d(angles, CU_KA1), CU_KA1)
        np.testing.assert_allclose(back, angles, atol=1e-9)

    def test_roundtrip_two_theta_q(self):
        angles = np.linspace(5.0, 150.0, 500)
        back = q_to_two_theta(two_theta_to_q(angles, CU_KA1), CU_KA1)
        np.testing.assert_allclose(back, angles, atol=1e-9)

    def test_roundtrip_d_q(self):
        d = np.linspace(0.5, 10.0, 200)
        np.testing.assert_allclose(q_to_d(d_to_q(d)), d, rtol=1e-12)


class TestAxisConversion:
    def test_convert_identity_returns_copy(self):
        x = np.array([1.0, 2.0, 3.0])
        out = convert(x, "two_theta", "two_theta")
        np.testing.assert_array_equal(out, x)
        # 必须是副本,改动输出不能影响输入
        out[0] = 999.0
        assert x[0] == 1.0

    def test_convert_generic_raises(self):
        with pytest.raises(ValueError, match="generic"):
            convert(np.array([1.0]), "two_theta", "generic")
        with pytest.raises(ValueError, match="generic"):
            convert(np.array([1.0]), "generic", "two_theta")

    def test_convert_chain_consistency(self):
        """convert() 的任意路径必须与单步函数结果一致。"""
        x = np.linspace(10.0, 80.0, 100)
        np.testing.assert_allclose(
            convert(x, "two_theta", "d", CU_KA1),
            two_theta_to_d(x, CU_KA1),
        )
        np.testing.assert_allclose(
            convert(x, "d", "two_theta", CU_KA1),
            d_to_two_theta(x, CU_KA1),
        )
        np.testing.assert_allclose(
            convert(x, "q", "two_theta", CU_KA1),
            q_to_two_theta(x, CU_KA1),
        )

    def test_scalar_and_array_agree(self):
        assert isinstance(two_theta_to_d(28.4427, CU_KA1), float)
        arr = two_theta_to_d(np.array([28.4427]), CU_KA1)
        assert isinstance(arr, np.ndarray)
        assert arr[0] == pytest.approx(two_theta_to_d(28.4427, CU_KA1))


class TestWavelengthDependence:
    def test_doubling_wavelength_doubles_d(self):
        """d = λ / (2 sinθ),同角度下 d 与 λ 成正比。"""
        a = two_theta_to_d(30.0, 1.0)
        b = two_theta_to_d(30.0, 2.0)
        assert b == pytest.approx(2.0 * a)

    def test_wavelength_units_cancel_in_d_to_q(self):
        """d → Q 不含波长,Q 只由 d 决定。"""
        assert d_to_q(3.135621) == pytest.approx(2.0 * math.pi / 3.135621)

    def test_high_angle_d_approaches_zero(self):
        d = two_theta_to_d(179.9, CU_KA1)
        assert d < 0.78  # 2θ→180 时 d→λ/2 = 0.770
        assert d == pytest.approx(CU_KA1 / 2.0, rel=1e-5)


class TestEdgeCases:
    def test_two_theta_zero_gives_large_d(self):
        """2θ=0 时 sinθ=0,d 发散,返回 inf 而不是抛异常。"""
        d = two_theta_to_d(0.0, CU_KA1)
        assert math.isinf(d)

    def test_impossible_reflection_returns_nan(self):
        """d < λ/2 的反射物理上不可观测,返回 NaN 而不是抛异常。"""
        assert math.isnan(d_to_two_theta(0.5, CU_KA1))
        result = d_to_two_theta(np.array([0.5, 3.0]), CU_KA1)
        assert math.isnan(result[0])
        assert np.isfinite(result[1])

    def test_q_beyond_range_returns_nan(self):
        assert math.isnan(q_to_two_theta(1e6, CU_KA1))

    def test_negative_two_theta(self):
        """负 2θ 在数学上有值(sin 为负),由上层按范围过滤。"""
        d = two_theta_to_d(-10.0, CU_KA1)
        assert d < 0


class TestAxisMetadata:
    def test_labels_are_unicode_correct(self):
        assert axis_label("two_theta") == "2θ (°)"
        assert axis_label("d") == "d (Å)"
        assert axis_label("q") == "Q (Å⁻¹)"

    def test_decimals_match_spec(self):
        """§4.4 规定:2θ 3 位小数,d 4 位,q 4 位。"""
        assert axis_display_decimals("two_theta") == 3
        assert axis_display_decimals("d") == 4
        assert axis_display_decimals("q") == 4

    def test_default_wavelength_is_cu_ka(self):
        assert pytest.approx(1.5418) == DEFAULT_WAVELENGTH
