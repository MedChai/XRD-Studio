"""CEX viewing sessions, native-curve queries and sample-library archival."""

from __future__ import annotations

import csv
import json
import math
import os
import re
import shutil
from collections import OrderedDict
from dataclasses import dataclass
from itertools import chain, pairwise
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np

from ..database.db import Database
from ..processing.downsample import select_minmax_indices
from ..services.data_store import DataStore, sha256_of_file
from .cex_dqdv import ALGORITHM, DqdvSettings, calculate_cycle
from .cex_native import (
    CYCLE_COLUMNS,
    RECORD_COLUMNS,
    REST_MODE,
    STEP_COLUMNS,
    CexError,
    CexFile,
    LandCex,
)

EXPORT_COLUMNS = {
    "cycles": {"number": "循环序号", **{key: c.heading for key, c in CYCLE_COLUMNS.items()}},
    "steps": {
        "number": "工步序号", "cycle": "循环序号", "mode": "工步模式",
        **{key: c.heading for key, c in STEP_COLUMNS.items()},
    },
    "records": {"number": "记录序号", **{key: c.heading for key, c in RECORD_COLUMNS.items()}},
}
CYCLE_TERM = re.compile(r"(r?\d+)(?:\s*-\s*(r?\d+))?(?:\s*s(\d+))?", re.IGNORECASE)
XL_DATA_ROWS = 1_048_575
DISPLAY_POINTS = 4000
DRAG_POINTS = 600
DQDV_COLUMNS = [
    "sample", "filename", "source_sha256", "channel", "cycle", "direction", "step", "segment",
    "record_first", "record_last", "current_mA", "sample_interval_mV", "charge_CV_mAh",
    "capacity_source", "mass_mg", "mass_source", "has_both_directions", "algorithm", "voltage_grid_mV",
    "smoothing_FWHM_mV", "unit", "voltage_V", "dQdV", "unsmoothed_dQdV", "source_record", "discharge_sign",
]


def cycle_indices(expression: str, size: int) -> list[int]:
    """Select zero-based native cycles by number, range, stride or reverse position."""
    expr = expression.strip().lower().replace("，", ",")
    if not expr or expr == "全部":
        return list(range(size))

    def index(text: str) -> int:
        number = int(text.removeprefix("r"))
        if not 1 <= number <= size:
            raise CexError(f"循环 {text} 超出文件范围（共 {size} 圈）")
        return size - number if text.startswith("r") else number - 1

    result = {}
    for term in expr.split(","):
        match = CYCLE_TERM.fullmatch(term.strip())
        if match is None:
            raise CexError("循环范围格式无效，可输入 1、1-20s5 或 r2")
        first, last, stride = match.groups()
        start, end = index(first), index(last or first)
        step = int(stride or "1")
        if step < 1:
            raise CexError("循环步长必须大于 0")
        direction = 1 if start <= end else -1
        result.update(dict.fromkeys(range(start, end + direction, step * direction)))
    return list(result)


@dataclass
class Document:
    id: str
    path: Path
    source: Path
    stamp: tuple[int, int]
    digest: str
    info: dict


