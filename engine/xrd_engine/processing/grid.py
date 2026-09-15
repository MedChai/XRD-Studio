"""数据网格检测与均匀化。

SciPy 的 savgol_filter / find_peaks 都假设等间距网格。真实数据未必满足:

  - 等 d 扫描(而不是等 2θ)会给出非均匀的 2θ 网格
  - 多段拼接、仪器丢点、导出手工编辑都会破坏等间距
  - 同步辐射数据常直接给 d 或 Q 轴,转换成 2θ 后同样非均匀

本模块负责:
  1. 检测网格是否均匀(§4.2 的 1e-3 判据)
  2. 对非均匀网格做等间距重采样,使得后续算法可用

重采样只在引擎内部进行,峰位回报给用户时会反插值回原始坐标,
用户看到的 2θ 永远是真实采集值,不会看到人为挪动过的峰位。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# §4.2 判据:max(diff)/min(diff) - 1 超过此值即视为非均匀
UNIFORMITY_TOLERANCE = 1e-3


@dataclass(frozen=True)
class GridInfo:
    """网格检测结果。"""

    is_uniform: bool
    point_count: int
    x_min: float
    x_max: float
    step: float
    """中位步长。非均匀网格上这是代表性步长,用于 UI 显示与 distance 换算。"""
    step_ratio: float
    """max(diff)/min(diff)。1.0 表示完全均匀。"""
    is_descending: bool
    """原始 x 是否为降序(部分仪器按高角度到低角度导出)。"""
    has_duplicates: bool


def detect_grid(x: np.ndarray) -> GridInfo:
    """检测 x 数组的网格性质。

    不修改输入。单点或空数组按均匀处理,step 记为 0,由 Validate 层
    负责在点数不足时拒绝。
    """
    x_arr = np.asarray(x, dtype=np.float64).ravel()
    n = x_arr.size

    if n < 2:
        return GridInfo(
            is_uniform=True,
            point_count=n,
            x_min=float(x_arr[0]) if n else 0.0,
            x_max=float(x_arr[0]) if n else 0.0,
            step=0.0,
            step_ratio=1.0,
            is_descending=False,
            has_duplicates=False,
        )

    diffs = np.diff(x_arr)
    is_descending = bool(np.all(diffs < 0))
    has_duplicates = bool(np.any(diffs == 0))

    # 用绝对值算间距统计,降序序列的间距取反后一样有意义
    abs_diffs = np.abs(diffs)
    positive = abs_diffs[abs_diffs > 0]

    if positive.size == 0:
        # 全部点相同
        return GridInfo(
            is_uniform=True,
            point_count=n,
            x_min=float(x_arr.min()),
            x_max=float(x_arr.max()),
            step=0.0,
            step_ratio=1.0,
            is_descending=False,
            has_duplicates=True,
        )

    step_ratio = float(positive.max() / positive.min())
    is_uniform = (step_ratio - 1.0) <= UNIFORMITY_TOLERANCE

    return GridInfo(
        is_uniform=is_uniform,
        point_count=n,
        x_min=float(x_arr.min()),
        x_max=float(x_arr.max()),
        step=float(np.median(positive)),
        step_ratio=step_ratio,
        is_descending=is_descending,
        has_duplicates=has_duplicates,
    )


def ensure_ascending(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """把降序的 x/y 翻转为升序。已经是升序时返回输入的副本。"""
    x_arr = np.asarray(x, dtype=np.float64).ravel()
    y_arr = np.asarray(y, dtype=np.float64).ravel()
    if x_arr.size >= 2 and x_arr[0] > x_arr[-1]:
        return x_arr[::-1].copy(), y_arr[::-1].copy()
    return x_arr.copy(), y_arr.copy()


def resample_to_uniform(
    x: np.ndarray,
    y: np.ndarray,
    point_count: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """把非均匀网格线性插值到等间距网格。

    point_count 缺省沿用原点数,保持数据规模不变。线性插值而非样条:
    样条会在峰顶产生过冲,直接污染随后要测量的 intensity 与 FWHM。

    返回 (x_uniform, y_uniform)。输入已经是均匀网格时原样返回副本,
    调用方不需要先判断。
    """
    x_arr, y_arr = ensure_ascending(x, y)

    info = detect_grid(x_arr)
    if info.is_uniform:
        return x_arr, y_arr

    n = point_count if point_count is not None else x_arr.size
    x_uniform = np.linspace(x_arr[0], x_arr[-1], n)
    y_uniform = np.interp(x_uniform, x_arr, y_arr)
    return x_uniform, y_uniform


def find_nearest_indices(x_src: np.ndarray, x_query: np.ndarray) -> np.ndarray:
    """在升序的 x_src 中为每个 x_query 找到最近点的下标。

    用于把重采样网格上的峰位映射回原始坐标:先在均匀网格上定位峰的
    索引,再用它去原始数组取真实的 2θ,避免向用户报告插值出来的位置。
    """
    x_src_arr = np.asarray(x_src, dtype=np.float64).ravel()
    x_query_arr = np.atleast_1d(np.asarray(x_query, dtype=np.float64))
    idx = np.searchsorted(x_src_arr, x_query_arr)
    idx = np.clip(idx, 0, x_src_arr.size - 1)
    # searchsorted 给出的是插入位置,比较左右邻居取更近的一个
    left = np.clip(idx - 1, 0, x_src_arr.size - 1)
    take_left = np.abs(x_query_arr - x_src_arr[left]) <= np.abs(x_query_arr - x_src_arr[idx])
    return np.where(take_left, left, idx)
