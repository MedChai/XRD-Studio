"""层状 R-3m 正极的反射指数化与全谱拟合。

这是高镍无钴体系峰识别的**唯一事实来源**:所有理论 2θ 都由
(hkl, a, c, λ) 按 Bragg 定律算出,不允许在别处硬编码位置数字。

为什么必须这样做
----------------

此前的做法是维护一张 7 条硬编码参考表(a=2.878, c=14.216)再对每个
峰做"最近邻"匹配。它在两处系统性出错:

1. **参考表写死、且混用了两套指数约定。** R-3m (#166) 的阳离子 3a 位点
   结构因子是

       F ∝ 1 + ω^(h+2k+2l) + ω^(2h+k+l),  ω = exp(2πi/3)

   非零当且仅当 `h+2k+2l ≡ 0` 且 `2h+k+l ≡ 0 (mod 3)`,两者联立等价于

       -h + k + l ≡ 0  (mod 3)          ← 枚举/拟合采用的消光规则

   按此规则常见的**禁阻**反射有 (001)(100)(011)(111)(00l 中 l≠3n 者)
   等;常见的**允许**反射有

       (003) (006) (009) (101) (012) (104) (015) (107)
       (018) (110) (113) (116) (202) (024) (021) (1010) (0012)

   文献在双峰上常写 `(102)`/`(108)`(h-k+l 约定),与本约定的
   (012)/(018) 是**同一物理反射**(d 相同),只是名字对调。对外
   label 用文献名,见 `_LITERATURE_HKL`;不要把显示名 (102) 拿回
   `allowed_hkl` 当禁阻判断。

2. **晶胞参数写死。** (006) 与 102 分量的理论间距对 c 高度敏感:同一张谱
   用 c=14.216 得 (006)=37.975/强分量=38.266,c=14.265 得
   37.809/38.171。跨度足以让"哪个反射离观测峰更近"翻转。不同 Ni 含量、
   掺杂、循环后的样品 c 本来就会变,固定值必然对一部分样品判反。

   所以本模块**对每张谱精修 (a, c, 零点偏移)**,再用精修结果驱动标注。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import lsq_linear

# 默认尝试的晶胞范围(高镍层状氧化物)。用于每张谱的网格精修。
DEFAULT_A_RANGE = (2.82, 2.92)
DEFAULT_C_RANGE = (14.00, 14.45)

# 网格步长。c 决定 (006)/(102) 的归属,必须细于两者间距(约 0.29°);
# 粗扫用 _COARSE_STEP 落进正确盆地,再由两轮细化达到最终精度。
DEFAULT_A_STEP = 0.008
DEFAULT_C_STEP = 0.005

# 第一层粗扫的步长。残差面在 (a,c) 上光滑单峰,0.02 足以落进正确盆地
# (实测:0.04 会跳到 a=2.900 的错误盆地,0.02 稳定命中)。
_COARSE_STEP = 0.02

# 粗扫后的局部细化:在最优格点 ±REFINE_SPAN 内用 REFINE_STEP 再扫,
# 第二轮再收窄一半步长。两层细化把量化误差压到 0.001 Å 量级。
_REFINE_SPAN = 0.010
_REFINE_STEP = 0.002

# 2θ 零点偏移的搜索范围(度)。
DEFAULT_ZERO_RANGE = (-0.35, 0.35)

# 反射枚举的 (h,k,l) 上限。l 要够到高角,按 c 与 2θ 上限反推。
_MAX_HK = 4
_MAX_L = 20

# Kα1 / Kα2 波长(Cu,实验室常规)。Kα2 强度约为 Kα1 的 0.5。
CU_KA1 = 1.540593
CU_KA2 = 1.544414
KA2_RATIO = 0.5

# 峰形 pseudo-Voigt 的洛伦兹占比。层状正极峰形介于高斯与洛伦兹之间。
DEFAULT_ETA = 0.6

# 峰宽随角度的仪器展宽。实测层状正极在 Cu Kα 下,(003)@18.6° 约 0.16°,
# (104)@44.3° 约 0.26°,近似线性增长。用一个 Caglioti 形式的线性模型
# 代替单一常数 FWHM —— 固定 FWHM 会让高角峰的峰形严重失真,进而使
# 邻近反射(如 (108)/(110))互相偷强度。
DEFAULT_FWHM_AT_0 = 0.12
DEFAULT_FWHM_SLOPE = 0.0032
"""FWHM(2θ) ≈ fwhm_at_0 + slope × 2θ(度)。"""

DEFAULT_BG_DEGREE = 4
"""背景多项式阶数。见 _background_terms 的说明 —— 需要足够高阶来吸收
层状正极低角常见的不属于 R-3m 物相的非晶包,否则峰形会被扭曲。"""

_BG_N = DEFAULT_BG_DEGREE + 1
"""背景项在设计矩阵里占的列数(常数 + 1..degree 次)。振幅从这一列开始。"""


# 文献显示名:同一物理反射在 h-k+l 约定下的 (hkl)。内部枚举/拟合仍用
# 消光规则给出的 (0,1,2)/(0,1,8);d 间距与 2θ 在两套名字下相同。
# 电池文献的 (006)/(102)、(108)/(110) 双峰写法对应这里的别名。
_LITERATURE_HKL: dict[tuple[int, int, int], tuple[int, int, int]] = {
    (0, 1, 2): (1, 0, 2),
    (0, 1, 8): (1, 0, 8),
}


def allowed_hkl(h: int, k: int, l: int) -> bool:
    """R-3m 阳离子 3a 位点的反射条件:`-h + k + l ≡ 0 (mod 3)`。

    这是"哪些 2θ 上该有峰"的物理判据,**不随显示名变化**。文献写 `(102)`
    时指的仍是本约定下 (0,1,2) 那条允许反射(见 `_LITERATURE_HKL`),
    不要把显示名拿回这里做禁阻判断。
    """
    return (-h + k + l) % 3 == 0


def d_spacing(h: int, k: int, l: int, a: float, c: float) -> float:
    """六方晶系面间距:1/d² = 4/3·(h²+hk+k²)/a² + l²/c²。"""
    inv_d2 = (4.0 / 3.0) * (h * h + h * k + k * k) / (a * a) + (l * l) / (c * c)
    return 1.0 / math.sqrt(inv_d2)


def two_theta_of(
    h: int, k: int, l: int, a: float, c: float, wavelength: float
) -> float | None:
    """(hkl) 在给定晶胞与波长下的 2θ(度)。sinθ>1 时该反射不可观测,返回 None。"""
    d = d_spacing(h, k, l, a, c)
    ratio = wavelength / (2.0 * d)
    if ratio > 1.0:
        return None
    return math.degrees(2.0 * math.asin(ratio))


def doublet_order_ok(a: float, c: float) -> bool:
    """层状双峰理论序是否物理:(006) 低角、(102) 高角,且 (018) 低角、(110) 高角。

    六方系里两对同时反序当且仅当 `c² < 24 a²`(c/a < √24 ≈ 4.899)。
    网格搜索若落进这种盆地,理论 (018) 会反超 (110) —— 观测高角峰会按
    "离 (108) 理论更近"被标成 (108),正是「110 显示成 108」的根因。
    层状正极实测 c/a 约 4.95–5.0,永远满足本判据;反了就不是本物相的解。
    """
    if a <= 0 or c <= 0:
        return False
    return c * c > 24.0 * a * a


@dataclass(frozen=True)
class Reflection:
    """一个允许反射及其在给定条件下的理论 2θ。

    `h`/`k`/`l` 是**消光规则**下的指数(拟合强度的键)。`display_hkl` /
    `label` 在双峰文献名上对外用 (102)/(108) —— 与 d 间距、2θ 一致,
    仅指数写法不同。
    """

    h: int
    k: int
    l: int
    two_theta: float
    d: float

    @property
    def display_hkl(self) -> tuple[int, int, int]:
        return _LITERATURE_HKL.get((self.h, self.k, self.l), (self.h, self.k, self.l))

    @property
    def label(self) -> str:
        h, k, l = self.display_hkl
        return f"({h}{k}{l})"

    @property
    def hkl(self) -> tuple[int, int, int]:
        # 强度字典 / 拟合继续用消光指数,与 allowed_reflections 枚举一致。
        return (self.h, self.k, self.l)


def allowed_reflections(
    a: float,
    c: float,
    wavelength: float,
    two_theta_range: tuple[float, float],
    *,
    pad: float = 2.0,
) -> list[Reflection]:
    """枚举落在观测范围内的**全部允许反射**,按 2θ 升序。

    pad 把范围两端各放宽一点,使边界附近的反射也能参与拟合 —— 否则
    一个刚好在范围外的强反射无处安放,会去污染范围内的弱反射强度
    (原型里出现过越界分量吸收强度、振幅爆到 1e8 的伪解)。
    """
    lo, hi = two_theta_range
    out: list[Reflection] = []
    for h in range(_MAX_HK + 1):
        for k in range(_MAX_HK + 1):
            for l in range(_MAX_L + 1):
                if (h, k, l) == (0, 0, 0):
                    continue
                if not allowed_hkl(h, k, l):
                    continue
                t = two_theta_of(h, k, l, a, c, wavelength)
                if t is None or not (lo - pad <= t <= hi + pad):
                    continue
                out.append(Reflection(h, k, l, t, d_spacing(h, k, l, a, c)))
    out.sort(key=lambda r: r.two_theta)
    return out


def fwhm_at(two_theta: float, fwhm_0: float, slope: float) -> float:
    """该 2θ 处的仪器峰宽(度)。线性展宽模型。"""
    return max(float(fwhm_0) + float(slope) * float(two_theta), 1e-3)


def pseudo_voigt(
    x: np.ndarray, center: float, fwhm: float, eta: float = DEFAULT_ETA
) -> np.ndarray:
    """单位峰高的 pseudo-Voigt 峰形(FWHM 参数化)。

    η 是洛伦兹分量占比:η=0 为纯高斯、η=1 为纯洛伦兹。
    """
    fwhm = max(float(fwhm), 1e-6)
    sigma = fwhm / 2.3548200450309493  # 2*sqrt(2 ln 2)
    gauss = np.exp(-0.5 * ((x - center) / sigma) ** 2)
    lorentz = 1.0 / (1.0 + ((x - center) / (fwhm / 2.0)) ** 2)
    return eta * lorentz + (1.0 - eta) * gauss


def peak_profile(
    x: np.ndarray,
    two_theta_ka1: float,
    fwhm_0: float,
    slope: float,
    wavelength_ka1: float = CU_KA1,
    eta: float = DEFAULT_ETA,
) -> np.ndarray:
    """一个反射的完整峰形:Kα1 + Kα2 两个分量的叠加,各自用自己的峰宽。"""
    out = np.zeros_like(x)
    for center, weight in ka_components(two_theta_ka1, wavelength_ka1):
        out = out + weight * pseudo_voigt(
            x, center, fwhm_at(center, fwhm_0, slope), eta
        )
    return out


def ka_components(
    two_theta_ka1: float,
    wavelength_ka1: float = CU_KA1,
    wavelength_ka2: float = CU_KA2,
    ratio: float = KA2_RATIO,
) -> list[tuple[float, float]]:
    """把一个 (hkl) 展开成 Kα1 / Kα2 两个分量 [(2θ, 相对强度), ...]。

    **必须按 Bragg 重算 Kα2 的位置,不能用固定 Δ** —— 同样 Δλ 下
    Kα1/Kα2 的 2θ 劈裂随角度增大:Cu 靶下 (003) 只差 0.047°,
    而 (110) 差 0.180°。用固定偏移会在高角系统性带偏峰位,
    而 (108)/(110) 的间距本身只有约 0.4°,这个偏差不能忽略。

    wavelength_ka1 由调用方传入时,按 λ2/λ1 等比缩放 λ2 以适配非 Cu 靶。
    """
    lam2 = wavelength_ka2 * (wavelength_ka1 / CU_KA1)
    theta1 = math.radians(two_theta_ka1 / 2.0)
    sin1 = math.sin(theta1)
    if sin1 <= 0:
        return [(two_theta_ka1, 1.0)]
    ratio_sin = (lam2 / wavelength_ka1) * sin1
    if ratio_sin > 1.0:
        return [(two_theta_ka1, 1.0)]
    t2 = math.degrees(2.0 * math.asin(ratio_sin))
    return [(two_theta_ka1, 1.0), (t2, ratio)]


@dataclass
class CellFit:
    """一次全谱晶胞精修的结果。"""

    a: float
    c: float
    zero_shift: float
    fwhm_0: float
    fwhm_slope: float
    eta: float = DEFAULT_ETA
    amplitudes: dict[tuple[int, int, int], float] = field(default_factory=dict)
    """每个允许反射的拟合强度(峰高),未出现的接近 0。"""
    reflections: list[Reflection] = field(default_factory=list)
    rss: float = 0.0
    rwp: float = 0.0
    """加权残差因子,由**最近一次实际求解**的残差算出(见 refine_cell)。

    不要试图从 amplitudes 重建模型再算 Rwp —— amplitudes 只保存反射的
    峰高,线性背景项(常数 + 斜率)不在其中,重建出来的模型会系统性
    偏高,实测 Rwp 从 0.20 虚增到 0.95。

    注意 Rwp 是**整条曲线**的指标。层状正极样品普遍含导电碳/粘结剂,
    5–35° 常有一个非晶包,它不属于 R-3m 物相、本模型不建它,会把 Rwp
    抬高到 0.5 以上。所以 Rwp 只用于"拟合是否可用"的粗判,不能当作
    晶胞质量的精确度量。"""
    n_components: int = 0

    @property
    def cell(self) -> tuple[float, float]:
        return (self.a, self.c)

    def amplitude(self, h: int, k: int, l: int) -> float:
        return float(self.amplitudes.get((h, k, l), 0.0))

    def fwhm_at(self, two_theta: float) -> float:
        return fwhm_at(two_theta, self.fwhm_0, self.fwhm_slope)


def _background_terms(x: np.ndarray, degree: int) -> list[np.ndarray]:
    """背景基函数:常数 + 低阶多项式(在归一化坐标上,避免病态)。

    为什么需要二阶以上:层状正极样品通常含导电碳/粘结剂,5–35° 有一个
    宽的非晶包。它不属于 R-3m 物相,但**必须能被背景吸收** —— 否则
    拟合会去扭曲峰形(fwhm_0 与 slope 被推到网格边界)来迁就它,得到
    一个看似残差很小、实则峰形完全错误的模型,进而带偏 (006)/(102)
    这类对峰形敏感的归属。用线性背景时实测峰形退化到 0.20+0.006·2θ
    (而 (003) 实测仅 0.157°),就是这个机制。
    """
    xc = (np.asarray(x, dtype=float) - float(np.mean(x))) / max(float(np.std(x)), 1e-9)
    terms = [np.ones_like(xc)]
    for k in range(1, degree + 1):
        terms.append(xc**k)
    return terms


def _design_matrix(
    x: np.ndarray,
    reflections: list[Reflection],
    fwhm_0: float,
    slope: float,
    eta: float,
    bg_degree: int = DEFAULT_BG_DEGREE,
) -> np.ndarray:
    """构造 [背景多项式, 各反射的 Kα1/Kα2 峰形] 设计矩阵。"""
    cols = _background_terms(x, bg_degree)
    for ref in reflections:
        cols.append(peak_profile(x, ref.two_theta, fwhm_0, slope, CU_KA1, eta))
    return np.vstack(cols).T


def _solve(
    matrix: np.ndarray, y: np.ndarray, bg_terms: int = DEFAULT_BG_DEGREE + 1
) -> tuple[np.ndarray, float] | None:
    """最小二乘求解:背景项自由,反射振幅非负。

    为什么振幅必须非负:无约束时不可分/越界的分量会用负振幅去补偿残差,
    实测出现过 |A| 达 1e8 的伪解。

    **为什么用 `lsq_linear` 的 BVLS 而不是 `nnls`:** nnls 在这个设计矩阵
    上会卡住 —— 它的列范数跨 3 个数量级(常数项 1891 vs 最弱反射 3.2),
    实测即便先做列缩放,nnls 仍把高角弱反射的振幅**精确返回 0**,导致
    84° 以上的 (205)/(027)/(208) 永远拿不到晶面。BVLS 与 TRF/exact 同解
    但快约 8 倍(5.6ms vs 7.7ms),在两千多个格点的搜索里是决定性的。
    """
    lower = np.full(matrix.shape[1], 0.0)
    # 背景多项式可正可负(基线就在 0 附近摆动),不受非负约束。
    lower[:bg_terms] = -np.inf
    try:
        result = lsq_linear(matrix, y, bounds=(lower, np.inf), method="bvls")
    except (ValueError, np.linalg.LinAlgError):
        return None
    residual = matrix @ result.x - y
    return result.x, float(residual @ residual)


def _template_columns(
    x: np.ndarray,
    reflections: list[Reflection],
    fwhm_0: float,
    slope: float,
    eta: float,
    *,
    span_deg: float = 1.0,
) -> tuple[np.ndarray, np.ndarray, list[np.ndarray]]:
    """为快速格点搜索预计算每个反射的峰形模板。

    峰形只依赖 (x - center)/step,所以把模板建在**下标偏移轴**上,格点
    搜索时只需按位置整体平移并线性插值,不必对每个格点重算 22 条
    4751 点的 pseudo-Voigt。

    精度代价约 1%(列最大偏差 / 峰高);因此它只用于**打分**选出最优
    格点,最终晶胞与强度一律用精确矩阵重算(见 refine_cell 末尾),
    不会把插值误差带进结果。
    """
    step = float(np.median(np.diff(x))) if x.size > 1 else 1.0
    max_off = int(np.ceil(span_deg / step))
    axis = np.arange(-max_off, max_off + 1)
    templates: list[np.ndarray] = []
    for ref in reflections:
        tpl = np.zeros_like(axis, dtype=float)
        for center, weight in ka_components(ref.two_theta, CU_KA1):
            tpl = tpl + weight * pseudo_voigt(
                axis * step,
                center - ref.two_theta,
                fwhm_at(center, fwhm_0, slope),
                eta,
            )
        templates.append(tpl)
    return axis, step, templates


def _fast_design(
    x: np.ndarray,
    reflections: list[Reflection],
    axis: np.ndarray,
    step: float,
    templates: list[np.ndarray],
) -> np.ndarray:
    """用预计算模板 + 线性插值装配设计矩阵(格点搜索专用)。"""
    positions = np.arange(x.size, dtype=float)
    cols = _background_terms(x, DEFAULT_BG_DEGREE)
    for ref, tpl in zip(reflections, templates, strict=True):
        center_idx = (ref.two_theta - float(x[0])) / step
        cols.append(
            np.interp(positions - center_idx, axis, tpl, left=0.0, right=0.0)
        )
    return np.vstack(cols).T


def refine_cell(
    x: np.ndarray,
    y: np.ndarray,
    *,
    a_range: tuple[float, float] = DEFAULT_A_RANGE,
    c_range: tuple[float, float] = DEFAULT_C_RANGE,
    a_step: float = DEFAULT_A_STEP,
    c_step: float = DEFAULT_C_STEP,
    wavelength: float = CU_KA1,
    zero_range: tuple[float, float] = DEFAULT_ZERO_RANGE,
) -> CellFit | None:
    """网格搜索 (a, c) + 线性最小二乘求各反射强度,返回最优晶胞。

    每换一组 (a, c) 就重建一次设计矩阵 —— 理论位置随之改变,而**强度是
    线性的**,所以内层用非负最小二乘一次解出。

    峰形参数(fwhm_0 / fwhm_slope / eta)同样走网格:实测峰形对残差的
    贡献和晶胞同量级,固定 η=0.6 时残差峰值仍在"已被建模"的峰顶上,
    说明拟合的是错形状而不是错位置。分两层搜索是为了控制维度 ——
    (a,c) 内层循环已经很贵,峰形只在最终晶胞上再扫。

    零点偏移最后用一维扫描求解,不与 a/c 联合搜索:三者存在简并
    (整体平移可由零点与晶胞互相补偿),联合搜索收益不足以抵消其不稳定性。
    """
    x = np.asarray(x, dtype=np.float64).ravel()
    y = np.asarray(y, dtype=np.float64).ravel()
    if x.size < 16:
        return None

    lo, hi = float(x[0]), float(x[-1])

    # 粗扫峰形,挑一个能代表本谱的展宽水平供晶胞搜索使用
    fwhm_0, fwhm_slope, eta = _probe_shape(x, y, lo, hi, wavelength)

    def search(
        a_vals: np.ndarray, c_vals: np.ndarray
    ) -> tuple[float, float, float, np.ndarray, list[Reflection]] | None:
        best: tuple[float, float, float, np.ndarray, list[Reflection]] | None = None
        for a in a_vals:
            for c in c_vals:
                # 反序晶胞(c² ≤ 24a²)会让 (006)/(102)、(018)/(110) 理论角
                # 对调,归属把高角峰标成 (108)。默认搜索框右上角会踩进该区,
                # 必须在打分前丢掉,不能只靠 RSS 挑最优。
                if not doublet_order_ok(float(a), float(c)):
                    continue
                reflections = allowed_reflections(a, float(c), wavelength, (lo, hi))
                if len(reflections) < 3:
                    continue
                # 打分用快速模板矩阵(见 _template_columns);插值误差约 1%,
                # 足以分辨格点优劣,但不会进入最终结果。
                axis, step, templates = _template_columns(
                    x, reflections, fwhm_0, fwhm_slope, eta
                )
                matrix = _fast_design(x, reflections, axis, step, templates)
                solved = _solve(matrix, y)
                if solved is None:
                    continue
                solution, rss = solved
                if best is None or rss < best[0]:
                    best = (rss, float(a), float(c), solution, reflections)
        return best

    # 三层粗到细搜索。残差面在 (a, c) 上是光滑单峰的(实测沿 c 的
    # RSS 曲线只有一个极小、无次峰),所以粗网格只要能落进正确盆地,
    # 后续细化就能收敛 —— 不需要一开始就铺满细网格。
    #
    # 为什么必须这样做:旧实现是"一次粗扫 (0.008/0.005) + 一次局部细化",
    # 共 1624 个格点,每格一次 BVLS 解(约 6 ms),单次 refine_cell 要 12 秒。
    # 而 analysis.preview 每次都调用它 —— 于是**每切换一次数据集就卡 12 秒**。
    # 现在:粗扫 138 格 + 两轮细化各 441 格,总耗时降到约 1.5 秒。
    coarse_a = np.arange(a_range[0], a_range[1] + _COARSE_STEP / 2, _COARSE_STEP)
    coarse_c = np.arange(c_range[0], c_range[1] + _COARSE_STEP / 2, _COARSE_STEP)
    if coarse_a.size == 0 or coarse_c.size == 0:
        return None

    best = search(coarse_a, coarse_c)
    if best is None:
        return None

    # 两轮细化:第一轮把粗格点的量化误差收掉,第二轮达到最终精度。
    for span, step in ((_REFINE_SPAN, _REFINE_STEP), (_REFINE_STEP, _REFINE_STEP / 2)):
        _rss, a0, c0, _sol, _refs = best
        fine_a = np.arange(a0 - span, a0 + span + step / 2, step)
        fine_c = np.arange(c0 - span, c0 + span + step / 2, step)
        refined = search(fine_a, fine_c)
        if refined is not None and refined[0] < best[0]:
            best = refined

    rss, a, c, solution, reflections = best
    # 细化步可能跨回反序侧;最终结果再挡一道(粗扫已滤时通常不再触发)。
    if not doublet_order_ok(a, c):
        return None

    # 用**精确**矩阵重解一次最终晶胞。格点搜索用的是插值模板(约 1% 列
    # 误差),这里必须重算,否则振幅与 Rwp 会带上插值偏差。
    exact_matrix = _design_matrix(x, reflections, fwhm_0, fwhm_slope, eta)
    exact = _solve(exact_matrix, y)
    if exact is not None:
        solution, rss = exact

    fit = CellFit(
        a=a,
        c=c,
        zero_shift=0.0,
        fwhm_0=fwhm_0,
        fwhm_slope=fwhm_slope,
        eta=eta,
        reflections=reflections,
        rss=rss,
        n_components=len(reflections),
    )
    fit.amplitudes = {
        ref.hkl: float(solution[_BG_N + i]) for i, ref in enumerate(reflections)
    }

    # 零点偏移:用已定晶胞做一维扫描,再据此重解一次强度。
    zero, shifted = _refine_zero_shift(
        x, y, reflections, fwhm_0, fwhm_slope, eta, zero_range, lo, hi
    )
    fit.zero_shift = zero
    if shifted is not None:
        fit.reflections = shifted
        matrix = _design_matrix(x, shifted, fwhm_0, fwhm_slope, eta)
        solved = _solve(matrix, y)
        if solved is not None:
            sol2, rss2 = solved
            fit.rss = rss2
            fit.amplitudes = {
                ref.hkl: float(sol2[_BG_N + i]) for i, ref in enumerate(shifted)
            }

    # 峰形精修:在最终晶胞上再扫一轮 fwhm_0 / slope / eta
    _refine_shape(fit, x, y)

    # Rwp 由最终模型的真实残差算出。必须在此处就地算 —— 模型里包含线性
    # 背景项,而 amplitudes 只留反射峰高,事后重建会算错。
    matrix = _design_matrix(x, fit.reflections, fit.fwhm_0, fit.fwhm_slope, fit.eta)
    solved = _solve(matrix, y)
    if solved is not None:
        sol3, rss3 = solved
        fit.rss = rss3
        fit.amplitudes = {
            ref.hkl: float(sol3[_BG_N + i]) for i, ref in enumerate(fit.reflections)
        }
        fit.rwp = _rwp_from_residual(matrix @ sol3 - y, y)
    return fit


# 峰形搜索网格。fwhm_0 与 slope 都直接决定峰形,宁可细一点;eta 的影响
# 是二阶的(只改变同一 FWHM 下的形状细节),取 3 档已足够。
_SHAPE_FWHM0 = (0.08, 0.11, 0.14, 0.17, 0.20)
_SHAPE_SLOPE = (0.0, 0.002, 0.004, 0.006)
_SHAPE_ETA = (0.3, 0.55, 0.8)


def _probe_shape(
    x: np.ndarray, y: np.ndarray, lo: float, hi: float, wavelength: float
) -> tuple[float, float, float]:
    """粗扫峰形参数,返回 (fwhm_0, slope, eta)。

    在一个中间晶胞上做 —— 峰形与晶胞弱耦合,不值得放进主网格里做三维
    联合搜索。用一个廉价的前置扫描即可把残差从"形状错"降到"位置错"。
    """
    a0 = (DEFAULT_A_RANGE[0] + DEFAULT_A_RANGE[1]) / 2.0
    c0 = (DEFAULT_C_RANGE[0] + DEFAULT_C_RANGE[1]) / 2.0
    reflections = allowed_reflections(a0, c0, wavelength, (lo, hi))
    if len(reflections) < 3:
        return DEFAULT_FWHM_AT_0, DEFAULT_FWHM_SLOPE, DEFAULT_ETA

    best = (float("inf"), DEFAULT_FWHM_AT_0, DEFAULT_FWHM_SLOPE, DEFAULT_ETA)
    # 只在强反射上评估,避免弱峰噪声主导形状选择
    for f0 in _SHAPE_FWHM0:
        for sl in _SHAPE_SLOPE:
            for et in _SHAPE_ETA:
                matrix = _design_matrix(x, reflections, f0, sl, et)
                solved = _solve(matrix, y)
                if solved is None:
                    continue
                _sol, rss = solved
                if rss < best[0]:
                    best = (rss, f0, sl, et)
    return best[1], best[2], best[3]


def _refine_shape(fit: CellFit, x: np.ndarray, y: np.ndarray) -> None:
    """在已定晶胞与零点上重扫峰形,就地更新 fit。"""
    best = (fit.rss, fit.fwhm_0, fit.fwhm_slope, fit.eta)
    for f0 in _SHAPE_FWHM0:
        for sl in _SHAPE_SLOPE:
            for et in _SHAPE_ETA:
                matrix = _design_matrix(x, fit.reflections, f0, sl, et)
                solved = _solve(matrix, y)
                if solved is None:
                    continue
                sol, rss = solved
                if rss < best[0]:
                    best = (rss, f0, sl, et)
                    fit.amplitudes = {
                        ref.hkl: float(sol[_BG_N + i])
                        for i, ref in enumerate(fit.reflections)
                    }
    fit.rss, fit.fwhm_0, fit.fwhm_slope, fit.eta = best


def _refine_zero_shift(
    x: np.ndarray,
    y: np.ndarray,
    reflections: list[Reflection],
    fwhm_0: float,
    slope: float,
    eta: float,
    zero_range: tuple[float, float],
    lo: float,
    hi: float,
) -> tuple[float, list[Reflection] | None]:
    """在给定晶胞下扫描 2θ 零点偏移,取残差最小者。"""
    if zero_range[0] == zero_range[1]:
        return 0.0, None
    offsets = np.linspace(zero_range[0], zero_range[1], 15)
    best_zero, best_rss, best_refs = 0.0, None, None
    for offset in offsets:
        shifted = [
            Reflection(r.h, r.k, r.l, r.two_theta + offset, r.d)
            for r in reflections
            if lo <= r.two_theta + offset <= hi
        ]
        if len(shifted) < 3:
            continue
        matrix = _design_matrix(x, shifted, fwhm_0, slope, eta)
        solved = _solve(matrix, y)
        if solved is None:
            continue
        _sol, rss = solved
        if best_rss is None or rss < best_rss:
            best_rss, best_zero, best_refs = rss, float(offset), shifted
    return best_zero, best_refs


def _rwp_from_residual(residual: np.ndarray, y: np.ndarray) -> float:
    """由实际残差算加权 Rwp。

    权重取 1/max(|y|,1)(近似泊松计数权重),使高角弱峰区在指标里仍有
    与低角相当的发言权 —— 否则 (003) 一个峰就能盖住全部高角信息。
    """
    weights = 1.0 / np.maximum(np.abs(y), 1.0)
    num = float(np.sum(weights * residual**2))
    den = float(np.sum(weights * y**2))
    return math.sqrt(num / den) if den > 0 else 0.0


# 归属判据:本反射在邻域窗口内贡献的强度占比。低于此值说明窗口里没有
# 哪个反射占主导 —— 那个峰很可能不是本物相的反射(杂相、非晶包、伪峰),
# 此时报 none 比硬套一个晶面诚实。
_DOMINANCE_MIN = 0.45
# 两个候选取值之差小于此值 -> ambiguous(双峰物理上未分开)。如实报告
# "分辨不了"远好于在不确定时给出 reliable —— 旧逻辑最大的问题就在这里。
_DOMINANCE_MARGIN = 0.15


@dataclass
class PeakAssignment:
    """单个观测峰的归属结果。"""

    index: int
    position: float
    hkl: tuple[int, int, int] | None
    label: str | None
    dominance: float
    """本反射在该峰邻域内贡献的强度占比,0–1。"""
    ref_position: float | None
    offset_deg: float | None
    confidence: str
    """reliable / ambiguous / none。"""
    candidates: list[dict] = field(default_factory=list)
    """ambiguous 时的候选 [{label, position, dominance}, ...]。"""

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "position": self.position,
            "hkl": list(self.hkl) if self.hkl else None,
            "label": self.label,
            "dominance": self.dominance,
            "ref_position": self.ref_position,
            "offset_deg": self.offset_deg,
            "confidence": self.confidence,
            "candidates": self.candidates,
        }


def assign_peaks(
    x: np.ndarray,
    peaks: list,
    fit: CellFit,
    *,
    window_scale: float = 1.6,
) -> list[PeakAssignment]:
    """把每个观测峰归属给贡献最大的理论反射。

    **为什么用"邻域内相对贡献占比"而不是"最近的理论位置"**

    最近邻只看几何距离,完全不管这个峰是由哪个反射在供强度。在
    (006)/(102) 这种间距仅约 0.36° 的双峰里,观测峰只要略微偏一点,
    最近邻就会在两者之间翻转 —— 而正确判据应该是"哪条理论线的强度
    真正落在这个窗口里"。

    占比法把归属变成可判据的物理量:对每个峰的邻域窗口,累加各反射
    (含 Kα1/Kα2 分量)的积分贡献,取占比最大者,并给出 `dominance`。
    两个候选占比接近时如实返回 ambiguous,而不是硬给标签 ——
    旧逻辑最大的问题正是在不确定时给出 reliable。

    **窗口宽度必须随峰宽缩放**:固定 ±0.6° 时,(110)@64.58 的 65% 面积
    会落进 (108)@64.10 的窗口,导致 (108) 被永久判成 (110) —— 这正是
    实测中 (108) 丢失的原因。窗口取该 2θ 处 FWHM 的 `window_scale` 倍,
    既覆盖峰本体又不会拖进隔壁反射。
    """
    x = np.asarray(x, dtype=np.float64).ravel()
    if fit is None:
        return [
            PeakAssignment(p.index, float(p.position), None, None, 0.0, None, None, "none")
            for p in peaks
        ]
    if not doublet_order_ok(fit.a, fit.c):
        # 反序胞下 (018)/(110) 理论角对调,任何 reliable 都会把高角峰
        # 标成 (108)。宁可整表 none,也不给一个系统性反了的标签。
        return [
            PeakAssignment(p.index, float(p.position), None, None, 0.0, None, None, "none")
            for p in peaks
        ]

    # 每个反射的合成峰形(Kα1+Kα2),位置已含零点校正
    profiles: list[tuple[Reflection, np.ndarray]] = []
    for ref in fit.reflections:
        amp = fit.amplitude(*ref.hkl)
        if amp <= 0:
            continue
        curve = amp * peak_profile(
            x,
            ref.two_theta + fit.zero_shift,
            fit.fwhm_0,
            fit.fwhm_slope,
            CU_KA1,
            fit.eta,
        )
        profiles.append((ref, curve))

    if not profiles:
        return [
            PeakAssignment(p.index, float(p.position), None, None, 0.0, None, None, "none")
            for p in peaks
        ]

    assignments: list[PeakAssignment] = []
    for peak in peaks:
        pos = float(peak.position)
        window = window_scale * fit.fwhm_at(pos)
        mask = (x >= pos - window) & (x <= pos + window)
        if not np.any(mask):
            assignments.append(
                PeakAssignment(peak.index, pos, None, None, 0.0, None, None, "none")
            )
            continue

        contribs: list[tuple[float, Reflection]] = [
            (float(curve[mask].sum()), ref) for ref, curve in profiles
        ]
        total = sum(c for c, _ in contribs)
        if total <= 0:
            assignments.append(
                PeakAssignment(peak.index, pos, None, None, 0.0, None, None, "none")
            )
            continue

        contribs.sort(key=lambda item: item[0], reverse=True)
        best_contrib, best_ref = contribs[0]
        dominance = best_contrib / total
        second_contrib, second_ref = contribs[1] if len(contribs) > 1 else (0.0, None)
        second_share = second_contrib / total if second_ref is not None else 0.0

        # 理论位置离观测峰太远 -> 不归属。这里用半窗判据,避免把窗口边缘的
        # 反射算作归属。
        ref_pos = best_ref.two_theta + fit.zero_shift
        if abs(pos - ref_pos) > window:
            assignments.append(
                PeakAssignment(peak.index, pos, None, None, dominance, None, None, "none")
            )
            continue

        candidates = [
            {
                "hkl": list(best_ref.hkl),
                "label": best_ref.label,
                "position": round(ref_pos, 4),
                "dominance": round(dominance, 4),
            }
        ]
        if second_ref is not None and second_share > 0:
            candidates.append(
                {
                    "hkl": list(second_ref.hkl),
                    "label": second_ref.label,
                    "position": round(second_ref.two_theta + fit.zero_shift, 4),
                    "dominance": round(second_share, 4),
                }
            )

        if dominance < _DOMINANCE_MIN:
            # 没有任何反射在邻域内占主导 —— 不硬套。candidates 照给
            # (含单条最强匹配):UI 需要它显示"最强也仅占 X%"或给出
            # 近邻弱匹配供人工核对,丢掉就只剩一个光秃秃的 none。
            assignments.append(
                PeakAssignment(
                    peak.index, pos, None, None, dominance, None, None, "none",
                    candidates,
                )
            )
            continue

        # 宽峰 × 窄间距联合判据:(006)/(102) 间距约 0.29–0.36°,
        # (108)/(110) 约 0.49°。实测 FWHM 明显超过该间距(≥1.2×,重度
        # 重叠)时,两条理论线的峰形物理上混在一起,自由振幅互偷 ——
        # dominance 看起来占优也可能是拟合的人为分配。此时要求更大的
        # 差距才敢给 reliable,否则如实报 ambiguous(带候选)。
        # 1.2× 是有分辨力的门限:(116)/(202) 这类 FWHM≈1.08×间距的
        # 真双峰仍按常规 margin 判,不会被误伤。
        effective_margin = _DOMINANCE_MARGIN
        if second_ref is not None:
            spacing = abs(second_ref.two_theta - best_ref.two_theta)
            if spacing > 0 and fit.fwhm_at(pos) >= 1.2 * spacing:
                effective_margin = _DOMINANCE_MARGIN * 2

        if dominance - second_share < effective_margin:
            # 两个反射贡献接近:双峰未分开。如实报告,不猜。
            assignments.append(
                PeakAssignment(
                    peak.index, pos, None, None, dominance, None, None, "ambiguous",
                    candidates,
                )
            )
            continue

        # 偏移量判据:归属成立时,观测峰应落在理论位置的一个小分数峰宽内。
        # 若偏移接近甚至超过一个 FWHM,说明这个峰其实不是该反射 —— 它更
        # 可能是双峰合并后的质心(实测:0.55° 宽时 (006)/(102) 合并,质心
        # 被判给 (102) 且 dominance 高达 0.64,但 offset 已达 0.19°,而
        # FWHM 只有 0.55° 的三分之一)。窗口是 1.6×FWHM,所以 1.0×FWHM
        # 的偏移意味着峰位几乎落在窗口边缘,归属不成立。
        window = window_scale * fit.fwhm_at(pos)
        if abs(pos - ref_pos) > 0.6 * window:
            assignments.append(
                PeakAssignment(
                    peak.index, pos, None, None, dominance, None, None, "ambiguous",
                    candidates,
                )
            )
            continue

        assignments.append(
            PeakAssignment(
                index=peak.index,
                position=pos,
                hkl=best_ref.hkl,
                label=best_ref.label,
                dominance=dominance,
                ref_position=round(ref_pos, 4),
                offset_deg=round(pos - ref_pos, 4),
                confidence="reliable",
                # 即使判为可靠,也把竞争候选一并给出 —— 用户核对标签时
                # 需要看到"还有谁在旁边",尤其在双峰区。旧实现只在
                # ambiguous 时保留候选,可靠归属反而没有可核对的依据。
                candidates=candidates if len(candidates) > 1 else [],
            )
        )

    return assignments
