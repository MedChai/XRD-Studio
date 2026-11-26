"""Regression checks for the V7 native hierarchy and the CEX sample-library workflow."""

import csv
import json
import os
import shutil
from pathlib import Path
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook

from xrd_engine.api.commands import Engine
from xrd_engine.electrochem.cex_native import CexError
from xrd_engine.electrochem.cex_viewer import DRAG_POINTS, cycle_indices
from xrd_engine.services.data_store import sha256_of_file


def test_cex_cycle_selection_accepts_strides_and_reverse_positions():
    assert cycle_indices("1-5s2,r2，3", 10) == [0, 2, 4, 8]
    assert cycle_indices("r1-r5s2", 10) == [9, 7, 5]
    assert cycle_indices("", 0) == []
    with pytest.raises(CexError, match="超出"):
        cycle_indices("r2", 1)
    with pytest.raises(CexError, match="步长"):
        cycle_indices("1-5s0", 10)


@pytest.fixture
def official_samples():
    samples = Path(os.environ.get("XRD_LAND_SAMPLE_DIR") or r"D:\LAND\Samples")
    dll = Path(os.environ.get("XRD_LAND_DLL") or r"D:\LAND\LANDCex_x64.dll")
    required = ("_010_5.cex", "_079_2.cex", "_079_8.cex", "循环套叠.cex")
    if os.name != "nt" or not dll.is_file() or not all((samples / f).is_file() for f in required):
        pytest.skip("requires the installed LAND V7 DLL and its official samples")
    return samples


def command(engine, action, **payload):
    result = engine.handle(action, payload)
    assert result["ok"], (action, result)
    return result["data"]


def test_cex_folder_scans_one_level_and_stays_in_selected_root(tmp_path):
    root = tmp_path / "原始蓝电"
    nested = root / "第一批"
    nested.mkdir(parents=True)
    (root / "B.cex").touch()
    (root / "a.CEX").touch()
    (root / "忽略.xlsx").touch()
    (nested / "样例.cex").touch()
    engine = Engine(tmp_path / "library")
    try:
        listing = command(engine, "cex.scan_folder", root=str(root))
        assert [(row["name"], row["kind"]) for row in listing["entries"]] == [
            ("第一批", "folder"), ("a.CEX", "cex"), ("B.cex", "cex"),
        ]
        folder = listing["entries"][0]["path"]
        assert folder == str(nested.resolve())
        child = command(engine, "cex.scan_folder", root=str(root), folder=folder)
        assert [row["name"] for row in child["entries"]] == ["样例.cex"]
        result = engine.handle("cex.scan_folder", {"root": str(root), "folder": str(root / "..")})
        assert not result["ok"] and "不在选定" in result["error"]
    finally:
        engine.close()


