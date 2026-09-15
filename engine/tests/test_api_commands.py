"""命令分发层测试(§10.4)。

覆盖 import → 查重 → curve → preview → commit → detect → presets 的
完整用户主流程(README §77 Success Flow),这是引擎的集成面。
"""

from __future__ import annotations

import json

import pytest

from xrd_engine.api.commands import Engine


@pytest.fixture
def engine(tmp_path):
    eng = Engine(tmp_path / "store")
    yield eng
    eng.close()


@pytest.fixture
def with_dataset(engine, fixtures_dir):
    """走一遍最小成功流程,返回 (engine, project_id, sample_id, dataset_id)。"""
    path = fixtures_dir / "lfp_500c_scan01.xy"
    if not path.exists():
        pytest.skip("fixture 未生成")
    pid = engine.handle("projects.create", {"name": "LFP Study"})["data"]["projectId"]
    r = engine.handle("datasets.import", {
        "paths": [str(path)],
        "project_id": pid,
        "new_sample": {"name": "LFP-500C", "temperature": "500"},
    })
    assert r["ok"], r
    sample_id = r["data"]["sampleId"]
    dataset_id = r["data"]["imported"][0]["id"]
    return engine, pid, sample_id, dataset_id


class TestDispatch:
    def test_unknown_command(self, engine):
        r = engine.handle("no.such.cmd", {})
        assert not r["ok"]
        assert "未知命令" in r["error"]

    def test_error_never_leaks_traceback(self, engine):
        r = engine.handle("datasets.curve", {"id": 99999})
        assert not r["ok"]
        assert "Traceback" not in r["error"]
        assert "File \"" not in r["error"]


class TestImport:
    def test_import_rejects_empty_paths(self, engine):
        engine.handle("projects.create", {"name": "P"})
        r = engine.handle("datasets.import", {"paths": [], "project_id": 1})
        assert not r["ok"]

    def test_import_requires_sample(self, engine, fixtures_dir):
        pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
        path = fixtures_dir / "lfp_500c_scan01.xy"
        if not path.exists():
            pytest.skip("fixture 未生成")
        r = engine.handle("datasets.import", {"paths": [str(path)], "project_id": pid})
        assert not r["ok"]
        assert "Sample" in r["error"]

    def test_duplicate_detection(self, with_dataset, fixtures_dir):
        engine, pid, sample_id, _ = with_dataset
        path = fixtures_dir / "lfp_500c_scan01.xy"
        r = engine.handle("datasets.import", {
            "paths": [str(path)], "project_id": pid, "sample_id": sample_id,
        })
        assert r["ok"]
        dups = r["data"]["duplicates"]
        assert len(dups) == 1
        assert dups[0]["existing"]["sampleName"] == "LFP-500C"

    def test_import_anyway(self, with_dataset, fixtures_dir):
        engine, pid, sample_id, _ = with_dataset
        path = fixtures_dir / "lfp_500c_scan01.xy"
        r = engine.handle("datasets.import", {
            "paths": [str(path)], "project_id": pid,
            "sample_id": sample_id, "import_anyway": True,
        })
        assert r["ok"]
        assert len(r["data"]["imported"]) == 1

    def test_unparseable_goes_to_failures(self, engine, fixtures_dir):
        pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
        path = fixtures_dir / "unparseable.txt"
        if not path.exists():
            pytest.skip("fixture 未生成")
        r = engine.handle("datasets.import", {
            "paths": [str(path)], "project_id": pid,
            "new_sample": {"name": "S"},
        })
        assert r["ok"]
        assert len(r["data"]["failures"]) + len(r["data"]["needColumns"]) == 1

    def test_single_column_needs_mapping(self, engine, fixtures_dir):
        pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
        path = fixtures_dir / "single_column.txt"
        if not path.exists():
            pytest.skip("fixture 未生成")
        r = engine.handle("datasets.import", {
            "paths": [str(path)], "project_id": pid,
            "new_sample": {"name": "S"},
        })
        assert r["ok"]
        assert len(r["data"]["needColumns"]) == 1
        need = r["data"]["needColumns"][0]
        assert need["previewRows"]


