"""CHI workstation text exports and complex-impedance least squares."""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import product

import numpy as np
from scipy.optimize import least_squares


class ChiError(Exception):
    """A workstation file or fit cannot be interpreted."""


@dataclass
class ChiData:
    kind: str
    metadata: dict
    points: np.ndarray
    segments: list[dict]
    warnings: list[str]


_CV_COLUMNS = ("potential/v", "current/a")
_EIS_COLUMNS = ("freq/hz", "z'/ohm", 'z"/ohm', "z/ohm", "phase/deg")
_SETTINGS = {
    "Instrument Model": "instrument",
    "Init E (V)": "initialPotential",
    "High E (V)": "highPotential",
    "Low E (V)": "lowPotential",
    "Final E (V)": "finalPotential",
    "Scan Rate (V/s)": "scanRate",
    "Segment": "plannedSegments",
    "Sample Interval (V)": "sampleInterval",
    "Amplitude (V)": "amplitude",
    "Quiet Time (sec)": "quietTime",
}


def _decode(blob: bytes) -> str:
    if blob.startswith((b"\xff\xfe", b"\xfe\xff")):
        return blob.decode("utf-16", errors="replace")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return blob.decode(encoding)
        except UnicodeDecodeError:
            continue
    return blob.decode("cp1252", errors="replace")


def _segments(potential: np.ndarray) -> list[dict]:
    increments = np.diff(potential)
    moving = np.flatnonzero(increments != 0)
    if not moving.size:
        return [{"start": 0, "end": len(potential), "direction": 0}]
    signs = np.sign(increments[moving]).astype(int)
    changes = np.flatnonzero(signs[1:] != signs[:-1]) + 1
    starts = [0, *(int(moving[i]) for i in changes)]
    ends = [*(start + 1 for start in starts[1:]), len(potential)]
    return [
        {"start": start, "end": end, "direction": int(signs[index])}
        for start, end, index in zip(starts, ends, [0, *changes], strict=True)
    ]


def parse_chi(blob: bytes) -> ChiData:
    lines = _decode(blob).splitlines()
    methods = {"cyclic voltammetry": "CV", "a.c. impedance": "EIS"}
    kind = next((methods[line.strip().lower()] for line in lines[:30]
                 if line.strip().lower() in methods), None)
    if kind is None:
        raise ChiError("未识别到 CHI 的 CV 或 EIS 实验表头")

    columns = _CV_COLUMNS if kind == "CV" else _EIS_COLUMNS
    metadata = {"date": next((line.strip() for line in lines if line.strip()), "")}
    head = None
    for index, line in enumerate(lines):
        parts = [value.strip().casefold() for value in line.split(",")]
        if tuple(parts[:len(columns)]) == columns:
            head = index
            break
        key, separator, value = re.sub(r"\s*[:=]\s*", "=", line.strip(), count=1).partition("=")
        if separator and key in _SETTINGS:
            try:
                value = float(value)
                if not np.isfinite(value):
                    continue
            except ValueError:
                if key != "Instrument Model":
                    continue
            metadata[_SETTINGS[key]] = value
    if head is None:
        raise ChiError("没有找到对应的 CHI 数据列")

    records = []
    skipped = 0
    for line in lines[head + 1:]:
        if not line.strip():
            continue
        parts = line.split(",")
        try:
            row = [float(value.strip()) for value in parts[:len(columns)]]
        except ValueError:
            skipped += 1
            continue
        if len(row) != len(columns) or not np.all(np.isfinite(row)) or (kind == "EIS" and row[0] <= 0):
            skipped += 1
            continue
        records.append(row)
    if len(records) < 2:
        raise ChiError("有效数据少于 2 个点")
    points = np.asarray(records, dtype=float)
    segments = _segments(points[:, 0]) if kind == "CV" else []
    warnings = [f"跳过 {skipped} 行无效记录"] if skipped else []
    if kind == "CV":
        planned = metadata.get("plannedSegments")
        if planned and planned != len(segments):
            warnings.append(f"仪器设定 {int(planned)} 段，按实测电位识别出 {len(segments)} 段")
    else:
        negative = int(np.count_nonzero(points[:, 1] < 0))
        inductive = int(np.count_nonzero(points[:, 2] > 0))
        if negative or inductive:
            warnings.append(f"实部为负 {negative} 点，虚部为正 {inductive} 点。原始点完整保留，常见预设可能无法描述这些点。")
    return ChiData(kind, metadata, points, segments, warnings)


FIT_MODELS = {
    "cpe": ("Rₛ + (R₁ ∥ CPE₁)", ("Rₛ", "R₁", "Q₁", "n₁")),
    "cpe2": ("Rₛ + (R₁ ∥ CPE₁) + (R₂ ∥ CPE₂)", ("Rₛ", "R₁", "Q₁", "n₁", "R₂", "Q₂", "n₂")),
    "warburg": ("Rₛ + (CPE₁ ∥ (R₁ + W))", ("Rₛ", "R₁", "Q₁", "n₁", "Aᵂ")),
}
_LOWER = {"R": 1e-8, "Q": 1e-15, "n": .05, "A": 1e-10}
_UPPER = {"R": 1e12, "Q": 1e4, "n": 1., "A": 1e12}


