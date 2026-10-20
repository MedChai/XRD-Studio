"""命令分发层测试(§10.4)。

覆盖 import → 查重 → curve → preview → commit → detect → presets 的
完整用户主流程(README §77 Success Flow),这是引擎的集成面。
"""

from __future__ import annotations

import base64
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

    def test_column_mapping_roundtrip(self, engine, tmp_path):
        """列映射的两次调用序列(前端 ImportSheet 走的就是这条)。

        第一次不带列信息 → 引擎返回 needColumns,并且**已经把 sample 建好了**
        并在返回体里给出 sampleId;第二次带 x_column/y_column + 那个 sample_id
        → 应当导入成功,且复用同一个 sample。

        这条契约是前端 B2 修复的依据:前端必须在处理 needColumns 之前就把
        sampleId 存下来,否则第二次调用发 sample_id=None 且没有 new_sample,
        引擎只能报「新样品必须填写 Sample Name」。

        这里用临时文件而不是 fixtures/ 里的现成文件:现有那几个「需要列映射」
        的 fixture(european.txt / unparseable.txt / single_column.txt)在解析
        层面就已经失败,列映射也救不回来,无法用来验往返。10 列文件走的是
        「列数超过 MAX_AUTO_COLUMNS 所以不猜列」这条路径,是唯一能真正往返的
        列映射场景。
        """
        pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]

        path = tmp_path / "ten_columns.txt"
        lines = ["# 2theta  intensity  c3 c4 c5 c6 c7 c8 c9 c10"]
        for i in range(200):
            x = 10.0 + i * 0.05
            y = 1000.0 + 500.0 * ((i * 37) % 100) / 100.0
            lines.append(f"{x:.3f} {y:.1f} 1 2 3 4 5 6 7 8")
        path.write_text(chr(10).join(lines), encoding="utf-8")

        first = engine.handle("datasets.import", {
            "paths": [str(path)], "project_id": pid,
            "new_sample": {"name": "S"},
        })
        assert first["ok"], first
        assert len(first["data"]["needColumns"]) == 1, first["data"]
        # 关键:第一次调用就要给出 sampleId,前端才有东西可回填
        sample_id = first["data"]["sampleId"]
        assert sample_id is not None

        second = engine.handle("datasets.import", {
            "paths": [str(path)], "project_id": pid,
            "sample_id": sample_id,
            "x_column": 0, "y_column": 1,
        })
        assert second["ok"], second
        assert len(second["data"]["imported"]) == 1, second["data"]
        # 复用同一个 sample,不再新建
        assert second["data"]["sampleId"] == sample_id

    def test_mapping_without_sample_is_rejected(self, engine, fixtures_dir):
        """反向确认:第二次调用若丢了 sample_id 又不给 new_sample,必须报错
        (这正是修复前前端必然踩到的坑)。"""
        pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
        path = fixtures_dir / "single_column.txt"
        if not path.exists():
            pytest.skip("fixture 未生成")
        r = engine.handle("datasets.import", {
            "paths": [str(path)], "project_id": pid,
            "x_column": 0, "y_column": 1,
        })
        assert not r["ok"]
        assert "Sample" in r["error"]


def _curve_arrays(data: dict, *names: str):
    """拆曲线信封,返回请求的数组(默认 x, y)。

    复刻前端 attachBinary 的切片逻辑 —— 前端若切错,这里也会错,
    所以两边的实现要保持同构。
    """
    from xrd_engine.api.envelope import unpack_arrays

    got = unpack_arrays(data["__bin__"], data["_binary"])
    want = names or ("x", "y")
    return tuple(got[n] for n in want)