class TestCurveAndPreview:
    def test_curve_roundtrip(self, with_dataset):
        engine, _, _, ds_id = with_dataset
        r = engine.handle("datasets.curve", {"id": ds_id})
        assert r["ok"]
        data = r["data"]
        assert data["pointCount"] == 3501
        assert data["x"][0] == pytest.approx(10.0)
        assert data["x"][-1] == pytest.approx(80.0)

    def test_preview_with_background_and_peaks(self, with_dataset):
        engine, _, _, ds_id = with_dataset
        r = engine.handle("analysis.preview", {
            "dataset_id": ds_id,
            "pipeline": {
                "background": {"enabled": True, "method": "asls",
                               "smoothness": 1e5, "asymmetry": 0.01, "iterations": 10},
                "peaks": {"sensitivity": "medium",
                          "min_height_pct": 5.0, "min_distance_deg": 0.1},
            },
        })
        assert r["ok"], r.get("error")
        peaks = r["data"]["peaks"]
        assert len(peaks) >= 10
        positions = [p["position"] for p in peaks]
        # LFP 主峰(020) 在 34.39° 附近必须被检出
        assert any(abs(pos - 34.39) < 0.05 for pos in positions)

    def test_preview_reports_smoothing_flag(self, with_dataset):
        engine, _, _, ds_id = with_dataset
        r = engine.handle("analysis.preview", {
            "dataset_id": ds_id,
            "pipeline": {
                "smoothing": {"enabled": True, "window_length": 15, "polyorder": 2},
                "peaks": {"sensitivity": "medium"},
            },
        })
        assert r["ok"]
        assert r["data"]["smoothingAffectsMetrics"] is True

    def test_preview_invalid_bg_params_error(self, with_dataset):
        engine, _, _, ds_id = with_dataset
        r = engine.handle("analysis.preview", {
            "dataset_id": ds_id,
            "pipeline": {"background": {"enabled": True, "method": "polynomial", "order": 9}},
        })
        assert not r["ok"]

    def test_commit_persists(self, with_dataset):
        engine, _, _, ds_id = with_dataset
        pipeline = {"peaks": {"sensitivity": "high"}}
        r = engine.handle("analysis.commit", {"dataset_id": ds_id, "pipeline": pipeline})
        assert r["ok"]
        analysis = engine.db.query_one(
            "SELECT * FROM analyses WHERE dataset_id = ? AND is_active = 1", (ds_id,)
        )
        assert json.loads(analysis["pipeline_json"])["peaks"]["sensitivity"] == "high"


class TestPeaksPersistence:
    def test_detect_writes_peaks_table(self, with_dataset):
        engine, _, _, ds_id = with_dataset
        analysis = engine.db.query_one(
            "SELECT * FROM analyses WHERE dataset_id = ? AND is_active = 1", (ds_id,)
        )
        engine.handle("analysis.commit", {
            "dataset_id": ds_id,
            "pipeline": {
                "background": {"enabled": True, "method": "asls",
                               "smoothness": 1e5, "asymmetry": 0.01, "iterations": 10},
                "peaks": {"sensitivity": "medium",
                          "min_height_pct": 5.0, "min_distance_deg": 0.1},
            },
        })
        r = engine.handle("peaks.detect", {"analysis_id": analysis["id"]})
        assert r["ok"]
        listed = engine.handle("peaks.list", {"analysis_id": analysis["id"]})
        assert len(listed["data"]["peaks"]) == len(r["data"]["peaks"])

    def test_bounds_edit_recomputes_area(self, with_dataset):
        engine, _, _, ds_id = with_dataset
        analysis = engine.db.query_one(
            "SELECT * FROM analyses WHERE dataset_id = ? AND is_active = 1", (ds_id,)
        )
        engine.handle("analysis.commit", {
            "dataset_id": ds_id,
            "pipeline": {"peaks": {"sensitivity": "medium"}},
        })
        engine.handle("peaks.detect", {"analysis_id": analysis["id"]})
        first = engine.handle("peaks.list", {"analysis_id": analysis["id"]})["data"]["peaks"][0]
        old_area = first["area"]
        assert old_area is not None

        r = engine.handle("peaks.update_bounds", {
            "peak_id": first["id"],
            "left": first["position"] - 0.5,
            "right": first["position"] + 0.5,
        })
        assert r["ok"], r.get("error")
        assert r["data"]["peak"]["manual_override"] is True or r["data"]["peak"]["manual_override"] == 1
        # 新边界(±0.5°)必然比自动边界面积大
        assert r["data"]["peak"]["area"] > old_area

    def test_bounds_edit_rejects_inverted(self, with_dataset):
        engine, _, _, ds_id = with_dataset
        analysis = engine.db.query_one(
            "SELECT * FROM analyses WHERE dataset_id = ? AND is_active = 1", (ds_id,)
        )
        engine.handle("analysis.commit", {"dataset_id": ds_id, "pipeline": {}})
        engine.handle("peaks.detect", {"analysis_id": analysis["id"]})
        peaks = engine.handle("peaks.list", {"analysis_id": analysis["id"]})["data"]["peaks"]
        if not peaks:
            pytest.skip("未检出峰")
        r = engine.handle("peaks.update_bounds", {
            "peak_id": peaks[0]["id"],
            "left": peaks[0]["position"] + 0.5,
            "right": peaks[0]["position"] - 0.5,
        })
        assert not r["ok"]


