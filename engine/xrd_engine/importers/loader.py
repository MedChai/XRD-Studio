"""数据集加载入口。

把「读文件 → 解析 → 列选择 → 校验 → 构建 Curve」串成一条链。
这是 API 层 datasets.import 直接调用的函数,也是测试的入口。

列选择策略(§7.3):
  1. 两列文件 → 直接用第 1、2 列
  2. 多列文件 → 用启发式猜测 X/Y 列,失败则要求调用方显式指定
  3. 单列或无法解析 → 返回 ColumnMappingRequired,交 UI 弹列映射对话框
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..models.types import LoadedDataset, ValidationReport
from .text_parser import ParsedTable, ParseFailure, read_table
from .validate import validate_and_build

# V0.3 支持的文件扩展名(§41)
SUPPORTED_EXTENSIONS = {".csv", ".txt", ".xy", ".dat"}

# 列数超过这个值就认为不是简单的两列谱,需要用户确认
MAX_AUTO_COLUMNS = 8


@dataclass
class ColumnMappingRequired:
    """需要用户介入选择列时返回此结果。"""

    path: str
    reason: str
    preview_rows: list[list[str]]
    column_count: int
    suggested_x: int = 0
    suggested_y: int = 1


@dataclass
class LoadFailure:
    """无法加载且用户也无从选择的失败。"""

    path: str
    reason: str
    detail: str = ""


def guess_columns(table: ParsedTable) -> tuple[int, int] | None:
    """启发式猜测 X / Y 列。

    判据:
      - X 列应当是单调的(升序或降序),且数值跨度合理
      - Y 列应当是变化的那一列(方差不为零)
      - 两列都必须是数值

    猜不准则返回 None,由 UI 让用户选 —— 猜错比不猜更糟,
    因为用户会以为软件读懂了他的文件。
    """
    data = table.data
    n_cols = data.shape[1]
    if n_cols < 2:
        return None

    candidate_x: list[tuple[int, float]] = []
    for col in range(n_cols):
        values = data[:, col]
        finite = values[np.isfinite(values)]
        if finite.size < 4:
            continue
        # 单调性:允许轻微非单调(噪声),但整体趋势必须向上或向下
        diffs = np.diff(finite)
        if diffs.size == 0:
            continue
        pos_ratio = float((diffs > 0).sum()) / diffs.size
        neg_ratio = float((diffs < 0).sum()) / diffs.size
        monotonic_score = max(pos_ratio, neg_ratio)
        if monotonic_score < 0.95:
            continue
        # 完全常数不能做 X
        if finite.max() == finite.min():
            continue
        candidate_x.append((col, monotonic_score))

    if not candidate_x:
        return None

    # X 取第一个满足单调性的列(通常是第 1 列)
    x_col = candidate_x[0][0]

    # Y 取方差最大的非 X 列
    best_y = -1
    best_var = 0.0
    for col in range(n_cols):
        if col == x_col:
            continue
        values = data[:, col]
        finite = values[np.isfinite(values)]
        if finite.size < 4:
            continue
        var = float(np.var(finite))
        if var > best_var:
            best_var = var
            best_y = col

    if best_y < 0 or best_var == 0.0:
        return None

    return x_col, best_y


def load_file(
    path: str | Path,
    x_axis_type: str = "two_theta",
    wavelength: float = 1.5418,
    x_column: int | None = None,
    y_column: int | None = None,
    allow_resample: bool = True,
) -> LoadedDataset | ColumnMappingRequired | LoadFailure:
    """加载一个 XRD 文件。

    返回值三选一:
      - LoadedDataset        成功
      - ColumnMappingRequired 需要用户选列
      - LoadFailure          失败(文件损坏、格式完全不认识等)
    """
    p = Path(path)

    if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return LoadFailure(
            path=str(p),
            reason=f"不支持的文件类型:{p.suffix}",
            detail=f"V0.3 支持 {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )

    result = read_table(p)
    if isinstance(result, ParseFailure):
        # 解析失败不代表要走列映射 —— 完全没有数字的文件是硬失败。
        # 但如果文件里有可展示的行,交给用户选列更友好。
        if result.preview_rows and result.detected_columns >= 2:
            from .text_parser import preview_columns

            return ColumnMappingRequired(
                path=str(p),
                reason=result.reason,
                preview_rows=preview_columns(p),
                column_count=result.detected_columns,
            )
        return LoadFailure(path=str(p), reason=result.reason, detail=result.detail)

    table, _encoding = result

    # 列数过多时让用户确认,不猜。
    # **但调用方已经显式给了列号时就不该再拦** —— 否则宽表(实验室导出
    # 十几列很常见)永远导不进来:用户选完列,第二次调用仍撞在这个分支上,
    # 表现为「选完列还是失败」。
    if table.column_count > MAX_AUTO_COLUMNS and (
        x_column is None or y_column is None
    ):
        from .text_parser import preview_columns

        return ColumnMappingRequired(
            path=str(p),
            reason=f"文件有 {table.column_count} 列,请指定哪两列是角度与强度",
            preview_rows=preview_columns(p),
            column_count=table.column_count,
        )

    if x_column is None or y_column is None:
        if table.column_count < 2:
            from .text_parser import preview_columns

            return ColumnMappingRequired(
                path=str(p),
                reason="文件只有一列,无法确定 X 与 Y",
                preview_rows=preview_columns(p),
                column_count=table.column_count,
            )
        if table.column_count == 2:
            x_column, y_column = 0, 1
        else:
            guessed = guess_columns(table)
            if guessed is None:
                from .text_parser import preview_columns

                return ColumnMappingRequired(
                    path=str(p),
                    reason="无法自动判断哪两列是角度与强度,请手动指定",
                    preview_rows=preview_columns(p),
                    column_count=table.column_count,
                )
            x_column, y_column = guessed

    built = validate_and_build(
        data=table.data,
        x_column=x_column,
        y_column=y_column,
        x_axis_type=x_axis_type,
        wavelength=wavelength,
        allow_resample=allow_resample,
    )

    if isinstance(built, ValidationReport):
        # 校验失败:如果是因为列选错(比如单列、全非数值),给列映射机会
        return LoadFailure(
            path=str(p),
            reason=";".join(built.errors),
            detail=f"使用了 X=第 {x_column + 1} 列、Y=第 {y_column + 1} 列",
        )

    return built


def load_curve(
    path: str | Path,
    x_axis_type: str = "two_theta",
    wavelength: float = 1.5418,
) -> Curve:  # noqa: F821
    """便捷函数:加载并直接返回 Curve,用于测试与内部调用。

    不适合 API 层使用 —— 它把「需要用户选列」的情况也当成异常抛出,
    而 API 层必须能把这个情况回传给 UI。
    """

    result = load_file(path, x_axis_type=x_axis_type, wavelength=wavelength)
    if isinstance(result, LoadedDataset):
        return result.curve
    if isinstance(result, ColumnMappingRequired):
        raise ValueError(f"需要手动指定列:{result.reason}")
    raise ValueError(f"无法加载 {path}:{result.reason}")
