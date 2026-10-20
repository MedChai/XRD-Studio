"""结构趋势(§11.4 / docs/DEVELOPMENT_HIGH_NI.md 阶段 H)。

把一个 Project 下各数据集的**结构指标**汇成一条序列,让用户看趋势:
煅烧温度 → a / c / c/a / I(003)/I(104) / FWHM。

## 指标口径必须统一

晶胞参数在本仓库有**两条计算路径**,数值不可混画:

1. `peaks/annotate.py` → `high_ni.indexing.refine_cell` —— 全谱精修,
   **带零点校正**,并对所有允许反射联合拟合。
2. `high_ni/analysis.py` 的 `_lattice_result` —— 只取 3–7 个峰做线性
   最小二乘,**零点恒为 0**(该函数里四处硬编码 `"zero_shift": 0.0`)。

两者在同一张谱上会给出不同的 a/c。把它们画在同一条趋势线上,用户看到的
"结构演变"里有一部分其实是**换了算法**,而不是样品真的变了 —— 这比不给
趋势图更糟,因为它看起来很像一个结论。

所以本模块**只用第 1 条路径**(与晶面标注同源),并在结果里如实标出
`source`,让 UI 能说明"这些点是怎么来的"。
"""

from __future__ import annotations

from typing import Any

import numpy as np

# 趋势指标白名单。UI 的 y 轴下拉直接用这份清单,避免前后端各写一份。
TREND_METRICS = (
    "a",
    "c",
    "c_over_a",
    "cell_volume",
    "i003_i104",
    "fwhm_003",
    "rwp",
)

# x 轴候选。temperature 需要用户填过才有值。
TREND_AXES = ("temperature", "batch", "index")


def _temperature_value(raw: str) -> float | None:
    """从自由文本里取出温度数值。

    用户会填 "750"、"750C"、"750 ℃"、"750-800" 各种写法。取第一段连续
    数字即可 —— 取不到就返回 None,由调用方把它排到序列末尾,而不是
    当成 0(当成 0 会让它排到最前面,趋势图开头凭空多一个点)。
    """
    text = str(raw or "").strip()
    if not text:
        return None
    num = ""
    for ch in text:
        if ch.isdigit() or (ch == "." and num):
            num += ch
        elif num:
            break
    try:
        return float(num) if num else None
    except ValueError:
        return None


def build_trend(
    datasets: list[dict[str, Any]],
    *,
    x_axis: str = "temperature",
    metric: str = "a",
) -> dict[str, Any]:
    """把已算好指标的 datasets 组装成趋势序列。

    `datasets` 每项形如::

        {"id", "name", "sample_name", "temperature", "batch",
         "x_value"?, "metrics": {...} | None}

    `metrics` 为 None 表示这条数据集**还没算过**结构分析。此时它仍然出现
    在返回里(带 `value: None`),由 UI 画成空心点 —— 直接丢掉会让用户
    以为"这条数据不存在",而实际上只是没算。
    """
    if metric not in TREND_METRICS:
        raise ValueError(f"未知的趋势指标:{metric}")
    if x_axis not in TREND_AXES:
        raise ValueError(f"未知的趋势横轴:{x_axis}")

    points: list[dict[str, Any]] = []
    for item in datasets:
        metrics = item.get("metrics")
        value = None if not metrics else metrics.get(metric)
        if value is not None and not np.isfinite(float(value)):
            value = None

        if x_axis == "temperature":
            x = _temperature_value(item.get("temperature", ""))
            label = str(item.get("temperature") or "").strip() or "未填温度"
        elif x_axis == "batch":
            x = None
            label = str(item.get("batch") or "").strip() or "未填批次"
        else:
            x = None
            label = str(item.get("sample_name") or "")

        points.append(
            {
                "datasetId": item["id"],
                "datasetName": item.get("name", ""),
                "sampleName": item.get("sample_name", ""),
                "xValue": x,
                "xLabel": label,
                "value": value,
                "source": (metrics or {}).get("source"),
            }
        )

    # 排序规则:x 轴是温度时按温度升序,取不到温度的排最后(而不是当 0);
    # 批次/序号轴按原始顺序 —— 那是用户的组织顺序,重排会打乱他的直觉。
    if x_axis == "temperature":
        points.sort(key=lambda p: (p["xValue"] is None, p["xValue"] or 0.0))

    return {
        "xAxis": x_axis,
        "metric": metric,
        "points": points,
        "availableMetrics": list(TREND_METRICS),
        "availableAxes": list(TREND_AXES),
    }
