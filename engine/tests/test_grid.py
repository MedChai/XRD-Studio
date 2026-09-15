"""网格检测与均匀化测试(§4.2)。

这一层的重要性在于:SciPy 的 savgol_filter 与 find_peaks 都假设等间距
网格。如果检测漏掉了非均匀数据,后续所有峰度量都会静默失真 —— 用户
不会看到报错,只会看到错的 FWHM。因此这里对判据边界测得很细。
"""

from __future__ import annotations

import numpy as np
import pytest

from xrd_engine.processing.grid import (
    UNIFORMITY_TOLERANCE,
    detect_grid,
    ensure_ascending,
    find_nearest_indices,
    resample_to_uniform,
)


class TestDetectGrid:
    def test_perfectly_uniform(self):
        x = np.arange(10.0, 80.0, 0.02)
        info = detect_grid(x)
        assert info.is_uniform
        assert info.step_ratio == pytest.approx(1.0)
        assert info.step == pytest.approx(0.02)
        assert not info.is_descending
        assert not info.has_duplicates

    def test_counts_and_range(self):
        x = np.linspace(10.0, 80.0, 3501)
        info = detect_grid(x)
        assert info.point_count == 3501
        assert info.x_min == pytest.approx(10.0)
        assert info.x_max == pytest.approx(80.0)

    def test_descending_detected(self):
        x = np.arange(80.0, 10.0, -0.02)
        info = detect_grid(x)
        assert info.is_descending

    def test_duplicates_detected(self):
        x = np.array([10.0, 10.0, 10.02, 10.04, 10.06])
        info = detect_grid(x)
        assert info.has_duplicates

    def test_all_points_identical(self):
        info = detect_grid(np.array([5.0, 5.0, 5.0]))
        assert info.has_duplicates
        assert info.step == 0.0
        # 全同点不算「非均匀」,因为无法定义步长;由 Validate 层按重复点处理
        assert info.is_uniform

    def test_single_point(self):
        info = detect_grid(np.array([42.0]))
        assert info.point_count == 1
        assert info.is_uniform

    def test_empty(self):
        info = detect_grid(np.array([]))
        assert info.point_count == 0

    def test_just_below_tolerance_is_uniform(self):
        """步长比刚好在容差内,应判为均匀。"""
        base = 0.02
        x = np.arange(0, 100) * base
        # 制造一个略大的步长,比值控制在容差内
        x = x.copy()
        x[-1] = x[-2] + base * (1.0 + UNIFORMITY_TOLERANCE * 0.5)
        info = detect_grid(x)
        assert info.is_uniform

    def test_just_above_tolerance_is_non_uniform(self):
        base = 0.02
        x = np.arange(0, 100) * base
        x = x.copy()
        x[-1] = x[-2] + base * (1.0 + UNIFORMITY_TOLERANCE * 10)
        info = detect_grid(x)
        assert not info.is_uniform

    def test_step_is_median_not_mean(self):
        """单个离群步长不应把代表性步长带偏,所以用中位数。"""
        x = np.arange(0, 100) * 0.02
        x = x.copy()
        x[50:] += 0.5  # 制造一个大跳跃
        info = detect_grid(x)
        assert info.step == pytest.approx(0.02)

    def test_descending_with_varying_step(self):
        """降序且非均匀 —— 真实仪器可能出现,两个标志都要置位。"""
        x = np.array([80.0, 79.9, 79.7, 79.6, 79.0])
        info = detect_grid(x)
        assert info.is_descending
        assert not info.is_uniform


class TestEnsureAscending:
    def test_ascending_passthrough(self):
        x = np.array([1.0, 2.0, 3.0])
        y = np.array([10.0, 20.0, 30.0])
        xo, yo = ensure_ascending(x, y)
        np.testing.assert_array_equal(xo, x)
        np.testing.assert_array_equal(yo, y)

    def test_descending_flipped(self):
        x = np.array([3.0, 2.0, 1.0])
        y = np.array([30.0, 20.0, 10.0])
        xo, yo = ensure_ascending(x, y)
        np.testing.assert_array_equal(xo, [1.0, 2.0, 3.0])
        np.testing.assert_array_equal(yo, [10.0, 20.0, 30.0])

    def test_returns_copies(self):
        """必须返回副本,否则下游就地修改会污染调用方的数组。"""
        x = np.array([1.0, 2.0, 3.0])
        y = np.array([1.0, 2.0, 3.0])
        xo, _yo = ensure_ascending(x, y)
        xo[0] = 99.0
        assert x[0] == 1.0

    def test_single_point(self):
        xo, _yo = ensure_ascending(np.array([5.0]), np.array([1.0]))
        assert xo[0] == 5.0


