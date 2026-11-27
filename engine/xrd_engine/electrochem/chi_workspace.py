"""Import, archive and export CHI workstation data through the desktop library."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from sqlite3 import Row

from ..database.db import Database
from ..services.data_store import DataStore
from .chi import FIT_MODELS, ChiData, ChiError, fit_eis, parse_chi

_EXTENSIONS = {".txt", ".csv"}
_MAX_BYTES = 10 * 1024 * 1024
_DATASETS = """SELECT c.*, s.name AS sample_name, s.project_id
               FROM chi_datasets c LEFT JOIN samples s ON s.id = c.sample_id
               LEFT JOIN projects p ON p.id = s.project_id
               WHERE (c.sample_id IS NULL OR (s.deleted_at IS NULL AND p.deleted_at IS NULL))"""


def _summary(row: Row) -> dict:
    return {
        "id": row["id"], "kind": row["kind"], "name": row["name"],
        "folder": Path(row["source_path"]).parent.name, "sampleId": row["sample_id"],
        "sampleName": row["sample_name"], "pointCount": row["point_count"],
    }


def _read(path: Path) -> bytes:
    with path.open("rb") as source:
        blob = source.read(_MAX_BYTES + 1)
    if len(blob) > _MAX_BYTES:
        raise ChiError("文件超过 10 MB，请在辰华软件分段导出文本")
    return blob


class ChiWorkspace:
    def __init__(self, store: DataStore, db: Database):
        self.store = store
        self.db = db

    def list(self, payload: dict) -> dict:
        rows = self.db.query_all(
            _DATASETS + " AND (c.sample_id IS NULL OR s.project_id = ?) ORDER BY c.id",
            (payload.get("project_id"),),
        )
        return {"files": [_summary(row) for row in rows],
                "models": {key: model[0] for key, model in FIT_MODELS.items()}}

    def import_files(self, payload: dict) -> dict:
        sample_id = payload.get("sample_id")
        project_folder = None
        if sample_id is not None:
            sample = self.db.query_one(
                """SELECT s.project_id FROM samples s JOIN projects p ON p.id = s.project_id
                   WHERE s.id = ? AND s.deleted_at IS NULL AND p.deleted_at IS NULL""", (sample_id,)
            )
            if sample is None:
                raise ChiError("归档样品不存在，请重新选择")
            project_folder = self.db.project_storage_folder(sample["project_id"])

        paths = []
        for item in payload.get("paths", []):
            path = Path(item).expanduser()
            if path.is_dir():
                paths.extend(sorted(file for file in path.rglob("*")
                                    if file.suffix.lower() in _EXTENSIONS and file.is_file()))
            else:
                paths.append(path)
        imported, duplicates, failures = [], [], []
        for path in dict.fromkeys(paths):
            stored = None
            try:
                if path.suffix.lower() not in _EXTENSIONS:
                    raise ChiError("请在辰华软件将原始文件导出为 TXT 或 CSV")
                blob = _read(path)
                digest = hashlib.sha256(blob).hexdigest()
                with self.db.transaction() as conn:
                    duplicate = conn.execute(
                        "SELECT id, name FROM chi_datasets WHERE sample_id IS ? AND sha256 = ?",
                        (sample_id, digest),
                    ).fetchone()
                    if duplicate:
                        duplicates.append(dict(duplicate))
                        continue
                    data = parse_chi(blob)
                    stored, copied_hash = self.store.import_file(
                        path, {"technique": data.kind, "sample_id": sample_id}, project_folder=project_folder,
                    )
                    if copied_hash != digest:
                        raise ChiError("复制时原文件发生变化，请保存后重新导入")
                    identifier = conn.execute(
                        """INSERT INTO chi_datasets
                           (sample_id, kind, name, source_path, stored_path, sha256, point_count)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (sample_id, data.kind, path.name, str(path.resolve()), stored, digest, len(data.points)),
                    ).lastrowid
                imported.append({"id": identifier, "kind": data.kind})
            except (ChiError, OSError, ValueError) as exc:
                if stored:
                    self.store.remove_stored(stored)
                failures.append({"name": path.name, "reason": str(exc)})
            except Exception:
                if stored:
                    self.store.remove_stored(stored)
                raise
        return {"imported": imported, "duplicates": duplicates, "failures": failures}

    def _load(self, identifier: int) -> tuple[Row, ChiData]:
        row = self.db.query_one(_DATASETS + " AND c.id = ?", (identifier,))
        if row is None:
            raise ChiError("数据不存在，或其样品已移至回收站")
        try:
            blob = _read(self.store.abs_path(row["stored_path"]))
        except OSError as exc:
            raise ChiError("读取项目中的数据副本失败") from exc
        if hashlib.sha256(blob).hexdigest() != row["sha256"]:
            raise ChiError("项目副本内容已改变，请重新导入原始文件")
        return row, parse_chi(blob)

    def open(self, payload: dict) -> dict:
        row, data = self._load(payload["id"])
        return {
            **_summary(row), "metadata": data.metadata, "sha256": row["sha256"],
            "points": data.points.tolist(), "segments": data.segments, "warnings": data.warnings,
            "fit": json.loads(row["fit_json"]) if row["fit_json"] else None,
        }

    def fit(self, payload: dict) -> dict:
        row, data = self._load(payload["id"])
        try:
            lower = float(payload["min_hz"])
            upper = float(payload["max_hz"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ChiError("请输入有效的拟合频段（Hz）") from exc
        fit = fit_eis(data, payload.get("model"), lower, upper, payload.get("weighting"))
        self.db.execute("UPDATE chi_datasets SET fit_json = ? WHERE id = ?",
                        (json.dumps(fit, allow_nan=False), row["id"]))
        return fit

    def export(self, identifier: int, target: Path) -> dict:
        row, data = self._load(identifier)
        fit = json.loads(row["fit_json"]) if row["fit_json"] else None
        try:
            with target.open("x", encoding="utf-8-sig", newline="") as output:
                writer = csv.writer(output)
                writer.writerow(("# CHI source", row["sha256"], row["kind"]))
                for key, value in data.metadata.items():
                    if str(value).startswith(("=", "+", "-", "@")) and isinstance(value, str):
                        value = "'" + value
                    writer.writerow(("# " + key, value))
                if data.kind == "CV":
                    writer.writerow(("record", "potential_V", "current_A"))
                    for index, point in enumerate(data.points, 1):
                        writer.writerow((index, *point))
                else:
                    self._export_eis(writer, data, fit)
        except OSError as exc:
            raise ChiError("无法写入导出文件，请选择其他位置") from exc
        return {"path": str(target), "rows": len(data.points)}

    @staticmethod
    def _export_eis(writer, data: ChiData, fit: dict | None) -> None:
        matched = {}
        if fit:
            writer.writerow(("# circuit", fit["circuit"], fit["weighting"]))
            writer.writerow(("# fitted_range_Hz", *fit["range"]))
            writer.writerow(("# relative_RMSE_pct", fit["relativeRmse"], "RMSE_ohm", fit["rmse"]))
            for parameter in fit["parameters"]:
                writer.writerow(("# fit_parameter", parameter["name"], parameter["value"], parameter["unit"]))
            for warning in fit["warnings"]:
                writer.writerow(("# warning", warning))
            matched = {int(index): (real, imag) for index, real, imag in fit["matched"]}
        writer.writerow(("frequency_Hz", "real_ohm", "imag_ohm", "modulus_ohm", "phase_deg",
                         "used_in_fit", "fit_real_ohm", "fit_imag_ohm", "residual_real_ohm", "residual_imag_ohm"))
        for index, point in enumerate(data.points):
            if index in matched:
                real, imag = matched[index]
                writer.writerow((*point, 1, real, imag, real - point[1], imag - point[2]))
            else:
                writer.writerow((*point, 0, "", "", "", ""))
