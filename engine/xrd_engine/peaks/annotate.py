"""晶面(hkl)标注:把检测到的峰归属到层状 R-3m 的允许反射。

两类路径
--------

**高镍层状路径(默认)** —— `annotate_high_ni`。对本张谱做全谱晶胞精修
(a, c, 零点偏移),再由拟合出的各反射强度决定每个观测峰归属给谁。所有
理论位置由 `high_ni.indexing` 从 (hkl, a, c, λ) 反算,没有硬编码数字。

旧实现是"逐峰独立最近邻匹配" + 一张 7 条硬编码参考表,有两个系统性缺陷:

1. 参考表用**写死晶胞**算位置,而 (006)/(102) 的理论间距对 c 高度敏感
   (实测胞差 0.06 Å 时可偏约 0.1°),归属会整体翻转。
   双峰显示名对外用文献 (006)/(102)、(108)/(110)(同一物理反射的
   另一套指数写法,见 `high_ni.indexing._LITERATURE_HKL`)。
2. 最近邻只看几何距离,不看这个峰由哪条理论线在供强度。而 (006)/(102)
   间距只有约 0.36°,(108)/(110) 约 0.49°,观测峰稍偏一点判定就翻转。

现在改用"邻域窗口内的相对强度贡献占比"归属,并在两个候选接近时如实
返回 ambiguous,而不是硬给一个 `reliable`。

**通用兜底路径** —— `annotate_peaks(reference_table=...)`。调用方显式给出
参考表时,仍走原来的最近邻逻辑。非层状样品 / 未知物相用这条,此时没有
晶体学模型可用,最近邻是合理且保守的选择。

归属**只用峰位与峰形**,不用观测峰的强度比 —— 不同材料同一 hkl 的强度比
千差万别,强度不该参与"这是哪个晶面"的判断。拟合出的各反射强度只用于
判断"哪条理论线落在这个窗口里",这正是晶体学上正确的判据。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# 通用兜底路径的最近邻容差(2θ 度)。层状正极特征峰在半高附近 ±0.15°
# 内即可对应;低角峰密集的样品可在命令参数里缩小。
DEFAULT_TOLERANCE_DEG = 0.15

# 标注置信度:reliable(唯一归属)/ ambiguous(双峰未分开)/ none(无归属)
AnnotConfidence = str

# 高镍路径的归属窗口 = 该 2θ 处 FWHM 的倍数。太窄会漏掉略微宽化的峰,
# 太宽会把隔壁反射拖进来(实测 ±0.60° 固定窗时 (110) 有 65% 面积落进
# (108) 的窗口,导致 (108) 被永久判成 (110))。
_ASSIGN_WINDOW_SCALE = 1.6


@dataclass
class AnnotatedPeak:
    index: int
    position: float
    hkl_label: str | None
    hkl_confidence: AnnotConfidence
    hkl: tuple[int, int, int] | None = None
    ref_position: float | None = None
    offset_deg: float | None = None
    # --- 高镍路径补充的溯源字段(通用兜底路径为 None) ---
    dominance: float | None = None
    """本反射在该峰邻域内贡献的强度占比(0–1)。归属的可核对依据。"""
    candidates: list[dict[str, Any]] = field(default_factory=list)
    """ambiguous 时的候选 [{hkl, label, position, dominance}, ...]。

    旧实现把 ambiguous 的候选**丢掉**了,UI 只显示 "—",用户无法知道
    它到底是哪几个反射没分开。这里保留下来供 UI 展示。"""
    phase: str | None = None
    """所属物相标签。为将来多物相留位。"""

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "position": self.position,
            "hkl_label": self.hkl_label,
            "hkl_confidence": self.hkl_confidence,
            "hkl": list(self.hkl) if self.hkl else None,
            "ref_position": self.ref_position,
            "offset_deg": self.offset_deg,
            "dominance": self.dominance,
            "candidates": self.candidates,
            "phase": self.phase,
        }


@dataclass
class AnnotationContext:
    """一次标注所用的拟合上下文,供 UI 展示"标签是怎么来的"。"""

    cell: tuple[float, float]
    zero_shift: float
    fwhm_0: float
    fwhm_slope: float
    rwp: float
    phase: str = "R-3m 层状"

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "a": self.cell[0],
            "c": self.cell[1],
            "zeroShift": self.zero_shift,
            "fwhm0": self.fwhm_0,
            "fwhmSlope": self.fwhm_slope,
            "rwp": self.rwp,
        }


def annotate_high_ni(
    x: np.ndarray,
    y: np.ndarray,
    peaks: list[Any],
    *,
    wavelength: float | None = None,
    dataset_id: int | None = None,
) -> tuple[list[AnnotatedPeak], AnnotationContext | None]:
    """按层状 R-3m 模型标注峰:先精修晶胞,再按强度贡献归属。

    返回 (标注列表, 拟合上下文)。拟合失败时上下文为 None,所有峰标记为
    none —— 宁可不标,也不要拿一个没拟合过的晶胞去硬套。

    x/y 必须是**该峰所在的同一条曲线**(原始谱即可;不需要扣背景,模型里
    自带多项式背景)。

    dataset_id 给定时,晶胞精修结果按数据集缓存(见 `_FIT_CACHE`)。这不是
    可有可无的优化:精修单次约 1.5–4.5 秒,而正式标注(peaks.detect /
    peaks.annotate / domain 分析)都走这里 —— 不缓存的话每次点标注、切
    数据集触发的补标都要重跑全谱精修。`analysis.preview` 交互预览传
    annotate=false,不再调用本函数(标签由 annotation_json 快照恢复)。
    """
    from ..high_ni.indexing import assign_peaks

    x_arr = np.asarray(x, dtype=np.float64).ravel()
    y_arr = np.asarray(y, dtype=np.float64).ravel()

    if x_arr.size < 16 or not peaks:
        return [_none(p) for p in peaks], None

    lam = float(wavelength) if wavelength else 1.5418
    fit = _cached_fit(x_arr, y_arr, lam, dataset_id)
    if fit is None:
        return [_none(p) for p in peaks], None

    assignments = assign_peaks(x_arr, peaks, fit)
    out: list[AnnotatedPeak] = []
    for a in assignments:
        out.append(
            AnnotatedPeak(
                index=a.index,
                position=a.position,
                hkl_label=a.label,
                hkl_confidence=a.confidence,
                hkl=a.hkl,
                ref_position=a.ref_position,
                offset_deg=a.offset_deg,
                dominance=round(a.dominance, 4),
                candidates=a.candidates,
                phase="R-3m 层状" if a.confidence != "none" else None,
            )
        )

    context = AnnotationContext(
        cell=fit.cell,
        zero_shift=fit.zero_shift,
        fwhm_0=fit.fwhm_0,
        fwhm_slope=fit.fwhm_slope,
        rwp=fit.rwp,
    )
    return out, context


# 晶胞精修缓存:dataset_id -> (数据指纹, CellFit)。
#
# **为什么必须缓存。** 精修一次全谱要跑上千个格点、每个格点解一次非负
# 最小二乘,单次约 1.5–4.5 秒(取决于点数)。而 analysis.preview 每次都
# 调用标注,前端切数据集 / 拖滑块都会触发 preview —— 用户看到的就是
# "切几次图就卡死"。晶胞只取决于原始曲线与波长,与处理参数无关,
# 所以同一数据集完全不必重算。
#
# 指纹用 (点数, x 首尾, y 和) 而不是 id 单键:数据集被删后 id 可能复用,
# 只按 id 缓存会拿到上一条曲线的晶胞 —— 那会让标签静默错位。
_FIT_CACHE: dict[int, tuple[tuple, object]] = {}
_FIT_CACHE_LIMIT = 64


def _data_fingerprint(x: np.ndarray, y: np.ndarray, lam: float) -> tuple:
    return (
        int(x.size),
        round(float(x[0]), 6),
        round(float(x[-1]), 6),
        round(float(y.sum()), 3),
        round(float(lam), 6),
    )


def _cached_fit(x: np.ndarray, y: np.ndarray, lam: float, dataset_id: int | None):
    """取(或计算)该数据集的晶胞精修结果。dataset_id 为 None 时不缓存。

    反序胞(c²≤24a²)与 refine_cell 的过滤双保险:历史缓存或并发窗口里
    若混进反序解,这里也不返回、不写入 —— 否则 assign_peaks 会把高角
    (110) 峰标成 (108)。
    """
    from ..high_ni.indexing import doublet_order_ok, refine_cell

    if dataset_id is None:
        fit = refine_cell(x, y, wavelength=lam)
        return fit if fit is not None and doublet_order_ok(fit.a, fit.c) else None

    key = int(dataset_id)
    fingerprint = _data_fingerprint(x, y, lam)
    hit = _FIT_CACHE.get(key)
    if hit is not None and hit[0] == fingerprint:
        fit = hit[1]
        if fit is not None and doublet_order_ok(fit.a, fit.c):
            return fit

    fit = refine_cell(x, y, wavelength=lam)
    if fit is not None and doublet_order_ok(fit.a, fit.c):
        if len(_FIT_CACHE) >= _FIT_CACHE_LIMIT:
            # 简单的 FIFO 淘汰:dict 保序,丢掉最早插入的即可
            _FIT_CACHE.pop(next(iter(_FIT_CACHE)))
        _FIT_CACHE[key] = (fingerprint, fit)
        return fit
    return None


def invalidate_fit_cache(dataset_id: int | None = None) -> None:
    """数据集被删除 / 重新导入时清缓存,避免 id 复用拿到旧晶胞。"""
    if dataset_id is None:
        _FIT_CACHE.clear()
    else:
        _FIT_CACHE.pop(int(dataset_id), None)


def annotate_peaks(
    peaks: list[Any],
    reference_table: list[dict[str, Any]] | None = None,
    tolerance: float = DEFAULT_TOLERANCE_DEG,
) -> list[AnnotatedPeak]:
    """通用兜底:按峰位最近邻匹配到调用方给定的参考表。

    **只在调用方显式给出 reference_table 时使用这条路径。** 默认(高镍)
    路径请用 `annotate_high_ni` —— 它做全谱晶胞精修,能区分 (006)/(102)
    这类间距仅 0.36° 的双峰,而最近邻不能。

    peaks: 有 `.position` 与 `.index` 的对象(引擎 Peak)。
    reference_table: [{"label", "center_2theta", "hkl"(可选)}, ...]。
    tolerance: 峰位到参考 2θ 的最大允许偏差。
    """
    if reference_table is None:
        # 没有参考表就无法做最近邻。这里不再回退到旧的高镍硬编码表 ——
        # 固定晶胞的表在无钴端元上会偏约 0.1°,静默使用会配错双峰。
        raise ValueError(
            "annotate_peaks 需要显式 reference_table;高镍层状样品请改用 annotate_high_ni"
        )

    refs = reference_table
    ref_positions = [float(r["center_2theta"]) for r in refs]

    out: list[AnnotatedPeak] = []
    for peak in peaks:
        pos = float(peak.position)
        idx = int(peak.index)

        diffs = [abs(pos - rp) for rp in ref_positions]
        if not diffs:
            out.append(_none(peak))
            continue
        min_diff = min(diffs)
        if min_diff > tolerance:
            out.append(_none(peak))
            continue

        # 容差内的全部候选按距离排序。旧实现只挑"与最小距离相差 < 1e-9"
        # 的(要求精确浮点相等,实际不可达),导致 ambiguous 形同虚设。
        # 这里改为:与最近者相差在容差的 25% 以内即视为并列候选。
        near = min(0.25 * tolerance, 0.05)
        candidates = [
            refs[i] for i, d in enumerate(diffs) if d - min_diff <= near
        ]
        if len(candidates) > 1:
            out.append(_none(peak, ambiguous=True, candidates=candidates))
            continue

        ref = candidates[0]
        hkl = tuple(int(v) for v in ref["hkl"]) if ref.get("hkl") else None
        out.append(
            AnnotatedPeak(
                index=idx,
                position=pos,
                hkl_label=ref["label"],
                hkl_confidence="reliable",
                hkl=hkl,
                ref_position=ref["center_2theta"],
                offset_deg=round(pos - ref["center_2theta"], 4),
                candidates=[
                    {
                        "hkl": list(hkl) if hkl else None,
                        "label": ref["label"],
                        "position": ref["center_2theta"],
                        "dominance": 1.0,
                    }
                ],
            )
        )
    return out


def _label_to_hkl(label: str) -> tuple[int, int, int] | None:
    """把 `"(102)"` 这样的标签解析回 (1,0,2)。

    存在的理由:高镍分析的双峰对窗口用**标签名**配置("006"/"102"),
    而归属需要按**理论位置**配对,理论位置又只能由 hkl 反算。没有这条
    通路,窗口配置与晶体学模型就会各写一份数字。

    双峰显示名跟文献走 (102)/(108);解析出的 (1,0,2) 与消光指数 (0,1,2)
    的 d 相同,理论位置一致。不要把这里的返回值再丢回 `allowed_hkl`。
    """
    text = label.strip().strip("()")
    if not text or not text.isdigit() or len(text) > 3:
        return None
    digits = [int(ch) for ch in text]
    # 一位数写法 "(003)" 是四字符去括号后三位;补零到三位。
    if len(digits) == 1:
        return (0, 0, digits[0])
    if len(digits) == 2:
        return (0, digits[0], digits[1])
    return (digits[0], digits[1], digits[2])


def pair_labels_by_position(
    pair: tuple[str, str],
    positions: list[float],
    a: float,
    c: float,
    wavelength: float,
) -> dict[str, float]:
    """把观测到的两个峰位按**理论位置**配到双峰的名字上。

    旧实现直接按 2θ 升序 zip 到 (低角名, 高角名)。那假设了"低角分量一定
    是 (006)",而 (006)/(102) 的理论间距对 c 高度敏感、且 102 才是强
    分量 —— 顺序假设一旦不成立,两个名字就整体对调,并且会一路传进
    晶胞最小二乘,把 a/c 一起带偏。

    这里改为:对每个观测位置,取理论位置最近的那个名字。
    """
    theo = _pair_theoretical(pair, a, c, wavelength)
    if len(theo) < 2:
        # 拿不到理论位置时退回顺序假设,但调用方应视之为低置信
        ordered = sorted(positions)
        return dict(zip(sorted(pair), ordered, strict=False))

    names = list(theo.keys())
    out: dict[str, float] = {}
    for pos in sorted(positions):
        best = min(names, key=lambda nm: abs(theo[nm] - pos))
        out[best] = pos
    return out


def _pair_theoretical(
    pair: tuple[str, str], a: float, c: float, wavelength: float
) -> dict[str, float]:
    out: dict[str, float] = {}
    for label in pair:
        hkl = _label_to_hkl(label)
        if hkl is None:
            continue
        h, k, l = hkl
        inv_d2 = (4.0 / 3.0) * (h * h + h * k + k * k) / (a * a) + (l * l) / (c * c)
        d = 1.0 / math.sqrt(inv_d2)
        ratio = wavelength / (2.0 * d)
        if ratio <= 1.0:
            out[label] = math.degrees(2.0 * math.asin(ratio))
    return out


def _none(
    peak: Any,
    ambiguous: bool = False,
    candidates: list[dict[str, Any]] | None = None,
) -> AnnotatedPeak:
    cands = []
    for c in candidates or []:
        hkl = c.get("hkl")
        cands.append(
            {
                "hkl": list(hkl) if hkl else None,
                "label": c.get("label"),
                "position": c.get("center_2theta"),
                "dominance": None,
            }
        )
    return AnnotatedPeak(
        index=int(peak.index),
        position=float(peak.position),
        hkl_label=None,
        hkl_confidence="ambiguous" if ambiguous else "none",
        candidates=cands,
    )