class TestCurveAndPreview:
    """曲线走二进制信封(§9.1),x/y 不再出现在 JSON 里。

    断言改用 `_curve_arrays()` 拆信封 —— 这比原来直接读 `data["x"]`
    **更强**:它同时验证了 JSON 头确实不含数组、二进制体长度自洽、
    且按 layout 切出来的值与原始曲线一致。只读 data["x"] 的话,
    信封长度算错也照样能通过。
    """

    def test_curve_roundtrip(self, with_dataset):
        engine, _, _, ds_id = with_dataset
        r = engine.handle("datasets.curve", {"id": ds_id})
        assert r["ok"]
        data = r["data"]
        assert data["pointCount"] == 3501
        # 大数组不再走 JSON
        assert "x" not in data and "y" not in data
        x, y = _curve_arrays(data)
        assert x[0] == pytest.approx(10.0, abs=1e-4)
        assert x[-1] == pytest.approx(80.0, abs=1e-4)
        assert len(x) == len(y) == data["pointCount"]

    def test_curve_display_downsample_does_not_change_source_count(self, with_dataset):
        engine, _, _, ds_id = with_dataset
        r = engine.handle("datasets.curve", {
            "id": ds_id,
            "max_points": 120,
            "x_min": 20.0,
            "x_max": 60.0,
        })
        assert r["ok"], r
        data = r["data"]
        assert data["pointCount"] <= 120
        assert data["sourcePointCount"] == 3501
        assert data["downsampled"] is True
        x, _y = _curve_arrays(data)
        assert x[0] == pytest.approx(20.0, abs=0.02)
        assert x[-1] == pytest.approx(60.0, abs=0.02)

    def test_envelope_body_matches_header(self, with_dataset):
        """二进制体长度必须与 __bin__ 描述一致 —— 错位会让图静默画错。"""
        engine, _, _, ds_id = with_dataset
        data = engine.handle("datasets.curve", {"id": ds_id})["data"]
        meta = data["__bin__"]
        assert meta["layout"] == ["x", "y"]
        assert meta["n"] == data["pointCount"]
        assert len(data["_binary"]) == meta["bytes"] == meta["n"] * 2 * 4

    def test_preview_carries_background_in_envelope(self, with_dataset):
        """背景曲线与主曲线等长,放进同一个信封,省掉第二次序列化。"""
        engine, _, _, ds_id = with_dataset
        data = engine.handle("analysis.preview", {
            "dataset_id": ds_id,
            "pipeline": {
                "background": {"enabled": True, "method": "asls"},
                "peaks": {"sensitivity": "medium"},
            },
        })["data"]["curve"]
        assert data["__bin__"]["layout"] == ["x", "y", "background"]
        x, y, bg = _curve_arrays(data, "x", "y", "background")
        assert len(bg) == len(x) == len(y)

    def test_preview_downsample_keeps_full_resolution_metrics(self, with_dataset):
        engine, _, _, ds_id = with_dataset
        full = engine.handle("analysis.preview", {
            "dataset_id": ds_id,
            "pipeline": {"peaks": {"sensitivity": "medium"}},
        })
        preview = engine.handle("analysis.preview", {
            "dataset_id": ds_id,
            "max_points": 150,
            "pipeline": {"peaks": {"sensitivity": "medium"}},
        })
        assert full["ok"] and preview["ok"]
        assert preview["data"]["curve"]["pointCount"] <= 150
        assert preview["data"]["curve"]["sourcePointCount"] == 3501
        assert len(preview["data"]["peaks"]) == len(full["data"]["peaks"])

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

    def test_fast_preview_skips_expensive_annotation(self, with_dataset, monkeypatch):
        """交互预览不应同步触发高镍全谱晶胞精修。"""
        engine, _, _, ds_id = with_dataset

        def fail_annotation(*_args, **_kwargs):
            raise AssertionError("快速预览不应调用高镍晶面标注")

        monkeypatch.setattr(engine, "_annotate_pipeline_peaks", fail_annotation)
        r = engine.handle("analysis.preview", {
            "dataset_id": ds_id,
            "annotate": False,
            "pipeline": {"peaks": {"sensitivity": "medium"}},
        })
        assert r["ok"], r
        assert r["data"]["annotation"] is None

    def test_analysis_state_restores_saved_pipeline_and_peaks(self, with_dataset):
        engine, _, _, ds_id = with_dataset
        pipeline = {
            "background": {"enabled": True, "method": "asls", "smoothness": 1e5},
            "peaks": {"sensitivity": "high"},
        }
        assert engine.handle("analysis.commit", {
            "dataset_id": ds_id, "pipeline": pipeline,
        })["ok"]
        detected = engine.handle("peaks.detect", {"dataset_id": ds_id})
        assert detected["ok"] and detected["data"]["peaks"]

        state = engine.handle("analysis.state", {
            "dataset_id": ds_id, "max_points": 120,
        })
        assert state["ok"], state
        assert state["data"]["pipeline"] == pipeline
        assert state["data"]["peaksStored"] is True
        assert len(state["data"]["peaks"]) == len(detected["data"]["peaks"])
        assert state["data"]["curve"]["pointCount"] <= 120

    def test_commit_invalidates_peaks_from_previous_pipeline(self, with_dataset):
        engine, _, _, ds_id = with_dataset
        assert engine.handle("peaks.detect", {"dataset_id": ds_id})["ok"]
        analysis = engine.db.query_one(
            "SELECT id FROM analyses WHERE dataset_id = ? AND is_active = 1", (ds_id,)
        )
        assert engine.handle("peaks.list", {"analysis_id": analysis["id"]})["data"]["peaks"]
        engine.db.insert(
            """INSERT INTO domain_results
               (analysis_id, domain, result_json, warnings_json, source_hash)
               VALUES (?,?,?,?,?)""",
            (analysis["id"], "high_ni_layered", "{}", "[]", "hash"),
        )

        assert engine.handle("analysis.commit", {
            "dataset_id": ds_id,
            "pipeline": {"peaks": {"sensitivity": "low"}},
        })["ok"]
        assert engine.handle("peaks.list", {
            "analysis_id": analysis["id"],
        })["data"]["peaks"] == []
        assert engine.db.query_one(
            "SELECT id FROM domain_results WHERE analysis_id = ?", (analysis["id"],)
        ) is None

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


class TestExports:
    def test_export_peaks_contains_metadata_and_avoids_overwrite(
        self, with_dataset, tmp_path
    ):
        engine, _, _, ds_id = with_dataset
        target = tmp_path / "peaks.csv"
        first = engine.handle("export.peaks", {
            "dataset_id": ds_id,
            "path": str(target),
        })
        assert first["ok"], first
        assert target.exists()
        content = target.read_text(encoding="utf-8-sig")
        assert "# source_file_hash_sha256:" in content
        assert "dataset_id,sample_name,peak_index" in content

        second = engine.handle("export.peaks", {
            "dataset_id": ds_id,
            "path": str(target),
        })
        assert second["ok"], second
        assert second["data"]["path"] != str(target)

    def test_export_data_respects_saved_crop(self, with_dataset, tmp_path):
        engine, _, _, ds_id = with_dataset
        committed = engine.handle("analysis.commit", {
            "dataset_id": ds_id,
            "pipeline": {
                "crop": {"enabled": True, "x_min": 20.0, "x_max": 40.0},
            },
        })
        assert committed["ok"]
        target = tmp_path / "processed.csv"
        result = engine.handle("export.data", {
            "dataset_id": ds_id,
            "path": str(target),
        })
        assert result["ok"], result
        lines = target.read_text(encoding="utf-8-sig").splitlines()
        header_index = next(i for i, line in enumerate(lines) if line == "x,raw_y,background,processed_y")
        rows = lines[header_index + 1:]
        assert rows
        assert float(rows[0].split(",")[0]) >= 20.0
        assert float(rows[-1].split(",")[0]) <= 40.0

    def test_export_figure_validates_png(self, engine, tmp_path):
        target = tmp_path / "figure.png"
        png = b"\x89PNG\r\n\x1a\nminimal-test"
        result = engine.handle("export.figure", {
            "path": str(target),
            "png_base64": base64.b64encode(png).decode("ascii"),
        })
        assert result["ok"], result
        assert target.read_bytes() == png


