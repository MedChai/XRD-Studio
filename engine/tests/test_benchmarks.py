"""性能基准(§9.4):Milestone 2 建立 harness,后续进 CI。

测的是引擎侧的硬指标;前端感知指标(切换 Sample、绘制)在
Milestone 3 用 Playwright/Tauri driver 补。

判定阈值取自 §9.4 表,以 15k 点(常见慢扫)与 100k 点(同步辐射)
两档为基线。这些不是单元测试断言 —— 机器差异大,基准报告数值,
只有超过 3× 阈值才算失败(防 CI 抖动)。
"""

from __future__ import annotations

import math
import time

import numpy as np
import pytest

from xrd_engine.models.types import (
    BackgroundParams,
    Curve,
    PeakDetectionParams,
    PipelineParams,
    SmoothingParams,
)
from xrd_engine.processing.pipeline import run_pipeline


def synth_curve(n: int) -> Curve:
    """n 点的合成 LFP 谱。"""
    x = np.linspace(10.0, 80.0, n)
    step = (x[1] - x[0]) if n > 1 else 0.02
    y = 120.0 + 850.0 * np.exp(-(x - 10.0) / 12.0)
    rng = np.random.default_rng(42)
    centers = np.arange(12.0, 78.0, 3.0)
    for c in centers:
        amp = float(rng.uniform(1000, 30000))
        fwhm = 0.18
        sigma = fwhm / 2.3548
        y = y + amp * np.exp(-((x - c) ** 2) / (2 * sigma**2))
    y = rng.poisson(np.maximum(y, 1)).astype(np.float64)
    _ = step
    return Curve(x=x, y=y)


# §9.4 的引擎目标(秒),3× 为 CI 红线
THRESHOLDS_S = {
    "asls_15k": 0.030 * 3,
    "asls_100k": 0.300 * 3,
    "pipeline_15k": 0.100 * 3,
    "pipeline_100k": 1.0 * 3,
    "import_15k": 1.0 * 3,
}


def time_best(fn, runs: int = 5) -> float:
    """取最好的一次计时,排除调度噪声。"""
    best = math.inf
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


@pytest.mark.parametrize("n", [15_000, 100_000])
def test_asls_speed(n):
    from xrd_engine.processing.background import asls_background

    curve = synth_curve(n)
    t = time_best(
        lambda: asls_background(curve.y, smoothness=1e5, asymmetry=0.01, iterations=5),
        runs=3,
    )
    limit = THRESHOLDS_S[f"asls_{n//1000}k"]
    print(f"\nAsLS {n:,} pts: {t*1000:.1f} ms (红线 {limit*1000:.0f} ms)")
    assert t < limit, f"AsLS {n} pts 超红线:{t*1000:.0f}ms > {limit*1000:.0f}ms"


@pytest.mark.parametrize("n", [15_000, 100_000])
def test_full_pipeline_speed(n):
    curve = synth_curve(n)
    params = PipelineParams(
        background=BackgroundParams(enabled=True, method="asls"),
        smoothing=SmoothingParams(enabled=True, window_length=9, polyorder=2),
        peaks=PeakDetectionParams(sensitivity="medium"),
    )
    t = time_best(lambda: run_pipeline(curve, params), runs=3)
    limit = THRESHOLDS_S[f"pipeline_{n//1000}k"]
    print(f"\nPipeline {n:,} pts: {t*1000:.1f} ms (红线 {limit*1000:.0f} ms)")
    assert t < limit, f"Pipeline {n} pts 超红线:{t*1000:.0f}ms > {limit*1000:.0f}ms"


def test_import_speed(tmp_path):
    """§9.4:导入 15k 点文件(含哈希+复制)< 1s ×3。"""
    from xrd_engine.importers.loader import LoadedDataset, load_file

    curve = synth_curve(15_000)
    path = tmp_path / "perf.xy"
    with path.open("w", encoding="utf-8") as fh:
        for xi, yi in zip(curve.x, curve.y, strict=False):
            fh.write(f"{xi:.3f} {yi:.2f}\n")

    t = math.inf
    for _ in range(3):
        t0 = time.perf_counter()
        result = load_file(path)
        t = min(t, time.perf_counter() - t0)
    assert isinstance(result, LoadedDataset)
    limit = THRESHOLDS_S["import_15k"]
    print(f"\nImport 15k: {t*1000:.1f} ms (红线 {limit*1000:.0f} ms)")
    assert t < limit
