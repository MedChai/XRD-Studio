"""Auditable screening metrics for high-Ni layered cathode XRD patterns."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import curve_fit
from scipy.signal import find_peaks

from ..coords import convert, two_theta_to_d
from ..models.types import Curve
from ..peaks.metrics import integrate_area
from .profiles import HighNiProfile, PeakWindow, default_profile

ANALYSIS_VERSION = "high-ni-0.1"


@dataclass
class _PeakObservation:
    key: str
    position: float
    height: float
    area: float
    fwhm: float | None
    fit_error: float | None = None
    source: str = "local"
    left_bound: float | None = None
    right_bound: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "height": self.height,
            "area": self.area,
            "fwhm": self.fwhm,
            "fit_error": self.fit_error,
            "source": self.source,
            "left_bound": self.left_bound,
            "right_bound": self.right_bound,
        }


def analyze_high_ni(
    curve: Curve,
    profile: HighNiProfile | None = None,
    *,
    modules: Iterable[str] = ("mixing", "layered", "lattice"),
    fit: bool = True,
    dataset_id: int | None = None,
    profile_id: int | None = None,
    source_hash: str = "",
    cell: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Analyze one processed curve and return a JSON-safe result.

    This is a screening layer.  It deliberately reports evidence, warnings
    and confidence instead of claiming a crystallographic occupancy result.

    cell: 全谱精修晶胞 (a, c)。给了就用它反算双峰理论位置配名;不给则
    回退典型 R-3m 高镍晶胞(旧行为)。**优先传精修值** —— 写死晶胞与
    实测胞差 0.06 Å 时 102 分量理论位置可偏约 0.1°,足以让 (006)/(102)
    配名翻转,还会把错名传进晶胞最小二乘。
    """
    profile = profile or default_profile(curve.wavelength)
    requested = set(modules)
    x, y, warnings = _as_two_theta(curve)
    if x.size < 20:
        return _empty_result(
            dataset_id, profile_id, source_hash,
            [*warnings, "有效数据点太少,无法进行高镍结构筛查"],
        )

    # 反序胞会让 (018)/(110) 理论角对调:高角单峰会被配成 "108"。
    # 与 indexing.doublet_order_ok 同判据;不合格就回退典型胞,不把错序
    # 传进配名与最小二乘。
    if cell is not None:
        from .indexing import doublet_order_ok

        if not doublet_order_ok(float(cell[0]), float(cell[1])):
            warnings.append("全谱精修晶胞双峰理论序异常,双峰配名已回退典型胞")
            cell = None

    observations: dict[str, Any] = {}
    for window in profile.resolved_windows():
        segment = _window_data(x, y, window)
        if segment is None:
            observations[window.key] = None
            continue
        sx, sy = segment
        if window.pair and fit:
            pair_result, pair_warnings = _fit_pair(
                window, sx, sy, profile.wavelength, cell=cell
            )
            observations[window.key] = pair_result
            warnings.extend(pair_warnings)
        elif window.pair:
            observations[window.key] = _observe_pair_without_fit(
                window, sx, sy, profile.wavelength, cell=cell
            )
        else:
            observations[window.key] = _observe_single(window.key, sx, sy)

    result: dict[str, Any] = {
        "domain": "high_ni_layered",
        "analysisVersion": ANALYSIS_VERSION,
        "datasetId": dataset_id,
        "profileId": profile_id,
        "sourceHash": source_hash,
        "wavelength": profile.wavelength,
        "profile": profile.as_dict(),
        "peaks": observations,
        "warnings": warnings,
        "confidence": "low",
    }

    if "mixing" in requested:
        result["mixing"] = _mixing_result(observations, profile)
    if "layered" in requested:
        result["layered"] = _layered_result(observations, warnings)
    if "lattice" in requested:
        result["lattice"] = _lattice_result(observations, profile.wavelength)

    result["confidence"] = _overall_confidence(result)
    return result


def _empty_result(
    dataset_id: int | None,
    profile_id: int | None,
    source_hash: str,
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "domain": "high_ni_layered",
        "analysisVersion": ANALYSIS_VERSION,
        "datasetId": dataset_id,
        "profileId": profile_id,
        "sourceHash": source_hash,
        "peaks": {},
        "warnings": warnings,
        "confidence": "low",
    }