class TestHighNiDomain:
    def test_profile_create_list_and_domain_analysis(self, with_dataset):
        engine, project_id, _sample_id, dataset_id = with_dataset
        created = engine.handle("profiles.create", {
            "project_id": project_id,
            "name": "NMA-90-05-05",
            "formula": "LiNi0.90Mn0.05Al0.05O2",
            "ni_fraction": 0.90,
            "wavelength": 1.5418,
        })
        assert created["ok"], created
        profile_id = created["data"]["profileId"]

        listed = engine.handle("profiles.list", {"project_id": project_id})
        assert listed["ok"]
        assert listed["data"]["profiles"][0]["name"] == "NMA-90-05-05"

        analyzed = engine.handle("domain.high_ni.analyze", {
            "dataset_id": dataset_id,
            "profile_id": profile_id,
            "modules": ["mixing", "layered", "lattice"],
            "fit": True,
        })
        assert analyzed["ok"], analyzed
        result = analyzed["data"]
        assert result["domain"] == "high_ni_layered"
        assert result["analysisId"]
        assert result["sourceHash"]
        assert "mixing" in result
        assert "layered" in result
        assert "lattice" in result

        saved = engine.db.query_one(
            """SELECT * FROM domain_results
               WHERE analysis_id = ? AND domain = ?""",
            (result["analysisId"], "high_ni_layered"),
        )
        assert saved is not None
        assert saved["source_hash"] == result["sourceHash"]

    def test_domain_analysis_reuses_saved_pipeline(self, with_dataset):
        engine, _, _, dataset_id = with_dataset
        committed = engine.handle("analysis.commit", {
            "dataset_id": dataset_id,
            "pipeline": {
                "crop": {"enabled": True, "x_min": 15.0, "x_max": 70.0},
            },
        })
        assert committed["ok"]
        result = engine.handle("domain.high_ni.analyze", {
            "dataset_id": dataset_id,
            "modules": ["mixing"],
            "fit": False,
        })
        assert result["ok"], result
        assert result["data"]["mixing"]["confidence"] == "low"

    def test_purge_cleans_domain_results_and_profiles(self, with_dataset):
        engine, project_id, _sample_id, dataset_id = with_dataset
        profile = engine.handle("profiles.create", {
            "project_id": project_id,
            "name": "cleanup-profile",
            "reference_dataset_id": dataset_id,
        })
        assert profile["ok"]
        analysis = engine.handle("domain.high_ni.analyze", {
            "dataset_id": dataset_id,
            "profile_id": profile["data"]["profileId"],
            "modules": ["mixing"],
        })
        assert analysis["ok"]

        assert engine.handle("meta.soft_delete", {
            "table": "datasets", "id": dataset_id,
        })["ok"]
        purged_dataset = engine.handle("meta.purge", {
            "table": "datasets", "id": dataset_id,
        })
        assert purged_dataset["ok"], purged_dataset
        assert engine.db.query_one(
            "SELECT id FROM domain_results WHERE analysis_id = ?",
            (analysis["data"]["analysisId"],),
        ) is None
        profile_row = engine.db.query_one(
            "SELECT reference_dataset_id FROM material_profiles WHERE id = ?",
            (profile["data"]["profileId"],),
        )
        assert profile_row["reference_dataset_id"] is None

        assert engine.handle("meta.soft_delete", {
            "table": "projects", "id": project_id,
        })["ok"]
        purged_project = engine.handle("meta.purge", {
            "table": "projects", "id": project_id,
        })
        assert purged_project["ok"], purged_project
        assert engine.db.query_one(
            "SELECT id FROM material_profiles WHERE id = ?",
            (profile["data"]["profileId"],),
        ) is None


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


