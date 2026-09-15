"""导入校验层(§7.2)。

引擎的第一道关。职责划分:

  - errors  → 拒绝导入,UI 显示人话原因
  - warnings → 允许导入,UI 以 toast 或 Inspector 提示告知用户

§7.2 规定的检查项在这里逐条落实。特别注意「不报错但也不该静默」的
几类情况:全平线、常数背景、NaN 剔除、非均匀网格重采样。
"""

from __future__ import annotations

import numpy as np

from ..models.types import Curve, LoadedDataset, ValidationReport
from ..processing.grid import detect_grid, ensure_ascending, resample_to_uniform

# §7.2 规定的最少有效点数
MIN_POINT_COUNT = 4

# 2θ 轴合理性区间。全部落在此区间外时提示用户可能选错了列。
TWO_THETA_VALID_RANGE = (-5.0, 180.0)

# 零方差判据:标准差低于此值视为全平线
FLAT_STD_THRESHOLD = 1e-12


def validate_and_build(
    data: np.ndarray,
    x_column: int,
    y_column: int,
    x_axis_type: str = "two_theta",
    wavelength: float = 1.5418,
    allow_resample: bool = True,
) -> LoadedDataset | ValidationReport:
    """从数值表构建曲线并做全部校验。

    成功返回 LoadedDataset(其中 report.errors 必为空),
    致命错误返回 ValidationReport(带 errors)。
    """
    report = ValidationReport()
    report.original_point_count = int(data.shape[0])

    if data.ndim != 2 or data.shape[1] < 2:
        report.add_error("数据至少需要两列(X 与 Y)")
        return report

    max_col = data.shape[1] - 1
    if not (0 <= x_column <= max_col) or not (0 <= y_column <= max_col):
        report.add_error(
            f"列下标超出范围:选择了 X=第 {x_column + 1} 列、Y=第 {y_column + 1} 列,"
            f"但文件只有 {data.shape[1]} 列"
        )
        return report

    if x_column == y_column:
        report.add_error("X 列与 Y 列不能是同一列")
        return report

    x_raw = data[:, x_column]
    y_raw = data[:, y_column]

    # --- 剔除 NaN / Inf ---
    # 只剔除 y 为 NaN 的行:x 为 NaN 时无法定位,同样剔除
    valid_mask = np.isfinite(x_raw) & np.isfinite(y_raw)
    dropped = int((~valid_mask).sum())
    if dropped:
        report.dropped_rows += dropped
        report.add_warning(f"跳过 {dropped} 个无效行(含空值或非数值)")

    x = x_raw[valid_mask]
    y = y_raw[valid_mask]

    if x.size < MIN_POINT_COUNT:
        report.add_error(
            f"有效数据点太少(只有 {x.size} 个,至少需要 {MIN_POINT_COUNT} 个)"
        )
        return report

    # --- 排序:降序输入翻转为升序 ---
    info_before = detect_grid(x)
    was_descending = info_before.is_descending
    x, y = ensure_ascending(x, y)
    if was_descending:
        report.add_warning("原始数据按降序排列,已翻转为升序显示")

    # --- 重复 x 检测 ---
    # 重复的 x 会让 find_peaks 与 FWHM 插值产生歧义,必须处理
    dup_mask = np.concatenate(([False], np.diff(x) == 0))
    dup_count = int(dup_mask.sum())
    if dup_count:
        # 保留每组重复中的第一个
        x = x[~dup_mask]
        y = y[~dup_mask]
        report.add_warning(f"发现 {dup_count} 个重复的角度值,已保留每组的首个")

    if x.size < MIN_POINT_COUNT:
        report.add_error(
            f"去除重复点后数据不足(剩 {x.size} 个,至少需要 {MIN_POINT_COUNT} 个)"
        )
        return report

    # --- 网格检测与重采样(§4.2) ---
    grid_info = detect_grid(x)
    original_grid = "uniform"

    if not grid_info.is_uniform:
        original_grid = "non_uniform"
        if allow_resample:
            # resample_to_uniform 内部已做线性插值;
            # 重采样后峰位需要能映射回原始坐标(见 grid.find_nearest_indices)
            x, y = resample_to_uniform(x, y)
            report.resampled = True
            report.add_warning(
                f"数据网格不均匀(步长最大/最小比为 {grid_info.step_ratio:.2f}),"
                f"已重采样到 {x.size} 个等间距点以便处理;"
                f"峰位显示仍使用原始角度值"
            )
        else:
            report.add_warning(
                f"数据网格不均匀(步长最大/最小比为 {grid_info.step_ratio:.2f}),"
                f"平滑与寻峰功能将不可用"
            )

    # --- 全平线检测(§7.2:允许查看,但提示无可检测峰) ---
    y_std = float(np.std(y))
    is_flat = y_std <= FLAT_STD_THRESHOLD
    if is_flat:
        report.add_warning("这条谱的强度是常数,没有可检测的峰")

    # --- 2θ 轴合理性(§7.2) ---
    if x_axis_type == "two_theta" and not is_flat:
        lo, hi = TWO_THETA_VALID_RANGE
        if x.min() > hi or x.max() < lo:
            report.add_warning(
                f"角度范围 {x.min():.3g}° – {x.max():.3g}° 超出了常规 2θ 区间,"
                f"请确认 X 列没有选错"
            )

    curve = Curve(x=x, y=y, x_axis_type=x_axis_type, wavelength=wavelength)  # type: ignore[arg-type]

    return LoadedDataset(
        curve=curve,
        report=report,
        original_grid=original_grid,
        source_columns=(x_column, y_column),
    )


def validate_loaded(dataset: LoadedDataset) -> ValidationReport:
    """对已构建的 LoadedDataset 做一次复核,供 API 层在返回前断言。"""
    report = dataset.report
    if dataset.curve.point_count < MIN_POINT_COUNT:
        report.add_error(f"有效数据点太少({dataset.curve.point_count} 个)")
    return report
