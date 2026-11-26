"""峰检测(§7.3)。

底层是 scipy.signal.find_peaks。但前端不直接暴露 SciPy 参数,而是暴露
三个语义参数:

    Sensitivity       Low / Medium / High
    Minimum Height    强度百分比
    Minimum Distance  角度间隔

Sensitivity 到 find_peaks 参数的映射表在本模块固化。这张表的数值需要
用基准谱人工校准(§12 Milestone 4 的出口条件),当前值是基于谱形分析
给出的初始估计,校准后应更新 CALIBRATION_VERSION。

映射设计的一个关键约束:Sensitivity 必须单调 —— 提高灵敏度只能让
检出峰集合变大,不能出现「提高灵敏度反而少了某个峰」的反直觉行为。
测试里对此有专门断言。
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass

import numpy as np
from scipy.signal import find_peaks, peak_widths

from ..models.types import Peak, PeakDetectionParams, PeakSensitivity

# 映射表版本。人工校准后递增,并记录在导出文件的注释头里,
# 使结果可追溯到具体的映射表版本(§7.4)。
CALIBRATION_VERSION = "0.1-initial"

# Sensitivity → prominence 系数(prominence = 系数 × 谱的强度范围)
#
# 为什么用 prominence 而不是 height 作为灵敏度的主控:
# height 只反映峰的绝对高度,背景抬升时会把噪声当峰;
# prominence 衡量峰相对周围地形的突出程度,对背景变化更稳健。
SENSITIVITY_PROMINENCE_FACTOR: dict[PeakSensitivity, float] = {
    "low": 0.15,
    "medium": 0.05,
    "high": 0.015,
}

# Sensitivity → 附加的最小峰宽约束(以点数为单位,None 表示不约束)。
# High 档放宽宽度约束以检出弱而窄的峰;Low 档要求峰有一定宽度,
# 避免把单点噪声当成峰。
SENSITIVITY_MIN_WIDTH_POINTS: dict[PeakSensitivity, int | None] = {
    "low": 3,
    "medium": 2,
    "high": None,
}

# Sensitivity → 局部噪声门限的 σ 倍数。与上面的全局系数一起决定最终
# prominence 门限(取两者较小者,见 build_detection_kwargs)。
# 数值按"3σ/5σ/8σ 检出"的常规判据取,并保证 high 档严格宽于 medium。
SENSITIVITY_LOCAL_SIGMA: dict[PeakSensitivity, float] = {
    "low": 8.0,
    "medium": 5.0,
    "high": 3.0,
}

# 局部门限相对全局门限的最小比例(下界)。见 build_detection_kwargs 的
# 说明:纯合成谱上 MAD 噪声尺度会小到让门限低于噪声本身。
LOCAL_FLOOR_MIN_RATIO = 0.1

# 峰值搜索的默认 half-prominence 高度,用于 FWHM 计算
FWHM_REL_HEIGHT = 0.5


class PeakDetectionError(ValueError):
    """寻峰参数不合法或无法进行。"""


@dataclass
class DetectionDiagnostics:
    """寻峰的中间信息,供 UI 显示与问题排查。"""

    prominence_threshold: float
    height_threshold: float
    distance_points: int
    intensity_range: float
    raw_count: int
    """find_peaks 的原始输出数量(未经边界过滤)。"""


def _resolve_intensity_range(y: np.ndarray) -> float:
    """谱的强度范围,用于把百分比语义参数换算成绝对阈值。

    高端用 max(最强峰顶),低端用中位数(基线的稳健估计)。

    为什么不用 P99.5-P0.5:在细网格上(0.005° 步长)一个 0.2° 宽的峰
    只占全谱 <0.2% 的点,P99.5 根本够不到峰顶,range 会坍缩到噪声量级,
    结果是整条基线噪声被当成几百个峰检出。max 对"最强反射"的定义
    与用户说「相对最强峰 5%」时的直觉一致;中位数对基线漂移与噪声
    都稳健。

    **注意这个量是全谱的。** 用它直接做 prominence 门限会在高角系统性
    漏峰:层状正极的 (003) 与 (113) 强度可差两个数量级,而背景在高角
    还更高。见 `_local_prominence_floor` —— 实际门限取全局与局部两者
    的较小者,既保留低角抗噪能力,又不至于把高角弱峰全部砍掉。
    """
    finite = y[np.isfinite(y)]
    if finite.size == 0:
        return 0.0
    span = float(finite.max() - np.median(finite))
    return span if span > 0 else float(finite.max() - finite.min())


def _local_prominence_floor(y: np.ndarray, k: float) -> float:
    """由**局部**噪声水平推出的 prominence 门限。

    做法:把谱按滑动中位窗估计基线,取残差的中位绝对偏差(MAD)作为噪声
    尺度 σ,门限取 k·σ。

    为什么必须补这一条:全局门限 = factor × (max − median)。实测一张
    SmartLab 谱 max−median = 87567,medium 档门限 4378 —— 而该谱高角
    弱峰 (113)=4592、(116)=2808、(027)=2058、(208)=1775,直接落选。
    这些峰在物理上是真实反射(全局拟合能解出 1000+ 的振幅),漏掉它们
    不是"保守",是错误。局部门限对同样的谱给出约 300–600,弱峰得以保留,
    而基线噪声的 MAD 也随之放大,不会把噪声当峰。

    用 MAD 而不是标准差:标准差会被峰本身抬高,在高角弱峰区尤其明显。
    """
    y_arr = np.asarray(y, dtype=np.float64).ravel()
    n = y_arr.size
    if n < 16:
        return 0.0
    # 滑动中位窗的窗宽取全谱的 2%(至少 15 点),足以跨过单条峰而跟随基线
    win = max(15, (n // 50) | 1)
    if win >= n:
        return 0.0
    # 用 stride trick 做滑动中位,避免 Python 循环
    try:
        from numpy.lib.stride_tricks import sliding_window_view

        windows = sliding_window_view(y_arr, win)
        baseline = np.median(windows, axis=1)
    except (ValueError, MemoryError):
        return 0.0
    # 基线只在窗口中心处有效,两端各截掉半个窗宽
    half = win // 2
    core = y_arr[half : half + baseline.size]
    if core.size == 0:
        return 0.0
    residual = core - baseline
    mad = float(np.median(np.abs(residual - np.median(residual))))
    # 1.4826 把 MAD 折算成等效高斯 σ
    sigma = 1.4826 * mad
    return max(k * sigma, 0.0)


def build_detection_kwargs(
    y: np.ndarray,
    x_step: float,
    params: PeakDetectionParams,
) -> tuple[dict, DetectionDiagnostics]:
    """把语义参数翻译成 find_peaks 的 kwargs。

    单独抽出是为了让测试能直接检查映射结果,不需要跑完整检测。
    """
    intensity_range = _resolve_intensity_range(y)

    if params.sensitivity not in SENSITIVITY_PROMINENCE_FACTOR:
        raise PeakDetectionError(f"未知的灵敏度档位:{params.sensitivity}")

    # prominence 门限取**全局与局部两者的较小者**,但局部门限不得低于
    # 全局门限的一个下界。
    #
    # 全局门限保证低角强峰区不会被噪声淹没;局部门限保证高角弱峰不会被
    # 一个只反映 (003) 强度的数字砍掉。
    #
    # 为什么要给局部门限设下界:局部噪声用 MAD 估计,在极干净的合成谱上
    # σ 只有 2 counts,5σ = 10,而该谱的噪声峰突出度实测可达 15 —— 门限
    # 比噪声本身还低,基线抖动被当成峰检出(实测一条单峰合成谱检出 3 个
    # 峰)。取 max(局部, 全局×下限系数) 既保留高角灵敏度,又不会把
    # 门限压到噪声之下。下限系数 0.1 的含义:最宽也只比全局门限宽 10 倍。
    factor = SENSITIVITY_PROMINENCE_FACTOR[params.sensitivity]
    global_floor = intensity_range * factor
    prominence = global_floor
    local_floor = _local_prominence_floor(y, SENSITIVITY_LOCAL_SIGMA[params.sensitivity])
    if local_floor > 0:
        prominence = min(global_floor, max(local_floor, global_floor * LOCAL_FLOOR_MIN_RATIO))
    # Advanced 区若给了显式 prominence,以用户值为准
    if params.prominence is not None:
        prominence = float(params.prominence)

    # height 门限:只用来挡噪声,不能把高角弱峰挡掉。
    #
    # 为什么不能沿用"全局 range × min_height_pct":height 约束的是峰的
    # **绝对 ordinate**,而高角处的 ordinate 本来就贴近基线。实测一张
    # SmartLab 谱 range=87567,5% 得 4378,而高角弱峰 (208) 的峰顶只有
    # 6650 —— 看着能过,但基线已抬到 5400,**相对基线的幅度**只剩 1250。
    # 用户调"最小峰高"时想表达的正是这个相对幅度,不是绝对读数。
    # 所以门限 = 基线水平 + 幅度下限(见 _baseline_quantile)。
    #
    # 另一个关键点:height 必须**随灵敏度一起放宽**。旧实现里 height 与
    # 灵敏度无关,于是 high 档把 prominence 降到 1/3 的同时 height 原地
    # 不动并成为唯一约束 —— 实测 medium 与 high 的检出集合完全相同,
    # 灵敏度滑块在最需要它的档位上失效。
    amplitude_floor = intensity_range * (params.min_height_pct / 100.0)
    if local_floor > 0:
        # 局部门限已经把弱峰放行时,height 不应再紧于它,否则前一条白做
        amplitude_floor = min(amplitude_floor, local_floor)
    height = amplitude_floor + _baseline_quantile(y)

    if params.min_distance_deg <= 0:
        raise PeakDetectionError(
            f"最小峰间距必须为正数,收到 {params.min_distance_deg}"
        )
    if x_step <= 0:
        raise PeakDetectionError(f"X 步长必须为正数,收到 {x_step}")
    distance_points = max(1, round(params.min_distance_deg / x_step))

    kwargs: dict = {
        "prominence": prominence,
        "height": height,
        "distance": distance_points,
    }

    min_width = SENSITIVITY_MIN_WIDTH_POINTS[params.sensitivity]
    if params.width is not None:
        kwargs["width"] = float(params.width)
    elif min_width is not None:
        kwargs["width"] = min_width

    diag = DetectionDiagnostics(
        prominence_threshold=prominence,
        height_threshold=height,
        distance_points=distance_points,
        intensity_range=intensity_range,
        raw_count=0,
    )
    return kwargs, diag


def _baseline_quantile(y: np.ndarray) -> float:
    """谱的基线水平:强度的低分位数。

    用作 height 门限的基准 —— height 约束的是峰的绝对 ordinate,而不同
    样品的基线高低差别很大(本底、荧光、非晶包)。以基线为基准,用户
    设 5% 时得到的是"比基线高出 5% 量程",这才是"最小峰高"的直觉。

    用 10% 分位数而不是滑动中位窗:层状正极的 (003) 是最强峰,一个
    95 点(约 1.9°)的滑动中位窗在很多位置上**整窗都落在峰内**,会把
    基线估到 11000(真基线约 5400),反而比旧实现更严。低分位数天然
    落在基线一侧,不受强峰影响。
    """
    y_arr = np.asarray(y, dtype=np.float64).ravel()
    finite = y_arr[np.isfinite(y_arr)]
    if finite.size == 0:
        return 0.0
    return float(np.percentile(finite, 10.0))


def detect_peaks(
    x: np.ndarray,
    y: np.ndarray,
    params: PeakDetectionParams,
    *, angles: np.ndarray | None = None,
) -> tuple[list[Peak], DetectionDiagnostics]:
    """在曲线 y(x) 上检测峰,返回峰列表与诊断信息。

    返回的 Peak 只填了 position / intensity / prominence,
    FWHM 与 Area 由 peaks/metrics.py 计算 —— 那需要峰边界搜索,
    是独立且更昂贵的步骤。
    """
    x_arr = np.asarray(x, dtype=np.float64).ravel()
    y_arr = np.asarray(y, dtype=np.float64).ravel()

    if x_arr.size != y_arr.size:
        raise PeakDetectionError("x 与 y 长度不一致")
    if x_arr.size < 3:
        return [], DetectionDiagnostics(0.0, 0.0, 1, 0.0, 0)

    x_step = float(np.median(np.diff(x_arr))) if x_arr.size > 1 else 0.0
    kwargs, diag = build_detection_kwargs(y_arr, x_step, params)

    if angles is not None:
        kwargs["distance"] = 1  # Actual angular distances vary along a uniform d or Q grid.
    indices, properties = find_peaks(y_arr, **kwargs)
    diag.raw_count = int(indices.size)

    if indices.size == 0:
        return [], diag

    prominences = properties.get("prominences", np.zeros(indices.size))

    if angles is not None:
        keep = np.ones(indices.size, dtype=bool)
        accepted: list[float] = []
        for candidate in np.argsort(-y_arr[indices], kind="stable"):
            angle = float(angles[indices[candidate]])
            if not np.isfinite(angle) or angle <= 0:
                continue  # Keep native-axis peaks outside the observable angular range unindexed.
            slot = bisect_left(accepted, angle)
            neighbors = accepted[max(0, slot - 1):slot + 1]
            if any(abs(other - angle) < params.min_distance_deg for other in neighbors):
                keep[candidate] = False
            else:
                accepted.insert(slot, angle)
        indices, prominences = indices[keep], prominences[keep]

    peaks: list[Peak] = []
    for order, idx in enumerate(indices):
        peaks.append(
            Peak(
                index=order,
                position=float(x_arr[idx]),
                intensity=float(y_arr[idx]),
                prominence=float(prominences[order]),
            )
        )

    return peaks, diag


def compute_fwhm_and_bounds(
    x: np.ndarray,
    y: np.ndarray,
    peaks: list[Peak],
    rel_height: float = FWHM_REL_HEIGHT,
) -> list[Peak]:
    """为每个峰计算 FWHM 与左右边界,就地更新 Peak 对象后返回。

    FWHM 定义(§37):半高宽 = 左右两侧强度降到峰高一半处之间的角度差。
    用 scipy.signal.peak_widths 求交点,它在离散数据上做线性插值,
    结果与手算一致。

    重叠峰的边界会被截断到相邻峰的鞍点,避免两个峰的 Area 重复计算
    同一段区间。
    """
    x_arr = np.asarray(x, dtype=np.float64).ravel()
    y_arr = np.asarray(y, dtype=np.float64).ravel()

    if not peaks:
        return peaks

    indices = np.array(
        [int(np.argmin(np.abs(x_arr - p.position))) for p in peaks], dtype=np.intp
    )

    # peak_widths 需要单调的 x 且 y 为实数;这里的数据已满足
    widths, _height, left_ips, right_ips = peak_widths(
        y_arr, indices, rel_height=rel_height
    )

    for i, peak in enumerate(peaks):
        if not np.isfinite(widths[i]) or widths[i] <= 0:
            peak.fwhm = None
            peak.left_bound = None
            peak.right_bound = None
            continue

        left_ip = float(left_ips[i])
        right_ip = float(right_ips[i])

        # 插值:交点位于下标 left_ip 处,取相邻两点的线性插值
        left_angle = _interp_at_ip(x_arr, left_ip)
        right_angle = _interp_at_ip(x_arr, right_ip)

        peak.fwhm = float(right_angle - left_angle)
        peak.left_bound = float(left_angle)
        peak.right_bound = float(right_angle)

    return peaks


def _interp_at_ip(x_arr: np.ndarray, ip: float) -> float:
    """把 peak_widths 返回的浮点下标插值成 x 值。"""
    lo = int(np.floor(ip))
    hi = min(lo + 1, x_arr.size - 1)
    lo = max(0, min(lo, x_arr.size - 1))
    frac = ip - lo
    if hi == lo:
        return float(x_arr[lo])
    return float(x_arr[lo] + frac * (x_arr[hi] - x_arr[lo]))


def clamp_bounds_to_neighbors(peaks: list[Peak]) -> list[Peak]:
    """把相邻峰的重叠边界截断到中点,防止 Area 重复计算。

    只在两峰边界确实交叠时才动,否则保留 FWHM 交点给出的自然边界 ——
    FWHM 边界在物理上更有意义(它就是半高位置)。
    """
    if len(peaks) < 2:
        return peaks

    sorted_peaks = sorted(peaks, key=lambda p: p.position)

    for i in range(len(sorted_peaks) - 1):
        left_peak = sorted_peaks[i]
        right_peak = sorted_peaks[i + 1]

        if left_peak.right_bound is None or right_peak.left_bound is None:
            continue

        if left_peak.right_bound > right_peak.left_bound:
            # 交界处取两峰之间的鞍点,退化为中点
            midpoint = 0.5 * (left_peak.position + right_peak.position)
            left_peak.right_bound = min(left_peak.right_bound, midpoint)
            right_peak.left_bound = max(right_peak.left_bound, midpoint)

    return peaks