class TestAnnotationPersists:
    """归属依据(dominance / hkl_candidates)必须落库并能读回。

    这条链路此前是断的:annotate 里算好了 dominance 与候选列表,
    `_annotate_pipeline_peaks` 只把 hkl / hkl_confidence 写回 Peak,
    `Peak.to_dict()` 也没有这两个字段 —— 前端峰表的"归属依据:该反射
    贡献 X%"因此**永远显示 0%**,候选列表永远为空。CPU 白算一场,
    而用户看到的是一个错误信息(0% 不等于"没算",它等于"这个反射不
    贡献强度",是相反的意思)。

    这里锁住三件事:① 命令返回值带 dominance;② peaks 表里存下来了;
    ③ 重新读回(peaks.list)仍然在 —— 只做 ① 的话重载数据集就丢。
    """

    def test_annotate_returns_and_persists_dominance(self, with_dataset):
        engine, _pid, _sid, dataset_id = with_dataset
        det = engine.handle("peaks.detect", {"dataset_id": dataset_id})
        assert det["ok"], det
        peaks = det["data"]["peaks"]
        assert peaks, "LFP 基准谱应当检出峰"

        # ① 命令返回值里必须带归属依据
        labeled = [p for p in peaks if p.get("hkl")]
        assert labeled, "LFP 谱在 R-3m 模型下应有被归属的峰"
        assert all(p.get("dominance") is not None for p in labeled), (
            "被归属的峰必须带 dominance,否则前端只能显示 0%"
        )

        # ② peaks 表里落下来了
        analysis_id = engine.db.query_one(
            "SELECT id FROM analyses WHERE dataset_id = ?", (dataset_id,)
        )["id"]
        rows = engine.db.query_all(
            "SELECT hkl, dominance, hkl_candidates FROM peaks "
            "WHERE analysis_id = ? AND hkl IS NOT NULL",
            (analysis_id,),
        )
        assert rows, "标注结果应写入 peaks 表"
        assert all(r["dominance"] is not None for r in rows), (
            "dominance 没有落库 —— 重新加载数据集后会退回 0%"
        )
        assert any(r["hkl_candidates"] for r in rows), (
            "候选列表应至少有一条,供用户核对归属依据"
        )

        # ③ 读回来仍然在
        listed = engine.handle("peaks.list", {"analysis_id": analysis_id})
        assert listed["ok"]
        back = [p for p in listed["data"]["peaks"] if p["hkl"]]
        assert back and all(p["dominance"] is not None for p in back)
        assert any(p["hkl_candidates"] for p in back)

    def test_annotate_command_returns_annotation_context(self, with_dataset):
        """peaks.annotate 是独立命令,此前命令层零测试。"""
        engine, _pid, _sid, dataset_id = with_dataset
        engine.handle("peaks.detect", {"dataset_id": dataset_id})
        r = engine.handle("peaks.annotate", {"dataset_id": dataset_id})
        assert r["ok"], r
        data = r["data"]
        assert data["count"] >= 0
        assert data["updated"] > 0, "按主键写回必须真的更新到行"
        # 拟合上下文供 UI 展示"标签是怎么来的"
        ctx = data["annotation"]
        assert ctx is not None
        assert 2.7 < ctx["a"] < 3.0 and 13.0 < ctx["c"] < 15.0
        for item in data["annotated"]:
            assert "dominance" in item and "candidates" in item

    def test_projects_list(self, engine):
        """projects.list 此前命令层零测试。"""
        assert engine.handle("projects.list", {})["data"]["projects"] == []
        engine.handle("projects.create", {"name": "P1"})
        engine.handle("projects.create", {"name": "P2"})
        names = [p["name"] for p in engine.handle("projects.list", {})["data"]["projects"]]
        assert names == ["P1", "P2"]


class TestAnnotationLifecycle:
    """标注自动保存 / 打开图恢复 / 参数变更不蒸发(annotation_json, SCHEMA v6)。"""

    def _analysis(self, engine, dataset_id):
        return engine.db.query_one(
            "SELECT * FROM analyses WHERE dataset_id = ? AND is_active = 1",
            (dataset_id,),
        )

    def test_annotate_without_prior_detect_still_persists(self, with_dataset):
        """peaks 表为空时 annotate 也必须落库(旧实现只 UPDATE,0 行)。"""
        engine, _p, _s, ds_id = with_dataset
        analysis = self._analysis(engine, ds_id)
        rows = engine.db.query_all(
            "SELECT id FROM peaks WHERE analysis_id = ?", (analysis["id"],)
        )
        assert not rows, "前提:尚未 detect"

        r = engine.handle("peaks.annotate", {"dataset_id": ds_id})
        assert r["ok"], r
        assert r["data"]["updated"] > 0, "空表时应走 INSERT 路径"
        stored = engine.handle(
            "peaks.list", {"analysis_id": analysis["id"]}
        )["data"]["peaks"]
        assert stored, "annotate 单独调用也必须写入 peaks 表"
        assert any(p["hkl"] for p in stored)

        # annotation_json 同步固化
        ann = engine._load_annotation(analysis["id"])
        assert ann is not None
        assert ann.get("labels") and ann.get("pipelineHash")

    def test_commit_keeps_annotation_json_but_deletes_peaks(self, with_dataset):
        engine, _p, _s, ds_id = with_dataset
        analysis = self._analysis(engine, ds_id)
        engine.handle("peaks.annotate", {"dataset_id": ds_id})
        before = engine._load_annotation(analysis["id"])
        assert before and before["labels"]

        new_pipeline = {"peaks": {"sensitivity": "high"}}
        r = engine.handle("analysis.commit", {
            "dataset_id": ds_id, "pipeline": new_pipeline,
        })
        assert r["ok"]
        peaks_after = engine.db.query_all(
            "SELECT id FROM peaks WHERE analysis_id = ?", (analysis["id"],)
        )
        assert not peaks_after, "参数变更后旧 peaks 行应删除(几何失效)"
        after = engine._load_annotation(analysis["id"])
        assert after == before, "annotation_json 必须原样保留"

    def test_state_restores_annotation_and_labels(self, with_dataset):
        engine, _p, _s, ds_id = with_dataset
        analysis = self._analysis(engine, ds_id)
        engine.handle("peaks.annotate", {"dataset_id": ds_id})
        # 模拟参数变更:peaks 行被删,annotation_json 仍在
        engine.handle("analysis.commit", {
            "dataset_id": ds_id, "pipeline": {"peaks": {"sensitivity": "high"}},
        })
        assert not engine.db.query_all(
            "SELECT id FROM peaks WHERE analysis_id = ?", (analysis["id"],)
        )

        r = engine.handle("analysis.state", {"dataset_id": ds_id})
        assert r["ok"], r
        data = r["data"]
        assert data["annotation"] is not None
        assert data["pipelineHash"], "state 必须回传当前 pipelineHash 供 stale 比对"
        labeled = [p for p in data["peaks"] if p.get("hkl")]
        assert labeled, "peaks 表空时应从 annotation_json.labels 合回标签"

    def test_state_stale_when_pipeline_changed(self, with_dataset):
        engine, _p, _s, ds_id = with_dataset
        engine.handle("peaks.annotate", {"dataset_id": ds_id})
        engine.handle("analysis.commit", {
            "dataset_id": ds_id, "pipeline": {"peaks": {"sensitivity": "high"}},
        })
        r = engine.handle("analysis.state", {"dataset_id": ds_id})
        ann = r["data"]["annotation"]
        assert ann["pipelineHash"] != r["data"]["pipelineHash"], (
            "参数变更后 annotation 的 pipelineHash 必须与当前不一致(→ stale)"
        )

    def test_preview_restore_annotation_merges_labels(self, with_dataset):
        engine, _p, _s, ds_id = with_dataset
        engine.handle("peaks.annotate", {"dataset_id": ds_id})
        # 改参数但不 commit:preview 用新 pipeline 出裸峰
        new_pipeline = {"peaks": {"sensitivity": "high"}}
        r = engine.handle("analysis.preview", {
            "dataset_id": ds_id, "pipeline": new_pipeline,
            "annotate": False, "restoreAnnotation": True,
        })
        assert r["ok"], r
        labeled = [p for p in r["data"]["peaks"] if p.get("hkl")]
        assert labeled, "restoreAnnotation=true 时应把 labels 快照合回 preview 峰"
        assert r["data"]["pipelineHash"]
        # 不 restore 则保持裸峰(mock/旧行为对照)
        r2 = engine.handle("analysis.preview", {
            "dataset_id": ds_id, "pipeline": new_pipeline, "annotate": False,
        })
        assert all(not p.get("hkl") for p in r2["data"]["peaks"])

    def test_detect_also_saves_annotation_json(self, with_dataset):
        engine, _p, _s, ds_id = with_dataset
        analysis = self._analysis(engine, ds_id)
        r = engine.handle("peaks.detect", {"dataset_id": ds_id})
        assert r["ok"]
        ann = engine._load_annotation(analysis["id"])
        assert ann is not None and ann.get("labels"), (
            "detect 是一次完整标注,annotation_json 必须同步写入"
        )


