"""Material profiles and configurable peak windows for high-Ni oxides."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class PeakWindow:
    key: str
    label: str
    center_2theta: float
    half_width: float
    hkl: tuple[int, int, int] | None = None
    pair: tuple[str, str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "center_2theta": self.center_2theta,
            "half_width": self.half_width,
            "hkl": self.hkl,
            "pair": self.pair,
        }


@dataclass
class HighNiProfile:
    """Project-level configuration shared by high-Ni analyses."""

    name: str = "高镍无钴层状正极默认档案"
    family: str = "high_ni_layered"
    formula: str = "LiNi0.90Mn0.05Al0.05O2"
    ni_fraction: float = 0.90
    li_tm_ratio: float = 1.0
    wavelength: float = 1.5418
    instrument_name: str = ""
    thresholds: dict[str, float] = field(default_factory=lambda: {
        # 仅作为 literature_hint,不是通用占位率标定。
        # 混排分级主要用峰高比 I(003)/I(104):经典约定 >1.2 表示层状良好。
        # area_low/area_good 保留给面积比参考(不与分级直接挂钩)。
        "i003_i104_height_low": 1.20,
        "i003_i104_height_good": 1.30,
        "i003_i104_area_low": 1.05,
        "i003_i104_area_good": 1.15,
    })
    windows: tuple[PeakWindow, ...] = field(default_factory=tuple)

    def resolved_windows(self) -> tuple[PeakWindow, ...]:
        if self.windows or self.wavelength == 1.5418:
            return self.windows or default_windows()
        def sine_at(angle: float) -> float:
            return math.sin(math.radians(angle / 2)) * self.wavelength / 1.5418

        windows = []
        for window in default_windows():
            lo = sine_at(window.center_2theta - window.half_width)
            hi = sine_at(window.center_2theta + window.half_width)
            if lo >= 1:
                continue
            left = 2 * math.degrees(math.asin(lo))
            right = 2 * math.degrees(math.asin(min(1, hi)))
            windows.append(replace(window, center_2theta=(left + right) / 2, half_width=(right - left) / 2))
        return tuple(windows)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "family": self.family,
            "formula": self.formula,
            "ni_fraction": self.ni_fraction,
            "li_tm_ratio": self.li_tm_ratio,
            "wavelength": self.wavelength,
            "instrument_name": self.instrument_name,
            "thresholds": self.thresholds,
            "windows": [window.as_dict() for window in self.resolved_windows()],
        }


def default_windows() -> tuple[PeakWindow, ...]:
    """Initial Cu Kα windows for a layered NCM/NMA-like pattern.

    These are intentionally broad and configurable.  The analyzer uses
    relative ordering and local peak evidence rather than treating one fixed
    2θ value as a phase proof.

    **双峰第二个分量对外显示文献名 `(102)`/`(108)`。** 物理指数仍是
    消光规则下的 (0,1,2)/(0,1,8)(见 `indexing._LITERATURE_HKL`);pair
    串与 `_label_to_hkl` 跟显示名走,d 间距在两套写法下相同。层状正极
    实验上 (006) 弱、强分量是 ~38.3° 那条,归属不能靠"低角就是 (006)"
    的顺序假设 —— 见 `_pair_labels`。
    """
    return (
        PeakWindow("003", "(003)", 18.60, 2.20, (0, 0, 3)),
        # (101) 与 006_102 窗口曾互相重叠([34.90,38.50] vs [37.00,40.30]),
        # 于是 (101)@36.5 会落进双峰窗、被当成 (006) 的候选。收窄到各自
        # 理论位置附近,并保证两窗不交叠。
        PeakWindow("101", "(101)", 36.60, 1.10, (1, 0, 1)),
        # 窗口中心取两分量的中点,半宽要同时覆盖两者。
        # (006)/(102) 理论间距约 0.29°、(108)/(110) 约 0.49°,窗口太窄会
        # 把较弱的分量直接切掉 —— 实测窄窗下高角分量落在窗沿外,双峰拟合
        # 永远只找到一个峰,进而把晶胞 c 带偏 0.15 Å。
        PeakWindow(
            "006_102", "(006)/(102)", 38.09, 0.85,
            pair=("006", "102"),
        ),
        PeakWindow("104", "(104)", 44.45, 2.20, (1, 0, 4)),
        PeakWindow(
            "018_110", "(108)/(110)", 64.54, 1.30,
            pair=("108", "110"),
        ),
    )


def default_profile(wavelength: float = 1.5418) -> HighNiProfile:
    profile = HighNiProfile(wavelength=wavelength)
    return profile


def annotate_refs(
    wavelength: float = 1.5418,
    a: float = 2.878,
    c: float = 14.216,
    two_theta_range: tuple[float, float] = (10.0, 80.0),
) -> list[dict[str, Any]]:
    """参考反射表:高镍层状正极**允许**反射的 (label, 2θ) 列表。

    仅供需要"静态参考表"的场景(如通用兜底标注的默认值、外部脚本)。
    **高镍样品的实际标注请走 `peaks.annotate.annotate_high_ni`** —— 它对
    每张谱精修晶胞,而这张表用的是固定 (a, c)。

    这张表由 `indexing.allowed_reflections` 生成,因此:

    - 只含 R-3m 允许反射。双峰第二个分量的**显示名**用文献的 `(102)`/`(108)`
      (与 h-k+l 约定同一物理反射,d 相同);消光判断仍走 `-h+k+l`,见
      `indexing._LITERATURE_HKL`。
    - 覆盖数据范围内**全部**允许反射,而不是写死 7 条。(015)/(107)/
      (113)/(116)/(202) 这些高角弱反射此前在表外,永远无法被标注。
    """
    from .indexing import allowed_reflections

    return [
        {
            "label": ref.label,
            "center_2theta": round(ref.two_theta, 3),
            "hkl": ref.hkl,
        }
        for ref in allowed_reflections(a, c, wavelength, two_theta_range)
    ]
