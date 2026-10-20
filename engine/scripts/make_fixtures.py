"""生成基准合成谱数据(fixtures)。

README §12.0 要求 fixtures 中的合成谱必须可复现,因此不手工放置二进制
文件,而是全部由本脚本生成。运行:

    python -m scripts.make_fixtures

生成的数据被测试与人工评审共同使用(§12 Milestone 4 的校准集)。

设计要点:
  - 峰位取自真实材料的已知衍射角,不是随手编的数字
  - 噪声用固定种子,保证每次生成结果一致,便于回归比对
  - 覆盖均匀/非均匀网格、降序、含 NaN、垃圾头等真实边界情况
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"

# 固定种子 —— 改动会打破回归测试的 blessed 输出
RANDOM_SEED = 20260915


def pseudo_voigt(x: np.ndarray, center: float, amplitude: float,
                 fwhm: float, eta: float = 0.5) -> np.ndarray:
    """伪 Voigt 峰形。eta=1 为纯 Lorentzian,eta=0 为纯 Gaussian。

    用伪 Voigt 而不是纯 Gaussian,因为真实 XRD 峰有 Lorentzian 分量,
    纯 Gaussian 生成的测试数据会让 FWHM 算法看起来比实际更准。
    """
    sigma = fwhm / (2.0 * np.sqrt(2.0 * np.log(2.0)))
    gamma = fwhm / 2.0
    gaussian = np.exp(-((x - center) ** 2) / (2.0 * sigma**2))
    lorentzian = gamma**2 / ((x - center) ** 2 + gamma**2)
    return amplitude * (eta * lorentzian + (1.0 - eta) * gaussian)


def make_lfp_pattern(
    x_min: float = 10.0,
    x_max: float = 80.0,
    step: float = 0.02,
    noise_level: float = 40.0,
    seed: int = RANDOM_SEED,
) -> tuple[np.ndarray, np.ndarray]:
    """生成一张 LFP(磷酸铁锂)衍射谱。

    峰位取自橄榄石结构 LFP 的常见衍射角(Cu Kα),强度比例参照实验谱。
    这是 Milestone 4 校准集里权重最高的一张,因为 README 全篇示例都用它。
    """
    x = np.arange(x_min, x_max + step / 2, step)
    rng = np.random.default_rng(seed)

    # (2θ 中心, 相对强度, FWHM)  —— FWHM 随角度增大的趋势符合仪器宽化
    peaks = [
        (17.15, 0.10, 0.16),
        (20.80, 0.22, 0.17),
        (25.58, 0.35, 0.17),
        (29.72, 0.55, 0.18),
        (32.21, 0.42, 0.18),
        (34.39, 1.00, 0.18),  # (020) 主峰
        (35.62, 0.61, 0.18),
        (36.85, 0.20, 0.19),
        (37.90, 0.15, 0.19),
        (39.22, 0.13, 0.19),
        (42.05, 0.09, 0.20),
        (45.60, 0.07, 0.20),
        (47.80, 0.05, 0.21),
        (52.55, 0.16, 0.21),
        (55.90, 0.06, 0.22),
        (58.20, 0.12, 0.22),
        (61.40, 0.05, 0.23),
        (63.90, 0.04, 0.23),
        (66.30, 0.03, 0.24),
        (70.10, 0.04, 0.24),
        (74.50, 0.03, 0.25),
    ]

    y = np.full_like(x, 120.0)  # 基线
    for center, rel_amp, fwhm in peaks:
        y += pseudo_voigt(x, center, rel_amp * 31542.0 * 0.32, fwhm)

    # 平滑下弯的背景(低角度散射)
    y += 850.0 * np.exp(-(x - x_min) / 12.0)

    # 泊松型噪声:计数统计的方差随强度增大,比固定高斯噪声更真实
    y = rng.poisson(np.maximum(y, 1.0)).astype(np.float64)
    if noise_level > 0:
        y += rng.normal(0.0, noise_level, size=x.shape)

    return x, np.maximum(y, 0.0)


def _r3m_two_theta(h: int, k: int, l: int, a: float, c: float, lam: float) -> float:
    """R-3m 六方晶胞的 (hkl) → 2θ。位置由晶体学反算,不写死数字。"""
    inv_d2 = (4.0 / 3.0) * (h * h + h * k + k * k) / (a * a) + (l * l) / (c * c)
    d = 1.0 / math.sqrt(inv_d2)
    return math.degrees(2.0 * math.asin(lam / (2.0 * d)))


def make_ncm811_pattern(seed: int = RANDOM_SEED + 1) -> tuple[np.ndarray, np.ndarray]:
    """生成一张 NCM811(层状氧化物)衍射谱。

    **峰位由 R-3m 晶胞反算,不再手写。** 手写的版本里 (006) 标在 38.18°
    而 (110) 标在 68.00° —— 后者按任何合理晶胞都对应 (113),(110) 应在
    64.6° 附近;而 38.18° 处按 a=2.874/c=14.24 实际是 102 分量而非 (006)。
    这种"注释与实际位置不符"的 fixture 会把错误的期望固化进测试。

    只列**允许**反射(-h+k+l ≡ 0 mod 3);(006) 弱、102 分量强(显示名
    跟文献走),与层状正极实验一致。
    """
    x = np.arange(10.0, 80.0 + 0.02 / 2, 0.02)
    rng = np.random.default_rng(seed)

    a, c, lam = 2.874, 14.240, 1.5418
    # (hkl): 相对强度。强度比参考层状正极典型谱:(003) 最强,高角依次衰减。
    reflections = [
        ((0, 0, 3), 1.00),
        ((1, 0, 1), 0.35),
        ((0, 0, 6), 0.03),   # 弱
        ((0, 1, 2), 0.11),   # 强于 (006)
        ((1, 0, 4), 0.42),
        ((0, 1, 5), 0.13),
        ((1, 0, 7), 0.12),
        ((0, 1, 8), 0.16),
        ((1, 1, 0), 0.20),
        ((1, 1, 3), 0.13),
        ((1, 1, 6), 0.05),
        ((2, 0, 2), 0.04),
    ]
    fwhm_at_0, fwhm_slope = 0.16, 0.0028

    y = np.full_like(x, 95.0)
    for (h, k, l), rel_amp in reflections:
        center = _r3m_two_theta(h, k, l, a, c, lam)
        fwhm = fwhm_at_0 + fwhm_slope * center
        y += pseudo_voigt(x, center, rel_amp * 26000.0 * 0.30, fwhm)
    y += 420.0 * np.exp(-(x - 10.0) / 15.0)  # 低角非晶包
    y = rng.poisson(np.maximum(y, 1.0)).astype(np.float64)
    return x, np.maximum(y, 0.0)


def make_cobalt_free_pattern(seed: int = RANDOM_SEED + 2) -> tuple[np.ndarray, np.ndarray]:
    """生成一张**无钴**高镍层状谱(NMA 型:LiNi0.90Mn0.05Al0.05O2)。

    与 ncm811_01(含钴, a=2.874/c=14.240)刻意不同:
      - 晶胞取无钴端元常见值 a=2.884 / c=14.278 —— 正是用户实测精修值
        附近的胞,写死 2.878/14.216 的旧逻辑在这张谱上会偏约 0.1°,
        (006)/(102) 配名可被检验出来;
      - (006) 极弱、102 分量明显强(层状正极分工), (108)/(110) 双峰保留;
      - 谱尾加一个 R-3m 允许反射表之外的小峰(39.5°,模拟残碱/杂相),
        用于检验"未建模峰报 none 而不误标"。
    """
    x = np.arange(10.0, 80.0 + 0.02 / 2, 0.02)
    rng = np.random.default_rng(seed)

    a, c, lam = 2.884, 14.278, 1.540593  # Cu Kα1,与用户 Rigaku 数据一致
    reflections = [
        ((0, 0, 3), 1.00),
        ((1, 0, 1), 0.33),
        ((0, 0, 6), 0.05),   # 弱,但要在 medium 灵敏度下可检出
        ((0, 1, 2), 0.12),   # 强于 (006)
        ((1, 0, 4), 0.40),
        ((0, 1, 5), 0.12),
        ((1, 0, 7), 0.11),
        ((0, 1, 8), 0.15),
        ((1, 1, 0), 0.19),
        ((1, 1, 3), 0.12),
        ((1, 1, 6), 0.05),
        ((2, 0, 2), 0.04),
    ]
    fwhm_at_0, fwhm_slope = 0.17, 0.0030

    y = np.full_like(x, 90.0)
    for (h, k, l), rel_amp in reflections:
        center = _r3m_two_theta(h, k, l, a, c, lam)
        fwhm = fwhm_at_0 + fwhm_slope * center
        y += pseudo_voigt(x, center, rel_amp * 24000.0 * 0.30, fwhm)
    # 杂相峰(不在 R-3m 允许表内):归属应报 none,而不是硬套最近的晶面
    y += pseudo_voigt(x, 39.50, 24000.0 * 0.30 * 0.06, 0.22)
    y += 400.0 * np.exp(-(x - 10.0) / 15.0)
    y = rng.poisson(np.maximum(y, 1.0)).astype(np.float64)
    return x, np.maximum(y, 0.0)


def write_xy(path: Path, x: np.ndarray, y: np.ndarray, decimals: int = 3) -> None:
    """写两列空格分隔的 .xy 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for xi, yi in zip(x, y, strict=False):
            fh.write(f"{xi:.{decimals}f} {yi:.2f}\n")