class TestWavelengthPropagation:
    """读取已存数据集时必须带上该样品的波长。

    两个读取路径(`datasets.curve` 与内部的 `_load_dataset_curve`)此前
    都只传 x_axis_type,波长静默退回默认的 Cu Kα。Co 靶数据会因此整体
    错位 —— 而且它是 x 轴换算(2θ↔d↔Q)与高镍归属的共同基准。
    """

    def test_curve_uses_sample_wavelength(self, engine, fixtures_dir):
        path = fixtures_dir / "lfp_500c_scan01.xy"
        if not path.exists():
            pytest.skip("fixture 未生成")
        pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
        r = engine.handle("datasets.import", {
            "paths": [str(path)], "project_id": pid,
            "new_sample": {"name": "Co-sample", "wavelength": 1.78892},
        })
        assert r["ok"], r
        ds_id = r["data"]["imported"][0]["id"]

        curve = engine.handle("datasets.curve", {"id": ds_id})
        assert curve["ok"], curve
        assert curve["data"]["wavelength"] == pytest.approx(1.78892), (
            "曲线载荷必须带出样品波长,不能退回默认 Cu Kα"
        )


class TestHighNiReadback:
    """domain_results 必须能读回 —— 此前它是只写表。"""

    def test_get_before_analyze(self, with_dataset):
        engine, _pid, _sid, dataset_id = with_dataset
        r = engine.handle("domain.high_ni.get", {"dataset_id": dataset_id})
        assert r["ok"], r
        assert r["data"]["found"] is False, "没算过应返回 found=false,而不是报错"

    def test_get_returns_stored_result(self, with_dataset):
        engine, _pid, _sid, dataset_id = with_dataset
        analyzed = engine.handle(
            "domain.high_ni.analyze", {"dataset_id": dataset_id, "fit": True}
        )
        assert analyzed["ok"], analyzed

        got = engine.handle("domain.high_ni.get", {"dataset_id": dataset_id})
        assert got["ok"], got
        assert got["data"]["found"] is True
        assert got["data"]["stale"] is False
        # 读回的必须是同一份,不能是重算的近似值
        assert (
            json.dumps(got["data"]["result"]["lattice"], sort_keys=True)
            == json.dumps(analyzed["data"]["lattice"], sort_keys=True)
        )

    def test_get_requires_an_identifier(self, engine):
        r = engine.handle("domain.high_ni.get", {})
        assert not r["ok"]
        assert "dataset_id" in r["error"] or "analysis_id" in r["error"]


class TestPresetValidation:
    def test_save_requires_existing_project(self, engine):
        r = engine.handle(
            "presets.save", {"project_id": 999, "name": "P", "pipeline": {}}
        )
        assert not r["ok"]
        # 必须是人话,不能是"引擎内部错误"(§68)
        assert "内部错误" not in r["error"]
        assert "Project" in r["error"]

    def test_save_requires_name(self, engine):
        pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
        r = engine.handle("presets.save", {"project_id": pid, "name": "   "})
        assert not r["ok"]
        assert "内部错误" not in r["error"]
        assert "名称" in r["error"]


