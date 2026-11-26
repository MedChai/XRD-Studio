"""Incremental capacity from contiguous, executed CEX constant-current steps."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter1d

from .cex_native import RECORD_COLUMNS, REST_MODE, CexError, CexFile

ALGORITHM = "cex-ica-1"
CHARGE_MODES = {0x03, 0x76}
DISCHARGE_MODES = {0x02, 0x75}
VOLTAGE_HOLD = 0x05
MIN_RECORDS = 8
MIN_BINS = 4


@dataclass(frozen=True)
class DqdvSettings:
    grid_mv: float = 2.0
    smoothing_mv: float = 0.0

    @classmethod
    def read(cls, value: dict | None) -> DqdvSettings:
        try:
            grid = float(value.get("gridMv", 2)) if value is not None else 2.0
            smoothing = float(value.get("smoothingMv", 0)) if value is not None else 0.0
        except (AttributeError, TypeError, ValueError) as exc:
            raise CexError("dQ/dV 计算参数无效") from exc
        if not 0.5 <= grid <= 50 or not 0 <= smoothing <= 100:
            raise CexError("电压步长须为 0.5–50 mV，平滑宽度须为 0–100 mV")
        return cls(grid, smoothing)


def _median_positive(values: np.ndarray) -> float:
    positive = values[np.isfinite(values) & (values > 0)]
    return float(np.median(positive)) if positive.size else 0.0


def _continuous_runs(records: np.ndarray, polarity: int, grid: float) -> list[np.ndarray]:
    time, current, voltage, _ = records.T
    valid = np.isfinite(records[:, :3]).all(axis=1) & (polarity * current > 0)
    pairs = valid[:-1] & valid[1:]
    dt = np.diff(time)
    dv = np.abs(np.diff(voltage))
    typical_dt = _median_positive(dt[pairs])
    typical_dv = _median_positive(dv[pairs])
    drift = np.abs(np.diff(current))
    current_scale = np.maximum(np.abs(current[:-1]), np.abs(current[1:]))
    boundaries = (
        ~pairs | (dt < 0) | (dt > 8 * typical_dt) | (dv > max(10 * grid, 8 * typical_dv))
        | (drift > 0.12 * current_scale)
    )
    return [part for part in np.split(np.arange(len(records)), np.flatnonzero(boundaries) + 1)
            if part.size >= MIN_RECORDS and valid[part[0]]]


def _capacity(time: np.ndarray, current: np.ndarray, native: np.ndarray) -> tuple[np.ndarray, str]:
    magnitude = np.abs(current)
    integral = np.r_[0.0, np.cumsum((magnitude[1:] + magnitude[:-1]) * np.diff(time) / 7200)]
    if np.isfinite(native).all():
        measured = native - native[0]
        if measured[-1] < 0:
            measured = -measured
        tolerance = max(1e-6, 0.001 * measured[-1])
        if measured[-1] > 0 and np.min(np.diff(measured)) >= -tolerance:
            # Measured capacity is preferred, including tests with coarse time stamps.
            return np.maximum.accumulate(measured), "cex"
    return integral, "integrated"


def _edge_capacities(voltage: np.ndarray, capacity: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """Interpolate inter-bin capacity while retaining increments at repeated voltages."""
    levels, first, counts = np.unique(voltage, return_index=True, return_counts=True)
    before = capacity[first]
    after = capacity[first + counts - 1]
    left = np.searchsorted(levels, edges, side="right") - 1
    left = np.clip(left, 0, levels.size - 1)
    right = np.minimum(left + 1, levels.size - 1)
    span = levels[right] - levels[left]
    fraction = np.divide(edges - levels[left], span, out=np.zeros_like(edges), where=span > 0)
    values = after[left] + np.clip(fraction, 0, 1) * (before[right] - after[left])
    if np.isclose(edges[0], levels[0], rtol=0.0, atol=1e-10):
        values[0] = before[0]
    return values


def _curve(records: np.ndarray, first_record: int, polarity: int, settings: DqdvSettings) -> dict:
    time, current, voltage, native = records.T
    grid = settings.grid_mv / 1000
    low_current, median_current, high_current = np.percentile(np.abs(current), [10, 50, 90])
    if high_current - low_current > 0.12 * median_current:
        raise CexError("实测电流变化过大，恒流段无法确认")

    oriented = polarity * voltage
    rising = np.maximum.accumulate(oriented)
    typical_dv = _median_positive(np.abs(np.diff(voltage)))
    if np.max(rising - oriented) > max(3 * grid, 5 * typical_dv):
        raise CexError("电压明显回跳，无法构建连续的容量–电压曲线")
    if np.unique(rising).size < 5:
        raise CexError("有效电压采样点太少")
    capacity, source = _capacity(time, current, native)
    records_at = np.arange(first_record, first_record + len(records))
    x = rising
    if polarity < 0:
        x = -rising[::-1]
        capacity = capacity[-1] - capacity[::-1]
        records_at = records_at[::-1]
    start = int(np.ceil(x[0] / grid - 1e-9))
    end = int(np.floor(x[-1] / grid + 1e-9))
    if end - start < MIN_BINS:
        raise CexError("有效电压范围太窄，请核对采样或调整电压步长")
    if end - start > 100_000:
        raise CexError("电压范围异常，请核对 CEX 的原始记录")
    edges = np.arange(start, end + 1) * grid
    q = _edge_capacities(x, capacity, edges)
    delta = float(q[-1] - q[0])
    if delta <= 0:
        raise CexError("有效电压范围内没有容量变化")
    raw = polarity * np.diff(q) / grid
    values = raw
    if settings.smoothing_mv > 0:
        # Smooth the cumulative capacity before differentiation. Restore its two
        # endpoint values so smoothing preserves the capacity in this voltage span.
        sigma = settings.smoothing_mv / (2.35482 * settings.grid_mv)
        smooth = gaussian_filter1d(q, sigma, mode="nearest")
        scale = delta / (smooth[-1] - smooth[0])
        values = polarity * np.diff(smooth) * scale / grid
    midpoints = edges[:-1] + grid / 2
    positions = np.minimum(np.searchsorted(x, midpoints), x.size - 1)
    previous = np.maximum(0, positions - 1)
    positions = np.where(abs(x[previous] - midpoints) < abs(x[positions] - midpoints), previous, positions)
    warnings = []
    if typical_dv > grid:
        warnings.append("实测电压间隔大于计算步长")
    if settings.smoothing_mv > 0.25 * (end - start) * settings.grid_mv:
        warnings.append("平滑窗口相对有效电压范围较宽")
    return {
        "startBin": start,
        "values": values.tolist(),
        "rawValues": raw.tolist() if settings.smoothing_mv else None,
        "records": records_at[positions].tolist(),
        "firstRecord": first_record,
        "lastRecord": first_record + len(records) - 1,
        "count": len(records),
        "currentMa": float(polarity * median_current),
        "capacityMah": delta,
        "capacitySource": source,
        "sampleMv": typical_dv * 1000,
        "warnings": warnings,
    }


def _read_records(file: CexFile, first: int, last: int) -> np.ndarray:
    columns = [RECORD_COLUMNS[name] for name in ("time", "current", "voltage", "capacity")]
    return np.array([[file.value(column, row) for column in columns] for row in range(first, last)],
                    dtype=np.float64)


def calculate_cycle(file: CexFile, index: int, settings: DqdvSettings) -> dict:
    first_step, last_step = file.cycle_span(index, "steps")
    traces = []
    issues = []
    cv_capacity = 0.0
    cv_unknown = False
    directions = set()
    for step in range(first_step, last_step):
        mode = file.step_mode(step)
        if mode == REST_MODE:
            continue
        first, last = file.step_span(step)
        if mode == VOLTAGE_HOLD:
            if last > first:
                q0 = file.value(RECORD_COLUMNS["capacity"], first)
                q1 = file.value(RECORD_COLUMNS["capacity"], last - 1)
                if q0 is None or q1 is None or last - first < 2:
                    cv_unknown = True
                else:
                    cv_capacity += abs(q1 - q0)
            continue
        if mode in CHARGE_MODES:
            direction, polarity = "charge", 1
        elif mode in DISCHARGE_MODES:
            direction, polarity = "discharge", -1
        else:
            issues.append(f"工步 {step + 1} 的模式 0x{mode:02X} 未用于恒流微分")
            continue
        if last > first:
            directions.add(direction)
        if last - first < MIN_RECORDS:
            issues.append(f"工步 {step + 1} 的原始记录不足")
            continue
        records = _read_records(file, first, last)
        runs = _continuous_runs(records, polarity, settings.grid_mv / 1000)
        if not runs:
            issues.append(f"工步 {step + 1} 缺少连续恒流记录")
        elif sum(len(run) for run in runs) < len(records) or len(runs) > 1:
            issues.append(f"工步 {step + 1} 的采样间断或电流变化已分段处理")
        for number, run in enumerate(runs, 1):
            try:
                result = _curve(records[run], int(first + run[0] + 1), polarity, settings)
            except CexError as exc:
                issues.append(f"工步 {step + 1} · {exc}")
                continue
            traces.append({"direction": direction, "step": step + 1, "part": number, **result})
    if not traces and not issues:
        issues.append("该圈没有恒流充放电工步")
    return {
        "cycle": index + 1,
        "paired": len(directions) == 2,
        "cvCapacityMah": None if cv_unknown else cv_capacity,
        "issues": issues,
        "curves": traces,
    }
