"""pytest 共享配置与 fixture。"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """基准数据目录。

    测试在 fixture 文件缺失时 skip 而不是 fail,这样在没有运行
    make_fixtures.py 的开发机上也能跑通纯逻辑测试。
    """
    return Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture(scope="session")
def lfp_curve(fixtures_dir: Path):
    """加载 LFP 基准谱,返回 Curve。供处理后测试复用。"""
    from xrd_engine.importers.loader import load_curve

    path = fixtures_dir / "lfp_500c_scan01.xy"
    if not path.exists():
        pytest.skip("fixture 未生成,请先运行 python -m scripts.make_fixtures")
    return load_curve(path)


@pytest.fixture
def synthetic_curve():
    """不依赖文件的合成曲线:两个已知位置与宽度的高斯峰。

    用于 FWHM / Area 的数值精度测试 —— 期望值可以解析算出,
    不需要 blessed 输出。
    """
    import numpy as np

    from xrd_engine.models.types import Curve

    x = np.arange(10.0, 80.0, 0.005)  # 细网格,避免离散化误差

    def gauss(center: float, amp: float, fwhm: float) -> np.ndarray:
        sigma = fwhm / (2.0 * np.sqrt(2.0 * np.log(2.0)))
        return amp * np.exp(-((x - center) ** 2) / (2.0 * sigma**2))

    # 两个孤立的峰,相隔足够远,边界互不干扰
    y = 100.0 + gauss(20.0, 5000.0, 0.20) + gauss(50.0, 8000.0, 0.30)
    return Curve(x=x, y=y)
