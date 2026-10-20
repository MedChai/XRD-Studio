"""结构趋势测试(high_ni.trend)。

重点是**不把"没算过"和"算出来是 0"混为一谈**,以及 x 轴排序不把
缺失值当 0 —— 这两处错了趋势图会给出看起来很像结论的错误图形。
"""

from __future__ import annotations

import pytest

from xrd_engine.high_ni.trend import _temperature_value, build_trend


def ds(id_, temp="", batch="", metrics=None, name=None):
    return {
        "id": id_, "name": name or f"d{id_}", "sample_name": f"s{id_}",
        "temperature": temp, "batch": batch, "metrics": metrics,
    }


class TestTemperatureParsing:
    def test_plain_number(self):
        assert _temperature_value("750") == 750.0

    def test_with_unit(self):
        assert _temperature_value("750C") == 750.0
        assert _temperature_value("750 ℃") == 750.0
        assert _temperature_value("750°C") == 750.0

    def test_range_takes_first(self):
        assert _temperature_value("750-800") == 750.0

    def test_empty_is_none(self):
        assert _temperature_value("") is None
        assert _temperature_value("   ") is None

    def test_non_numeric_is_none(self):
        """填了"室温"这种词不能变成 0 —— 那会把它排到最前面。"""
        assert _temperature_value("室温") is None


class TestBuildTrend:
    def test_sorts_by_temperature(self):
        r = build_trend(
            [ds(1, "800", metrics={"a": 2.87}),
             ds(2, "700", metrics={"a": 2.88}),
             ds(3, "750", metrics={"a": 2.875})],
            x_axis="temperature", metric="a",
        )
        assert [p["datasetId"] for p in r["points"]] == [2, 3, 1]
        assert [p["value"] for p in r["points"]] == [2.88, 2.875, 2.87]

    def test_missing_temperature_goes_last_not_zero(self):
        """没填温度的排最后。当成 0 会排到最前,趋势图开头凭空多一个点。"""
        r = build_trend(
            [ds(1, "", metrics={"a": 1.0}),
             ds(2, "700", metrics={"a": 2.0})],
            x_axis="temperature", metric="a",
        )
        assert [p["datasetId"] for p in r["points"]] == [2, 1]

    def test_unanalyzed_dataset_is_kept_with_none(self):
        """没算过的点必须保留(画成空心),丢掉会让用户以为数据不存在。"""
        r = build_trend(
            [ds(1, "700", metrics={"a": 2.88}), ds(2, "750", metrics=None)],
            x_axis="temperature", metric="a",
        )
        assert len(r["points"]) == 2
        assert r["points"][1]["value"] is None
        assert r["points"][1]["datasetId"] == 2

    def test_missing_metric_is_none_not_zero(self):
        """该指标没算出来 → None,不是 0。0 会被画成"晶胞塌了"。"""
        r = build_trend(
            [ds(1, "700", metrics={"i003_i104": 1.3})],
            x_axis="temperature", metric="a",
        )
        assert r["points"][0]["value"] is None

    def test_nan_becomes_none(self):
        r = build_trend(
            [ds(1, "700", metrics={"a": float("nan")})],
            x_axis="temperature", metric="a",
        )
        assert r["points"][0]["value"] is None

    def test_batch_axis_keeps_input_order(self):
        """批次/序号轴不重排 —— 那是用户的组织顺序。"""
        r = build_trend(
            [ds(1, batch="B", metrics={"a": 1.0}),
             ds(2, batch="A", metrics={"a": 2.0})],
            x_axis="batch", metric="a",
        )
        assert [p["datasetId"] for p in r["points"]] == [1, 2]

    def test_carries_source_for_audit(self):
        """source 要带出来,让 UI 能说明这些点用的是哪条算法。"""
        r = build_trend(
            [ds(1, "700", metrics={"a": 2.88, "source": "refine_cell"})],
            x_axis="temperature", metric="a",
        )
        assert r["points"][0]["source"] == "refine_cell"

    def test_lists_available_metrics_and_axes(self):
        r = build_trend([], x_axis="temperature", metric="a")
        assert "a" in r["availableMetrics"] and "c" in r["availableMetrics"]
        assert "temperature" in r["availableAxes"]

    def test_empty_input(self):
        r = build_trend([], x_axis="temperature", metric="a")
        assert r["points"] == []

    def test_rejects_unknown_metric(self):
        with pytest.raises(ValueError):
            build_trend([], metric="nope")

    def test_rejects_unknown_axis(self):
        with pytest.raises(ValueError):
            build_trend([], x_axis="nope")

    def test_all_metrics_selectable(self):
        """每个白名单指标都要能取出来,不能有写进清单却取不到的。"""
        metrics = {m: 1.5 for m in
                   ("a", "c", "c_over_a", "cell_volume", "i003_i104",
                    "fwhm_003", "rwp")}
        for m in metrics:
            r = build_trend([ds(1, "700", metrics=metrics)], metric=m)
            assert r["points"][0]["value"] == 1.5, m
