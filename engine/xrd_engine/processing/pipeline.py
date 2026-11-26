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

## 阶段缓存(§9.3)

"整链重跑"说的是**语义**,不是"每一步都真的重算"。背景估计(AsLS)
实测约 23 ms,SG 平滑约 0.4 ms —— 差 60 倍。用户拖平滑窗口时背景
一点没变,却要陪着重算一遍。

`_stage_cache` 按**参数前缀**缓存各阶段输出:背景只依赖
(crop, background),平滑依赖 (crop, background, smoothing)。
前缀不变即命中。这样"只改 Smooth 不重算 Background"就是自然的,
不需要为每种组合写特例。

**键必须是整段前缀,不能只放单个字段** —— 用户同时改了 crop 和
background 时,只比 background 会命中一个用旧 crop 算出来的背景。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

import numpy as np

from ..coords import convert
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


# 阶段缓存:key = (曲线指纹, 阶段名, 参数前缀) -> 该阶段输出。
#
# 有界:缓存里放的是整条曲线量级的数组(4751 点 × 8 字节 ≈ 38 KB),
# 32 条足够覆盖"来回切几个数据集 + 拖参数"的实际用法。
_STAGE_CACHE: dict[tuple, object] = {}
_STAGE_CACHE_LIMIT = 32


def clear_stage_cache() -> None:
    """清空阶段缓存。数据集被删/替换后必须调用 —— 指纹能挡住大部分
    情况,但显式清更稳妥(与 annotate.invalidate_fit_cache 同一理由)。"""
    _STAGE_CACHE.clear()


def _fingerprint(curve: Curve) -> tuple:
    """曲线指纹。**不能只用 id**:曲线对象没有 id,而且数据集删除后
    id 可能复用,只按 id 会拿到上一条曲线的中间结果。"""
    y = curve.y
    return (
        int(y.size),
        round(float(curve.x[0]), 6) if y.size else 0.0,
        round(float(curve.x[-1]), 6) if y.size else 0.0,
        round(float(y.sum()), 3),
        curve.x_axis_type,
    )


def _cache_get(key: tuple):
    return _STAGE_CACHE.get(key)


def _cache_put(key: tuple, value: object) -> None:
    if len(_STAGE_CACHE) >= _STAGE_CACHE_LIMIT:
        _STAGE_CACHE.pop(next(iter(_STAGE_CACHE)))
    _STAGE_CACHE[key] = value


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
    fp = _fingerprint(curve)

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
    #
    # 缓存键 = (曲线指纹, 阶段, 参数前缀)。背景只依赖 crop 与 background
    # 两组参数,所以拖平滑窗口时这一步会命中 —— AsLS 实测约 23 ms,
    # 而 SG 平滑只有 0.4 ms,不缓存就是让用户为没变的计算等 60 倍时间。
    background: np.ndarray | None = None
    if params.background.enabled:
        bg_key = (
            fp,
            "background",
            json.dumps(asdict(params.crop), sort_keys=True),
            json.dumps(asdict(params.background), sort_keys=True),
        )
        cached_bg = _cache_get(bg_key)
        if cached_bg is not None:
            background = cached_bg  # type: ignore[assignment]
        else:
            try:
                background = estimate_background(x, y, params.background)
            except BackgroundError as exc:
                raise PipelineError(
                    reason=str(exc), stage="background", detail=type(exc).__name__
                ) from exc
            _cache_put(bg_key, background)

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
        sm_key = (
            fp,
            "smoothing",
            json.dumps(asdict(params.crop), sort_keys=True),
            json.dumps(asdict(params.background), sort_keys=True),
            json.dumps(asdict(params.smoothing), sort_keys=True),
        )
        cached_sm = _cache_get(sm_key)
        if cached_sm is not None:
            y_smooth = cached_sm  # type: ignore[assignment]
        else:
            try:
                y_smooth = savgol_smooth(y, params.smoothing)
            except SmoothingError as exc:
                raise PipelineError(
                    reason=str(exc), stage="smoothing", detail=type(exc).__name__
                ) from exc
            _cache_put(sm_key, y_smooth)

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
            angles = None
            if working.x_axis_type in ("d", "q"):
                with np.errstate(divide="ignore", invalid="ignore"):
                    angles = convert(x, working.x_axis_type, "two_theta", working.wavelength)
            peaks, _diag = detect_peaks(x, y_metric, params.peaks, angles=angles)
        except Exception as exc:
            raise PipelineError(
                reason=f"寻峰失败:{exc}", stage="peaks", detail=type(exc).__name__
            ) from exc

        if peaks and compute_peak_metrics:
            # FWHM 与边界基于峰形本身,在 y_metric 上算
            compute_fwhm_and_bounds(x, y_metric, peaks, rel_height=params.peaks.rel_height)
            # 相邻峰边界重叠时截断,避免 Area 重复计算
            clamp_bounds_to_neighbors(peaks)
            # §38:默认峰边界取局部极小值(全峰面积);
            # FWHM 边界只用于报告半高宽,不用于 Area 积分区间
            compute_areas(x, y_metric, peaks, use_fwhm_bounds=False)
        # compute_peak_metrics=False(拖动预览):detect_peaks 返回的 Peak
        # 本身 fwhm/area/left_bound/right_bound 就是 None,直接跳过即可 ——
        # 寻峰仍给位置/强度让峰标记跟手,度量由收尾 preview 再补。

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
