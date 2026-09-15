"""处理链编排(§7.1)。

这是引擎的核心。所有处理的顺序在这里唯一确定:

    Load → Validate → Crop → Background → Subtract → Smooth → Peaks → Metrics

**为什么 Background 必须在 Smooth 之前**(Rev 2 对 v1 的修正):

  1. 在含背景的谱上做 SG 平滑,背景曲率大的区域会被畸变;
  2. 更关键的是 SG 平滑本身会压低峰高、展宽峰宽,先平滑后测 FWHM
     会系统性偏大 —— 而用户对此毫不知情。对用 FWHM 观察晶粒尺寸
     趋势的用户,这是静默的、系统性的偏差。

因此:
  - Smooth 默认关闭
  - Smooth 开启时,结果的 metrics_affected_by_smoothing 置位,
    UI 必须显示提示(§7.1)

每次求值都从原始曲线开始重算,不做跨请求的状态累积。这是「非破坏性」
原则在引擎层的体现:参数变了就整链重跑,不存在「处理到一半」的中间态。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..models.types import (
    Curve,
    PipelineParams,
    ProcessingResult,
    ValidationReport,
)
from ..peaks.detection import (
    clamp_bounds_to_neighbors,
    compute_fwhm_and_bounds,
    detect_peaks,
)
from ..peaks.metrics import compute_areas
from .background import BackgroundError, estimate_background, subtract_background
from .smoothing import SmoothingError, savgol_smooth, smoothing_broadening_estimate


@dataclass
class PipelineError(Exception):
    """处理链执行失败。携带用户可见的中文原因,不暴露 traceback。"""

    reason: str
    stage: str
    detail: str = ""

    def __str__(self) -> str:
        return self.reason


def apply_crop(curve: Curve, x_min: float | None, x_max: float | None) -> Curve:
    """按区间裁剪曲线。区间外的点直接丢弃。

    裁剪是整链的第一步:先裁掉不关心的角度范围,后续处理的数据量更小,
    背景估计也不会被范围外的强峰干扰。
    """
    if x_min is None and x_max is None:
        return curve.copy()

    x = curve.x
    lo = x_min if x_min is not None else float(x.min())
    hi = x_max if x_max is not None else float(x.max())

    if lo >= hi:
        raise PipelineError(
            reason=f"裁剪区间无效:左端 {lo:g}° 不小于右端 {hi:g}°",
            stage="crop",
        )

    mask = (x >= lo) & (x <= hi)
    if int(mask.sum()) < 4:
        raise PipelineError(
            reason=f"裁剪区间 [{lo:g}, {hi:g}] 内的数据点太少,请放宽范围",
            stage="crop",
        )

    return Curve(
        x=x[mask],
        y=curve.y[mask],
        x_axis_type=curve.x_axis_type,
        wavelength=curve.wavelength,
    )


def run_pipeline(
    curve: Curve,
    params: PipelineParams,
    compute_peak_metrics: bool = True,
) -> ProcessingResult:
    """执行完整处理链,返回所有中间结果与最终曲线。

    compute_peak_metrics=False 时跳过 FWHM/Area 计算 ——
    拖动参数滑块时只需要看曲线形状,跳过这步能明显降低延迟(§9.2)。
    """
    warnings: list[str] = []

    # --- 1. Crop ---
    working = curve
    if params.crop.enabled:
        try:
            working = apply_crop(curve, params.crop.x_min, params.crop.x_max)
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(
                reason="裁剪失败", stage="crop", detail=str(exc)
            ) from exc

    x = working.x
    y = working.y

    # --- 2. Background 估计 ---
    background: np.ndarray | None = None
    if params.background.enabled:
        try:
            background = estimate_background(x, y, params.background)
        except BackgroundError as exc:
            raise PipelineError(
                reason=str(exc), stage="background", detail=type(exc).__name__
            ) from exc

        # 背景高于信号是常见的异常信号,提示用户调整参数
        if background.max() > y.max():
            warnings.append(
                "背景估计值高于信号峰值,可能 smoothness 过小,建议增大"
            )

        # --- 3. 扣背景 ---
        y = subtract_background(y, background)

    # --- 4. Smooth(在扣背景之后,§7.1)---
    metrics_affected_by_smoothing = False
    y_metric = y
    if params.smoothing.enabled:
        try:
            y_smooth = savgol_smooth(y, params.smoothing)
        except SmoothingError as exc:
            raise PipelineError(
                reason=str(exc), stage="smoothing", detail=type(exc).__name__
            ) from exc

        y = y_smooth
        y_metric = y_smooth
        metrics_affected_by_smoothing = True

        # 估算平滑引入的额外峰宽,放进 warning 让用户有量级概念
        x_step = float(np.median(np.diff(x))) if x.size > 1 else 0.0
        broaden = smoothing_broadening_estimate(
            params.smoothing.window_length, params.smoothing.polyorder, x_step
        )
        if broaden > 0:
            warnings.append(
                f"已启用平滑:峰高会降低、FWHM 会增大约 {broaden:.3f}°,"
                f"峰度量基于平滑后的曲线"
            )

    result_curve = Curve(
        x=x,
        y=y,
        x_axis_type=working.x_axis_type,
        wavelength=working.wavelength,
    )

    # --- 5. 寻峰 ---
    peaks = []
    if params.peaks is not None and x.size >= 3:
        try:
            peaks, _diag = detect_peaks(x, y_metric, params.peaks)
        except Exception as exc:
            raise PipelineError(
                reason=f"寻峰失败:{exc}", stage="peaks", detail=type(exc).__name__
            ) from exc

        if peaks:
            # FWHM 与边界基于峰形本身,在 y_metric 上算
            compute_fwhm_and_bounds(x, y_metric, peaks, rel_height=params.peaks.rel_height)
            # 相邻峰边界重叠时截断,避免 Area 重复计算
            clamp_bounds_to_neighbors(peaks)

            if compute_peak_metrics:
                compute_areas(
                    x,
                    y_metric,
                    peaks,
                    use_fwhm_bounds=not params.background.enabled,
                )

    if x.size < 3:
        warnings.append("数据点太少,未进行寻峰")

    return ProcessingResult(
        curve=result_curve,
        background=background,
        metric_curve_y=y_metric.copy(),
        peaks=peaks,
        metrics_affected_by_smoothing=metrics_affected_by_smoothing,
        warnings=warnings,
    )


def validate_params(curve: Curve, params: PipelineParams) -> ValidationReport:
    """在不实际执行的情况下预检参数,供 UI 在用户输入时即时反馈。

    比直接跑一遍 pipeline 便宜得多,适合滑块拖动过程中调用。
    """
    report = ValidationReport()
    x = curve.x

    if params.crop.enabled:
        lo = params.crop.x_min if params.crop.x_min is not None else float(x.min())
        hi = params.crop.x_max if params.crop.x_max is not None else float(x.max())
        if lo >= hi:
            report.add_error(f"裁剪区间无效:{lo:g}° 不小于 {hi:g}°")
        elif int(((x >= lo) & (x <= hi)).sum()) < 4:
            report.add_error("裁剪区间内数据点不足 4 个")

    if params.smoothing.enabled:
        try:
            from .smoothing import validate_savgol_params

            validate_savgol_params(
                params.smoothing.window_length,
                params.smoothing.polyorder,
                int(x.size),
            )
        except SmoothingError as exc:
            report.add_error(str(exc))

    if params.background.enabled:
        from .background import (
            ASYMMETRY_MAX,
            ASYMMETRY_MIN,
            SMOOTHNESS_MAX,
            SMOOTHNESS_MIN,
        )

        if params.background.method == "asls":
            if not (SMOOTHNESS_MIN <= params.background.smoothness <= SMOOTHNESS_MAX):
                report.add_error(
                    f"smoothness 应在 {SMOOTHNESS_MIN:g} – {SMOOTHNESS_MAX:g} 之间"
                )
            if not (ASYMMETRY_MIN <= params.background.asymmetry <= ASYMMETRY_MAX):
                report.add_error(
                    f"asymmetry 应在 {ASYMMETRY_MIN:g} – {ASYMMETRY_MAX:g} 之间"
                )
        elif params.background.order >= x.size:
            report.add_error("多项式阶数不能大于数据点数")

    if params.peaks.min_distance_deg <= 0:
        report.add_error("最小峰间距必须为正数")
    if not (0 <= params.peaks.min_height_pct <= 100):
        report.add_error("最小峰高百分比应在 0 – 100 之间")

    return report
