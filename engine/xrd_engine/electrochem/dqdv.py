"""dQ/dV 计算(M1)。

恒流段中电压存在小幅抖动(记录噪声/仪器调节),直接按记录插值 Q(V) 会重复
计数。本模块只取"创新高"的电压点(充电取 running max、放电取 running min)
构造单调 V-Q 序列,再插值到均匀电压网格差分,最后 Savitzky-Golay 平滑。

容量归一化: 传入 mass_mg 时输出 mAh/g 与 mAh/(g·V),否则 mAh 与 mAh/V。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

DEFAULT_DV_MV = 2.0
DEFAULT_SG_WINDOW = 11
DEFAULT_SG_POLY = 3


@dataclass
class DQDVParams:
    """dQ/dV 计算参数(全部可调,存 preset)。"""

    dv_mv: float = DEFAULT_DV_MV
    sg_window: int = DEFAULT_SG_WINDOW
    sg_poly: int = DEFAULT_SG_POLY
    v_min: float = 2.5      # 分析窗口(默认 2.7-4.3 由调用方按材料传入)
    v_max: float = 4.35
    mass_mg: float | None = None   # 活性物质质量,归一化 mAh/g

    def validate(self) -> None:
        if self.dv_mv <= 0 or self.dv_mv > 50:
            raise ValueError(f"dv_mv 越界: {self.dv_mv}")
        if self.sg_window < 5 or self.sg_window % 2 == 0:
            raise ValueError(f"sg_window 必须为 ≥5 的奇数: {self.sg_window}")
        if not 0 < self.sg_poly < self.sg_window:
            raise ValueError(f"sg_poly 越界: {self.sg_poly}")
        if self.v_max - self.v_min < 0.1:
            raise ValueError(f"电压窗口过窄: {self.v_min}~{self.v_max}")
        if self.mass_mg is not None and self.mass_mg <= 0:
            raise ValueError(f"mass_mg 非法: {self.mass_mg}")


@dataclass
class DQDVResult:
    """单圈单方向的 dQ/dV 结果。"""

    direction: str                 # charge / discharge
    v: np.ndarray                  # 均匀电压网格(网格点,长度 N)
    dqdv: np.ndarray               # 平滑后 dQ/dV,长度 N-1,对应 bin 中心
    q_grid: np.ndarray             # 网格上累计容量(与 v 等长,单位见 per_gram)
    per_gram: bool
    q_total: float                 # 本段总容量(梯形积分,同单位)
    n_records: int                 # 参与计算的原始记录数
    n_monotonic: int               # 单调化后保留的点数
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "per_gram": self.per_gram,
            "q_total": self.q_total,
            "n_records": self.n_records,
            "n_monotonic": self.n_monotonic,
            "warnings": list(self.warnings),
            "dv_mv": float(self.v[1] - self.v[0]) if len(self.v) > 1 else None,
        }


def _monotonic_points(v: np.ndarray, q: np.ndarray, direction: str) -> tuple[np.ndarray, np.ndarray]:
    """剔除电压抖动:充电保留创新高点,放电保留创新低点。返回 (v_mono, q_mono) 升序。"""
    if direction == "charge":
        running = np.maximum.accumulate(v)
        keep = np.concatenate(([True], v[1:] > running[:-1]))
    else:
        running = np.minimum.accumulate(v)
        keep = np.concatenate(([True], v[1:] < running[:-1]))
    vm, qm = v[keep], q[keep]
    if direction == "discharge":
        vm, qm = vm[::-1], qm[::-1]  # 反转为 V 升序
    return vm, qm


def compute_dqdv(
    t: np.ndarray,
    i: np.ndarray,
    v: np.ndarray,
    direction: str,
    params: DQDVParams | None = None,
) -> DQDVResult:
    """一段恒流记录 → dQ/dV。

    t: 秒(累计), i: mA(充正放负,取绝对值方向由 direction 决定), v: V。
    记录需按时间升序;状态过滤由调用方完成(只传恒流段)。
    """
    if params is None:
        params = DQDVParams()
    params.validate()
    if direction not in ("charge", "discharge"):
        raise ValueError(f"direction 非法: {direction}")
    if not (len(t) == len(i) == len(v)) or len(t) < 5:
        raise ValueError(f"记录数不足或长度不一致: {len(t)}")

    warnings: list[str] = []
    dt = np.diff(t)
    n_back = int((dt < 0).sum())
    if n_back:
        # 时间回退(仪器重启/导出拼接)处 Δt 计 0,容量按各连续段累计。
        # **不能只删回退后的第一个点**:删完残留的 dt 仍是大负数,实测
        # 一次重启就把 q_total 腰斩,且 q 上的负阶跃经插值会变成深谷
        # 污染峰检测。
        warnings.append(f"时间存在 {n_back} 处回退(疑似仪器重启/导出拼接),已按连续段累计容量")
    dt = np.where(dt > 0, dt, 0.0)

    # 累计容量(mAh):梯形积分,方向绝对值
    q = np.concatenate(([0.0], np.cumsum(np.abs(i[:-1] + i[1:]) * 0.5 * dt) / 3600.0))

    v_mono, q_mono = _monotonic_points(v, q, direction)
    if v_mono.size < 5:
        raise ValueError(f"单调化后点数不足({v_mono.size}),无法计算 dQ/dV")
    if v_mono[-1] - v_mono[0] < 10 * params.dv_mv / 1000.0:
        raise ValueError(f"电压跨度不足: {v_mono[-1] - v_mono[0]:.3f} V")

    # 均匀网格(截到分析窗口 & 实际数据范围)
    lo = max(params.v_min, v_mono[0])
    hi = min(params.v_max, v_mono[-1])
    if hi - lo < 5 * params.dv_mv / 1000.0:
        raise ValueError(f"分析窗口与数据交集过窄: {lo:.3f}~{hi:.3f} V")
    grid = np.arange(lo, hi + 1e-9, params.dv_mv / 1000.0)
    q_grid = np.interp(grid, v_mono, q_mono)

    # 差分 → bin 中心。放电方向 V 升序对应 Q 递减(电荷被放出),
    # 取负使 dQ/dV 为"每伏释放的容量",与充电同为正峰。
    sign = -1.0 if direction == "discharge" else 1.0
    dqdv_raw = sign * np.diff(q_grid) / (params.dv_mv / 1000.0)
    v_centers = 0.5 * (grid[:-1] + grid[1:])

    # Savitzky-Golay 平滑(窗口自适应收缩,避免小数组越界)
    win = params.sg_window
    while win >= len(dqdv_raw) and win > 5:
        win -= 2
    if len(dqdv_raw) > 5:
        dqdv, sg_ok = _savgol(dqdv_raw, win, params.sg_poly)
        if not sg_ok:
            # 退化为窗口均值后峰形与 SG 完全不同,必须让用户知道
            warnings.append("SG 平滑未能生效(窗口/多项式阶不满足),已退化为窗口均值")
    else:
        dqdv = dqdv_raw

    per_gram = params.mass_mg is not None
    scale = 1000.0 / params.mass_mg if per_gram else 1.0   # mAh→mAh/g (mg)
    q_total = float(np.abs(q_mono[-1] - q_mono[0]))
    if q_total * scale < 1e-9:
        warnings.append("段容量近零")

    return DQDVResult(
        direction=direction,
        v=v_centers,
        dqdv=dqdv * scale,
        q_grid=q_grid * scale,
        per_gram=per_gram,
        q_total=q_total * scale,
        n_records=len(t),
        n_monotonic=int(v_mono.size),
        warnings=warnings,
    )


def _savgol(x: np.ndarray, window: int, poly: int) -> tuple[np.ndarray, bool]:
    """SG 平滑,返回 (y, 是否真正走成 SG)。

    scipy 不可用或窗口 ≤ 多项式阶时退化为窗口均值 —— 形状与 SG 完全
    不同,调用方必须把退化写进 warnings,不能静默换算法。
    """
    if window % 2 == 0:
        window -= 1
    if window <= poly or window < 3:
        poly = max(0, window - 2)
        if window < 3:
            return x.copy(), False
    try:
        from scipy.signal import savgol_filter

        return savgol_filter(x, window, poly), True
    except Exception:
        kernel = np.ones(window) / window
        pad = window // 2
        padded = np.pad(x, pad, mode="edge")
        return np.convolve(padded, kernel, mode="valid"), False
