"""合成 LAND 导出 xlsx 夹具(测试专用)。

中文表头 / 混合时间格式(datetime.time + "D-HH:MM:SS") / R-RateC-RateD
状态 / 电压抖动 / 两种倍率(前 n_slow 圈 0.1C) —— 覆盖真实样例的已知怪癖。
"""

from __future__ import annotations

from datetime import time
from pathlib import Path

import numpy as np


def _fmt_time(total_sec: float, style: str) -> object:
    """style: 'obj'(datetime.time,首日内) / 'str'(HH:MM:SS) / 'str_d'(D-HH:MM:SS)"""
    if style == "obj":
        s = int(total_sec) % 86400
        return time(s // 3600, s % 3600 // 60, s % 60)
    d = int(total_sec) // 86400
    s = int(total_sec) % 86400
    if style == "str":
        return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"
    return f"{d}-{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _sim_cycle_v(q_frac: np.ndarray) -> np.ndarray:
    """充电电压轨迹 3.0→4.3V,斜率含正弦调制(产生 dQ/dV 峰形变化)。"""
    return 3.0 + 1.3 * (q_frac + 0.08 * np.sin(2 * np.pi * q_frac))


def build_land_xlsx(
    path: str | Path,
    n_slow: int = 2,
    n_fast: int = 4,
    i_slow: float = 0.032,
    i_fast: float = 0.070,
    q_nominal: float = 0.24,
    rec_interval: float = 10.0,
    wobble_v: float = 0.0008,
    seed: int = 42,
) -> dict:
    """生成夹具,返回期望值(容量为精确真值:恒流恒定,梯形积分无误差)。"""
    import openpyxl

    rng = np.random.default_rng(seed)
    rows: list[tuple] = []
    t = 0.0
    step = 0
    expected: dict[int, dict[str, float]] = {}

    def add_rest(cyc: int, dur: float) -> None:
        nonlocal t, step
        step += 1
        v0 = rows[-1][2] if rows else 2.4
        n = int(dur / rec_interval)
        for k in range(n + 1):
            rows.append((t + k * rec_interval, 0.0, v0, cyc, step, "R"))
        t += n * rec_interval

    for cyc in range(1, n_slow + n_fast + 1):
        i_chg = i_slow if cyc <= n_slow else i_fast
        q_d = q_nominal * (0.97 if cyc > 1 else 0.74)
        dur_c = q_nominal / i_chg * 3600.0
        dur_d = q_d / i_chg * 3600.0

        add_rest(cyc, 120.0)
        step += 1
        n = int(dur_c / rec_interval)
        q_frac = np.linspace(0.0, 1.0, n + 1)
        v = _sim_cycle_v(q_frac) + wobble_v * rng.standard_normal(n + 1)
        for k in range(n + 1):
            rows.append((t + k * rec_interval, i_chg, float(v[k]), cyc, step, "RateC"))
        t += n * rec_interval
        add_rest(cyc, 120.0)
        step += 1
        n = int(dur_d / rec_interval)
        q_frac = np.linspace(1.0, 1.0 - q_d / q_nominal, n + 1)
        v = _sim_cycle_v(q_frac) + wobble_v * rng.standard_normal(n + 1)
        for k in range(n + 1):
            rows.append((t + k * rec_interval, -i_chg, float(v[k]), cyc, step, "RateD"))
        t += n * rec_interval
        expected[cyc] = {"q_charge": q_nominal, "q_discharge": q_d,
                         "i_med": i_chg, "slow": cyc <= n_slow}

    wb = openpyxl.Workbook()
    ws_rec = wb.active
    ws_rec.title = "记录表"
    ws_rec.append(["测试时间", "步骤时间", "电流/mA", "电压/V", "循环序号", "工步序号", "工步状态"])
    n_written = 1
    prev_cycle_t: dict[int, float] = {}
    for (ts, cur, vol, cyc, stp, st) in rows:
        # 首 30 条用 datetime.time,首日内 HH:MM:SS,之后 D-HH:MM:SS
        style = "obj" if n_written < 30 else ("str" if ts < 86400 else "str_d")
        step_t = ts - prev_cycle_t.get(cyc, ts)
        prev_cycle_t[cyc] = ts
        ws_rec.append([_fmt_time(ts, style), _fmt_time(step_t, "str"),
                       cur, vol, cyc, stp, st])
        n_written += 1

    ws_cyc = wb.create_sheet("循环表")
    ws_cyc.append(["循环序号", "充电容量/mAh", "放电容量/mAh", "充电比容量/mAh/g",
                   "放电比容量/mAh/g", "效率/%"])
    for cyc, exp in expected.items():
        ce = exp["q_discharge"] / exp["q_charge"] * 100
        ws_cyc.append([cyc, exp["q_charge"], exp["q_discharge"],
                       exp["q_charge"] / 1.77 * 1000, exp["q_discharge"] / 1.77 * 1000, ce])

    ws_flow = wb.create_sheet("测试流程表")
    ws_flow.append(["测试名", "fixture-倍率循环", ""])
    ws_flow.append(["通道", "FIX_1", ""])
    ws_flow.append(["开始时间", "2026/10/06 00:00:00", ""])
    ws_flow.append(["结束时间", "2026/10/06 12:00:00", ""])
    ws_flow.append(["工步序号", "", ""])
    ws_flow.append([1, "静置", "直到: 单步时间≥00:02:00"])
    ws_flow.append([2, "倍率充电: 0.1*C", "直到: 电压*≥4.3V"])
    ws_flow.append([3, "倍率放电: 0.1*C", "直到: 电压*≤3.0V"])
    ws_flow.append([4, "测 试 结 束", ""])

    wb.save(path)
    return {"n_cycles": n_slow + n_fast, "expected": expected,
            "n_slow": n_slow, "n_records": len(rows)}