class TestFigureExportProvenance:
    def test_figure_export_writes_sidecar_metadata(self, with_dataset, tmp_path):
        """PNG 导出也要带溯源头,否则一张图放进报告就说不清来源。"""
        engine, _pid, _sid, dataset_id = with_dataset
        png = base64.b64encode(
            b"\x89PNG\r\n\x1a\n" + b"fake-body"
        ).decode()
        target = tmp_path / "fig.png"
        r = engine.handle("export.figure", {
            "dataset_id": dataset_id,
            "png_base64": png,
            "path": str(target),
        })
        assert r["ok"], r
        assert target.exists()
        meta = r["data"].get("metadataPath")
        assert meta, "带 dataset_id 时必须写出溯源旁车文件"
        text = (tmp_path / "fig.png.txt").read_text(encoding="utf-8")
        assert "source_file_hash_sha256" in text
        assert "pipeline_json" in text

    def test_figure_export_without_dataset_still_works(self, engine, tmp_path):
        """没有 dataset_id 时不应阻断导出(将来的模板图不属于任何数据集)。"""
        png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x").decode()
        target = tmp_path / "bare.png"
        r = engine.handle("export.figure", {"png_base64": png, "path": str(target)})
        assert r["ok"], r
        assert target.exists()


class TestBatches:
    """批次分组(用户反馈:导入后每份数据自成一个文件夹,没法归到一起)。

    层级是 项目 > 实验(sample) > 批次(batch) > 数据(dataset)。
    批次是**可选**的第三层:不给 batch_id/batch_name 时不建批次,
    数据集直接挂样品 —— 不强迫用户维护一个他不需要的层级。
    """

    def test_import_into_existing_sample(self, engine, fixtures_dir):
        """**这是用户反馈的核心**:同一实验下能再导入别的数据。

        此前导入只能"新建样品",于是每导一次就多一个文件夹。
        """
        path = fixtures_dir / "lfp_500c_scan01.xy"
        path2 = fixtures_dir / "lfp_500c_scan02.xy"
        if not path.exists() or not path2.exists():
            pytest.skip("fixture 未生成")
        pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]

        first = engine.handle("datasets.import", {
            "paths": [str(path)], "project_id": pid,
            "new_sample": {"name": "LFP-500C"},
        })
        assert first["ok"], first
        sample_id = first["data"]["sampleId"]

        second = engine.handle("datasets.import", {
            "paths": [str(path2)], "project_id": pid, "sample_id": sample_id,
        })
        assert second["ok"], second
        assert second["data"]["sampleId"] == sample_id

        tree = engine.handle("library.tree", {})["data"]
        assert len(tree["samples"]) == 1, "复用样品时不该多出一个样品"
        assert len(tree["samples"][0]["datasets"]) == 2

    def test_import_creates_batch_by_name(self, engine, fixtures_dir):
        path = fixtures_dir / "lfp_500c_scan01.xy"
        path2 = fixtures_dir / "lfp_500c_scan02.xy"
        if not path.exists() or not path2.exists():
            pytest.skip("fixture 未生成")
        pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
        sid = engine.handle("datasets.import", {
            "paths": [str(path)], "project_id": pid,
            "new_sample": {"name": "S"}, "batch_name": "A",
        })["data"]["sampleId"]

        r = engine.handle("datasets.import", {
            "paths": [str(path2)], "project_id": pid,
            "sample_id": sid, "batch_name": "A",
        })
        assert r["ok"], r
        batches = engine.db.query_all("SELECT id, name FROM batches")
        assert len(batches) == 1

        tree = engine.handle("library.tree", {})["data"]
        assert len(tree["samples"][0]["batches"]) == 1
        assert len(tree["samples"][0]["batches"][0]["datasets"]) == 2

    def test_import_without_batch_leaves_batch_null(self, engine, fixtures_dir):
        """不给批次时不建 —— 不强迫用户维护不需要的层级。"""
        path = fixtures_dir / "lfp_500c_scan01.xy"
        if not path.exists():
            pytest.skip("fixture 未生成")
        pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
        r = engine.handle("datasets.import", {
            "paths": [str(path)], "project_id": pid,
            "new_sample": {"name": "S"},
        })
        assert r["ok"], r
        assert r["data"]["batchId"] is None
        assert engine.db.query_all("SELECT id FROM batches") == []

    def test_import_rejects_unknown_batch(self, engine, fixtures_dir):
        path = fixtures_dir / "lfp_500c_scan01.xy"
        if not path.exists():
            pytest.skip("fixture 未生成")
        pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
        r = engine.handle("datasets.import", {
            "paths": [str(path)], "project_id": pid,
            "new_sample": {"name": "S"}, "batch_id": 999,
        })
        assert not r["ok"]
        assert "批次" in r["error"]

    def test_batch_rename(self, engine, fixtures_dir):
        """批次也要能重命名 —— 四层都要支持(用户明确要求)。"""
        path = fixtures_dir / "lfp_500c_scan01.xy"
        if not path.exists():
            pytest.skip("fixture 未生成")
        pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
        bid = engine.handle("datasets.import", {
            "paths": [str(path)], "project_id": pid,
            "new_sample": {"name": "S"}, "batch_name": "old",
        })["data"]["batchId"]
        r = engine.handle("meta.rename", {
            "table": "batches", "id": bid, "name": "new",
        })
        assert r["ok"], r
        assert engine.db.query_one(
            "SELECT name FROM batches WHERE id = ?", (bid,)
        )["name"] == "new"

    def test_batch_soft_delete_and_restore(self, engine, fixtures_dir):
        path = fixtures_dir / "lfp_500c_scan01.xy"
        if not path.exists():
            pytest.skip("fixture 未生成")
        pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
        bid = engine.handle("datasets.import", {
            "paths": [str(path)], "project_id": pid,
            "new_sample": {"name": "S"}, "batch_name": "B",
        })["data"]["batchId"]

        d = engine.handle("meta.soft_delete", {"table": "batches", "id": bid})
        assert d["ok"], d
        tree = engine.handle("library.tree", {})["data"]
        assert tree["samples"][0]["batches"] == []

        r = engine.handle("meta.restore", {"table": "batches", "id": bid})
        assert r["ok"], r
        tree = engine.handle("library.tree", {})["data"]
        assert len(tree["samples"][0]["batches"]) == 1

    def test_batch_in_trash_list(self, engine, fixtures_dir):
        path = fixtures_dir / "lfp_500c_scan01.xy"
        if not path.exists():
            pytest.skip("fixture 未生成")
        pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
        bid = engine.handle("datasets.import", {
            "paths": [str(path)], "project_id": pid,
            "new_sample": {"name": "S"}, "batch_name": "B",
        })["data"]["batchId"]
        engine.handle("meta.soft_delete", {"table": "batches", "id": bid})
        items = engine.handle("trash.list", {})["data"]["items"]
        assert any(i["kind"] == "batches" and i["id"] == bid for i in items)

    def test_purge_batch_removes_it(self, engine, fixtures_dir):
        path = fixtures_dir / "lfp_500c_scan01.xy"
        if not path.exists():
            pytest.skip("fixture 未生成")
        pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
        bid = engine.handle("datasets.import", {
            "paths": [str(path)], "project_id": pid,
            "new_sample": {"name": "S"}, "batch_name": "B",
        })["data"]["batchId"]
        engine.handle("meta.soft_delete", {"table": "batches", "id": bid})
        r = engine.handle("meta.purge", {"table": "batches", "id": bid})
        assert r["ok"], r
        assert engine.db.query_all("SELECT id FROM batches") == []


