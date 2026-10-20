"""peaks 模块测试:检峰精度、截断标记、宽峰轨迹(M2 验收)。"""

import numpy as np
import pytest

from tests.test_ec_dqdv import _make_segment
from xrd_engine.electrochem import DQDVParams, compute_dqdv
from xrd_engine.electrochem.peaks import (
    CyclePeak,
    PeakParams,
    PeakWindow,
    build_tracks,
    detect_window_peaks,
)


@pytest.fixture
def charge_result():
    t, i, v, _ = _make_segment([(3.65, 0.9, 0.055), (4.18, 0.55, 0.045)])
    return compute_dqdv(t, i, v, "charge", DQDVParams(v_min=3.0, v_max=4.35))


def test_detect_both_windows(charge_result):
    params = PeakParams(windows=[
        PeakWindow("H1H2", "H1→H2", 3.55, 3.80),
        PeakWindow("H2H3", "H2→H3", 4.10, 4.30),
    ])
    h1 = detect_window_peaks(charge_result, params.windows[0], params, cycle=1)
    h3 = detect_window_peaks(charge_result, params.windows[1], params, cycle=1)
    assert len(h1) == 1 and abs(h1[0].position - 3.65) < 0.005
    assert len(h3) == 1 and abs(h3[0].position - 4.18) < 0.005
    assert h1[0].truncated is False and h3[0].truncated is False
    # 面积:高斯 σ=0.055,±50mV 定宽积分 = h·σ·√(2π)·erf(0.05/(σ√2))
    gauss_part = 0.9 * 0.055 * 2.5066 * 0.6365
    assert h1[0].area == pytest.approx(gauss_part, rel=0.15)


def test_peak_near_window_edge_detected(charge_result):
    """峰贴窗口边但在完整数据内:外扩检测让它存活,且不算截断。"""
    params = PeakParams(windows=[PeakWindow("H2H3", "H2→H3", 4.10, 4.19)])
    h3 = detect_window_peaks(charge_result, params.windows[0], params, cycle=1)
    assert h3 and abs(h3[0].position - 4.18) < 0.01
    assert h3[0].truncated is False
    assert h3[0].area > 0


def test_peak_at_data_edge_marked_truncated(charge_result):
    """数据边界(充电截止)切掉峰右侧 → truncated。"""
    params = PeakParams(windows=[PeakWindow("H2H3", "H2→H3", 4.10, 4.30)])
    # 截窄数据范围:曲线只到 4.19,峰 4.18 的右半侧被数据切断
    narrow = charge_result
    keep = narrow.v <= 4.19
    from xrd_engine.electrochem import DQDVResult
    narrowed = DQDVResult(
        direction=narrow.direction, v=narrow.v[keep], dqdv=narrow.dqdv[keep],
        q_grid=narrow.q_grid[1:][keep], per_gram=narrow.per_gram,
        q_total=narrow.q_total, n_records=narrow.n_records,
        n_monotonic=narrow.n_monotonic,
    )
    h3 = detect_window_peaks(narrowed, params.windows[0], params, cycle=1)
    assert h3 and h3[0].truncated is True


def test_broad_peak_found_with_residual_noise():
    """宽峰(老电池放电形貌)在去趋势残差噪声下可检出。"""
    # 跨 300mV 的宽峰:原始 MAD 估噪会把它当噪声(真实数据教训)
    t, i, v, _ = _make_segment([(3.65, 0.5, 0.15)], v_lo=3.3, v_hi=4.0)
    res = compute_dqdv(t, i, v, "charge", DQDVParams(v_min=3.3, v_max=4.05))
    params = PeakParams(windows=[PeakWindow("H1H2", "H1→H2", 3.4, 3.95)])
    peaks = detect_window_peaks(res, params.windows[0], params, cycle=1)
    assert peaks and abs(peaks[0].position - 3.65) < 0.02


def _mk_peak(cycle, pos, fwhm=0.06, height=100.0, direction="discharge",
             window="H1H2", truncated=False):
    return CyclePeak(cycle=cycle, direction=direction, window_key=window,
                     position=pos, height=height, area=height * fwhm,
                     fwhm=fwhm, prominence=height, onset=pos - fwhm,
                     truncated=truncated)


def test_track_matching_continuous():
    """峰位缓慢漂移 → 单条轨迹覆盖全部圈。"""
    peaks = [_mk_peak(n, 3.65 + 0.0004 * n) for n in range(1, 51)]
    tracks = build_tracks(peaks, PeakParams())
    assert len(tracks) == 1
    assert tracks[0].first_cycle == 1 and tracks[0].last_cycle == 50
    assert tracks[0].continuity == 1.0


def test_track_gap_tolerance():
    """单圈缺失不碎轨(track_gap=1)。"""
    peaks = [_mk_peak(n, 3.65) for n in range(1, 21) if n != 10]
    tracks = build_tracks(peaks, PeakParams())
    assert len(tracks) == 1
    assert tracks[0].continuity == pytest.approx(19 / 20)


def test_track_fwhm_adaptive_tolerance():
    """宽峰顶点游移大:容差随 fwhm 放宽,不再碎轨(真实数据教训)。"""
    # fwhm=0.15V 的宽峰,顶点正弦游移(相邻圈最大 ~42mV,固定 30mV 容差会碎)
    peaks = [
        _mk_peak(n, 3.65 + 0.03 * np.sin(2 * np.pi * n / 8.0), fwhm=0.15)
        for n in range(1, 31)
    ]
    tracks = build_tracks(peaks, PeakParams())
    assert len(tracks) == 1
    assert tracks[0].continuity == 1.0


def test_truncated_peaks_excluded_from_tracks():
    """截断峰不进轨迹(位置不可靠),但保留在输入列表。"""
    peaks = [_mk_peak(n, 3.65) for n in range(1, 11)]
    peaks.append(_mk_peak(11, 4.29, window="H2H3", direction="charge", truncated=True))
    tracks = build_tracks(peaks, PeakParams())
    assert all(t.window_key != "H2H3" for t in tracks)


def test_separate_directions_separate_tracks():
    peaks = [_mk_peak(n, 3.65, direction="charge") for n in range(1, 6)]
    peaks += [_mk_peak(n, 3.68, direction="discharge") for n in range(1, 6)]
    tracks = build_tracks(peaks, PeakParams())
    assert len(tracks) == 2
