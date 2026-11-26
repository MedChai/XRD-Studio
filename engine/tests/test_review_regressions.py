"""End-to-end regressions for import, analysis provenance and scientific units."""

import csv
from pathlib import Path

import numpy as np
import pytest

from xrd_engine.api.commands import Engine
from xrd_engine.coords import convert
from xrd_engine.high_ni.profiles import default_profile
from xrd_engine.importers.loader import load_curve


@pytest.fixture
def api(tmp_path):
    engine = Engine(tmp_path / "store")

    def call(command, **payload):
        response = engine.handle(command, payload)
        assert response["ok"], response
        return response["data"]

    yield call
    engine.close()


def test_plain_lfp_never_receives_layered_labels(api, fixtures_dir):
    project = api("projects.create", name="P")["projectId"]
    data = api("datasets.import", paths=[str(fixtures_dir / "lfp_500c_scan01.xy")],
               project_id=project, new_sample={"name": "LFP"})
    ds = data["imported"][0]["id"]
    for cmd in ("analysis.preview", "peaks.detect", "analysis.state"):
        result = api(cmd, dataset_id=ds)
        assert result["annotation"] is None
        assert result["peaks"] and all(p["hkl"] is None for p in result["peaks"])


def test_preset_export_matches_crop(api, fixtures_dir):
    project = api("projects.create", name="P")["projectId"]
    ds = api("datasets.import", paths=[str(fixtures_dir / "lfp_500c_scan01.xy")],
             project_id=project, new_sample={"name": "S"})["imported"][0]["id"]
    assert any(p["position"] < 30 for p in api("peaks.detect", dataset_id=ds)["peaks"])
    api("domain.high_ni.analyze", dataset_id=ds)
    preset = api("presets.save", project_id=project, name="crop",
                 pipeline={"crop": {"enabled": True, "x_min": 30, "x_max": 40}})
    api("presets.apply", preset_id=preset["presetId"], dataset_ids=[ds])
    assert not api("domain.high_ni.get", dataset_id=ds)["found"]
    assert not api("analysis.state", dataset_id=ds)["peaksStored"]
    exported = api("export.peaks", dataset_id=ds)
    with Path(exported["path"]).open(encoding="utf-8-sig") as stream:
        peaks = list(csv.DictReader(line for line in stream if not line.startswith("#")))
    assert peaks and all(30 <= float(row["position"]) <= 40 for row in peaks)


def test_acquisition_wavelength_and_inherited_results(api, fixtures_dir):
    project = api("projects.create", name="P")["projectId"]
    a = api("datasets.import", paths=[str(fixtures_dir / "ncm811_01.xy")],
            new_sample={"name": "S"}, project_id=project)
    first, sample = a["imported"][0]["id"], a["sampleId"]
    second = api("datasets.import", paths=[str(fixtures_dir / "cobalt_free_01.xy")],
                 sample_id=sample, project_id=project, wavelength=1.789)["imported"][0]["id"]
    api("domain.high_ni.analyze", dataset_id=first)
    api("peaks.annotate", dataset_id=first)
    api("samples.update", id=sample, wavelength=1.6)
    assert api("datasets.curve", id=first)["wavelength"] == 1.6
    assert api("datasets.curve", id=second)["wavelength"] == 1.789
    assert api("domain.high_ni.get", dataset_id=first)["stale"]
    assert api("analysis.state", dataset_id=first)["annotation"] is None
    assert all(p["value"] is None for p in api("trend.series", project_id=project)["points"])
    exported = api("export.data", dataset_id=second)
    assert "# wavelength_angstrom: 1.789" in Path(exported["path"]).read_text(encoding="utf-8-sig")


def test_layered_annotation_in_native_coordinates(api, fixtures_dir, tmp_path):
    project = api("projects.create", name="P")["projectId"]
    curve = load_curve(fixtures_dir / "ncm811_01.xy")
    for axis in ("two_theta", "d", "q"):
        x = convert(curve.x, "two_theta", axis, curve.wavelength)
        path = tmp_path / (axis + ".xy")
        np.savetxt(path, np.column_stack((x, curve.y)))
        ds = api("datasets.import", paths=[str(path)], project_id=project,
                 x_axis_type=axis, new_sample={"name": axis})["imported"][0]["id"]
        annotated = api("peaks.annotate", dataset_id=ds)
        assert annotated["annotation"] is not None
        state = api("analysis.state", dataset_id=ds)
        peaks = {pk["hkl"]: pk["position"] for pk in state["peaks"] if pk["hkl"]}
        for label, degrees in (("(003)", 18.7), ("(104)", 44.5)):
            assert label in peaks, (axis, peaks)
            angle = convert([peaks[label]], axis, "two_theta", curve.wavelength)[0]
            assert abs(angle - degrees) < 0.5


def test_co_reflection_is_inside_co_search_window():
    window = next(w for w in default_profile(1.789).resolved_windows() if w.key == "003")
    assert abs(window.center_2theta - 21.62) < window.half_width
    assert abs(window.center_2theta - 18.60) > window.half_width

def test_v7_column_recovery_keeps_dataset_id(api, tmp_path):
    import sqlite3

    project = api("projects.create", name="P")["projectId"]
    path = tmp_path / "wide.txt"
    x = np.linspace(10, 80, 200)
    np.savetxt(path, np.column_stack([x, x % 3, x % 7, *([x] * 7)]))
    info = api("datasets.import", project_id=project, new_sample={"name": "S"},
               paths=[str(path)], x_column=0, y_column=2)
    ds = info["imported"][0]["id"]
    dbfile = next((tmp_path / "store").rglob("*.db"))
    with sqlite3.connect(dbfile) as conn:
        conn.execute("UPDATE datasets SET x_column=NULL, y_column=NULL WHERE id=?", (ds,))
    again = api("datasets.import", project_id=project, sample_id=info["sampleId"], paths=[str(path)],
                x_column=0, y_column=2)
    assert again["imported"] == info["imported"]
    assert api("datasets.curve", id=ds)["pointCount"] == 200