class TestFolderScan:
    """扫描文件夹 -> 建议分组。只做建议,不落库。"""

    def test_scan_groups_user_filenames(self, engine, tmp_path):
        for name in [
            "GENBB-1-14-20260915-125048.txt",
            "GENBB-1-15-20260915-130209.txt",
            "GENBB-2-06-20260915-160058.txt",
        ]:
            (tmp_path / name).write_text('10 100\n20 200\n', encoding="utf-8")
        r = engine.handle("datasets.scan_folder", {"folder": str(tmp_path)})
        assert r["ok"], r
        groups = {g["name"]: g["paths"] for g in r["data"]["groups"]}
        assert sorted(groups) == ["GENBB-1", "GENBB-2"]
        assert len(groups["GENBB-1"]) == 2

    def test_scan_does_not_import(self, engine, tmp_path):
        """扫描必须只读 —— 确认步骤不能省。"""
        (tmp_path / "a-1.txt").write_text('10 100\n', encoding="utf-8")
        engine.handle("projects.create", {"name": "P"})
        engine.handle("datasets.scan_folder", {"folder": str(tmp_path)})
        assert engine.db.query_all("SELECT id FROM datasets") == []

    def test_scan_empty_folder_is_a_clear_error(self, engine, tmp_path):
        r = engine.handle("datasets.scan_folder", {"folder": str(tmp_path)})
        assert not r["ok"]
        assert "没有可导入" in r["error"]
        assert "内部错误" not in r["error"]

    def test_scan_missing_folder(self, engine, tmp_path):
        r = engine.handle("datasets.scan_folder", {"folder": str(tmp_path / "no")})
        assert not r["ok"]
        assert "内部错误" not in r["error"]

    def test_scan_requires_folder(self, engine):
        r = engine.handle("datasets.scan_folder", {})
        assert not r["ok"]
        assert "文件夹" in r["error"]


