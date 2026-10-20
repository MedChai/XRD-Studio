"""数据模型定义。

所有跨模块传递的数据结构集中在 models 包,避免各层各定义一套。
使用 dataclass 而非 pydantic:引擎内部传递用 dataclass 更轻,
只有跨 IPC 边界的 schema 才需要 pydantic 做校验与 TS 类型生成(见 api/)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

AxisType = Literal["two_theta", "d", "q", "generic"]
GridKind = Literal["uniform", "non_uniform"]
PeakSensitivity = Literal["low", "medium", "high"]


@dataclass
class Curve:
    """一条已加载的 XRD 曲线。

    x / y 始终是等长的 Float64 数组,x 保证升序。原始采集顺序若为降序,
    在加载阶段就已翻转,下游不需要再关心。
    """

    x: np.ndarray
    y: np.ndarray
    x_axis_type: AxisType = "two_theta"
    wavelength: float = 1.5418

    def __post_init__(self) -> None:
        self.x = np.asarray(self.x, dtype=np.float64)
        self.y = np.asarray(self.y, dtype=np.float64)
        if self.x.shape != self.y.shape:
            raise ValueError(
                f"x 与 y 长度不一致:x={self.x.shape}, y={self.y.shape}"
            )

    @property
    def point_count(self) -> int:
        return int(self.x.size)

    def copy(self) -> Curve:
        return Curve(
            x=self.x.copy(),
            y=self.y.copy(),
            x_axis_type=self.x_axis_type,
            wavelength=self.wavelength,
        )


@dataclass
class ValidationReport:
    """导入校验结果。warnings 是用户可见的提示,errors 非空则拒绝导入。"""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dropped_rows: int = 0
    """因 NaN/Inf 或列数不符而丢弃的行数。"""
    resampled: bool = False
    """是否因非均匀网格做了重采样。"""
    original_point_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def add_error(self, message: str) -> None:
        self.errors.append(message)

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)


@dataclass
class LoadedDataset:
    """解析器 + 校验层的输出,可被引擎直接处理。"""

    curve: Curve
    report: ValidationReport
    original_grid: str = "uniform"
    """重采样前原始数据的网格性质,非均匀时为 non_uniform。"""
    source_columns: tuple[int, int] = (0, 1)
    """实际使用的 X / Y 列下标,用于列映射的往返与审计。"""


@dataclass
class SmoothingParams:
    """§7.3 Savitzky–Golay 参数。"""

    enabled: bool = False
    method: Literal["savgol"] = "savgol"
    window_length: int = 7
    polyorder: int = 2


@dataclass
class BackgroundParams:
    """§7.3 背景估计参数。AsLS 与多项式二选一。"""

    enabled: bool = False
    method: Literal["asls", "polynomial"] = "asls"
    # AsLS:λ 用 log 刻度暴露,这里存线性值
    smoothness: float = 1e5
    asymmetry: float = 0.01
    iterations: int = 10
    # Polynomial
    order: int = 3


@dataclass
class CropParams:
    """裁剪区间。None 表示该侧不裁剪。"""

    enabled: bool = False
    x_min: float | None = None
    x_max: float | None = None


@dataclass
class NormalizationParams:
    """§10.2 归一化只在前端做,这里仅记录用户选择以便持久化。"""

    mode: Literal["raw", "max1", "max100"] = "raw"


@dataclass
class PeakDetectionParams:
    """§7.3 寻峰参数。前三个是暴露给用户的语义参数。"""

    sensitivity: PeakSensitivity = "medium"
    min_height_pct: float = 5.0
    min_distance_deg: float = 0.10
    # Advanced 折叠区的直通参数
    prominence: float | None = None
    width: float | None = None
    rel_height: float = 0.5


@dataclass
class PipelineParams:
    """完整处理链参数,即 §7.4 的 pipeline_snapshot。"""

    crop: CropParams = field(default_factory=CropParams)
    background: BackgroundParams = field(default_factory=BackgroundParams)
    smoothing: SmoothingParams = field(default_factory=SmoothingParams)
    normalization: NormalizationParams = field(default_factory=NormalizationParams)
    peaks: PeakDetectionParams = field(default_factory=PeakDetectionParams)

    def to_snapshot(self) -> dict:
        """转为可写入 analyses.pipeline_json 的字典。"""
        from dataclasses import asdict

        return asdict(self)


@dataclass
class Peak:
    """单条峰分析结果。"""

    index: int
    position: float
    intensity: float
    fwhm: float | None = None
    area: float | None = None
    left_bound: float | None = None
    right_bound: float | None = None
    manual_override: bool = False
    # 置信度相关的中间量,导出时可选带出
    prominence: float | None = None
    # 晶面归属(由 peaks.annotate 填充;未标注时为 None)
    hkl: str | None = None
    hkl_confidence: str | None = None
    # 归属依据。dominance 是本反射在该峰邻域内贡献的强度占比(0-1),
    # candidates 是竞争候选 —— 前端据此把"为什么是这个晶面"摊开给用户
    # 核对。**必须一路带到 UI**:CPU 里算完却在写回时丢掉,用户看到的
    # 就是"归属依据:该反射贡献 0%",比不显示更糟。
    dominance: float | None = None
    hkl_candidates: list[dict] | None = None
    row_id: int | None = None
    """写入 peaks 表后的主键。仅由 peaks.detect 设置,供后续按主键更新 ——
    **不要用 position 做相等匹配**:数据库存的是未舍入的双精度值,而
    调用方往往拿 round(pos, 6) 去查,两者不等时 UPDATE 静默匹配 0 行。"""

    def to_dict(self) -> dict:
        return {
            # row_id:peaks 表主键。detect/annotate 落库后才有值;preview
            # 的临时峰为 None。前端编辑 bounds 靠它调 peaks.update_bounds。
            "id": self.row_id,
            "index": self.index,
            "position": self.position,
            "intensity": self.intensity,
            "fwhm": self.fwhm,
            "area": self.area,
            "left_bound": self.left_bound,
            "right_bound": self.right_bound,
            "manual_override": self.manual_override,
            "prominence": self.prominence,
            "hkl": self.hkl,
            "hkl_confidence": self.hkl_confidence,
            "dominance": self.dominance,
            "hkl_candidates": self.hkl_candidates,
        }


@dataclass
class ProcessingResult:
    """一次处理链求值的完整输出。

    同时保留中间量:UI 要画背景曲线,导出要写处理后数据,
    峰度量要知道自己算在哪个状态上(见 §7.1 的偏差提示)。
    """

    curve: Curve
    """当前启用的处理链输出,即用户在图上看的那条线。"""
    background: np.ndarray | None = None
    """背景估计曲线,仅在 background.enabled 时非空。"""
    metric_curve_y: np.ndarray | None = None
    """峰度量所依据的 y 值。Smooth 关闭时等于 curve.y;
    Smooth 开启时是平滑后的曲线 —— 与 curve.y 相同,单独列出是为了
    让 UI 能明确知道是否存在平滑导致的 FWHM 偏差。"""
    peaks: list[Peak] = field(default_factory=list)
    metrics_affected_by_smoothing: bool = False
    """为 True 时 Inspector 要显示 §7.1 规定的提示。"""
    warnings: list[str] = field(default_factory=list)