class TestPresets:
    def test_save_list_apply(self, with_dataset, fixtures_dir):
        engine, pid, _sample_id, ds_id = with_dataset
        # 第二个样品第二个数据集,用于批量应用
        r = engine.handle("datasets.import", {
            "paths": [str(fixtures_dir / "lfp_400c_scan01.xy")],
            "project_id": pid,
            "new_sample": {"name": "LFP-400C"},
        })
        assert r["ok"], r
        ds2 = r["data"]["imported"][0]["id"]

        pipeline = {"background": {"enabled": True, "method": "asls"}}
        pr = engine.handle("presets.save", {
            "project_id": pid, "name": "标准处理", "pipeline": pipeline,
        })
        assert pr["ok"]
        preset_id = pr["data"]["presetId"]

        lr = engine.handle("presets.list", {"project_id": pid})
        assert lr["data"]["presets"][0]["name"] == "标准处理"

        ar = engine.handle("presets.apply", {
            "preset_id": preset_id, "dataset_ids": [ds_id, ds2],
        })
        assert ar["ok"] and ar["data"]["applied"] == 2

        a1 = engine.db.query_one(
            "SELECT pipeline_json, preset_id FROM analyses WHERE dataset_id = ? AND is_active = 1",
            (ds_id,),
        )
        assert json.loads(a1["pipeline_json"])["background"]["enabled"] is True
        assert a1["preset_id"] == preset_id


class TestLibrary:
    def test_tree_structure(self, with_dataset):
        engine, _, _sample_id, ds_id = with_dataset
        r = engine.handle("library.tree", {})
        assert r["ok"]
        samples = r["data"]["samples"]
        assert len(samples) == 1
        assert samples[0]["name"] == "LFP-500C"
        assert samples[0]["datasets"][0]["id"] == ds_id

    def test_rename_and_soft_delete(self, with_dataset):
        engine, _, sample_id, _ = with_dataset
        rr = engine.handle("meta.rename", {"table": "samples", "id": sample_id, "name": "LFP-600C"})
        assert rr["ok"]
        tree = engine.handle("library.tree", {})["data"]["samples"]
        assert tree[0]["name"] == "LFP-600C"

        dr = engine.handle("meta.soft_delete", {"table": "samples", "id": sample_id})
        assert dr["ok"]
        assert engine.handle("library.tree", {})["data"]["samples"] == []


class TestResampledDataset:
    def test_nonuniform_import_marks_grid(self, engine, fixtures_dir):
        path = fixtures_dir / "nonuniform_dscan.xy"
        if not path.exists():
            pytest.skip("fixture 未生成")
        pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
        r = engine.handle("datasets.import", {
            "paths": [str(path)], "project_id": pid,
            "new_sample": {"name": "S"},
        })
        assert r["ok"] and r["data"]["imported"]
        ds_id = r["data"]["imported"][0]["id"]
        row = engine.db.query_one("SELECT grid FROM datasets WHERE id = ?", (ds_id,))
        assert row["grid"] == "non_uniform"
        # 预览仍能工作(引擎内部已均匀化)
        pv = engine.handle("analysis.preview", {
            "dataset_id": ds_id, "pipeline": {"peaks": {"sensitivity": "medium"}},
        })
        assert pv["ok"]