def test_cex_library_hides_legacy_imports_and_exports_their_history(tmp_path):
    engine = Engine(tmp_path / "library")
    try:
        project = command(engine, "projects.create", name="P")["projectId"]
        sample = engine.db.insert("INSERT INTO samples(project_id, name) VALUES (?, 'S')", (project,))
        legacy = engine.store.abs_path("data/legacy/land.xlsx")
        legacy.parent.mkdir()
        legacy.write_bytes(b"original workbook")
        legacy.with_name("dqdv_test.npz").write_bytes(b"derived curve")
        for name, kind, stored in [("Old", "xlsx", "data/legacy/land.xlsx"), ("New", "cex", "data/file.cex")]:
            engine.db.insert(
                """INSERT INTO ec_cells(sample_id, name, original_filename, stored_path, file_hash, format)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (sample, name, Path(stored).name, stored, kind, kind),
            )
        engine.db.insert("INSERT INTO ec_analyses(cell_id, kind, result_json) VALUES (1, 'dqdv', '{\"peaks\": []}')")
        listing = command(engine, "cex.cells", sample_id=sample)
        assert [cell["name"] for cell in listing["cells"]] == ["New"]
        assert listing["historyCount"] == 1
        assert not engine.handle("ec.import", {"paths": [str(legacy)]})["ok"]
        backup = tmp_path / "history.zip"
        out = command(engine, "cex.backup_legacy", sample_id=sample, path=str(backup))
        assert out["count"] == 1 and out["missing"] == 0
        with ZipFile(backup) as archive:
            entries = json.loads(archive.read("manifest.json"))
            assert entries[0]["analyses"][0]["kind"] == "dqdv"
            assert archive.read("cell-1/source/land.xlsx") == b"original workbook"
            assert archive.read("cell-1/cache/dqdv_test.npz") == b"derived curve"
        assert legacy.read_bytes() == b"original workbook"
    finally:
        engine.close()


def test_cex_dqdv_csv_keeps_numeric_discharge_values(tmp_path):
    engine = Engine(tmp_path / "library")
    try:
        target = tmp_path / "curves.csv"
        out = command(engine, "cex.dqdv_export", path=str(target),
                      columns=["source", "dQ/dV"], rows=[["=1+1.cex", -2.5]])
        assert out["rows"] == 1
        with target.open(encoding="utf-8-sig", newline="") as stream:
            assert list(csv.reader(stream)) == [["source", "dQ/dV"], ["'=1+1.cex", "-2.5"]]
    finally:
        engine.close()


def test_cex_native_hierarchy_and_cross_file_comparison(tmp_path, official_samples):
    engine = Engine(tmp_path / "library")
    try:
        sample = command(engine, "cex.open", path=str(official_samples / "_010_5.cex"))
        assert sample["counts"] == {"cycles": 60, "steps": 291, "records": 60405}
        assert sample["channel"] == "010_5"
        full = command(engine, "cex.plot", id=sample["id"], kind="time", first=1, last=60)
        records = [p[2] for p in full["series"][0]["points"]]
        assert (records[0], records[-1]) == (1, 60405)
        selection = {"id": sample["id"], "kind": "time", "first": 11, "last": 25}
        exact = command(engine, "cex.plot", **selection)["series"]
        quick = command(engine, "cex.plot", **selection, preview=True)["series"]
        assert 2 <= len(quick[0]["points"]) <= DRAG_POINTS < len(exact[0]["points"])
        assert [p[2] for p in quick[0]["points"]] == [p[2] for p in quick[1]["points"]]
        for preview_axis, exact_axis in zip(quick, exact, strict=True):
            assert preview_axis["points"][0] == exact_axis["points"][0]
            assert preview_axis["points"][-1] == exact_axis["points"][-1]
        p = command(engine, "cex.page", id=sample["id"], level="records", count=3)
        assert p["rows"][0]["voltage"] == pytest.approx(4.140625)
        assert p["rows"][0]["current"] == pytest.approx(-1400.22314453125)
        assert p["rows"][0]["specificCapacity"] is None
        assert p["rows"][1]["time"] == 11
        location = command(engine, "cex.locate", id=sample["id"], number=451)
        assert (location["cycle"], location["step"]["number"]) == (2, 3)
        nested = command(engine, "cex.open", path=str(official_samples / "循环套叠.cex"))
        assert nested["counts"] == {"cycles": 20, "steps": 44, "records": 130}
        for kind in ("time", "voltageCapacity", "cycles", "specificCycles", "steps"):
            plot = command(engine, "cex.plot", id=sample["id"], kind=kind, first=1, last=3)
            assert plot["series"]
            preview = command(engine, "cex.plot", id=sample["id"], kind=kind, first=1, last=3, preview=True)
            assert [series["name"] for series in preview["series"]] == [series["name"] for series in plot["series"]]
            if kind == "time":
                voltage, current = plot["series"]
                assert [p[2] for p in voltage["points"]] == [p[2] for p in current["points"]]
        pair = [
            command(engine, "cex.open", path=str(official_samples / name))
            for name in ("_079_2.cex", "_079_8.cex")
        ]
        comp = command(
            engine, "cex.compare", sources=[{"id": doc["id"]} for doc in pair],
            cycles="r2", ignoreRest=True,
        )
        assert [row["number"] for row in comp["rows"]] == [6, 6]
        assert comp["rows"][0]["qDischarge"] == pytest.approx(568.33349609375)
        assert comp["rows"][1]["sqDischarge"] is None
        assert len(comp["series"]) == 4
    finally:
        engine.close()


def test_cex_dqdv_uses_only_constant_current_and_links_to_records(tmp_path, official_samples):
    engine = Engine(tmp_path / "library")
    try:
        doc = command(engine, "cex.open", path=str(official_samples / "_010_5.cex"))
        response = command(engine, "cex.dqdv", id=doc["id"], cycles="1,2", offset=1)
        assert response["total"] == 2
        assert response["sha256"] == sha256_of_file(official_samples / "_010_5.cex")
        cycle, = response["results"]
        assert cycle["cycle"] == 2 and cycle["paired"]
        assert cycle["cvCapacityMah"] == pytest.approx(216.994, abs=0.01)
        assert {curve["step"] for curve in cycle["curves"]} == {3, 6}
        for curve in cycle["curves"]:
            polarity = 1 if curve["direction"] == "charge" else -1
            assert 0.002 * polarity * sum(curve["values"]) == pytest.approx(curve["capacityMah"])
            source = command(engine, "cex.dqdv_source", id=doc["id"], record=curve["records"][0])
            assert source["step"]["number"] == curve["step"] and source["points"]
        path = tmp_path / "curves.csv"
        command(engine, "cex.dqdv_export_curves", direction="discharge", path=str(path),
                sources=[{"id": doc["id"], "cycles": [2], "sha256": response["sha256"]}])
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        discharge = [curve for curve in cycle["curves"] if curve["direction"] == "discharge"]
        assert len(rows) == sum(len(curve["values"]) for curve in discharge)
        assert rows[0]["source_record"] == str(discharge[0]["records"][0])
        assert float(rows[0]["dQdV"]) == pytest.approx(discharge[0]["values"][0])
        assert all(row["source_sha256"] == response["sha256"] for row in rows)
        specific = tmp_path / "specific.csv"
        command(engine, "cex.dqdv_export_curves", direction="discharge", path=str(specific), normalized=True,
                sources=[{"id": doc["id"], "cycles": [2], "sha256": response["sha256"], "massOverrideMg": 5}])
        with specific.open(encoding="utf-8-sig", newline="") as stream:
            scaled = next(csv.DictReader(stream))
        assert scaled["mass_source"] == "manual" and scaled["unit"] == "mAh/(g·V)"
        assert float(scaled["dQdV"]) == pytest.approx(200 * float(rows[0]["dQdV"]))
        sparse = command(engine, "cex.open", path=str(official_samples / "循环套叠.cex"))
        result, = command(engine, "cex.dqdv", id=sparse["id"], cycles="2")["results"]
        assert not result["curves"]
        assert any("记录不足" in issue for issue in result["issues"])
    finally:
        engine.close()


def test_cex_archival_persists_source_and_exports_selected_records(tmp_path, official_samples):
    source = tmp_path / "样例🔋.cex"
    shutil.copyfile(official_samples / "_010_5.cex", source)
    digest = sha256_of_file(source)
    root = tmp_path / "library"
    engine = Engine(root)
    try:
        project = command(engine, "projects.create", name="P")["projectId"]
        sample = engine.db.insert("INSERT INTO samples(project_id,name) VALUES (?,?)", (project, "S1"))
        other = engine.db.insert("INSERT INTO samples(project_id,name) VALUES (?,?)", (project, "S2"))
        doc = command(engine, "cex.open", path=str(source))
        args = {"ids": [doc["id"]], "sample_id": sample}
        invalid = command(engine, "cex.archive", **args, names={doc["id"]: "无效/名称"})
        assert not invalid["imported"] and "Windows 文件名" in invalid["failures"][0]["reason"]
        archived = command(engine, "cex.archive", **args, names={doc["id"]: "0.2C 循环.cex"})
        link = archived["imported"][0]
        cell = link["cellId"]
        assert link["name"] == "0.2C 循环"
        duplicate = command(engine, "cex.archive", ids=[doc["id"]], sample_id=other)
        assert duplicate["duplicates"][0]["sampleId"] == sample
        counts = {row["id"]: row["electrochem_count"] for row in engine.db.get_library_tree()}
        assert (counts[sample], counts[other]) == (1, 0)
        locator = command(engine, "meta.file_path", table="ec_cells", id=cell, kind="stored")
        stored = Path(locator["path"])
        assert stored.relative_to(root.resolve()) == Path("projects", f"{project}-P", f"{sample}-S1", "蓝电数据", "0.2C 循环.cex")
        assert sha256_of_file(stored) == sha256_of_file(source) == digest
        source.unlink()
    finally:
        engine.close()

    reopened = Engine(root)
    try:
        doc = command(reopened, "cex.open", cell_id=cell)
        assert (doc["filename"], doc["sampleId"]) == ("样例🔋.cex", sample)
        assert doc["name"] == "0.2C 循环"
        for fmt in ("xlsx", "csv", "txt"):
            result = command(
                reopened, "cex.export", id=doc["id"], level="records", cycles="1",
                columns=["number", "voltage"], format=fmt,
            )
            assert result["rows"] == 450
            if fmt == "xlsx":
                book = load_workbook(result["path"], read_only=True)
                try:
                    assert list(book.active.values)[1] == (1, 4.140625)
                finally:
                    book.close()
            else:
                with open(result["path"], encoding="utf-8-sig", newline="") as stream:
                    rows = list(csv.reader(stream, delimiter="," if fmt == "csv" else "\t"))
                assert rows[1] == ["1", "4.140625"]
                assert rows[-1][0] == "450"
    finally:
        reopened.close()


def test_cex_same_name_and_purge_preserve_sibling_archives(tmp_path, official_samples):
    engine = Engine(tmp_path / "library")
    try:
        project = command(engine, "projects.create", name="项目")["projectId"]
        sample = command(engine, "samples.create", project_id=project, name="样品")["sampleId"]

        def archive(filename):
            doc = command(engine, "cex.open", path=str(official_samples / filename))
            link = command(engine, "cex.archive", ids=[doc["id"]], sample_id=sample,
                           names={doc["id"]: "循环"})["imported"][0]
            file = command(engine, "meta.file_path", table="ec_cells", id=link["cellId"], kind="stored")
            return link["cellId"], Path(file["path"])

        first_id, first = archive("_079_2.cex")
        command(engine, "meta.rename", table="samples", id=sample, name="新样品名称")
        second_id, second = archive("_079_8.cex")
        assert first.parent == second.parent
        assert (first.name, second.name) == ("循环.cex", "循环 (2).cex")

        source = official_samples / "循环套叠.cex"
        legacy_path, legacy_hash = engine.store.import_file(source, {})
        legacy = engine.db.insert(
            """INSERT INTO ec_cells (sample_id, name, original_filename, source_path, stored_path, file_hash, format)
               VALUES (?, ?, ?, ?, ?, ?, 'cex')""", (sample, source.stem, source.name, str(source), legacy_path, legacy_hash),
        )
        assert command(engine, "cex.open", cell_id=legacy)["counts"]["cycles"] == 20

        command(engine, "meta.soft_delete", table="ec_cells", id=first_id)
        assert first.exists()
        command(engine, "meta.restore", table="ec_cells", id=first_id)
        assert command(engine, "cex.open", cell_id=first_id)["cellId"] == first_id
        command(engine, "meta.soft_delete", table="ec_cells", id=first_id)
        assert command(engine, "meta.purge", table="ec_cells", id=first_id)["removedFiles"] == 1
        assert not first.exists() and second.exists()
        assert command(engine, "cex.open", cell_id=second_id)["name"] == "循环 (2)"
        command(engine, "meta.soft_delete", table="projects", id=project)
        assert command(engine, "meta.purge", all=True)["removedFiles"] == 2
        assert not second.exists() and not engine.store.abs_path(legacy_path).exists()
        assert source.exists() and (official_samples / "_079_2.cex").exists()
    finally:
        engine.close()
