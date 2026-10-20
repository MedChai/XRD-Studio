"""XRD 坐标系统换算。

支持 2θ(度)、d-spacing(Å)、Q(Å⁻¹)三种采集轴之间的换算。

换算关系(布拉格定律与散射矢量定义):

    λ = 2 d sinθ
    Q = 2π / d = 4π sinθ / λ

其中 θ 是掠射角,而实验室粉末衍射仪记录的是 2θ,因此:

    θ = (2θ) / 2

本模块是 §4.1 在**引擎侧**的唯一实现点,引擎内所有坐标换算必须走这里,
不允许在其它引擎模块里另写一份公式。

例外:视图层 2θ/d/Q 切换是纯显示参数(§7,不走引擎),其换算在前端
`app/src/services/coords.ts` —— 那是本文件的**镜像实现**,公式必须一致,
改任一边都要同步另一边。
"""

from __future__ import annotations

import math
from typing import Literal

import numpy as np

# 采集轴类型。generic 表示无法判定的自由轴,不做换算。
AxisType = Literal["two_theta", "d", "q", "generic"]

# 常用辐射波长(Å)。Cu Kα1 为默认值。
WAVELENGTHS: dict[str, float] = {
    "Cu Ka1": 1.5405980,
    "Cu Ka": 1.5418,  # Kα1/Kα2 加权平均,实验室常规使用
    "Co Ka1": 1.7889650,
    "Co Ka": 1.7890,
    "Fe Ka1": 1.9360420,
    "Fe Ka": 1.9373,
    "Mo Ka1": 0.7093000,
    "Mo Ka": 0.7107,
    "Cr Ka1": 2.2897000,
    "Cr Ka": 2.2910,
    "Ag Ka1": 0.5594075,
    "Ga Ka1": 1.3401000,
}

DEFAULT_WAVELENGTH = WAVELENGTHS["Cu Ka"]

# 同步辐射常用的固定能量/波长不在上表内,由用户在 Sample metadata
# 里直接填 wavelength 数值(V0.3 已包含该字段,见 §8.2)。


def two_theta_to_d(two_theta_deg: np.ndarray | float, wavelength: float) -> np.ndarray | float:
    """2θ(度)→ d-spacing(Å)。

    由 λ = 2 d sinθ 得 d = λ / (2 sin(2θ/2))。
    """
    theta = np.radians(np.asarray(two_theta_deg, dtype=np.float64) / 2.0)
    sin_theta = np.sin(theta)
    # 2θ → 0 时 d → ∞,这里不抛异常,交由调用方按坐标轴下限过滤
    with np.errstate(divide="ignore", invalid="ignore"):
        result = wavelength / (2.0 * sin_theta)
    if np.isscalar(two_theta_deg):
        return float(result)
    return result


def d_to_two_theta(d_spacing: np.ndarray | float, wavelength: float) -> np.ndarray | float:
    """d-spacing(Å)→ 2θ(度)。

    由 d = λ / (2 sinθ) 得 sinθ = λ / (2d),2θ = 2 asin(λ / 2d)。
    d < λ/2 时无解(该反射在物理上不可观测),返回 NaN。
    """
    d = np.asarray(d_spacing, dtype=np.float64)
    ratio = wavelength / (2.0 * d)
    with np.errstate(invalid="ignore"):
        result = np.degrees(2.0 * np.arcsin(np.where(np.abs(ratio) <= 1.0, ratio, np.nan)))
    if np.isscalar(d_spacing):
        return float(result)
    return result


def two_theta_to_q(two_theta_deg: np.ndarray | float, wavelength: float) -> np.ndarray | float:
    """2θ(度)→ Q(Å⁻¹),Q = 4π sinθ / λ。"""
    theta = np.radians(np.asarray(two_theta_deg, dtype=np.float64) / 2.0)
    result = 4.0 * math.pi * np.sin(theta) / wavelength
    if np.isscalar(two_theta_deg):
        return float(result)
    return result


def q_to_two_theta(q: np.ndarray | float, wavelength: float) -> np.ndarray | float:
    """Q(Å⁻¹)→ 2θ(度)。

    由 Q = 4π sinθ / λ 得 sinθ = Qλ / 4π。
    """
    q_arr = np.asarray(q, dtype=np.float64)
    ratio = q_arr * wavelength / (4.0 * math.pi)
    with np.errstate(invalid="ignore"):
        result = np.degrees(2.0 * np.arcsin(np.where(np.abs(ratio) <= 1.0, ratio, np.nan)))
    if np.isscalar(q):
        return float(result)
    return result


def d_to_q(d_spacing: np.ndarray | float) -> np.ndarray | float:
    """d-spacing(Å)→ Q(Å⁻¹),Q = 2π / d。无需波长。"""
    result = 2.0 * math.pi / np.asarray(d_spacing, dtype=np.float64)
    if np.isscalar(d_spacing):
        return float(result)
    return result


def q_to_d(q: np.ndarray | float) -> np.ndarray | float:
    """Q(Å⁻¹)→ d-spacing(Å)。"""
    result = 2.0 * math.pi / np.asarray(q, dtype=np.float64)
    if np.isscalar(q):
        return float(result)
    return result


def convert(
    x: np.ndarray,
    from_axis: AxisType,
    to_axis: AxisType,
    wavelength: float = DEFAULT_WAVELENGTH,
) -> np.ndarray:
    """在任意两种采集轴之间换算整条曲线的 x 数组。

    generic 轴不参与换算:from 与 to 不同且任一为 generic 时抛 ValueError。
    同轴返回输入的副本,避免调用方意外共享内存。
    """
    x_arr = np.asarray(x, dtype=np.float64)
    if from_axis == to_axis:
        return x_arr.copy()
    if from_axis == "generic" or to_axis == "generic":
        raise ValueError(f"无法在 generic 轴上换算:{from_axis} → {to_axis}")

    if from_axis == "two_theta":
        if to_axis == "d":
            return np.asarray(two_theta_to_d(x_arr, wavelength))
        return np.asarray(two_theta_to_q(x_arr, wavelength))

    if from_axis == "d":
        if to_axis == "two_theta":
            return np.asarray(d_to_two_theta(x_arr, wavelength))
        return np.asarray(d_to_q(x_arr))

    # from_axis == "q"
    if to_axis == "two_theta":
        return np.asarray(q_to_two_theta(x_arr, wavelength))
    return np.asarray(q_to_d(x_arr))


def axis_label(axis: AxisType) -> str:
    """返回坐标轴在 UI 与导出文件里的标准显示名。"""
    return {
        "two_theta": "2θ (°)",
        "d": "d (Å)",
        "q": "Q (Å⁻¹)",
        "generic": "x",
    }[axis]


def axis_display_decimals(axis: AxisType) -> int:
    """§4.4 规定的各轴显示精度。"""
    return {"two_theta": 3, "d": 4, "q": 4, "generic": 3}[axis]
