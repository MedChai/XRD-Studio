"""相变峰检测与跨圈轨迹追踪(M2)。

真实数据(170214_005_7,184 圈 0.2C 长循环)暴露的三个设计修正:

1. **噪声估计必须与曲率解耦**:老化电池的放电 dQ/dV 是跨 ~300 mV 的
   宽峰,窗口原始 MAD 把峰体当噪声;而"曲线 − 大窗平滑"残差在陡峭
   上升沿又会把曲率当噪声。这里用**二阶差分 MAD/√6**(只放大 Bin 级
   白噪声,对斜率与缓变曲率不敏感)。
2. **充电侧高端峰会被截止电压截断**(H2→H3 相变在 4.3 V 窗口内做不完,
   "峰"其实是数据边缘的上升沿):半高交叉在数据内缺失的峰标记
   `truncated`、不进轨迹 —— 它本身就是"窗口截断型损失"的直接证据。
   检测在窗口外扩 60 mV 的段上进行:贴窗口边的峰在完整数据上仍有
   真实突出度,按窗口内的被切形状算 prominence 会把它误杀。
3. **末圈可能不完整**(测试中途停止,容量骤降、CE 异常):调用方传入的
   圈统计里带 `incomplete` 标记,追踪时跳过。

轨迹匹配:同 (direction, window) 内按圈序贪心最近邻,容差 track_tol_v,
允许 1 圈空洞(相变剧烈时峰位漂移快,连续两要求太苛刻会碎轨)。

仅依赖 numpy(+scipy.signal 可选,缺失时降级)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .dqdv import DQDVResult

# ---------------------------------------------------------------- 数据结构


@dataclass
class PeakWindow:
    """相变窗口定义。"""

    key: str          # H1H2 / H2H3 / 自定义
    label: str        # "H1→H2"
    v_lo: float
    v_hi: float


def default_windows() -> list[PeakWindow]:
    """无钴高镍 2.7-4.3 V 常规窗口默认值(可按材料 preset 覆盖)。"""
    return [
        PeakWindow("H1H2", "H1→H2", 3.55, 3.80),
        PeakWindow("H2H3", "H2→H3", 4.10, 4.30),
    ]


@dataclass
class PeakParams:
    """峰检测与轨迹参数。"""

    windows: list[PeakWindow] = field(default_factory=default_windows)
    mad_mult: float = 8.0         # 噪声倍率
    height_frac: float = 0.05     # prominence 下限 = 窗内最大值 × 此比例
    distance_bins: int = 25       # 峰最小间距(bin 数)
    track_tol_v: float = 0.03     # 跨圈匹配容差 30 mV
    track_gap: int = 1            # 允许的轨迹空洞圈数
    onset_frac: float = 0.10      # 起始电压 = 峰高 10% 首越点
    area_half_width_v: float = 0.05  # 峰面积积分半宽(固定,保证跨圈可比)

    def validate(self) -> None:
        if not (1.0 <= self.mad_mult <= 50.0):
            raise ValueError(f"mad_mult 越界: {self.mad_mult}")
        if not (0.0 < self.height_frac < 0.5):
            raise ValueError(f"height_frac 越界: {self.height_frac}")
        if self.track_tol_v <= 0 or self.track_tol_v > 0.2:
            raise ValueError(f"track_tol_v 越界: {self.track_tol_v}")
        if self.area_half_width_v <= 0 or self.area_half_width_v > 0.3:
            raise ValueError(f"area_half_width_v 越界: {self.area_half_width_v}")


@dataclass
class CyclePeak:
    """单圈单方向单窗口内的一个检出峰。"""

    cycle: int
    direction: str          # charge / discharge
    window_key: str
    position: float         # V
    height: float           # dQ/dV
    area: float             # 窗口内定宽积分(dQ 单位)
    fwhm: float             # V, 半高宽
    prominence: float
    onset: float            # 起始电压(峰高 10% 首越),可能 nan
    truncated: bool         # 贴窗口/数据边缘,峰不完整
    track_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle": self.cycle, "direction": self.direction,
            "window": self.window_key, "position": self.position,
            "height": self.height, "area": self.area, "fwhm": self.fwhm,
            "prominence": self.prominence, "onset": None if np.isnan(self.onset) else self.onset,
            "truncated": self.truncated, "trackId": self.track_id,
        }


@dataclass
class Track:
    """跨圈峰轨迹(同方向同窗口内的一串对应峰)。"""

    track_id: int
    direction: str
    window_key: str
    label: str
    peaks: list[CyclePeak] = field(default_factory=list)

    @property
    def first_cycle(self) -> int:
        return self.peaks[0].cycle

    @property
    def last_cycle(self) -> int:
        return self.peaks[-1].cycle

    @property
    def continuity(self) -> float:
        if not self.peaks:
            return 0.0
        span = self.last_cycle - self.first_cycle + 1
        return len(self.peaks) / span if span else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "trackId": self.track_id, "direction": self.direction,
            "window": self.window_key, "label": self.label,
            "firstCycle": self.first_cycle, "lastCycle": self.last_cycle,
            "nPeaks": len(self.peaks), "continuity": round(self.continuity, 3),
        }


# ---------------------------------------------------------------- 检测


def _residual_noise(y: np.ndarray) -> float:
    """Bin 级噪声估计:二阶差分的 MAD / √6。

    "曲线 − 大窗平滑"的残差在陡峭上升沿/截断边会把**曲率**当成噪声
    (合成夹具实测:阈值被抬高到峰突出度之上,窗内明明有峰却检出为空)。
    二阶差分对平滑曲线的斜率与缓变曲率不敏感,只放大 Bin 级白噪声
    (方差 6σ²),是这里更稳的估计量。
    """
    if y.size < 5:
        return 0.0
    d2 = y[2:] - 2.0 * y[1:-1] + y[:-2]
    return float(np.median(np.abs(d2 - np.median(d2))) * 1.4826 / np.sqrt(6.0))


def _fwhm_of(x: np.ndarray, y: np.ndarray, peak_idx: int) -> tuple[float, bool, bool]:
    """峰的半高宽(线性插值左右交点)。

    返回 (fwhm, 左交叉存在, 右交叉存在)。交叉缺失 = 峰被窗口或数据
    边界切断(充电截止处 H2→H3 的典型形貌)—— 这是截断峰的直接判据,
    比按 bin 距离猜"离边多近算截断"更物理。
    """
    half = y[peak_idx] / 2.0
    n = y.size

    def cross(from_k: int, to_k: int) -> float | None:
        step = 1 if to_k > from_k else -1
        k = from_k
        while k != to_k:
            k2 = k + step
            if (y[k] - half) * (y[k2] - half) <= 0 and y[k] != y[k2]:
                frac = (y[k] - half) / (y[k] - y[k2])
                return float(x[k] + frac * (x[k2] - x[k]))
            k = k2
        return None

    left = cross(peak_idx, 0)
    right = cross(peak_idx, n - 1)
    if left is None or right is None:
        return (float(x[n - 1] - x[0]) if n else 0.0, left is not None, right is not None)
    return right - left, True, True


def detect_window_peaks(
    res: DQDVResult,
    window: PeakWindow,
    params: PeakParams | None = None,
    cycle: int = -1,
) -> list[CyclePeak]:
    """一条 dQ/dV 曲线在单个窗口内检峰。

    检测在窗口外扩 pad(默认 60 mV)的段上进行 —— 窗口是人为的分析
    区间,峰贴窗口边时其真实突出度仍要在完整数据上看,否则贴边峰
    的 prominence 只剩被切的一半,会被噪声阈值误杀。位置落在窗口
    内的峰才上报。

    truncated 判定:半高交叉在**数据**内缺失(典型为充电截止 4.3V 处
    的 H2→H3,相变在窗口内做不完)或峰位贴数据边界 2 bin。窗口切峰
    不算截断 —— 数据还在,面积照样积。
    """
    if params is None:
        params = PeakParams()
    pad = 0.06
    m = (res.v >= window.v_lo - pad) & (res.v <= window.v_hi + pad)
    x, y = res.v[m], res.dqdv[m]
    if x.size < 10:
        return []
    noise = _residual_noise(y)
    prom_floor = max(noise * params.mad_mult, float(y.max()) * params.height_frac)
    try:
        from scipy.signal import find_peaks
        pk, props = find_peaks(y, prominence=prom_floor, distance=params.distance_bins)
    except ImportError:
        pk, props = _find_peaks_numpy(y, prom_floor, params.distance_bins)
    bin_v = float(x[1] - x[0]) if x.size > 1 else 0.002
    out: list[CyclePeak] = []
    for k, p in enumerate(pk):
        pos, height = float(x[p]), float(y[p])
        if not (window.v_lo - 1e-9 <= pos <= window.v_hi + 1e-9):
            continue  # 外扩段上检出但位置在窗口外:不报
        prom = float(props["prominences"][k]) if "prominences" in props else height
        fwhm, left_ok, right_ok = _fwhm_of(x, y, int(p))
        # 截断判定:半高交叉在数据内缺失,或峰位贴数据边界不足 2 bin。
        # 数据边界 = 外扩段的两端(≈ 数据实际起止)。
        near_data = (pos - x[0] < 2 * bin_v) or (x[-1] - pos < 2 * bin_v)
        truncated = bool(not (left_ok and right_ok) or near_data)
        # 面积:峰位 ± 固定半宽,截到数据界(不是窗口界)
        lo = max(pos - params.area_half_width_v, x[0])
        hi = min(pos + params.area_half_width_v, x[-1])
        am = (res.v >= lo) & (res.v <= hi)
        area = float(np.trapezoid(res.dqdv[am], res.v[am])) if am.sum() > 2 else 0.0
        # 起始电压:从窗口下沿到峰位,首个 ≥ onset_frac×height 处
        om = (res.v >= max(lo, pos - 0.2)) & (res.v <= pos)
        onset = float("nan")
        if om.sum() and height > 0:
            above = np.flatnonzero(res.dqdv[om] >= params.onset_frac * height)
            if above.size:
                onset = float(res.v[om][above[0]])
        out.append(CyclePeak(
            cycle=cycle, direction=res.direction, window_key=window.key,
            position=pos, height=height, area=area,
            fwhm=fwhm, prominence=prom,
            onset=onset, truncated=truncated,
        ))
    out.sort(key=lambda c: -c.height)
    return out


def _find_peaks_numpy(y: np.ndarray, prom_floor: float, distance: int):
    """scipy 缺失时的简版检峰(局部极大 + 简化 prominence)。"""
    n = y.size
    cand = [k for k in range(1, n - 1) if y[k] > y[k - 1] and y[k] >= y[k + 1]]
    kept: list[int] = []
    for k in cand:
        left = y[max(0, k - 50):k + 1].min()
        right = y[k:k + 51].min()
        prom = y[k] - max(left, right)
        if prom >= prom_floor:
            if kept and k - kept[-1] < distance:
                if y[k] > y[kept[-1]]:
                    kept[-1] = k
            else:
                kept.append(k)
    idx = np.asarray(kept, dtype=int)
    proms = {"prominences": np.asarray(
        [y[k] - max(y[max(0, k - 50):k + 1].min(), y[k:k + 51].min()) for k in kept])}
    return idx, proms


# ---------------------------------------------------------------- 轨迹


def build_tracks(
    all_peaks: list[CyclePeak],
    params: PeakParams | None = None,
    window_labels: dict[str, str] | None = None,
) -> list[Track]:
    """跨圈贪心最近邻匹配 → 轨迹列表。

    输入 all_peaks 应为一个 cell 全部圈的检峰结果(含两个方向全部窗口)。
    截断峰不进轨迹(位置不可靠),但保留在返回的 peaks 列表外由调用方处置。
    """
    if params is None:
        params = PeakParams()
    labels = window_labels or {}
    groups: dict[tuple[str, str], list[CyclePeak]] = {}
    for pk in all_peaks:
        if pk.truncated:
            continue
        groups.setdefault((pk.direction, pk.window_key), []).append(pk)

    tracks: list[Track] = []
    next_id = 1
    for (direction, wkey), peaks in sorted(groups.items()):
        peaks.sort(key=lambda p: p.cycle)
        open_tracks: list[Track] = []
        for pk in peaks:
            best: Track | None = None
            best_d = float("inf")
            for tr in open_tracks:
                last = tr.peaks[-1]
                gap = pk.cycle - last.cycle
                if gap <= 0 or gap > params.track_gap + 1:
                    continue
                d = abs(pk.position - last.position)
                # 容差自适应:宽峰的顶点游移可达 fwhm 量级,固定 30 mV 会
                # 把同一物理峰的轨迹切碎(真实数据:放电宽峰被切成 3 段)。
                # 空洞轨迹再放宽一倍。
                tol = max(params.track_tol_v, 0.4 * last.fwhm)
                tol *= 2.0 if gap > 1 else 1.0
                if d <= tol and d < best_d:
                    best, best_d = tr, d
            if best is not None:
                pk.track_id = best.track_id
                best.peaks.append(pk)
            else:
                tr = Track(
                    track_id=next_id, direction=direction, window_key=wkey,
                    label=labels.get(wkey, wkey),
                )
                pk.track_id = next_id
                tr.peaks.append(pk)
                tracks.append(tr)
                open_tracks.append(tr)
                next_id += 1
            # 关闭已超空洞的开放轨迹(其位置参考已不可靠)
            open_tracks = [
                tr for tr in open_tracks
                if pk.cycle - tr.peaks[-1].cycle <= params.track_gap + 1
            ]
    return tracks
