"""背景估计(§7.3)。

两种方法:

  AsLS(Asymmetric Least Squares)—— 默认方法,适合有明显背景的谱
  Polynomial                      —— 适合背景平滑、峰较少的谱

AsLS 的 λ 跨度是 10²–10¹⁰,UI 用 log 刻度暴露(§7.3),但引擎内部
一律用线性值计算。这样 UI 改刻度不影响这里的数值语义。

实现要点:AsLS 是迭代重加权最小二乘,每次迭代解一个三对角系统。
点数多时用稀疏矩阵,15k 点应在 30ms 量级完成(§9.3 的基准)。
"""

from __future__ import annotations

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import spsolve

from ..models.types import BackgroundParams

# §7.3 规定的默认值
DEFAULT_SMOOTHNESS = 1e5
DEFAULT_ASYMMETRY = 0.01
DEFAULT_ITERATIONS = 10
DEFAULT_POLY_ORDER = 3

# 参数合法范围,供 UI 与校验共用
SMOOTHNESS_MIN = 1e2
SMOOTHNESS_MAX = 1e10
ASYMMETRY_MIN = 1e-4
ASYMMETRY_MAX = 0.5
ITERATIONS_MIN = 1
ITERATIONS_MAX = 50
POLY_ORDER_MIN = 1
POLY_ORDER_MAX = 8


class BackgroundError(ValueError):
    """背景估计的输入或参数不合法。"""


def _second_difference_matrix(n: int) -> sparse.spmatrix:
    """构造二阶差分矩阵 D,使得 D @ z 给出 z 的二阶差分。

    AsLS 的目标是惩罚背景曲线的二阶差分(即曲率),使背景尽可能平滑。
    """
    if n < 3:
        return sparse.csr_matrix((n, n), dtype=np.float64)
    # 每行是 [1, -2, 1] 的三对角错位
    diags = np.array([np.ones(n - 2), -2.0 * np.ones(n - 2), np.ones(n - 2)])
    offsets = np.array([0, 1, 2])
    return sparse.diags(diags, offsets, shape=(n - 2, n), format="csc")


def asls_background(
    y: np.ndarray,
    smoothness: float = DEFAULT_SMOOTHNESS,
    asymmetry: float = DEFAULT_ASYMMETRY,
    iterations: int = DEFAULT_ITERATIONS,
) -> np.ndarray:
    """非对称最小二乘背景估计(Eilers & Boelens)。

    迭代形式:

        z = argmin_z  Σ w_i (y_i - z_i)² + λ Σ (Δ²z)²
        w_i = p       若 y_i > z_i   (点高于背景 → 可能是峰,权重要小)
        w_i = 1 - p   若 y_i ≤ z_i   (点低于背景 → 应是背景,权重保持)

    p 小(如 0.01)时迭代结果会「忽略」高于自己的点,从而贴着峰的下缘走。
    """
    y_arr = np.asarray(y, dtype=np.float64).ravel()
    n = y_arr.size

    if n < 3:
        # 点太少无法构造二阶差分,直接返回原曲线
        return y_arr.copy()

    if not (SMOOTHNESS_MIN <= smoothness <= SMOOTHNESS_MAX):
        raise BackgroundError(
            f"smoothness 应在 [{SMOOTHNESS_MIN:g}, {SMOOTHNESS_MAX:g}] 之间,收到 {smoothness:g}"
        )
    if not (ASYMMETRY_MIN <= asymmetry <= ASYMMETRY_MAX):
        raise BackgroundError(
            f"asymmetry 应在 [{ASYMMETRY_MIN:g}, {ASYMMETRY_MAX:g}] 之间,收到 {asymmetry:g}"
        )
    if not (ITERATIONS_MIN <= iterations <= ITERATIONS_MAX):
        raise BackgroundError(
            f"iterations 应在 [{ITERATIONS_MIN}, {ITERATIONS_MAX}] 之间,收到 {iterations}"
        )

    d = _second_difference_matrix(n)
    dtd = (d.T @ d).tocsc()

    # 初始权重全设为 p(即假设所有点都可能是峰)
    weights = np.full(n, asymmetry, dtype=np.float64)
    z = y_arr.copy()

    # I 用稀疏单位阵,避免构造稠密 n×n
    identity = sparse.identity(n, format="csc")

    for _ in range(iterations):
        weight_matrix = sparse.diags(weights, 0, format="csc")
        lhs = (weight_matrix + smoothness * dtd).tocsc()
        # 加一个极小的岭项,防止极端参数下矩阵奇异
        lhs = lhs + 1e-12 * identity
        z = spsolve(lhs, weights * y_arr)
        if not np.all(np.isfinite(z)):
            raise BackgroundError(
                "背景估计未收敛,请减小 smoothness 或增大 asymmetry"
            )
        # 更新权重:高于背景的点降权
        weights = np.where(y_arr > z, asymmetry, 1.0 - asymmetry)

    return z


