"""显示降采样测试。

降采样不应改变引擎里的分析结果，只负责返回更小且仍保留局部极值的
曲线供前端绘图。
"""

from __future__ import annotations

import numpy as np
import pytest

from xrd_engine.processing.downsample import select_minmax_indices


def test_small_curve_is_returned_unchanged():
    x = np.arange(5.0)
    y = np.array([1.0, 4.0, 2.0, 3.0, 0.0])
    indices = select_minmax_indices(x, y, max_points=10)
    np.testing.assert_array_equal(indices, np.arange(5))


def test_window_preserves_range_endpoints_and_peak_extremes():
    x = np.linspace(0.0, 100.0, 1001)
    y = np.sin(x / 7.0)
    y[503] = 20.0
    y[701] = -20.0

    indices = select_minmax_indices(x, y, max_points=100, x_min=20.0, x_max=80.0)

    assert len(indices) <= 100
    assert x[indices[0]] == pytest.approx(20.0)
    assert x[indices[-1]] == pytest.approx(80.0)
    assert 503 in indices
    assert 701 in indices


def test_output_indices_are_sorted_and_unique():
    x = np.linspace(0.0, 10.0, 1001)
    y = np.cos(x)
    indices = select_minmax_indices(x, y, max_points=37)
    assert np.all(np.diff(indices) > 0)
    assert len(indices) == len(set(indices.tolist()))


def test_invalid_curve_lengths_are_rejected():
    with pytest.raises(ValueError, match="长度不一致"):
        select_minmax_indices(np.arange(4), np.arange(3), max_points=3)


def test_empty_window_returns_empty_indices():
    x = np.arange(5.0)
    y = np.arange(5.0)
    indices = select_minmax_indices(x, y, x_min=8.0, x_max=9.0)
    assert indices.size == 0
