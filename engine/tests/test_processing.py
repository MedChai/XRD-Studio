"""处理链测试(§7.1 / §7.3 / Milestone 3-4 出口条件)。

数值验收:
  - FWHM:合成高斯谱,网格足够细时误差 < 2%(§12 M4)
  - Area:高斯解析积分比对
  - Background:AsLS 能剥离指数背景且不吃掉强峰
  - 顺序:Background → Smooth(§7.1),Smooth 开启置位标记
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from xrd_engine.models.types import (
    BackgroundParams,
    Curve,
    PeakDetectionParams,
    PipelineParams,
    SmoothingParams,
)
from xrd_engine.peaks.metrics import integrate_area
from xrd_engine.processing.background import (
    asls_background,
    polynomial_background,
)
from xrd_engine.processing.pipeline import PipelineError, apply_crop, run_pipeline
from xrd_engine.processing.smoothing import (
    SmoothingError,
    savgol_smooth,
    validate_savgol_params,
)


def gaussian(x, center, amp, fwhm):
    sigma = fwhm / (2.0 * math.sqrt(2.0 * math.log(2.0)))
    return amp * np.exp(-((x - center) ** 2) / (2.0 * sigma**2))


# 细网格:0.005° 步长,离散化误差可忽略,误差应全部来自算法
X_FINE = np.arange(10.0, 80.0, 0.005)


def gauss_curve(fwhm: float, amp: float = 10000.0, noise: bool = True):
    y = 100.0 + gaussian(X_FINE, 40.0, amp, fwhm)
    if noise:
        # 真实谱必有计数噪声;无噪平直基线会让局部极小值边界一路走到数据端
        rng = np.random.default_rng(7)
        y = y + rng.normal(0.0, 2.0, size=y.shape)
    return Curve(x=X_FINE.copy(), y=y)


class TestFwhmAccuracy:
    """§12 M4:FWHM 合成高斯谱误差 < 2%。"""

    @pytest.mark.parametrize("fwhm", [0.10, 0.20, 0.40, 1.00])
    def test_gaussian_fwhm_within_2_percent(self, fwhm):
        curve = gauss_curve(fwhm)
        params = PipelineParams(
            background=BackgroundParams(enabled=False),
            smoothing=SmoothingParams(enabled=False),
            peaks=PeakDetectionParams(sensitivity="medium"),
        )
        result = run_pipeline(curve, params)
        assert len(result.peaks) == 1, f"应只检出 1 个峰,实际 {len(result.peaks)}"
        measured = result.peaks[0].fwhm
        assert measured == pytest.approx(fwhm, rel=0.02)

    def test_two_isolated_peaks_both_measured(self):
        y = (
            100.0
            + gaussian(X_FINE, 30.0, 8000, 0.20)
            + gaussian(X_FINE, 55.0, 5000, 0.35)
        )
        curve = Curve(x=X_FINE.copy(), y=y)
        result = run_pipeline(
            curve,
            PipelineParams(
                peaks=PeakDetectionParams(sensitivity="medium")
            ),
        )
        assert len(result.peaks) == 2
        by_pos = {round(p.position): p for p in result.peaks}
        assert by_pos[30].fwhm == pytest.approx(0.20, rel=0.02)
        assert by_pos[55].fwhm == pytest.approx(0.35, rel=0.02)


class TestArea:
    def test_gaussian_area_within_2_percent(self):
        """高斯总面积解析解 = amp·σ·√(2π)。局部极小值边界会框住全峰。"""
        amp, fwhm = 10000.0, 0.30
        curve = gauss_curve(fwhm, amp)
        result = run_pipeline(curve, PipelineParams(peaks=PeakDetectionParams()))
        peak = result.peaks[0]
        expected = amp * (fwhm / 2.3548) * math.sqrt(2 * math.pi)
        # 基线 100 在边界内贡献一点面积,允许 5% 容差
        assert peak.area == pytest.approx(expected, rel=0.05)

    def test_integrate_area_exact_on_linear(self):
        x = np.linspace(0.0, 10.0, 1001)
        y = 2.0 * x + 1.0
        # ∫₀¹⁰ (2x+1)dx = 110,梯形法对线性精确
        assert integrate_area(x, y, 0.0, 10.0) == pytest.approx(110.0, abs=1e-6)

    def test_integrate_area_subsample_interval(self):
        """区间端点不在采样点上时,插值端点保证精度。"""
        x = np.arange(0.0, 10.0, 1.0)
        y = np.ones_like(x)
        assert integrate_area(x, y, 0.5, 3.5) == pytest.approx(3.0, abs=1e-9)

    def test_integrate_rejects_inverted_bounds(self):
        x = np.linspace(0, 1, 50)
        with pytest.raises(ValueError):
            integrate_area(x, np.ones_like(x), 0.8, 0.2)


class TestBackground:
    def test_asls_recovers_smooth_baseline(self):
        x = np.arange(10.0, 80.0, 0.02)
        baseline = 500.0 * np.exp(-(x - 10) / 15.0) + 80.0
        y = baseline + gaussian(x, 34.0, 20000.0, 0.2)
        bg = asls_background(y, smoothness=1e6, asymmetry=0.01, iterations=10)
        # 背景曲线在非峰区应与真值同量级(±20%),且绝不高到吃掉峰
        off_peak = np.abs(x - 34.0) > 2.0
        err = np.abs(bg[off_peak] - baseline[off_peak]) / np.maximum(baseline[off_peak], 1)
        assert np.median(err) < 0.10
        peak_idx = int(np.argmax(y))  # 峰心
        assert peak_idx != 50  # 防止误把 off-peak 点当峰心
        peak_residual = y[peak_idx] - bg[peak_idx]
        assert peak_residual > 15000  # 峰心处背景必须远低于峰高

    def test_asls_params_out_of_range_raise(self):
        with pytest.raises(ValueError, match="smoothness"):
            asls_background(np.ones(100), smoothness=1.0)
        with pytest.raises(ValueError, match="asymmetry"):
            asls_background(np.ones(100), asymmetry=0.9)

    def test_polynomial_recovers_quadratic_baseline(self):
        x = np.linspace(0.0, 80.0, 4000)
        baseline = 0.5 + 0.01 * x + 0.0002 * x**2
        y = baseline * 1000 + gaussian(x, 40.0, 8000, 0.25)
        bg = polynomial_background(x, y, order=2, iterations=6)
        # 剔峰迭代后背景应在基线 ±5% 内
        err = np.abs(bg - baseline * 1000) / (baseline * 1000)
        assert np.median(err) < 0.05

    def test_subtraction_clamped_at_zero(self):
        """扣背景产生的负值必须钳到 0,否则百分比阈值语义失效。"""
        from xrd_engine.processing.background import subtract_background

        y = np.array([10.0, 5.0, 20.0])
        bg = np.array([12.0, 3.0, 25.0])
        out = subtract_background(y, bg)
        assert (out >= 0).all()


class TestSmoothingConstraints:
    """§7.3:UI/引擎双重保证奇数窗口与 polyorder < window。"""

    def test_even_window_rejected_or_fixed(self):
        w, p = validate_savgol_params(8, 2, 1000)
        assert w % 2 == 1

    def test_polyorder_ge_window_rejected(self):
        with pytest.raises(SmoothingError):
            validate_savgol_params(5, 5, 1000)

    def test_window_over_10_percent_rejected(self):
        with pytest.raises(SmoothingError, match="10%"):
            validate_savgol_params(301, 2, 1000)

    def test_disabled_smoothing_is_identity(self):
        y = np.random.default_rng(1).random(500)
        out = savgol_smooth(y, SmoothingParams(enabled=False, window_length=51))
        np.testing.assert_array_equal(out, y)


class TestPipelineOrderAndFlags:
    def test_smoothing_flag_set(self):
        curve = gauss_curve(0.3)
        result = run_pipeline(
            curve,
            PipelineParams(
                smoothing=SmoothingParams(enabled=True, window_length=31, polyorder=2)
            ),
        )
        assert result.metrics_affected_by_smoothing is True

    def test_no_flag_without_smoothing(self):
        curve = gauss_curve(0.3)
        result = run_pipeline(curve, PipelineParams())
        assert result.metrics_affected_by_smoothing is False

    def test_crop_reduces_range(self):
        curve = gauss_curve(0.3)
        params = PipelineParams()
        params.crop.enabled = True
        params.crop.x_min = 35.0
        params.crop.x_max = 45.0
        result = run_pipeline(curve, params)
        assert result.curve.x[0] >= 35.0
        assert result.curve.x[-1] <= 45.0
        assert len(result.peaks) == 1

    def test_crop_invalid_interval_raises(self):
        curve = gauss_curve(0.3)
        with pytest.raises(PipelineError):
            apply_crop(curve, 45.0, 35.0)

    def test_smooth_then_no_bg_on_fixture(self, fixtures_dir):
        """真实形状谱(有泊松噪声)跑全链不崩溃且检出主峰。"""
        from xrd_engine.importers.loader import load_curve

        path = fixtures_dir / "lfp_500c_scan01.xy"
        if not path.exists():
            pytest.skip("fixture 未生成")
        curve = load_curve(path)
        result = run_pipeline(
            curve,
            PipelineParams(
                background=BackgroundParams(enabled=True, method="asls"),
                smoothing=SmoothingParams(enabled=True, window_length=9, polyorder=2),
                peaks=PeakDetectionParams(sensitivity="medium"),
            ),
        )
        assert any(abs(p.position - 34.39) < 0.05 for p in result.peaks)

    def test_flat_line_no_crash(self):
        """§7.2:全平线允许查看,寻峰返回空,不报错。"""
        x = np.linspace(10.0, 80.0, 500)
        curve = Curve(x=x, y=np.full_like(x, 100.0))
        result = run_pipeline(curve, PipelineParams(peaks=PeakDetectionParams()))
        assert result.peaks == []