def _impedance(params: np.ndarray, omega: np.ndarray, model: str) -> np.ndarray:
    rs, r, q, n = params[:4]
    branch = r + (params[4] / np.sqrt(omega) if model == "warburg" else 0)
    z = rs + 1 / (1 / branch + q * omega**n)
    if model == "cpe2":
        r, q, n = params[4:]
        z += 1 / (1 / r + q * omega**n)
    return z


def fit_eis(data: ChiData, model: str, lower: float, upper: float, weighting: str) -> dict:
    if data.kind != "EIS" or model not in FIT_MODELS or weighting not in ("modulus", "uniform"):
        raise ChiError("请选择有效的 EIS 电路和权重")
    if not 0 < lower < upper < float("inf"):
        raise ChiError("请输入有效的拟合频段（Hz）")
    selected = np.flatnonzero((data.points[:, 0] >= lower) & (data.points[:, 0] <= upper))
    frequency, real, imaginary = data.points[selected, :3].T
    circuit, names = FIT_MODELS[model]
    required = len(names) + 1
    if len(np.unique(frequency)) < required:
        raise ChiError(f"所选电路至少需要 {required} 个不同频率的点")
    impedance = real + 1j * imaginary
    omega = 2j * np.pi * frequency
    magnitude = abs(impedance)
    scale = max(float(np.median(magnitude)), 1e-12)
    safe_magnitude = np.maximum(magnitude, 1e-12 * scale)
    divisor = safe_magnitude if weighting == "modulus" else scale
    low_bounds = np.log([_LOWER[name[0]] for name in names])
    high_bounds = np.log([_UPPER[name[0]] for name in names])
    resistance = max(float(np.ptp(real)), scale, 1e-6)
    rs = max(float(real[np.argmax(frequency)]), 1e-6)

    def residual(log_params: np.ndarray) -> np.ndarray:
        delta = (_impedance(np.exp(log_params), omega, model) - impedance) / divisor
        return np.r_[delta.real, delta.imag]

    starts = []
    peaks = np.geomspace(frequency.min(), frequency.max(), 7)[1:-1]
    for n, peak in product((.62, .88), peaks):
        q = 1 / (resistance * (2 * np.pi * peak)**n)
        if model == "cpe2":
            for second_peak in peaks[::2]:
                if second_peak < peak:
                    q2 = 2 / (resistance * (2 * np.pi * second_peak)**n)
                    starts.append([rs, resistance / 2, q * 2, n, resistance / 2, q2, n])
        elif model == "warburg":
            starts.append([rs, resistance, q, n, resistance * np.sqrt(2 * np.pi * frequency.min())])
        else:
            starts.append([rs, resistance, q, n])
    results = [
        least_squares(residual, np.clip(np.log(start), low_bounds + 1e-8, high_bounds - 1e-8),
                      bounds=(low_bounds, high_bounds), max_nfev=400)
        for start in starts
    ]
    best = min(results, key=lambda result: result.cost)
    params = np.exp(best.x)
    # List the higher characteristic frequency first.
    if model == "cpe2" and (
        -np.log(params[1] * params[2]) / params[3] < -np.log(params[4] * params[5]) / params[6]
    ):
        params = params[[0, 4, 5, 6, 1, 2, 3]]
    predicted = _impedance(params, omega, model)
    delta = predicted - impedance
    relative = float(100 * np.sqrt(np.mean((abs(delta) / safe_magnitude)**2)))
    actual_span = [float(frequency.min()), float(frequency.max())]
    dense = np.geomspace(actual_span[1], actual_span[0], 320)
    curve = _impedance(params, 2j * np.pi * dense, model)
    warnings = []
    if np.any(real < 0) or np.any(imaginary > 0):
        warnings.append("拟合频段含负实部或正虚部，所选电路无法描述这些频点。")
    if not best.success:
        warnings.append("优化未完全收敛，请检查频段和模型。")
    if np.any(best.active_mask):
        warnings.append("有参数达到约束边界，请结合残差判断。")
    if relative >= 30:
        warnings.append("实测值与模型偏差较大，请结合残差判断。")
    if np.max(abs(predicted)) < 0.05 * np.max(magnitude):
        warnings.append("拟合曲线接近零，参数不宜解读。")
    if model in ("cpe", "cpe2"):
        offsets = (1, 4) if model == "cpe2" else (1,)
        for branch, offset in enumerate(offsets, 1):
            r, q, n = params[offset:offset + 3]
            log_frequency = -np.log(r * q) / n - np.log(2 * np.pi)
            if not np.log(actual_span[0]) <= log_frequency <= np.log(actual_span[1]):
                label = f"约 {np.exp(log_frequency):.3g} Hz，" if abs(log_frequency) < 700 else ""
                warnings.append(
                    f"支路 {branch} 的特征频率{label}超出拟合频段，参数约束较弱。"
                )
    units = {"R": "Ω", "Q": "S·sⁿ", "n": "", "A": "Ω·s⁻½"}
    return {
        "model": model, "circuit": circuit, "weighting": weighting,
        "requestedRange": [lower, upper], "range": actual_span, "count": len(selected),
        "relativeRmse": relative, "rmse": float(np.sqrt(np.mean(abs(delta)**2))),
        "parameters": [{"name": name, "value": float(value), "unit": units[name[0]]}
                       for name, value in zip(names, params, strict=True)],
        "warnings": warnings,
        "curve": np.column_stack((dense, curve.real, curve.imag)).tolist(),
        "matched": np.column_stack((selected, predicted.real, predicted.imag)).tolist(),
    }