def polynomial_background(
    x: np.ndarray,
    y: np.ndarray,
    order: int = DEFAULT_POLY_ORDER,
    iterations: int = 5,
) -> np.ndarray:
    """多项式背景拟合,带迭代剔峰。

    直接对全谱做多项式拟合会被强峰拉高背景。因此迭代几轮:
    每轮用当前背景作参考,把高于背景 + 2σ 的点排除后再拟合。

    比 AsLS 快,但对复杂背景(多个宽包)适应性差 —— UI 在残差过大时
    会提示用户改用 AsLS(§7.3)。
    """
    x_arr = np.asarray(x, dtype=np.float64).ravel()
    y_arr = np.asarray(y, dtype=np.float64).ravel()

    if x_arr.size != y_arr.size:
        raise BackgroundError("x 与 y 长度不一致")
    if not (POLY_ORDER_MIN <= order <= POLY_ORDER_MAX):
        raise BackgroundError(
            f"多项式阶数应在 [{POLY_ORDER_MIN}, {POLY_ORDER_MAX}] 之间,收到 {order}"
        )
    if x_arr.size <= order + 1:
        raise BackgroundError(
            f"数据点({x_arr.size})不足以拟合 {order} 阶多项式"
        )

    # 归一化 x 到 [-1, 1]:高阶多项式在原始量级(10–80)上会数值病态
    x_mid = 0.5 * (x_arr.min() + x_arr.max())
    x_half = 0.5 * (x_arr.max() - x_arr.min())
    x_scaled = (x_arr - x_mid) / x_half if x_half > 0 else np.zeros_like(x_arr)

    mask = np.ones(x_arr.size, dtype=bool)
    coeffs = np.zeros(order + 1)

    for _ in range(iterations):
        if mask.sum() <= order + 1:
            # 被剔得太多,回退到上一轮结果
            break
        coeffs = np.polyfit(x_scaled[mask], y_arr[mask], order)
        fitted = np.polyval(coeffs, x_scaled)

        residual = y_arr - fitted
        sigma = np.std(residual[mask]) if mask.sum() > 1 else 0.0
        mask = residual < (2.0 * sigma)

    return np.polyval(coeffs, x_scaled)


def estimate_background(x: np.ndarray, y: np.ndarray, params: BackgroundParams) -> np.ndarray:
    """按 params 分派到具体方法。params.enabled 为 False 时返回全零。

    返回的背景曲线与输入等长,可直接用于相减与绘图。
    """
    if not params.enabled:
        return np.zeros_like(np.asarray(y, dtype=np.float64))

    if params.method == "asls":
        return asls_background(
            y,
            smoothness=params.smoothness,
            asymmetry=params.asymmetry,
            iterations=params.iterations,
        )
    if params.method == "polynomial":
        return polynomial_background(x, y, order=params.order)

    raise BackgroundError(f"未知的背景估计方法:{params.method}")


def subtract_background(y: np.ndarray, background: np.ndarray) -> np.ndarray:
    """从曲线中扣除背景。

    结果钳到 0 以上:AsLS 在噪声区可能给出略高于信号的背景,
    扣完会出现小的负值。保留负值会让后续的 height 判据与
    「Minimum Height 5%」的语义变得难以解释。
    """
    y_arr = np.asarray(y, dtype=np.float64)
    if y_arr.shape != np.asarray(background).shape:
        raise BackgroundError("曲线与背景形状不一致")
    return np.maximum(y_arr - background, 0.0)