def _stamp(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


class CexViewer:
    def __init__(self, store: DataStore, db: Database):
        self.store = store
        self.db = db
        self.directory = store.root / "cache" / f"cex-{uuid4().hex}"
        self.documents: dict[str, Document] = {}
        self.active_file: tuple[str, CexFile] | None = None
        self.api: LandCex | None = None
        self.dqdv_cache: OrderedDict[tuple[str, str, int, DqdvSettings], dict] = OrderedDict()

    def _document(self, key: str) -> Document:
        if key not in self.documents:
            raise CexError("CEX 查看会话已结束，请重新打开文件")
        return self.documents[key]

    def _file(self, doc: Document) -> CexFile:
        if self.active_file is not None and self.active_file[0] == doc.id:
            return self.active_file[1]
        if self.api is None:
            dll = os.environ.get("XRD_LAND_DLL") or r"D:\LAND\LANDCex_x64.dll"
            self.api = LandCex(Path(dll).absolute())
        # Keep only one SDK handle alive. Multiple tabs retain their snapshots.
        self._close_file()
        file = self.api.open(doc.path)
        self.active_file = (doc.id, file)
        return file

    def _close_file(self) -> None:
        if self.active_file is not None:
            self.active_file[1].close()
            self.active_file = None

    @staticmethod
    def scan_folder(p: dict) -> dict:
        if not p.get("root"):
            raise CexError("请选择蓝电数据文件夹")
        try:
            root = Path(p["root"]).resolve(strict=True)
            folder = Path(p.get("folder") or root).resolve(strict=True)
            if not folder.is_relative_to(root):
                raise CexError("子文件夹不在选定的蓝电数据目录内")
            entries = []
            with os.scandir(folder) as listing:
                for entry in listing:
                    path = Path(entry.path)
                    if entry.is_dir(follow_symlinks=False) and path.resolve() == path:
                        kind = "folder"
                    elif path.suffix.casefold() == ".cex" and entry.is_file(follow_symlinks=False):
                        kind = "cex"
                    else:
                        continue
                    entries.append({"path": str(path), "name": entry.name, "kind": kind})
        except CexError:
            raise
        except (OSError, ValueError, RuntimeError) as exc:
            raise CexError(f"无法读取蓝电数据文件夹：{exc}") from exc
        entries.sort(key=lambda row: (row["kind"] != "folder", row["name"].casefold(), row["name"]))
        return {"path": str(folder), "name": folder.name or str(folder), "entries": entries}

    def open(self, p: dict) -> dict:
        cell_id = p.get("cell_id")
        sample_id = None
        if cell_id is None:
            if not p.get("path"):
                raise CexError("请选择 CEX 文件")
            source = Path(os.path.abspath(p["path"]))
            filename = source.name
            name = source.stem
        else:
            row = self.db.query_one(
                """SELECT c.* FROM ec_cells c JOIN samples s ON s.id=c.sample_id
                   JOIN projects p ON p.id=s.project_id
                   WHERE c.id=? AND c.format='cex' AND c.deleted_at IS NULL
                     AND s.deleted_at IS NULL AND p.deleted_at IS NULL""", (cell_id,),
            )
            if row is None:
                raise CexError("样品中的 CEX 数据不存在或已在回收站中")
            source = self.store.abs_path(row["stored_path"])
            filename, name, sample_id = row["original_filename"], row["name"], row["sample_id"]
        if source.suffix.lower() != ".cex" or not source.is_file():
            raise CexError("找不到原始 CEX 文件，请选择 .cex 文件")
        try:
            stamp = _stamp(source)
            for doc in self.documents.values():
                if doc.source == source and doc.stamp == stamp and doc.info["cellId"] == cell_id:
                    return doc.info
            self.directory.mkdir(parents=True, exist_ok=True)
            key = uuid4().hex
            working_copy = self.directory / f"{key}.cex"
            doc = Document(key, working_copy, source, stamp, "", {
                "id": key, "name": name, "filename": filename, "path": str(source),
                "cellId": cell_id, "sampleId": sample_id,
            })
            try:
                shutil.copyfile(source, working_copy)
                doc.digest = sha256_of_file(working_copy)
                if doc.digest != sha256_of_file(source) or stamp != _stamp(source):
                    raise CexError("CEX 正在写入，暂未能获得一致的快照，请稍后重新打开")
                file = self._file(doc)
                doc.info.update(counts=file.counts, **file.session_info())
                self.documents[key] = doc
                return doc.info
            except Exception:
                self.close_document(key)
                working_copy.unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise CexError(f"无法读取 CEX 文件：{exc}") from exc

    def close_document(self, key: str) -> None:
        if self.active_file is not None and self.active_file[0] == key:
            self._close_file()
        if key in self.documents:
            self.documents.pop(key).path.unlink(missing_ok=True)

    def close(self) -> None:
        self._close_file()
        self.documents.clear()
        self.dqdv_cache.clear()
        if self.directory.is_dir():
            shutil.rmtree(self.directory, ignore_errors=True)

    def page(self, p: dict) -> dict:
        file = self._file(self._document(p.get("id")))
        kind = p.get("level")
        readers = {"cycles": file.cycle_row, "steps": file.step_row, "records": file.record_row}
        if kind not in readers:
            raise CexError("请选择循环、工步或记录")
        start = int(p.get("start", 0))
        count = int(p.get("count", 100))
        total = file.counts[kind]
        if start < 0 or start > total or not 1 <= count <= 500:
            raise CexError("CEX 表格分页范围无效")
        return {
            "start": start, "total": total,
            "rows": [readers[kind](row) for row in range(start, min(start + count, total))],
        }

    def locate(self, p: dict) -> dict:
        file = self._file(self._document(p.get("id")))
        record = int(p["number"]) - 1
        if not 0 <= record < file.counts["records"]:
            raise CexError("记录序号超出文件范围")
        return {"record": file.record_row(record), **file.locate(record)}

    def dqdv(self, p: dict) -> dict:
        doc = self._document(p.get("id"))
        settings = DqdvSettings.read(p.get("settings"))
        cycles = cycle_indices(str(p.get("cycles") or ""), doc.info["counts"]["cycles"])
        try:
            offset = int(p.get("offset", 0))
            limit = int(p.get("limit", 1))
        except (TypeError, ValueError) as exc:
            raise CexError("dQ/dV 循环分页无效") from exc
        if not 0 <= offset <= len(cycles) or not 1 <= limit <= 8:
            raise CexError("dQ/dV 循环分页无效")
        return {
            "algorithm": ALGORITHM, "sha256": doc.digest, "total": len(cycles),
            "offset": offset,
            "results": [self._dqdv_cycle(doc, index, settings) for index in cycles[offset:offset + limit]],
        }

    def _dqdv_cycle(self, doc: Document, index: int, settings: DqdvSettings) -> dict:
        key = (doc.digest, ALGORITHM, index, settings)
        result = self.dqdv_cache.get(key)
        if result is not None:
            self.dqdv_cache.move_to_end(key)
            return result
        result = calculate_cycle(self._file(doc), index, settings)
        if sum(len(curve["values"]) for curve in result["curves"]) <= 8000:
            self.dqdv_cache[key] = result
            if len(self.dqdv_cache) > 96:
                self.dqdv_cache.popitem(last=False)
        return result

    def dqdv_source(self, p: dict) -> dict:
        doc = self._document(p.get("id"))
        file = self._file(doc)
        number = int(p.get("record", 0))
        if not 1 <= number <= file.counts["records"]:
            raise CexError("原始记录序号超出文件范围")
        location = file.locate(number - 1)
        step = location["step"]
        if step is None:
            raise CexError("该记录没有可定位的工步")
        first, last = file.step_span(step["number"] - 1)
        return {
            **location,
            "record": file.record_row(number - 1),
            "points": _record_points(file, first, last, "capacity", "voltage"),
        }

    @staticmethod
    def export_dqdv(target: Path, columns: list, rows: list) -> dict:
        if target.suffix.lower() != ".csv":
            raise CexError("dQ/dV 表格须保存为 CSV 文件")
        if (not isinstance(columns, list) or not columns or not isinstance(rows, list)
                or any(not isinstance(row, list) or len(row) != len(columns) for row in rows)):
            raise CexError("dQ/dV 导出行列不一致")

        try:
            written = _write_table(target, "csv", columns, ([_csv_value(value) for value in row] for row in rows))
        except OSError as exc:
            raise CexError(f"无法保存 dQ/dV 表格：{exc}") from exc
        return {"path": str(target), "rows": written}

    def export_dqdv_curves(self, p: dict, target: Path) -> dict:
        if target.suffix.lower() != ".csv":
            raise CexError("dQ/dV 曲线须保存为 CSV 文件")
        settings = DqdvSettings.read(p.get("settings"))
        absolute = p.get("absolute") is True
        normalized = p.get("normalized") is True
        allowed = p.get("direction", "both")
        if allowed not in {"both", "charge", "discharge"}:
            raise CexError("请选择充电、放电或充放电")
        selections = []
        for source in p.get("sources") or []:
            doc = self._document(source.get("id"))
            if source.get("sha256") != doc.digest:
                raise CexError("CEX 快照已变更，请重新计算后再导出")
            cycles = source.get("cycles")
            if not isinstance(cycles, list) or any(
                type(cycle) is not int or not 1 <= cycle <= doc.info["counts"]["cycles"] for cycle in cycles
            ):
                raise CexError("选择的 dQ/dV 导出循环无效")
            override = source.get("massOverrideMg")
            if override is not None and (type(override) not in {float, int} or not math.isfinite(override) or override <= 0):
                raise CexError("归一化质量必须是大于零的有限数值")
            mass = override if override is not None else doc.info.get("massMg")
            mass_source = "manual" if override is not None else "cex" if mass else "missing"
            label = str(source.get("displayName") or doc.info["name"])
            selections.append((doc, list(dict.fromkeys(cycles)), mass, mass_source, label))
        if not selections:
            raise CexError("请先计算需要导出的 CEX 循环")

        def rows():
            for doc, cycles, mass, mass_source, label in selections:
                if normalized and not mass:
                    continue
                scale = 1000 / mass if normalized else 1
                unit = "mAh/(g·V)" if normalized else "mAh/V"
                for cycle in cycles:
                    result = self._dqdv_cycle(doc, cycle - 1, settings)
                    for curve in result["curves"]:
                        if allowed not in {"both", curve["direction"]}:
                            continue
                        factor = -scale if absolute and curve["direction"] == "discharge" else scale
                        prefix = [
                            _csv_value(label), _csv_value(doc.info["filename"]), doc.digest,
                            _csv_value(doc.info["channel"]), cycle, curve["direction"], curve["step"], curve["part"],
                            curve["firstRecord"], curve["lastRecord"], curve["currentMa"], curve["sampleMv"],
                            result["cvCapacityMah"], curve["capacitySource"], mass, mass_source, int(result["paired"]),
                            ALGORITHM, settings.grid_mv, settings.smoothing_mv, unit,
                        ]
                        raw = curve["rawValues"]
                        for i, y in enumerate(curve["values"]):
                            voltage = (curve["startBin"] + i + 0.5) * settings.grid_mv / 1000
                            yield [*prefix, voltage, factor * y, None if raw is None else factor * raw[i],
                                   curve["records"][i], "absolute" if absolute else "signed"]

        try:
            written = _write_table(target, "csv", DQDV_COLUMNS, rows())
        except OSError as exc:
            raise CexError(f"无法保存 dQ/dV 曲线：{exc}") from exc
        return {"path": str(target), "rows": written}

    def cells(self, p: dict) -> dict:
        sample_id = p.get("sample_id")
        rows = self.db.query_all(
            """SELECT id, name, original_filename, active_mass_mg,
                      (SELECT COUNT(*) FROM ec_cycles WHERE cell_id = ec_cells.id) AS cycle_count
               FROM ec_cells WHERE sample_id = ? AND deleted_at IS NULL AND format = 'cex'
               ORDER BY name""",
            (sample_id,),
        )
        history = self._legacy_rows(sample_id)
        return {"cells": [
            {"id": row["id"], "name": row["name"], "filename": row["original_filename"],
             "massMg": row["active_mass_mg"], "cycleCount": row["cycle_count"]}
            for row in rows
        ], "historyCount": len(history)}

    def _legacy_rows(self, sample_id: int | None):
        return self.db.query_all(
            """SELECT * FROM ec_cells WHERE sample_id = ? AND deleted_at IS NULL
               AND LOWER(COALESCE(format, '')) <> 'cex' ORDER BY id""",
            (sample_id,),
        )

    def backup_legacy(self, sample_id: int, target: Path) -> dict:
        if target.suffix.lower() != ".zip":
            raise CexError("历史备份须保存为 ZIP 文件")
        rows = self._legacy_rows(sample_id)
        if not rows:
            raise CexError("这个样品没有历史表格数据")
        staging = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        entries = []
        try:
            with ZipFile(staging, "x", compression=ZIP_DEFLATED) as archive:
                for row in rows:
                    cell_id = row["id"]
                    folder = f"cell-{cell_id}"
                    source = self.store.abs_path(row["stored_path"])
                    files = []
                    if source.is_file():
                        archive.write(source, f"{folder}/source/{source.name}")
                        files.append(f"{folder}/source/{source.name}")
                    for cache in [source.with_name("records.npz"), *source.parent.glob("dqdv_*.npz")]:
                        if cache.is_file() and not cache.is_symlink():
                            archive.write(cache, f"{folder}/cache/{cache.name}")
                            files.append(f"{folder}/cache/{cache.name}")
                    cycles = self.db.query_all("SELECT * FROM ec_cycles WHERE cell_id = ?", (cell_id,))
                    analyses = self.db.query_all("SELECT * FROM ec_analyses WHERE cell_id = ?", (cell_id,))
                    entries.append({"cell": dict(row), "files": files, "sourceMissing": not source.is_file(),
                                    "cycles": list(map(dict, cycles)), "analyses": list(map(dict, analyses))})
                archive.writestr("manifest.json", json.dumps(entries, ensure_ascii=False, indent=2))
            staging.replace(target)
        except OSError as exc:
            raise CexError(f"无法保存历史备份：{exc}") from exc
        finally:
            staging.unlink(missing_ok=True)
        return {"path": str(target), "count": len(entries),
                "missing": sum(entry["sourceMissing"] for entry in entries)}

    def plot(self, p: dict) -> dict:
        doc = self._document(p.get("id"))
        file = self._file(doc)
        kind = p.get("kind", "time")
        if kind not in {"time", "voltageCapacity", "cycles", "specificCycles", "steps"}:
            raise CexError("未知的 CEX 曲线类型")
        size = file.counts["cycles"]
        first = int(p.get("first", 1))
        last = int(p.get("last", size or 1))
        if not 1 <= first <= last <= max(size, 1):
            raise CexError("曲线的循环范围无效")
        indices = range(first - 1, last) if size else range(0)
        preview = p.get("preview") is True
        if kind == "voltageCapacity":
            selected = int(p.get("cycle", first)) - 1
            if not 0 <= selected < size:
                return {"series": []}
            return {"series": _cycle_series(file, selected, "本文件", False, preview=preview)[0]}
        if kind == "time":
            a, b = _section(file, indices, "records")
            if p.get("recordStart") is not None or p.get("recordEnd") is not None:
                a = max(0, int(p.get("recordStart", a)))
                b = min(file.counts["records"], int(p.get("recordEnd", b)))
            return {"series": _time_series(file, a, b, preview=preview)}
        if kind == "steps":
            a, b = _section(file, indices, "steps")
            return {"series": [
                _scalar_series(file, range(a, b), STEP_COLUMNS["capacity"], "工步容量", 0, preview=preview),
                _scalar_series(file, range(a, b), STEP_COLUMNS["energy"], "工步能量", 1, preview=preview),
            ]}
        charge, discharge = ("sqCharge", "sqDischarge") if kind == "specificCycles" else (
            "qCharge", "qDischarge",
        )
        return {"series": [
            _scalar_series(file, indices, CYCLE_COLUMNS[charge], "充电", 0, preview=preview),
            _scalar_series(file, indices, CYCLE_COLUMNS[discharge], "放电", 0, preview=preview),
            _scalar_series(file, indices, CYCLE_COLUMNS["efficiency"], "库仑效率", 1, preview=preview),
        ]}

    def compare(self, p: dict) -> dict:
        sources = p.get("sources") or []
        if not sources:
            raise CexError("请选中参加循环对比的文件")
        selected = []
        for source in sources:
            doc = self._document(source["id"])
            indices = cycle_indices(
                source.get("cycles") or p.get("cycles") or "r1", doc.info["counts"]["cycles"],
            )
            selected.extend((doc, index) for index in indices)
        if not selected:
            raise CexError("选中的文件没有可对比的循环")
        ignore_rest = bool(p.get("ignoreRest"))
        series, rows = [], []
        point_limit = min(DISPLAY_POINTS, max(4, 24000 // len(selected)))
        for doc, index in selected:
            file = self._file(doc)
            name = f"{doc.info['name']} · 第 {index + 1} 圈"
            curves, row = _cycle_series(file, index, name, ignore_rest, point_limit)
            series.extend({**s, "id": f"{doc.id}:{index}:{s['name']}"} for s in curves)
            rows.append({
                **row, "id": doc.id, "filename": doc.info["filename"],
                "channel": doc.info["channel"], "barcode": doc.info["barcode"],
                "startTime": doc.info["startTime"],
            })
        return {"series": series, "rows": rows}

    def archive(self, p: dict) -> dict:
        keys = list(dict.fromkeys(p.get("ids") or []))
        if not keys:
            raise CexError("请选择需要归档的 CEX 文件")
        sample_id = p.get("sample_id")
        if self._active_sample(self.db, sample_id) is None:
            raise CexError("目标样品不存在或已在回收站中")
        names = p.get("names") or {}
        if not isinstance(names, dict):
            raise CexError("数据名称参数无效")
        result: dict[str, list] = {"imported": [], "duplicates": [], "failures": []}
        for key in keys:
            try:
                doc = self._document(key)
                kind, row = self._archive_one(doc, sample_id, names.get(key, Path(doc.info["filename"]).stem))
                result[kind].append({"id": key, **row})
            except (OSError, ValueError) as exc:
                result["failures"].append({"id": key, "reason": str(exc)})
        return result

    @staticmethod
    def _active_sample(db: Database, sample_id: int | None):
        return db.query_one(
            """SELECT s.id, s.name, s.project_id, p.name AS project
               FROM samples s JOIN projects p ON p.id=s.project_id
               WHERE s.id=? AND s.deleted_at IS NULL AND p.deleted_at IS NULL""", (sample_id,),
        )

    def _archive_one(self, doc: Document, sample_id: int, name: str) -> tuple[str, dict]:
        stored = ""
        try:
            with self.db.transaction() as conn:
                target = self._active_sample(self.db, sample_id)
                if target is None:
                    raise CexError("目标样品不存在或已在回收站中")
                existing = conn.execute(
                    """SELECT c.id AS cellId, c.name, s.id AS sampleId, s.name AS sample,
                              p.name AS project
                       FROM ec_cells c JOIN samples s ON s.id=c.sample_id
                       JOIN projects p ON p.id=s.project_id
                       WHERE c.file_hash=? AND c.deleted_at IS NULL AND s.deleted_at IS NULL
                         AND p.deleted_at IS NULL ORDER BY c.id LIMIT 1""", (doc.digest,),
                ).fetchone()
                if existing:
                    return "duplicates", dict(existing)
                file = self._file(doc)
                project_folder = self.db.project_storage_folder(target["project_id"])
                sample_folder = self.db.sample_storage_folder(sample_id)
                stored, digest = self.store.import_sample_cex(doc.path, project_folder, sample_folder, name)
                if digest != doc.digest:
                    raise CexError("CEX 快照校验失败，请重新打开源文件")
                name = Path(stored).stem
                session = {
                    "channel": doc.info["channel"], "start_time": doc.info["startTime"],
                    "end_time": doc.info["endTime"], "test_name": doc.info["name"],
                    "program": [p["name"] for p in doc.info["programs"]],
                }
                cursor = conn.execute(
                    """INSERT INTO ec_cells
                       (sample_id, name, original_filename, source_path, stored_path,
                        file_hash, format, active_mass_mg, meta_json)
                       VALUES (?,?,?,?,?,?,'cex',?,?)""",
                    (sample_id, name, doc.info["filename"], str(doc.source), stored,
                     digest, file.mass_mg, json.dumps({"session": session}, ensure_ascii=False)),
                )
                cell_id = cursor.lastrowid
                conn.executemany(
                    """INSERT INTO ec_cycles (cell_id, cycle_index, q_charge, q_discharge, ce)
                       VALUES (?,?,?,?,?)""", _cycle_summaries(file, cell_id),
                )
            return "imported", {
                "cellId": cell_id, "sampleId": sample_id, "name": name,
                "sample": target["name"], "project": target["project"],
            }
        except Exception:
            if stored:
                self.store.remove_stored(stored)
            raise

    def export(self, p: dict) -> dict:
        doc = self._document(p.get("id"))
        file = self._file(doc)
        level = p.get("level")
        if level not in EXPORT_COLUMNS:
            raise CexError("请选择要导出的数据表")
        headings = EXPORT_COLUMNS[level]
        columns = p.get("columns", list(headings))
        if not columns or any(key not in headings for key in columns):
            raise CexError("选择了不属于该数据表的导出列")
        suffix = str(p.get("format", "csv")).lower()
        if suffix not in ("csv", "txt", "xlsx"):
            raise CexError("导出格式须为 Excel、CSV 或 TXT")
        target = Path(p["path"]) if p.get("path") else self.store.export_path(
            f"{doc.info['name']}-{level}.{suffix}",
        )
        if target.suffix.lower() != f".{suffix}":
            raise CexError("保存文件的扩展名与导出格式不一致")
        readers = {"cycles": file.cycle_row, "steps": file.step_row, "records": file.record_row}
        rows = _export_indices(file, level, str(p.get("cycles") or ""))
        try:
            written = _write_table(target, suffix, [headings[key] for key in columns], (
                [row[key] for key in columns]
                for row in (readers[level](index) for index in rows)
            ))
        except OSError as exc:
            raise CexError(f"无法保存导出文件：{exc}") from exc
        return {"path": str(target), "rows": written}


def _cycle_summaries(file: CexFile, cell_id: int):
    charge, discharge, ce = (CYCLE_COLUMNS[key] for key in ("qCharge", "qDischarge", "efficiency"))
    for index in range(file.counts["cycles"]):
        efficiency = file.value(ce, index)
        yield (
            cell_id, index + 1, file.value(charge, index), file.value(discharge, index),
            efficiency / 100 if efficiency is not None else None,
        )


def _section(file: CexFile, indices: range, level: str) -> tuple[int, int]:
    if not indices or len(indices) == file.counts["cycles"]:
        return (0, file.counts[level])
    start, end = None, 0
    for index in indices:
        a, b = file.cycle_span(index, level)
        if a < b:
            start = a if start is None else min(a, start)
            end = max(b, end)
    return (start or 0, end)


def _series(name: str, points: list, axis: int, *, dashed: bool = False) -> dict:
    return {"id": name, "name": name, "points": points, "axis": axis, "dashed": dashed}


def _drag_indices(start: int, end: int):
    """Keep both ends of the range and bound the native reads during a drag."""
    if end - start <= DRAG_POINTS:
        return range(start, end)
    return map(int, np.linspace(start, end - 1, DRAG_POINTS, dtype=np.int64))


def _scalar_series(file: CexFile, indices: range, column, name: str, axis: int, *, preview: bool = False) -> dict:
    if preview:
        return _series(name, [[i + 1, file.value(column, i), None] for i in _drag_indices(indices.start, indices.stop)], axis)
    positions = np.asarray(indices, dtype=np.int64)
    values = np.fromiter(
        (file.value(column, int(index)) for index in positions), dtype=np.float64, count=positions.size,
    )
    kept = select_minmax_indices(positions, values, DISPLAY_POINTS)
    return _series(name, [
        [int(positions[k]) + 1, float(values[k]) if np.isfinite(values[k]) else None, None] for k in kept
    ], axis)


def _record_points(
    file: CexFile, start: int, end: int, x: str, y: str, limit: int = DISPLAY_POINTS,
    *, preview: bool = False,
) -> list:
    if end <= start:
        return []
    x_column, y_column = RECORD_COLUMNS[x], RECORD_COLUMNS[y]
    if preview:
        return [
            [x_value, file.value(y_column, i), i + 1]
            for i in _drag_indices(start, end) if (x_value := file.value(x_column, i)) is not None
        ]
    multiplier = file.multiplier(y_column)
    read = file.dll.GetDataAsDouble
    handle = file.handle
    # Keep the scan bounded in memory. Each plot contains source record numbers, even after decimation.
    chunks = min(max(1, limit // 4), math.ceil((end - start) / 65536))
    edges = np.linspace(start, end, chunks + 1, dtype=np.int64)
    points = []
    for lo, hi in pairwise(map(int, edges)):
        values = np.fromiter(
            (read(handle, y_column.code, index) for index in range(lo, hi)),
            dtype=np.float64, count=hi - lo,
        )
        mask = select_minmax_indices(np.arange(hi - lo), values, max(4, limit // chunks))
        for offset in map(int, mask):
            index = lo + offset
            x_value = file.value(x_column, index)
            y_value = float(values[offset]) * multiplier if np.isfinite(values[offset]) else None
            if x_value is not None:
                points.append([x_value, y_value, index + 1])
    return points


def _time_series(file: CexFile, start: int, end: int, *, preview: bool = False) -> list[dict]:
    # Both axes use the same record indices so the time cursor reads a voltage/current pair.
    fields = ("voltage", "current")
    points = {
        field: {
            point[2]: point for point in _record_points(file, start, end, "time", field, DISPLAY_POINTS // 2, preview=preview)
        }
        for field in fields
    }
    coordinates = {
        number: point[0] for data in points.values() for number, point in data.items()
    }
    output = []
    for axis, (field, label) in enumerate(zip(fields, ("电压", "电流"), strict=True)):
        saved = points[field]
        column = RECORD_COLUMNS[field]
        combined = [
            saved[number] if number in saved else [coordinates[number], file.value(column, number - 1), number]
            for number in sorted(coordinates)
        ]
        output.append(_series(label, combined, axis))
    return output


def _cycle_series(
    file: CexFile, index: int, title: str, ignore_rest: bool, limit: int = DISPLAY_POINTS, *, preview: bool = False,
) -> tuple[list[dict], dict]:
    row = file.cycle_row(index)
    curves = []
    for direction, name, suffix in (("charge", "充电", "Charge"), ("discharge", "放电", "Discharge")):
        first, end = file.cycle_span(index, direction)
        if ignore_rest:
            while end > first and file.step_mode(end - 1) == REST_MODE:
                end -= 1
        spans = [span for step in range(first, end) if (span := file.step_span(step))[0] < span[1]]
        if not spans:
            continue
        start, stop = spans[0][0], spans[-1][1]
        if ignore_rest:
            row[f"q{suffix}"] = file.value(RECORD_COLUMNS["capacity"], stop - 1)
            row[f"sq{suffix}"] = file.value(RECORD_COLUMNS["specificCapacity"], stop - 1)
        points = _record_points(file, start, stop, "capacity", "voltage", limit, preview=preview)
        if points:
            curves.append({**_series(f"{title} · {name}", points, 0, dashed=direction == "discharge"),
                           "group": title})
    if ignore_rest and row["qCharge"] and row["qDischarge"] is not None:
        row["efficiency"] = row["qDischarge"] / row["qCharge"] * 100
    return curves, row


def _export_indices(file: CexFile, level: str, expression: str):
    if not expression.strip() or expression.strip() == "全部":
        return range(file.counts[level])
    indices = cycle_indices(expression, file.counts["cycles"])
    if level == "cycles":
        return indices
    return chain.from_iterable(range(*file.cycle_span(index, level)) for index in indices)


def _write_table(target: Path, suffix: str, header: list[str], rows) -> int:
    staging = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    written = 0
    try:
        if suffix == "xlsx":
            from openpyxl import Workbook

            book = Workbook(write_only=True)
            sheet = book.create_sheet("数据")
            sheet.append(header)
            try:
                for written, row in enumerate(rows, 1):
                    if written > 1 and (written - 1) % XL_DATA_ROWS == 0:
                        sheet = book.create_sheet(f"数据{(written - 1) // XL_DATA_ROWS + 1}")
                        sheet.append(header)
                    sheet.append(row)
                book.save(staging)
            finally:
                book.close()
        else:
            with staging.open("x", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream, delimiter="," if suffix == "csv" else "\t")
                writer.writerow(header)
                for row in rows:
                    writer.writerow(row)
                    written += 1
        staging.replace(target)
        return written
    finally:
        staging.unlink(missing_ok=True)


def _csv_value(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value