def main() -> int:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    # --- 基准谱:LFP 主峰示例数据,与 README §4.3 的规定值一致 ---
    x_lfp, y_lfp = make_lfp_pattern()
    write_xy(FIXTURES_DIR / "lfp_500c_scan01.xy", x_lfp, y_lfp)

    # 同一样品的第二张谱,用于测试多 Dataset 与样品的层叠显示
    x_lfp2, y_lfp2 = make_lfp_pattern(noise_level=55.0, seed=RANDOM_SEED + 7)
    write_xy(FIXTURES_DIR / "lfp_500c_scan02.xy", x_lfp2, y_lfp2)

    # 系列样品,用于测试 Overlay / Stacked 与 Preset 批量应用(§1.2 S4/S5)
    for temp in (400, 450, 550):
        x_s, y_s = make_lfp_pattern(
            noise_level=45.0 + (temp - 400) / 10.0,
            seed=RANDOM_SEED + temp,
        )
        # 不同温度下峰宽略有差异,模拟晶粒长大
        write_xy(FIXTURES_DIR / f"lfp_{temp}c_scan01.xy", x_s, y_s)

    x_ncm, y_ncm = make_ncm811_pattern()
    write_xy(FIXTURES_DIR / "ncm811_01.xy", x_ncm, y_ncm)

    # 无钴高镍(a/c 偏离写死值,含杂相峰)—— 标注回归的无钴基准
    x_cf, y_cf = make_cobalt_free_pattern()
    write_xy(FIXTURES_DIR / "cobalt_free_01.xy", x_cf, y_cf)

    # --- 非均匀网格:等 d 扫描,转成 2θ 后步长天然不均匀(§4.2) ---
    d_min, d_max = 0.909, 8.83  # 对应 Cu Kα 下约 10°–80°
    d = np.linspace(d_min, d_max, 3501)
    lam = 1.5405980
    ratio = lam / (2.0 * d)
    x_nonuni = np.degrees(2.0 * np.arcsin(np.clip(ratio, -1.0, 1.0)))
    x_nonuni = np.sort(x_nonuni)
    # 在非均匀网格上直接算 LFP 强度
    y_nonuni = np.full_like(x_nonuni, 120.0)
    for center, rel_amp, fwhm in [
        (17.15, 0.10, 0.16), (25.58, 0.35, 0.17), (34.39, 1.00, 0.18),
        (35.62, 0.61, 0.18), (52.55, 0.16, 0.21),
    ]:
        y_nonuni += pseudo_voigt(x_nonuni, center, rel_amp * 31542.0 * 0.32, fwhm)
    rng = np.random.default_rng(RANDOM_SEED + 99)
    y_nonuni = rng.poisson(np.maximum(y_nonuni, 1.0)).astype(np.float64)
    write_xy(FIXTURES_DIR / "nonuniform_dscan.xy", x_nonuni, y_nonuni)

    # --- 降序导出(部分仪器按高角度到低角度写文件) ---
    write_xy(FIXTURES_DIR / "descending.xy", x_lfp[::-1], y_lfp[::-1])

    # --- 含垃圾头 + 注释行 + 科学计数法的 CSV ---
    with (FIXTURES_DIR / "messy_header.csv").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("XRD Measurement Report\n")
        fh.write("Instrument: Bruker D8 Advance\n")
        fh.write("Anode: Cu, 40kV/40mA\n")
        fh.write("Scan: 10-80 deg, step 0.02\n")
        fh.write("#\n")
        fh.write("# 2Theta,Intensity\n")
        for xi, yi in zip(x_lfp[:500], y_lfp[:500], strict=False):
            fh.write(f"{xi:.3f},{yi:.4E}\n")

    # --- 分号分隔 + 中文字符头(GB18030 编码) ---
    with (FIXTURES_DIR / "semicolon_gb.csv").open("w", encoding="gb18030", newline="\n") as fh:
        fh.write("角度;强度\n")
        fh.write("2θ(度);计数\n")
        for xi, yi in zip(x_lfp[:800], y_lfp[:800], strict=False):
            fh.write(f"{xi:.3f};{yi:.1f}\n")

    # --- 含 NaN 与空值占位符的数据 ---
    with (FIXTURES_DIR / "with_nan.dat").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# 含有缺失值的数据\n")
        for i, (xi, yi) in enumerate(zip(x_lfp[:600], y_lfp[:600], strict=False)):
            if i in (50, 51, 200):
                fh.write(f"{xi:.3f} -\n")       # 缺失占位符
            elif i == 300:
                fh.write(f"{xi:.3f} NaN\n")     # 显式 NaN
            else:
                fh.write(f"{xi:.3f} {yi:.2f}\n")

    # --- 全平线(§7.2 边界情况) ---
    with (FIXTURES_DIR / "flat_line.xy").open("w", encoding="utf-8", newline="\n") as fh:
        for xi in np.arange(10.0, 80.0, 0.02):
            fh.write(f"{xi:.3f} 100.00\n")

    # --- 数据点不足(应被拒绝) ---
    with (FIXTURES_DIR / "too_few_points.xy").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("10.0 100\n10.5 120\n")

    # --- 完全无法解析(应触发列映射) ---
    with (FIXTURES_DIR / "unparseable.txt").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("This file contains no numeric data at all.\n")
        fh.write("Just some prose describing an experiment.\n")

    # --- 单列(应触发列映射) ---
    with (FIXTURES_DIR / "single_column.txt").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("Intensity only\n")
        for yi in y_lfp[:300]:
            fh.write(f"{yi:.2f}\n")

    # --- 制表符分隔、点号做小数点、逗号做千分位(欧陆风格) ---
    with (FIXTURES_DIR / "european.txt").open("w", encoding="utf-8", newline="\n") as fh:
        for i in range(400):
            xi = 10.0 + i * 0.02
            yi = y_lfp[i]
            fh.write(f"{xi:.3f}".replace(".", ",") + "\t" + f"{yi:.1f}\n")

    # --- 记录生成参数,便于审计 ---
    with (FIXTURES_DIR / "README.txt").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("本目录下的数据全部由 scripts/make_fixtures.py 生成。\n")
        fh.write(f"随机种子:{RANDOM_SEED}\n")
        fh.write("请勿手工编辑 —— 修改应通过脚本并同步更新 blessed 输出。\n")
        fh.write("\n文件清单:\n")
        for p in sorted(FIXTURES_DIR.glob("*")):
            if p.name != "README.txt":
                fh.write(f"  {p.name}\n")

    generated = sorted(p.name for p in FIXTURES_DIR.glob("*") if p.name != "README.txt")
    print(f"已生成 {len(generated)} 个 fixture 文件到 {FIXTURES_DIR}")
    for name in generated:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
