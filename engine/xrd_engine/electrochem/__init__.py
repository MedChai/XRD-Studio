"""电化学数据分析(LAND 平台)。

    land_parser  LAND 导出文件解析(xlsx)→ 记录数组 + 圈统计(M0)
    dqdv         恒流段 dQ/dV 计算(M1)
    peaks        相变窗峰检测 + 跨圈轨迹(M2)
    attribution  容量衰减三分量归因(M3)

设计约定(与包其余部分一致):仅依赖 numpy(+scipy.signal,缺失降级);
中文错误信息由各自模块抛 ValueError,api 层翻译成 EngineError。
"""

from .attribution import (
    AttributionCycle,
    AttributionParams,
    AttributionResult,
    run_attribution,
)
from .dqdv import DQDVParams, DQDVResult, compute_dqdv
from .land_parser import (
    LandCycle,
    LandFile,
    LandParseError,
    SessionInfo,
    cycle_dicts,
    parse_land_path,
)
from .peaks import (
    CyclePeak,
    PeakParams,
    PeakWindow,
    Track,
    build_tracks,
    default_windows,
    detect_window_peaks,
)

__all__ = [
    "AttributionCycle",
    "AttributionParams",
    "AttributionResult",
    "CyclePeak",
    "DQDVParams",
    "DQDVResult",
    "LandCycle",
    "LandFile",
    "LandParseError",
    "PeakParams",
    "PeakWindow",
    "SessionInfo",
    "Track",
    "build_tracks",
    "compute_dqdv",
    "cycle_dicts",
    "default_windows",
    "detect_window_peaks",
    "parse_land_path",
    "run_attribution",
]