class TestResampleToUniform:
    def test_uniform_input_unchanged(self):
        x = np.linspace(10.0, 80.0, 1000)
        y = np.sin(x)
        xu, yu = resample_to_uniform(x, y)
        np.testing.assert_allclose(xu, x)
        np.testing.assert_allclose(yu, y)

    def test_preserves_point_count_by_default(self):
        """默认沿用原点数,使重采样前后数据规模一致。"""
        d = np.linspace(1.0, 5.0, 500)
        x = np.degrees(2.0 * np.arcsin(np.clip(1.5406 / (2.0 * d), -1.0, 1.0)))
        order = np.argsort(x)
        x, y = x[order], np.sin(x[order])
        xu, _yu = resample_to_uniform(x, y)
        assert xu.size == 500

    def test_output_is_uniform(self):
        # 等 d 扫描 → 非均匀 2θ
        d = np.linspace(1.0, 5.0, 800)
        x = np.degrees(2.0 * np.arcsin(np.clip(1.5406 / (2.0 * d), -1.0, 1.0)))
        y = np.sin(x)
        order = np.argsort(x)
        xu, _yu = resample_to_uniform(x[order], y[order])
        info = detect_grid(xu)
        assert info.is_uniform

    def test_explicit_point_count(self):
        d = np.linspace(1.0, 5.0, 500)
        x = np.degrees(2.0 * np.arcsin(np.clip(1.5406 / (2.0 * d), -1.0, 1.0)))
        order = np.argsort(x)
        x, y = x[order], np.sin(x[order])
        xu, _yu = resample_to_uniform(x, y, point_count=1000)
        assert xu.size == 1000

    def test_endpoints_preserved(self):
        """重采样不能改变数据的角度覆盖范围。"""
        d = np.linspace(1.0, 5.0, 300)
        x = np.degrees(2.0 * np.arcsin(np.clip(1.5406 / (2.0 * d), -1.0, 1.0)))
        order = np.argsort(x)
        x, y = x[order], np.sin(x[order])
        xu, _yu = resample_to_uniform(x, y)
        assert xu[0] == pytest.approx(x[0])
        assert xu[-1] == pytest.approx(x[-1])

    def test_linear_interp_accuracy_on_linear_signal(self):
        """线性信号上线性插值必须无误差 —— 若用了样条这里会有过冲。"""
        d = np.linspace(1.0, 5.0, 400)
        x = np.degrees(2.0 * np.arcsin(np.clip(1.5406 / (2.0 * d), -1.0, 1.0)))
        order = np.argsort(x)
        x = x[order]
        y = 3.0 * x + 7.0  # 严格线性
        xu, yu = resample_to_uniform(x, y)
        np.testing.assert_allclose(yu, 3.0 * xu + 7.0, rtol=1e-9)

    def test_descending_input_handled(self):
        x = np.linspace(80.0, 10.0, 400)
        y = np.sin(x)
        xu, _yu = resample_to_uniform(x, y)
        assert xu[0] < xu[-1]
        assert xu.size == 400

    def test_no_overshoot_on_peak(self):
        """核心回归:重采样后峰高不得超出原始数据的最大值。

        样条插值会在峰顶过冲,直接污染随后要测的 intensity 与 FWHM。
        """
        d = np.linspace(0.9, 8.0, 2000)
        x = np.degrees(2.0 * np.arcsin(np.clip(1.5406 / (2.0 * d), -1.0, 1.0)))
        order = np.argsort(x)
        x = x[order]
        # 一个尖峰
        y = np.exp(-((x - 34.39) ** 2) / (2 * 0.05**2))
        _xu, yu = resample_to_uniform(x, y)
        assert yu.max() <= y.max() + 1e-12
        assert yu.min() >= y.min() - 1e-12

    def test_2d_input_raveled(self):
        """列向量输入不应导致形状错误。"""
        x = np.linspace(10.0, 80.0, 100).reshape(-1, 1)
        y = np.ones((100, 1))
        xu, yu = resample_to_uniform(x, y)
        assert xu.ndim == 1
        assert yu.ndim == 1


class TestFindNearestIndices:
    def test_exact_hits(self):
        src = np.array([1.0, 2.0, 3.0, 4.0])
        idx = find_nearest_indices(src, np.array([2.0, 4.0]))
        np.testing.assert_array_equal(idx, [1, 3])

    def test_rounds_to_nearest(self):
        src = np.array([0.0, 1.0, 2.0])
        # 1.4 更接近 1.0(下标 1)
        idx = find_nearest_indices(src, np.array([1.4]))
        assert idx[0] == 1
        # 1.6 更接近 2.0(下标 2)
        idx = find_nearest_indices(src, np.array([1.6]))
        assert idx[0] == 2

    def test_below_range_clamped(self):
        src = np.array([10.0, 20.0, 30.0])
        idx = find_nearest_indices(src, np.array([-100.0]))
        assert idx[0] == 0

    def test_above_range_clamped(self):
        src = np.array([10.0, 20.0, 30.0])
        idx = find_nearest_indices(src, np.array([999.0]))
        assert idx[0] == 2

    def test_vectorized(self):
        src = np.linspace(0.0, 10.0, 101)
        query = np.array([0.0, 5.0, 10.0])
        idx = find_nearest_indices(src, query)
        np.testing.assert_array_equal(idx, [0, 50, 100])

    def test_scalar_query_returns_array(self):
        src = np.array([1.0, 2.0, 3.0])
        idx = find_nearest_indices(src, 2.5)
        assert isinstance(idx, np.ndarray)
        assert idx.size == 1
