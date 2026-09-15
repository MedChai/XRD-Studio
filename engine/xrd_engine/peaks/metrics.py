"""峰度量:Area 计算与边界编辑(§38)。

FWHM 在 detection.py 里随峰检测一起算(它依赖 peak_widths 的中间量),
本模块负责 Area 与用户编辑边界后的重算。

Area 定义:

    Area = ∫ I(x) dx   (在 [left_bound, right_bound] 区间上)

积分对象是「当前处理链输出的曲线」—— 若背景已扣,就是对净峰面积
积分;若未扣背景,Area 会包含背景贡献。导出时 pipeline 快照里记了
是哪种情况(§7.4),所以结果不含糊,但用户需要知道这一点,
UI 在背景未启用时对 Area 列显示提示。
"""

from __future__ import annotations

import numpy as np

from ..models.types import Peak


class PeakMetricsError(ValueError):
    """峰度量计算的输入不合法。"""


def integrate_area(
    x: np.ndarray,
    y: np.ndarray,
    left: float,
    right: float,
) -> float:
    """在 [left, right] 区间上对 y(x) 做梯形积分。

    区间端点落在采样点之间时做线性插值,不简单地取最近点 ——
    峰边界是用户可编辑的浮点数,取整会引入与步长同量级的误差。
    """
    x_arr = np.asarray(x, dtype=np.float64).ravel()
    y_arr = np.asarray(y, dtype=np.float64).ravel()

    if x_arr.size != y_arr.size:
        raise PeakMetricsError("x 与 y 长度不一致")
    if left >= right:
        raise PeakMetricsError(f"左边界({left})必须小于右边界({right})")

    # 只保留落在区间内的采样点
    mask = (x_arr >= left) & (x_arr <= right)
    if not np.any(mask):
        # 区间太窄,没有采样点落入 —— 用端点插值做单段梯形
        y_left = float(np.interp(left, x_arr, y_arr))
        y_right = float(np.interp(right, x_arr, y_arr))
        return 0.5 * (y_left + y_right) * (right - left)

    x_sel = x_arr[mask]
    y_sel = y_arr[mask]

    # 把精确端点并入,保证积分区间严格等于 [left, right]
    if x_sel[0] > left:
        y_left = float(np.interp(left, x_arr, y_arr))
        x_sel = np.concatenate(([left], x_sel))
        y_sel = np.concatenate(([y_left], y_sel))
    if x_sel[-1] < right:
        y_right = float(np.interp(right, x_arr, y_arr))
        x_sel = np.concatenate((x_sel, [right]))
        y_sel = np.concatenate((y_sel, [y_right]))

    if x_sel.size < 2:
        return 0.0

    return float(np.trapezoid(y_sel, x_sel))


def find_local_minima_bounds(
    x: np.ndarray,
    y: np.ndarray,
    peak_position: float,
) -> tuple[float, float]:
    """从峰位向两侧搜索局部极小值作为默认边界(§38)。

    搜索策略:向两侧走,遇到「连续上升」即认为到达谷底,取该处。
    比全局寻找最近极小值更稳健 —— 不会跳过浅谷去够很远的深谷。
    """
    x_arr = np.asarray(x, dtype=np.float64).ravel()
    y_arr = np.asarray(y, dtype=np.float64).ravel()

    if x_arr.size < 3:
        raise PeakMetricsError("数据点太少,无法确定峰边界")

    peak_idx = int(np.argmin(np.abs(x_arr - peak_position)))

    # 向左搜索(下标递减):走过谷底后,继续向左 y 会「上升」,
    # 即 y[i] > y[i+1]。连续 2 次判定到达谷底,谷停在 i+1。
    left_idx = peak_idx
    rising_count = 0
    for i in range(peak_idx - 1, 0, -1):
        if y_arr[i] > y_arr[i + 1]:
            rising_count += 1
            if rising_count >= 2:
                left_idx = i + 1
                break
        else:
            rising_count = 0
            left_idx = i
    else:
        left_idx = max(0, left_idx)

    # 向右搜索(下标递增):谷底之后 y[i] > y[i-1]
    right_idx = peak_idx
    rising_count = 0
    for i in range(peak_idx + 1, x_arr.size - 1):
        if y_arr[i] > y_arr[i - 1]:
            rising_count += 1
            if rising_count >= 2:
                right_idx = i - 1
                break
        else:
            rising_count = 0
            right_idx = i
    else:
        right_idx = min(x_arr.size - 1, right_idx)

    if right_idx <= left_idx:
        # 搜索失败(单调曲线),退回峰位两侧各一个步长
        step = float(np.median(np.diff(x_arr)))
        return peak_position - step, peak_position + step

    return float(x_arr[left_idx]), float(x_arr[right_idx])


def compute_areas(
    x: np.ndarray,
    y: np.ndarray,
    peaks: list[Peak],
    use_fwhm_bounds: bool = False,
) -> list[Peak]:
    """为每个峰计算 Area,就地更新后返回。

    use_fwhm_bounds=False(默认)时,用局部极小值作为边界;
    用户编辑过边界的峰(manual_override=True)一律尊重用户值。
    """
    x_arr = np.asarray(x, dtype=np.float64).ravel()

    for peak in peaks:
        if (peak.manual_override and peak.left_bound is not None and peak.right_bound is not None) or (use_fwhm_bounds and peak.left_bound is not None and peak.right_bound is not None):
            left, right = peak.left_bound, peak.right_bound
        else:
            left, right = find_local_minima_bounds(x_arr, y, peak.position)
            peak.left_bound = left
            peak.right_bound = right

        try:
            peak.area = integrate_area(x_arr, y, left, right)
        except PeakMetricsError:
            peak.area = None

    return peaks


def recompute_peak_after_bound_edit(
    x: np.ndarray,
    y: np.ndarray,
    peak: Peak,
    left: float,
    right: float,
) -> Peak:
    """用户编辑边界后重算该峰的 Area(§38)。

    FWHM 不因边界编辑而改变 —— 它由峰形本身决定,与积分区间无关。
    用户改边界只影响 Area,这一点在 UI 上也应体现(FWHM 字段保持可读)。
    """
    x_arr = np.asarray(x, dtype=np.float64).ravel()

    if left >= right:
        raise PeakMetricsError(f"左边界({left})必须小于右边界({right})")
    if left < x_arr[0] or right > x_arr[-1]:
        raise PeakMetricsError(
            f"边界 [{left}, {right}] 超出了数据范围 [{x_arr[0]}, {x_arr[-1]}]"
        )

    peak.left_bound = float(left)
    peak.right_bound = float(right)
    peak.manual_override = True
    peak.area = integrate_area(x_arr, y, left, right)
    return peak
