"""峰值保留型曲线降采样。

降采样只用于显示，不参与背景、寻峰、FWHM 或 Area 计算。每个 bucket
保留 y 最小点和最大点，避免简单抽点把窄峰或背景转折直接丢掉。
"""

from __future__ import annotations

from itertools import pairwise

import numpy as np


def _window_indices(x: np.ndarray, x_min: float | None, x_max: float | None) -> np.ndarray:
    """返回升序 x 数组中落在显示范围内的原始下标。"""

    n = x.size
    lo = 0 if x_min is None else int(np.searchsorted(x, x_min, side="left"))
    hi = n if x_max is None else int(np.searchsorted(x, x_max, side="right"))
    lo = max(0, min(lo, n))
    hi = max(lo, min(hi, n))
    return np.arange(lo, hi, dtype=np.int64)


def select_minmax_indices(
    x: np.ndarray,
    y: np.ndarray,
    max_points: int | None = None,
    x_min: float | None = None,
    x_max: float | None = None,
) -> np.ndarray:
    """选择显示用下标，保持 x 顺序并尽量保留局部极值。

    返回值始终是原始数组下标，因此同一组下标可以安全地应用到 y、背景
    和其他与曲线对齐的数组。max_points 只限制返回点数，不改变分析数据。
    """

    x_arr = np.asarray(x, dtype=np.float64).ravel()
    y_arr = np.asarray(y, dtype=np.float64).ravel()
    if x_arr.size != y_arr.size:
        raise ValueError("x 与 y 长度不一致")
    if x_arr.size == 0:
        return np.empty(0, dtype=np.int64)

    window = _window_indices(x_arr, x_min, x_max)
    if window.size == 0:
        return window
    if max_points is None or max_points <= 0 or window.size <= max_points:
        return window
    if max_points < 3:
        return window[np.linspace(0, window.size - 1, max_points, dtype=np.int64)]

    # 端点固定，核心区域按 bucket 取 min/max。bucket 数控制最终点数，
    # 后面的 unique 会处理 min/max 落在同一个位置的情况。
    first = int(window[0])
    last = int(window[-1])
    core = window[1:-1]
    bucket_count = max(1, (max_points - 2) // 2)
    edges = np.linspace(0, core.size, bucket_count + 1, dtype=np.int64)
    selected: list[int] = [first]

    for start, end in pairwise(edges):
        bucket = core[int(start) : int(end)]
        if bucket.size == 0:
            continue
        values = y_arr[bucket]
        selected.extend((int(bucket[int(np.argmin(values))]), int(bucket[int(np.argmax(values))])))

    selected.append(last)
    result = np.asarray(sorted(set(selected)), dtype=np.int64)

    # 极端情况下浮点重复或很短 bucket 可能少于目标点；超过目标时均匀
    # 从候选中压缩，但始终保留首尾。
    if result.size > max_points:
        middle_count = max_points - 2
        middle = result[1:-1]
        chosen = middle[np.linspace(0, middle.size - 1, middle_count, dtype=np.int64)]
        result = np.concatenate(([first], chosen, [last]))
    return result