def _as_two_theta(curve: Curve) -> tuple[np.ndarray, np.ndarray, list[str]]:
    if curve.x_axis_type == "two_theta":
        return curve.x, curve.y, []
    if curve.x_axis_type in ("d", "q"):
        try:
            converted = convert(curve.x, curve.x_axis_type, "two_theta", curve.wavelength)
            order = np.argsort(converted)
            return converted[order], curve.y[order], []
        except ValueError as exc:
            return curve.x, curve.y, [f"x 轴无法换算为 2θ: {exc}"]
    return curve.x, curve.y, [
        f"当前 x 轴为 {curve.x_axis_type},无法按 2θ 峰窗进行高镍分析"
    ]


def _window_data(
    x: np.ndarray, y: np.ndarray, window: PeakWindow
) -> tuple[np.ndarray, np.ndarray] | None:
    mask = (x >= window.center_2theta - window.half_width) & (
        x <= window.center_2theta + window.half_width
    )
    if int(mask.sum()) < 8:
        return None
    return x[mask], y[mask]


def _baseline_correct(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    edge = max(2, min(x.size // 8, 40))
    edge_x = np.r_[x[:edge], x[-edge:]]
    edge_y = np.r_[y[:edge], y[-edge:]]
    coeff = np.polyfit(edge_x, edge_y, 1)
    return np.maximum(y - np.polyval(coeff, x), 0.0)


def _peak_bounds(
    x: np.ndarray, y: np.ndarray, idx: int
) -> tuple[float, float]:
    """收敛一个峰的积分边界:以峰顶为锚,取两侧单调下降段的边界。

    之前用局部极小值定界——但在扣除基线并把尾部裁剪到 0 后,窗内
    并没有局部极小值可收敛,会退化到整窗;整窗积分等于把 003 窗内任何
    旁峰 / 隆起背底都算进 Area(003)。改用「到改校正强度小于半峰高即停」,
    得到峰本体边界,与用户峰表的 Area 口径一致(缺陷 1)。
    """
    peak_height = float(y[idx])
    if peak_height <= 0:
        raise RuntimeError("peak height non-positive, cannot bound")
    half = peak_height * 0.5
    left_i = idx
    while left_i > 0 and float(y[left_i]) >= half:
        left_i -= 1
    right_i = idx
    while right_i < y.size - 1 and float(y[right_i]) >= half:
        right_i += 1
    if left_i == idx or right_i == idx:
        # 峰几乎顶到窗沿(截断峰),半高区间不确定,退回整窗。
        return float(x[0]), float(x[-1])
    # 半高区间外扩一点,把随 FWHM 一同缩放的峰底纳入(≈±FWHM),
    # 统一口径:两个峰都用各自的半高跨度做边界。
    half_width = float(x[right_i] - x[left_i])
    step = _x_step(x)
    span = max(half_width, 2.0 * step)
    left = max(x[0], float(x[idx]) - span)
    right = min(x[-1], float(x[idx]) + span)
    if right <= left:
        raise RuntimeError(f"peak bounds degenerate for {x[idx]:.3f}°")
    return left, right


def _observe_single(key: str, x: np.ndarray, y: np.ndarray) -> dict[str, Any] | None:
    corrected = _baseline_correct(x, y)
    idx = int(np.argmax(corrected))
    height = float(corrected[idx])
    if height <= 0:
        return None
    position = float(x[idx])
    # 峰本体面积:先在扣除线性基线的窗口上收敛局部极小值边界,再对边界内
    # 积峰。绝不对整窗做 np.trapezoid —— 那会把旁峰/隆起背底一并算进去,
    # 与用户看到的峰表 Area 口径不一致(缺陷 1)。
    left, right = _peak_bounds(x, corrected, idx)
    area = integrate_area(x, corrected, left, right)
    fwhm = _estimate_fwhm(x, corrected, idx)
    return _PeakObservation(
        key, position, height, area, fwhm,
        left_bound=left, right_bound=right,
    ).as_dict()


# 典型 R-3m 高镍晶胞。**仅在调用方没给精修胞时回退** —— 写死值不是默认
# 答案:实测用户数据精修出 a=2.884/c=14.278,与此差足以让 102 分量理论
# 位置偏约 0.1°,双峰配名可能整体翻转(见 analyze_high_ni 的 cell 参数)。
_TYPICAL_CELL: tuple[float, float] = (2.878, 14.216)


def _pair_positions_by_label(
    labels: tuple[str, ...],
    positions: list[float],
    wavelength: float,
    cell: tuple[float, float] | None = None,
) -> dict[str, float]:
    """把观测峰位按**晶体学理论位置**配到双峰的名字上。

    为什么不能按 2θ 升序 zip:旧实现把低角分量固定叫 labels[0](即 (006))。
    但层状正极实验上 **(006) 弱、102 分量强**,而且两者的理论间距对 c 高度
    敏感 —— 顺序假设一旦不成立,两个名字就整体对调,还会一路传进晶胞
    最小二乘把 a/c 一起带偏。

    cell 给定时用精修胞反算理论位置;否则回退典型胞(旧行为)。
    """
    from ..peaks.annotate import _label_to_hkl

    theo: dict[str, float] = {}
    a, c = cell if cell is not None else _TYPICAL_CELL
    for label in labels:
        hkl = _label_to_hkl(label)
        if hkl is None:
            continue
        h, k, l = hkl
        inv_d2 = (4.0 / 3.0) * (h * h + h * k + k * k) / (a * a) + (l * l) / (c * c)
        d = 1.0 / np.sqrt(inv_d2)
        ratio = wavelength / (2.0 * d)
        if ratio <= 1.0:
            theo[label] = float(np.degrees(2.0 * np.arcsin(ratio)))

    ordered = sorted(positions)
    if len(theo) < 2:
        # 解析不出 hkl 时退回顺序假设(调用方无法区分,但至少不崩)
        return dict(zip(sorted(labels), ordered, strict=False))

    names = list(theo.keys())
    out: dict[str, float] = {}
    remaining = list(names)
    for pos in ordered:
        best = min(remaining, key=lambda nm: abs(theo[nm] - pos))
        out[best] = pos
        remaining.remove(best)
    return out


def _pair_component_indices(
    x: np.ndarray,
    corrected: np.ndarray,
    window: PeakWindow,
    wavelength: float,
    cell: tuple[float, float] | None = None,
) -> list[int]:
    """在双峰窗口内找出两个分量各自的下标。

    **不依赖全窗统一的 prominence 门限。** 旧实现用
    `max(std*1.5, max*0.01)` 做门限,而较强分量的存在会把 std 抬高到
    较弱分量之上 —— 实测 (108)/(110) 窗内 prom=4465,而 (108) 的相对
    突出度不足,于是永远只找到一个峰,双峰拟合退化成单峰,晶胞 c 被
    带偏约 0.15 Å。

    做法分两步:

    1. 用**低**门限找出窗内全部局部极大值(门限只用于挡噪声,不用来
       区分强弱分量 —— 那是物理强度差异,不是噪声)。
    2. 每个名字取其理论位置最近的极大值;同一个极大值不被两个名字
       重复占用。

    这样两种情况都处理正确:双峰真正分开时各得其所;峰合并成一个包时
    只有一个极大值,另一个名字自然缺席(如实反映"未分开",而不是硬
    编一个位置出来)。
    """
    labels = window.pair or ()
    theo = _pair_theoretical_positions(labels, wavelength, cell=cell)
    step = _x_step(x)
    if len(theo) < 2:
        distance = max(1, round(0.12 / step)) if step > 0 else 1
        prominence = max(float(np.std(corrected) * 1.5), float(corrected.max()) * 0.01)
        idx, _ = find_peaks(corrected, distance=distance, prominence=prominence)
        return sorted(int(i) for i in idx[np.argsort(corrected[idx])[::-1]][:2])

    ordered = sorted(theo.items(), key=lambda kv: kv[1])
    spacing = min(
        ordered[i + 1][1] - ordered[i][1] for i in range(len(ordered) - 1)
    )
    # 距离约束取半间距的一个分数:允许分辨到约 0.4×理论间距的两个分量
    distance = max(1, round(max(spacing * 0.4, 2 * step) / step))
    # 低门限:只挡噪声。取窗内最大值的 3% 与 3σ 中的较大者。
    floor = max(float(corrected.max()) * 0.03, float(np.std(corrected)) * 0.5)
    idx, _ = find_peaks(corrected, distance=distance, prominence=floor)
    candidates = [int(i) for i in idx]
    if not candidates:
        return []

    # 按理论位置就近配对,一个极大值只用一次
    available = list(candidates)
    found: list[int] = []
    for _name, center in ordered:
        if not available:
            break
        best = min(available, key=lambda i: abs(float(x[i]) - center))
        # 太远的极大值不属于这个分量(窗内没有对应峰)
        if abs(float(x[best]) - center) > max(spacing, 4 * step):
            continue
        found.append(best)
        available.remove(best)
    return sorted(set(found))


def _pair_theoretical_positions(
    labels: tuple[str, ...],
    wavelength: float,
    cell: tuple[float, float] | None = None,
) -> dict[str, float]:
    """双峰名字 → 理论 2θ。cell 给定用精修胞,否则回退典型 R-3m 胞。"""
    from ..peaks.annotate import _label_to_hkl

    a, c = cell if cell is not None else _TYPICAL_CELL
    out: dict[str, float] = {}
    for label in labels:
        hkl = _label_to_hkl(label)
        if hkl is None:
            continue
        h, k, l = hkl
        inv_d2 = (4.0 / 3.0) * (h * h + h * k + k * k) / (a * a) + (l * l) / (c * c)
        d = 1.0 / np.sqrt(inv_d2)
        ratio = wavelength / (2.0 * d)
        if ratio <= 1.0:
            out[label] = float(np.degrees(2.0 * np.arcsin(ratio)))
    return out


def _observe_pair_without_fit(
    window: PeakWindow,
    x: np.ndarray,
    y: np.ndarray,
    wavelength: float,
    cell: tuple[float, float] | None = None,
) -> dict[str, Any]:
    corrected = _baseline_correct(x, y)
    step = _x_step(x)
    indices = np.asarray(
        _pair_component_indices(x, corrected, window, wavelength, cell=cell),
        dtype=np.intp,
    )
    pairs: dict[str, Any] = {}
    labels = window.pair or ()
    # 按理论位置配对,不按 2θ 升序 zip —— 旧写法假设"低角分量 = labels[0]",
    # 而层状正极上 (006) 弱、102 分量强,顺序假设不成立时两个名字整体对调。
    named = _pair_positions_by_label(
        labels, [float(x[i]) for i in indices], wavelength, cell=cell
    )
    by_pos = {float(x[i]): int(i) for i in indices}
    for label, pos in named.items():
        idx = by_pos.get(pos)
        if idx is None:
            continue
        pairs[label] = _PeakObservation(
            label,
            float(x[idx]),
            float(corrected[idx]),
            float(_local_area(x, corrected, int(idx))),
            _estimate_fwhm(x, corrected, int(idx)),
        ).as_dict()
    split_status = _split_status(pairs, None, None, step)
    if len(indices) < 2:
        split_status = "unresolved"
    return {
        "peaks": pairs,
        "peakSeparation": _separation(pairs),
        "valleyRatio": _valley_ratio(x, corrected, pairs),
        "singleFitError": None,
        "doubleFitError": None,
        "fitImprovement": None,
        "splitStatus": split_status,
        "fit": False,
    }


def _fit_pair(
    window: PeakWindow,
    x: np.ndarray,
    y: np.ndarray,
    wavelength: float,
    cell: tuple[float, float] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    corrected = _baseline_correct(x, y)
    step = _x_step(x)
    # 与 _observe_pair_without_fit 用同一个分量定位器:按晶体学理论位置
    # 就近各找一个局部极大值,不依赖全窗统一的 prominence 门限(那个门限
    # 会被强分量抬高,导致弱分量永远检不出,双峰退化单峰)。
    candidates = np.asarray(
        _pair_component_indices(x, corrected, window, wavelength, cell=cell),
        dtype=np.intp,
    )
    if candidates.size < 2:
        return _observe_pair_without_fit(window, x, y, wavelength, cell=cell), [
            f"{window.label} 未找到两个可分离峰,按未解析处理"
        ]

    candidates = np.sort(candidates)
    centers = x[candidates]
    amplitudes = corrected[candidates]
    sigma0 = max(step * 1.5, (x[-1] - x[0]) / 30.0)
    baseline0 = float(np.median(np.r_[y[:2], y[-2:]]))

    def one_model(xx, b0, b1, amp, center, sigma):
        return b0 + b1 * (xx - x.mean()) + amp * np.exp(
            -0.5 * ((xx - center) / sigma) ** 2
        )

    def two_model(xx, b0, b1, amp1, center1, sigma1, amp2, center2, sigma2):
        return (
            b0 + b1 * (xx - x.mean())
            + amp1 * np.exp(-0.5 * ((xx - center1) / sigma1) ** 2)
            + amp2 * np.exp(-0.5 * ((xx - center2) / sigma2) ** 2)
        )

    span = max(window.half_width, 0.25)
    lower_one = [-np.inf, -np.inf, 0.0, centers[0] - span, max(step / 2, 1e-5)]
    upper_one = [np.inf, np.inf, np.inf, centers[0] + span, window.half_width]
    lower_two = [
        -np.inf, -np.inf, 0.0, x[0], max(step / 2, 1e-5),
        0.0, x[0], max(step / 2, 1e-5),
    ]
    upper_two = [
        np.inf, np.inf, np.inf, x[-1], window.half_width,
        np.inf, x[-1], window.half_width,
    ]
    try:
        one_params, _ = curve_fit(
            one_model,
            x,
            y,
            p0=[baseline0, 0.0, float(amplitudes[0]), float(centers[0]), sigma0],
            bounds=(lower_one, upper_one),
            maxfev=20000,
        )
        two_params, _ = curve_fit(
            two_model,
            x,
            y,
            p0=[
                baseline0, 0.0, float(amplitudes[0]), float(centers[0]), sigma0,
                float(amplitudes[1]), float(centers[1]), sigma0,
            ],
            bounds=(lower_two, upper_two),
            maxfev=30000,
        )
    except Exception as exc:
        return _observe_pair_without_fit(window, x, y, wavelength, cell=cell), [
            f"{window.label} 双峰拟合失败,已退回局部峰检测: {type(exc).__name__}"
        ]

    one_error = float(np.mean((y - one_model(x, *one_params)) ** 2))
    two_error = float(np.mean((y - two_model(x, *two_params)) ** 2))
    improvement = (one_error - two_error) / max(one_error, 1e-12)
    fitted = sorted(
        [
            ("first", two_params[2], two_params[3], two_params[4]),
            ("second", two_params[5], two_params[6], two_params[7]),
        ],
        key=lambda item: item[2],
    )
    labels = window.pair or ("peak1", "peak2")
    peaks: dict[str, Any] = {}
    # 按**理论位置**配对,不按 2θ 升序 zip。
    #
    # 旧的 `zip(labels, fitted)` 假设"低角分量 = labels[0]"(即 (006))。
    # 但层状正极实验上 (006) 弱、102 分量强,而且两者的理论间距对 c 高度
    # 敏感 —— 顺序假设一旦不成立,两个名字整体对调,还会一路传进晶胞
    # 最小二乘把 a/c 带偏。改成按晶体学位置就近配对。
    center_list = [float(item[2]) for item in fitted]
    amp_by_center = {float(item[2]): float(item[1]) for item in fitted}
    sigma_by_center = {float(item[2]): float(item[3]) for item in fitted}
    named = _pair_positions_by_label(labels, center_list, wavelength, cell=cell)
    for label, center in named.items():
        amp = max(0.0, amp_by_center.get(center, 0.0))
        sigma = max(sigma_by_center.get(center, 0.0), 0.0)
        peaks[label] = _PeakObservation(
            label,
            float(center),
            amp,
            float(amp * sigma * np.sqrt(2.0 * np.pi)),
            float(2.354820045 * sigma),
            fit_error=two_error,
            source="double_gaussian",
        ).as_dict()

    valley = _valley_ratio(x, corrected, peaks)
    separation = _separation(peaks)
    return {
        "peaks": peaks,
        "peakSeparation": separation,
        "valleyRatio": valley,
        "singleFitError": one_error,
        "doubleFitError": two_error,
        "fitImprovement": float(improvement),
        "splitStatus": _split_status(peaks, improvement, valley, step),
        "fit": True,
    }, []


def _separation(peaks: dict[str, Any]) -> float | None:
    values = [v["position"] for v in peaks.values() if v]
    return float(values[1] - values[0]) if len(values) >= 2 else None


def _valley_ratio(x: np.ndarray, y: np.ndarray, peaks: dict[str, Any]) -> float | None:
    values = sorted(
        [(v["position"], v["height"]) for v in peaks.values() if v],
        key=lambda item: item[0],
    )
    if len(values) < 2:
        return None
    mask = (x >= values[0][0]) & (x <= values[1][0])
    if int(mask.sum()) < 2:
        return None
    valley = float(np.min(y[mask]))
    return float(valley / max(min(values[0][1], values[1][1]), 1e-12))


def _split_status(
    peaks: dict[str, Any],
    improvement: float | None,
    valley: float | None,
    step: float,
) -> str:
    separation = _separation(peaks)
    if separation is None:
        return "not_found"
    if separation < max(3.0 * step, 0.12):
        return "unresolved"
    if improvement is not None and improvement >= 0.20 and (valley is None or valley < 0.88):
        return "clear"
    if (improvement is not None and improvement >= 0.05) or (valley is not None and valley < 0.96):
        return "weak"
    return "unresolved"


def _mixing_result(observations: dict[str, Any], profile: HighNiProfile) -> dict[str, Any]:
    peak003 = observations.get("003")
    peak104 = observations.get("104")
    area_ratio = _ratio(peak003, peak104, "area")
    height_ratio = _ratio(peak003, peak104, "height")
    pair = observations.get("006_102") or {}
    pair_peaks = pair.get("peaks", {}) if isinstance(pair, dict) else {}
    p101 = observations.get("101")
    # R_order = [A(006)+A(102)] / A(101)。分子要求 006/102 真正分裂成两峰,
    # 未分裂时只给单峰面积会凭空压低分母 —— 意义不成立,返回 None 并提示。
    r_order = None
    split_status = pair.get("splitStatus") if isinstance(pair, dict) else None
    if p101 and p101.get("area"):
        areas = [p.get("area") for p in pair_peaks.values() if p]
        if len(areas) >= 2 and split_status not in ("unresolved", "not_found"):
            r_order = float(sum(areas) / p101["area"])

    risk, reference_mode, confidence, risk_warning = _risk_from_height_ratio(
        height_ratio, area_ratio, profile
    )
    warnings = [risk_warning] if risk_warning else []
    if area_ratio is None and height_ratio is None:
        warnings.append("003 或 104 峰未可靠识别,无法计算面积比")
    if split_status in ("unresolved", "not_found") and (p101 and p101.get("area")):
        warnings.append("006/102 未分裂,无法按 R_order = [A(006)+A(102)]/A(101) 报告层状有序性")
    return {
        "i003_i104_area_ratio": area_ratio,
        "i003_i104_height_ratio": height_ratio,
        "r_order": r_order,
        "risk": risk,
        "referenceMode": reference_mode,
        "confidence": confidence,
        "evidence": ["003/104 面积比", "003/104 峰高比", "006/102 双峰结果"],
        "warnings": warnings,
        # 各观察峰的积分边界,供 UI 在图上叠加
        "peaks_area_bounds": {
            "003": [peak003.get("left_bound"), peak003.get("right_bound")] if peak003 else None,
            "104": [peak104.get("left_bound"), peak104.get("right_bound")] if peak104 else None,
            "101": [p101.get("left_bound"), p101.get("right_bound")] if p101 else None,
        },
    }


def _risk_from_height_ratio(
    height_ratio: float | None,
    area_ratio: float | None,
    profile: HighNiProfile,
) -> tuple[str, str, str, str | None]:
    """按 003/104 峰高比做混排风险分级。

    文献常用的混排筛查量是峰高比 I(003)/I(104) > 1.2(代表良好层状)。
    峰高不受两峰固有展宽差影响,而面积比会被 (FWHM_003/FWHM_104) 系统性
    压低 —— 用面积比分级会把清晰分层的样品误判成高混排风险(缺陷 1)。
    面积比仍返回给用户作参考,但分级以高度比为准。
    """
    primary = height_ratio if height_ratio is not None else area_ratio
    if primary is None:
        return "unknown", "none", "low", None
    # 优先读峰高比阈值;旧档案只存了面积比阈值时回退(向后兼容)。
    low = profile.thresholds.get("i003_i104_height_low")
    good = profile.thresholds.get("i003_i104_height_good")
    if low is None or good is None:
        low = profile.thresholds.get("i003_i104_area_low")
        good = profile.thresholds.get("i003_i104_area_good")
    if low is None or good is None:
        return "unknown", "none", "low", "未配置参考样品或阈值,仅报告比值"
    if primary < low:
        risk = "high"
    elif primary < good:
        risk = "medium"
    else:
        risk = "low"
    return risk, "literature_hint", "low", "风险分级使用经验阈值(峰高比),不是 Li/Ni 占位率标定"


def _layered_result(observations: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    required = [observations.get(key) for key in ("003", "101", "104")]
    split006 = observations.get("006_102") or {}
    split018 = observations.get("018_110") or {}
    statuses = [split006.get("splitStatus"), split018.get("splitStatus")]
    found_primary = all(item is not None for item in required)
    if not found_primary:
        order = "unknown"
    elif statuses == ["clear", "clear"]:
        order = "good"
    elif any(status == "not_found" for status in statuses):
        order = "degraded"
    elif any(status == "unresolved" for status in statuses):
        order = "acceptable"
    else:
        order = "acceptable"

    evidence: list[str] = []
    if found_primary:
        evidence.append("003/101/104 主峰顺序可识别")
    if split006.get("splitStatus"):
        evidence.append(f"006/102 {split006['splitStatus']}")
    if split018.get("splitStatus"):
        evidence.append(f"108/110 {split018['splitStatus']}")
    local_warnings = list(warnings)
    local_warnings.append("R-3m 结论是粉末 XRD 层状特征筛查,不等于排除表面岩盐重构")
    return {
        "layeredOrder": order,
        "split006_102": split006,
        "split018_110": split018,
        "evidence": evidence,
        "confidence": "medium" if found_primary else "low",
        "warnings": local_warnings,
    }


def _hkl_from_pair_label(
    name: str, labels: tuple[str, ...], pair_key: str
) -> tuple[int, int, int] | None:
    """把双峰分量名(如 "102")解析成 hkl。

    名字可能带窗口前缀("006_102:102")也可能不带,两种都接受。
    """
    from ..peaks.annotate import _label_to_hkl

    bare = name.split(":")[-1]
    if bare not in labels:
        return None
    return _label_to_hkl(bare)


def _lattice_result(observations: dict[str, Any], wavelength: float) -> dict[str, Any]:
    indexed: list[tuple[tuple[int, int, int], float, str]] = []
    for key, hkl in (("003", (0, 0, 3)), ("101", (1, 0, 1)), ("104", (1, 0, 4))):
        peak = observations.get(key)
        if peak:
            indexed.append((hkl, float(peak["position"]), key))
    for pair_key, labels in (
        ("006_102", ("006", "102")),
        ("018_110", ("108", "110")),
    ):
        pair = observations.get(pair_key) or {}
        pair_peaks = pair.get("peaks", {})
        # hkl **由名字解析**,不再写死。
        #
        # 顺序假设(低角一定 = 006)一旦不成立两个 hkl 就整体对调。
        # 用名字解析后,标签、晶胞、归属三者用的是同一套晶体学定义。
        for name, peak in pair_peaks.items():
            if not peak:
                continue
            hkl = _hkl_from_pair_label(name, labels, pair_key)
            if hkl is not None:
                indexed.append((hkl, float(peak["position"]), f"{pair_key}:{name}"))

    if len(indexed) < 3:
        return {
            "a": None, "c": None, "c_over_a": None, "cell_volume": None,
            "fit_rms": None, "zero_shift": 0.0,
            "uncertainty_a": None, "uncertainty_c": None,
            "used_peaks": [item[2] for item in indexed],
            "confidence": "low",
            "warnings": ["可靠峰数量不足,无法联合拟合晶胞参数"],
        }

    rows: list[list[float]] = []
    values: list[float] = []
    used: list[str] = []
    for hkl, position, name in indexed:
        d = float(two_theta_to_d(position, wavelength))
        if not np.isfinite(d) or d <= 0:
            continue
        h, k, l_idx = hkl
        rows.append([4.0 / 3.0 * (h * h + h * k + k * k), float(l_idx * l_idx)])
        values.append(1.0 / (d * d))
        used.append(name)
    if len(rows) < 3:
        return {
            "a": None, "c": None, "c_over_a": None, "cell_volume": None,
            "fit_rms": None, "zero_shift": 0.0,
            "uncertainty_a": None, "uncertainty_c": None,
            "used_peaks": used, "confidence": "low",
            "warnings": ["峰位无法转换为有效 d-spacing"],
        }

    matrix = np.asarray(rows, dtype=np.float64)
    target = np.asarray(values, dtype=np.float64)
    solution, _residuals, _rank, _singular = np.linalg.lstsq(matrix, target, rcond=None)
    if np.any(solution <= 0):
        return {
            "a": None, "c": None, "c_over_a": None, "cell_volume": None,
            "fit_rms": None, "zero_shift": 0.0,
            "uncertainty_a": None, "uncertainty_c": None,
            "used_peaks": used, "confidence": "low",
            "warnings": ["联合拟合得到非物理解,请检查峰标记或零点偏移"],
        }
    a = float(1.0 / np.sqrt(solution[0]))
    c = float(1.0 / np.sqrt(solution[1]))
    predicted = matrix @ solution
    rms = float(np.sqrt(np.mean((predicted - target) ** 2)))
    volume = float(np.sqrt(3.0) / 2.0 * a * a * c)
    uncertainty_a, uncertainty_c = _lattice_uncertainty(matrix, target, solution, a, c)
    return {
        "a": a,
        "c": c,
        "c_over_a": float(c / a),
        "cell_volume": volume,
        "fit_rms": rms,
        "zero_shift": 0.0,
        "uncertainty_a": uncertainty_a,
        "uncertainty_c": uncertainty_c,
        "used_peaks": used,
        "confidence": "medium" if len(used) >= 4 else "low",
        "warnings": ["零点校正尚未拟合,晶胞参数适合做同仪器同体系趋势比较"],
    }


def _lattice_uncertainty(
    matrix: np.ndarray,
    target: np.ndarray,
    solution: np.ndarray,
    a: float,
    c: float,
) -> tuple[float | None, float | None]:
    if matrix.shape[0] <= 2:
        return None, None
    dof = matrix.shape[0] - 2
    variance = float(np.sum((matrix @ solution - target) ** 2) / dof)
    try:
        covariance = variance * np.linalg.inv(matrix.T @ matrix)
    except np.linalg.LinAlgError:
        return None, None
    sigma_u = float(np.sqrt(max(covariance[0, 0], 0.0)))
    sigma_v = float(np.sqrt(max(covariance[1, 1], 0.0)))
    return float(0.5 * a**3 * sigma_u), float(0.5 * c**3 * sigma_v)


def _overall_confidence(result: dict[str, Any]) -> str:
    levels = []
    for key in ("mixing", "layered", "lattice"):
        value = result.get(key)
        if isinstance(value, dict) and value.get("confidence"):
            levels.append(value["confidence"])
    if "high" in levels:
        return "high"
    if levels and all(level == "medium" for level in levels):
        return "medium"
    return "low"


def _ratio(first: dict[str, Any] | None, second: dict[str, Any] | None, key: str) -> float | None:
    if not first or not second or not second.get(key):
        return None
    return float(first.get(key, 0.0) / second[key])


def _local_area(x: np.ndarray, y: np.ndarray, idx: int) -> float:
    left = max(0, idx - max(2, x.size // 20))
    right = min(x.size, idx + max(3, x.size // 20) + 1)
    return float(np.trapezoid(y[left:right], x[left:right]))


def _estimate_fwhm(x: np.ndarray, y: np.ndarray, idx: int) -> float | None:
    half = y[idx] * 0.5
    left = idx
    while left > 0 and y[left] >= half:
        left -= 1
    right = idx
    while right < y.size - 1 and y[right] >= half:
        right += 1
    if left == idx or right == idx:
        return None
    return float(x[right] - x[left])


def _x_step(x: np.ndarray) -> float:
    if x.size < 2:
        return 0.0
    return float(np.median(np.diff(x)))