class TestSampleMetadata:
    """实验元数据的创建与编辑(docs/DEVELOPMENT_HIGH_NI.md §11.4)。

    温度/时长/材料/批次是结构趋势图的自变量。此前这些列**只有导入时能写,
    之后完全无法修改**,而导入弹窗又只让填名字 —— 用户即使想填也无处可填,
    趋势图因而永远只能按导入顺序排。
    """

    def test_create_experiment_without_import(self, engine):
        """先建实验再导数据 —— 此前做不到,样品只能作为导入的副作用产生。"""
        pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
        r = engine.handle("samples.create", {
            "project_id": pid, "name": "GENBB-1", "temperature": "750",
        })
        assert r["ok"], r
        tree = engine.handle("library.tree", {})["data"]
        assert len(tree["samples"]) == 1
        assert tree["samples"][0]["name"] == "GENBB-1"
        assert tree["samples"][0]["temperature"] == "750"
        assert tree["samples"][0]["datasets"] == []

    def test_create_requires_project_and_name(self, engine):
        assert not engine.handle("samples.create", {"project_id": 1, "name": "X"})["ok"]
        pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
        r = engine.handle("samples.create", {"project_id": pid, "name": "  "})
        assert not r["ok"]
        assert "名称" in r["error"] and "内部错误" not in r["error"]

    def test_update_sample_metadata(self, with_dataset):
        engine, _pid, sample_id, _ds = with_dataset
        r = engine.handle("samples.update", {
            "id": sample_id, "temperature": "800", "duration": "12h",
            "material": "NCM811", "batch": "B1", "notes": "试烧",
        })
        assert r["ok"], r
        assert r["data"]["updated"] == 1
        tree = engine.handle("library.tree", {})["data"]
        s = tree["samples"][0]
        assert s["temperature"] == "800"
        assert s["duration"] == "12h"
        assert s["material"] == "NCM811"
        assert s["batch"] == "B1"
        assert s["notes"] == "试烧"

    def test_update_only_touches_given_fields(self, with_dataset):
        """只改温度不应把波长一起清掉。"""
        engine, _pid, sample_id, _ds = with_dataset
        before = engine.db.query_one(
            "SELECT wavelength FROM samples WHERE id = ?", (sample_id,)
        )["wavelength"]
        engine.handle("samples.update", {"id": sample_id, "temperature": "900"})
        after = engine.db.query_one(
            "SELECT wavelength FROM samples WHERE id = ?", (sample_id,)
        )["wavelength"]
        assert after == before

    def test_update_rejects_bad_wavelength(self, with_dataset):
        engine, _pid, sample_id, _ds = with_dataset
        r = engine.handle("samples.update", {"id": sample_id, "wavelength": "abc"})
        assert not r["ok"]
        assert "数字" in r["error"] and "内部错误" not in r["error"]
        r = engine.handle("samples.update", {"id": sample_id, "wavelength": 99})
        assert not r["ok"]
        assert "范围" in r["error"]

    def test_update_unknown_sample(self, engine):
        r = engine.handle("samples.update", {"id": 999, "temperature": "1"})
        assert not r["ok"]
        assert "不存在" in r["error"] and "内部错误" not in r["error"]

    def test_update_with_no_fields_is_a_noop(self, with_dataset):
        engine, _pid, sample_id, _ds = with_dataset
        r = engine.handle("samples.update", {"id": sample_id})
        assert r["ok"] and r["data"]["updated"] == 0

    def test_tree_exposes_metadata_for_trend_chart(self, with_dataset):
        """library.tree 必须带出趋势图要用的字段。

        此前只返回 id/name/temperature/duration —— 前端拿不到 material
        与 wavelength,用户填了也看不到。
        """
        engine, _pid, _sid, _ds = with_dataset
        s = engine.handle("library.tree", {})["data"]["samples"][0]
        for key in ("material", "batch", "wavelength", "notes", "batches"):
            assert key in s, f"library.tree 缺少 {key}"


class TestTrendSeries:
    """结构趋势命令(§11.4)。指标只从已存结果读,不重算。"""

    def test_trend_lists_datasets_without_metrics(self, engine, fixtures_dir):
        path = fixtures_dir / "lfp_500c_scan01.xy"
        if not path.exists():
            pytest.skip("fixture 未生成")
        pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
        engine.handle("datasets.import", {
            "paths": [str(path)], "project_id": pid,
            "new_sample": {"name": "S1", "temperature": "700"},
        })
        r = engine.handle("trend.series", {"project_id": pid})
        assert r["ok"], r
        pts = r["data"]["points"]
        assert len(pts) == 1
        # 没算过 -> value 为 None,而不是 0
        assert pts[0]["value"] is None

    def test_trend_reads_stored_metrics(self, engine, fixtures_dir):
        """算过之后趋势点要能取到值,且 a/c 来自全谱精修那条路径。"""
        path = fixtures_dir / "ncm811_01.xy"
        if not path.exists():
            pytest.skip("fixture 未生成")
        pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
        ds_id = engine.handle("datasets.import", {
            "paths": [str(path)], "project_id": pid,
            "new_sample": {"name": "S1", "temperature": "750"},
        })["data"]["imported"][0]["id"]

        an = engine.handle("domain.high_ni.analyze", {"dataset_id": ds_id, "fit": True})
        assert an["ok"], an
        assert "fullCell" in an["data"], "分析结果必须带全谱精修的晶胞"
        assert an["data"]["fullCell"]["source"] == "refine_cell"

        r = engine.handle("trend.series", {"project_id": pid, "metric": "a"})
        assert r["ok"], r
        pt = r["data"]["points"][0]
        assert pt["value"] is not None
        assert pt["source"] == "refine_cell"
        # 与直接返回的值一致(读的是同一份存储结果)
        assert abs(pt["value"] - an["data"]["fullCell"]["a"]) < 1e-9

    def test_trend_sorts_by_temperature(self, engine, fixtures_dir):
        path = fixtures_dir / "lfp_500c_scan01.xy"
        if not path.exists():
            pytest.skip("fixture 未生成")
        pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
        for temp in ("800", "700"):
            engine.handle("datasets.import", {
                "paths": [str(path)], "project_id": pid,
                "new_sample": {"name": f"S{temp}", "temperature": temp},
                "import_anyway": True,
            })
        r = engine.handle("trend.series", {"project_id": pid})
        temps = [p["xLabel"] for p in r["data"]["points"]]
        assert temps == ["700", "800"]

    def test_trend_rejects_unknown_metric(self, engine):
        pid = engine.handle("projects.create", {"name": "P"})["data"]["projectId"]
        r = engine.handle("trend.series", {"project_id": pid, "metric": "bogus"})
        assert not r["ok"]
        assert "内部错误" not in r["error"]

    def test_trend_rejects_missing_project(self, engine):
        r = engine.handle("trend.series", {"project_id": 999})
        assert not r["ok"]
        assert "Project" in r["error"]
