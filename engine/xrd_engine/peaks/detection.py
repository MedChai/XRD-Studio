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
    """
    finite = y[np.isfinite(y)]
    if finite.size == 0:
        return 0.0
    span = float(finite.max() - np.median(finite))
    return span if span > 0 else float(finite.max() - finite.min())


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

    prominence = intensity_range * SENSITIVITY_PROMINENCE_FACTOR[params.sensitivity]
    # Advanced 区若给了显式 prominence,以用户值为准
    if params.prominence is not None:
        prominence = float(params.prominence)

    height = intensity_range * (params.min_height_pct / 100.0)

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


def detect_peaks(
    x: np.ndarray,
    y: np.ndarray,
    params: PeakDetectionParams,
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

    indices, properties = find_peaks(y_arr, **kwargs)
    diag.raw_count = int(indices.size)

    if indices.size == 0:
        return [], diag

    prominences = properties.get("prominences", np.zeros(indices.size))

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
