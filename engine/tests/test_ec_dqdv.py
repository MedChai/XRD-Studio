"""dqdv 模块测试:合成已知 dQ/dV 真值的恒流段,断言恢复精度(M1 验收)。"""

import numpy as np
import pytest

from xrd_engine.electrochem import DQDVParams, compute_dqdv

# 双峰真值:H1→H2(3.65V)与 H2→H3(4.18V),模拟无钴高镍充电
PEAKS = [(3.65, 0.9, 0.055), (4.18, 0.55, 0.045)]


def _make_segment(
    peaks: list[tuple[float, float, float]],
    i_ma: float = 0.070,
    v_lo: float = 3.0,
    v_hi: float = 4.3,
    rec_interval: float = 10.0,
    noise_v: float = 0.0005,
    seed: int = 7,
):
    """由 dQ/dV 高斯峰构造恒流充电记录。

    dQ/dV(V) = Σ 高斯;Q(V) = ∫;恒流下 Q(t)=I·t 反解 V(t)。
    返回 (t, i, v, (v_fine, dqdv_fine, q_total))。
    """
    rng = np.random.default_rng(seed)

    def dqdv_fn(x):
        return sum(h * np.exp(-0.5 * ((x - c) / s) ** 2) for c, h, s in peaks)

    v_fine = np.linspace(v_lo, v_hi, 20001)
    dqdv_fine = dqdv_fn(v_fine)
    q_fine = np.concatenate(
        ([0.0], np.cumsum((dqdv_fine[1:] + dqdv_fine[:-1]) * 0.5 * np.diff(v_fine)))
    )
    q_total = q_fine[-1]

    dur = q_total / i_ma * 3600.0
    n = int(dur / rec_interval)
    t = np.arange(n + 1) * rec_interval
    q_t = np.clip(i_ma * t / 3600.0, 0.0, q_total)
    v_t = np.interp(q_t, q_fine, v_fine) + noise_v * rng.standard_normal(q_t.size)
    order = np.argsort(v_t)
    return t, np.full_like(t, i_ma), v_t[order], (v_fine, dqdv_fine, q_total)


def test_peak_positions_recovered():
    t, i, v, _ = _make_segment(PEAKS)
    res = compute_dqdv(t, i, v, "charge", DQDVParams(dv_mv=2.0, v_min=3.0, v_max=4.35))
    for center, _, _ in PEAKS:
        near = np.abs(res.v - center) < 0.15
        idx = int(np.argmax(np.where(near, res.dqdv, -1)))
        assert abs(res.v[idx] - center) < 0.005
    for center, height, _ in PEAKS:
        window = np.abs(res.v - center) < 0.02
        est = float(res.dqdv[window].max())
        assert est == pytest.approx(height, rel=0.10)


def test_total_capacity_recovered():
    t, i, v, (_, _, q_total) = _make_segment(PEAKS)
    res = compute_dqdv(t, i, v, "charge", DQDVParams(v_min=3.0, v_max=4.35))
    assert res.q_total == pytest.approx(q_total, rel=0.01)


def test_per_gram_normalization():
    mass_mg = 1.77
    t, i, v, _ = _make_segment(PEAKS)
    raw = compute_dqdv(t, i, v, "charge", DQDVParams(v_min=3.0, v_max=4.35))
    g = compute_dqdv(
        t, i, v, "charge", DQDVParams(v_min=3.0, v_max=4.35, mass_mg=mass_mg)
    )
    assert g.per_gram
    assert g.q_total == pytest.approx(raw.q_total * 1000.0 / mass_mg, rel=1e-6)
    assert np.allclose(g.dqdv, raw.dqdv * 1000.0 / mass_mg)


def test_discharge_direction_positive():
    """放电方向:V 递减,dQ/dV 应为正(每伏释放的容量)。"""
    _, _, v, _ = _make_segment(PEAKS)
    t_d = np.arange(v.size) * 10.0
    i_d = np.full_like(t_d, -0.070)
    v_d = v[::-1]
    res = compute_dqdv(t_d, i_d, v_d, "discharge", DQDVParams(v_min=3.0, v_max=4.35))
    assert res.dqdv.max() > 0.3
    assert res.q_total > 0
    assert float(res.dqdv.min()) > -0.05  # 无整体翻转


def test_voltage_wobble_not_double_counted():
    """电压抖动只取创新高点:抖动不改变总容量。"""
    t, i, v, (_, _, q_total) = _make_segment(PEAKS, noise_v=0.0)
    t2, i2, v2, _ = _make_segment(PEAKS, noise_v=0.003, seed=11)
    r1 = compute_dqdv(t, i, v, "charge", DQDVParams(v_min=3.0, v_max=4.35))
    r2 = compute_dqdv(t2, i2, v2, "charge", DQDVParams(v_min=3.0, v_max=4.35))
    assert r2.q_total == pytest.approx(q_total, rel=0.02)
    assert r1.q_total == pytest.approx(q_total, rel=0.01)


def test_bad_params_rejected():
    t, i, v, _ = _make_segment(PEAKS)
    with pytest.raises(ValueError):
        compute_dqdv(t, i, v, "charge", DQDVParams(dv_mv=0))
    with pytest.raises(ValueError):
        compute_dqdv(t, i, v, "charge", DQDVParams(sg_window=10))
    with pytest.raises(ValueError):
        compute_dqdv(t, i, v, "charge", DQDVParams(v_min=4.2, v_max=4.25))


def test_too_short_segment_raises():
    with pytest.raises(ValueError):
        compute_dqdv(np.arange(4.0), np.ones(4) * 0.07, np.linspace(3, 4, 4), "charge")
