"""平滑处理(§7.3 Savitzky–Golay)。

重要定位说明 —— 本模块对 FWHM 测量有系统性影响:

SG 平滑会压低峰高、展宽峰宽。如果用户在「先平滑、后测 FWHM」的链上
读数,得到的 FWHM 会比真实值偏大、峰高偏小,而用户不会收到任何提示。

v1 草稿把 Smooth 放在 Background 之前且默认开启,这是错的。Rev 2 的规定:

  1. pipeline 顺序为 Background → Smooth(见 pipeline.py)
  2. 默认 Smooth 关闭
  3. Smooth 开启时,ProcessingResult.metrics_affected_by_smoothing
     置位,Inspector 显示提示(§7.1)

本模块只负责计算,是否提示由上游根据 params.enabled 决定。
"""

from __future__ import annotations

import numpy as np
from scipy.signal import savgol_filter

from ..models.types import SmoothingParams

# §7.3 规定的窗口限幅
MIN_WINDOW = 3
MAX_WINDOW = 501
MAX_WINDOW_FRACTION = 0.10
"""窗口不得超过数据长度的 10%,否则大半个谱会被边界效应污染。"""

MIN_POLYORDER = 1
DEFAULT_WINDOW = 7
DEFAULT_POLYORDER = 2


class SmoothingError(ValueError):
    """平滑参数不合法。"""


def validate_savgol_params(
    window_length: int,
    polyorder: int,
    point_count: int,
) -> tuple[int, int]:
    """校验并规范化 SG 参数,返回可用的 (window_length, polyorder)。

    §7.3 要求 UI 保证 window 为奇数且 polyorder < window。这里做二次
    校验而不是信任调用方 —— 引擎可能被 API 或测试直接调用。
    """
    if polyorder < MIN_POLYORDER:
        raise SmoothingError(f"多项式阶数至少为 {MIN_POLYORDER},收到 {polyorder}")
    if window_length < MIN_WINDOW:
        raise SmoothingError(f"窗口长度至少为 {MIN_WINDOW},收到 {window_length}")

    # 窗口必须为奇数:savgol_filter 的硬要求
    if window_length % 2 == 0:
        window_length += 1

    if window_length > MAX_WINDOW:
        raise SmoothingError(f"窗口长度不能超过 {MAX_WINDOW},收到 {window_length}")

    if polyorder >= window_length:
        raise SmoothingError(
            f"多项式阶数({polyorder})必须小于窗口长度({window_length})"
        )

    # 窗口不能超过数据长度的 10%
    max_allowed = int(point_count * MAX_WINDOW_FRACTION)
    if max_allowed % 2 == 0:
        max_allowed -= 1
    if max_allowed < MIN_WINDOW:
        raise SmoothingError(
            f"数据点太少({point_count}),无法进行窗口为 {window_length} 的平滑"
        )
    if window_length > max_allowed:
        raise SmoothingError(
            f"窗口长度({window_length})超过了数据长度的 "
            f"{MAX_WINDOW_FRACTION:.0%}(上限 {max_allowed} 点)"
        )

    return window_length, polyorder


def savgol_smooth(y: np.ndarray, params: SmoothingParams) -> np.ndarray:
    """对曲线做 Savitzky–Golay 平滑。

    params.enabled 为 False 时返回输入的副本,不做任何改动 ——
    调用方不需要先判断开关,链式调用更简单。
    """
    y_arr = np.asarray(y, dtype=np.float64).ravel()

    if not params.enabled:
        return y_arr.copy()

    if params.method != "savgol":
        raise SmoothingError(f"未知的平滑方法:{params.method}")

    if y_arr.size < MIN_WINDOW:
        raise SmoothingError(f"数据点太少({y_arr.size}),无法平滑")

    window, order = validate_savgol_params(
        params.window_length, params.polyorder, y_arr.size
    )

    return savgol_filter(y_arr, window_length=window, polyorder=order)


def smoothing_broadening_estimate(window_length: int, polyorder: int, x_step: float) -> float:
    """估算 SG 平滑引入的额外峰宽(与 x 步长同单位)。

    用于 UI 提示的粗估:SG 平滑相当于与一个高阶多项式窗卷积,
    其等效宽度约为窗口长度的一半。这不是严谨的仪器宽化修正,
    只用于让用户意识到「平滑会改变 FWHM」这一事实。

    返回值量级参考:window=7、step=0.02° → 约 0.07°。
    """
    if x_step <= 0:
        return 0.0
    # 经验系数:窗口点数 × 步长 × 0.5,再按多项式阶数衰减
    effective_points = max(1.0, (window_length - 1) * 0.5)
    order_damping = 1.0 / (1.0 + 0.15 * polyorder)
    return float(effective_points * x_step * order_damping)
