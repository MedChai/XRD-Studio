"""命令分发层(§10.4)。

引擎对外的唯一入口。所有调用形态统一为:

    handle(command: str, payload: dict) -> dict

生产环境经 stdio/Rust 转发调用;开发期可选的 FastAPI 也只是把这层
包成 HTTP 路由。返回一律是可 JSON 序列化的 dict,曲线数据的二进制
优化在 §9.1 的 Envelope 层处理(stdio 帧编码),不在这里。

错误协议:引擎不向调用方抛 traceback(§68 Toast 要求),
所有异常被收敛为 {"ok": false, "error": 人话, "detail": 技术信息}。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from ..database.db import Database
from ..importers.loader import (
    ColumnMappingRequired,
    LoadFailure,
    load_file,
)
from ..models.types import (
    BackgroundParams,
    CropParams,
    NormalizationParams,
    Peak,
    PeakDetectionParams,
    PipelineParams,
    SmoothingParams,
)
from ..processing.pipeline import PipelineError, run_pipeline, validate_params
from ..services.data_store import DataStore, sha256_of_file


class EngineError(Exception):
    """携带用户可见中文原因的引擎错误。"""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def params_from_json(data: dict[str, Any]) -> PipelineParams:
    """从存储/传输的 JSON 字典还原 PipelineParams,容忍缺字段。"""

    def sub(cls, key: str):
        raw = data.get(key) or {}
        fields = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in raw.items() if k in fields})

    return PipelineParams(
        crop=sub(CropParams, "crop"),
        background=sub(BackgroundParams, "background"),
        smoothing=sub(SmoothingParams, "smoothing"),
        normalization=sub(NormalizationParams, "normalization"),
        peaks=sub(PeakDetectionParams, "peaks"),
    )


def curve_payload(curve, background=None) -> dict:
    """Curve → JSON 友好的负载(开发期;生产替换为二进制 Envelope,§9.1)。"""
    out: dict[str, Any] = {
        "x": curve.x.tolist(),
        "y": curve.y.tolist(),
        "xAxisType": curve.x_axis_type,
        "wavelength": curve.wavelength,
        "pointCount": int(curve.x.size),
    }
    if background is not None:
        out["background"] = np.asarray(background).tolist()
    return out


class Engine:
    """持有 Database + DataStore 的引擎实例。"""

    def __init__(self, data_root: str | Path):
        self.store = DataStore(data_root)
        self.db = Database(self.store.db_path)

    # ---------- dispatch ----------

    def handle(self, command: str, payload: dict) -> dict:
        """执行命令。返回 {"ok": true, "data": ...} 或 {"ok": false, ...}。"""
        handler = getattr(self, "_cmd_" + command.replace(".", "_"), None)
        if handler is None:
            return _fail(EngineError(f"未知命令:{command}"))
        try:
            return {"ok": True, "data": handler(payload or {})}
        except EngineError as exc:
            return _fail(exc)
        except PipelineError as exc:
            return _fail(exc)
        except Exception as exc:
            # traceback 进日志文件,不给用户看(§68)
            return _fail(
                EngineError("引擎内部错误,请重试或查看日志", detail=str(exc))
            )

    # ---------- datasets ----------

    def _cmd_datasets_import(self, p: dict) -> dict:
        """导入一批文件。

        payload: {paths, project_id, sample_id?, new_sample?: {name,...},
                  x_column?, y_column?, x_axis_type?, wavelength?,
                  import_anyway?: bool}
        """
        paths = p.get("paths") or []
        if not paths:
            raise EngineError("没有指定要导入的文件")
        project_id = p.get("project_id")
        row = self.db.query_one(
            "SELECT id FROM projects WHERE id = ? AND deleted_at IS NULL",
            (project_id,),
        )
        if row is None:
            raise EngineError("目标 Project 不存在")

        sample_id = p.get("sample_id")
        if sample_id is None:
            ns = p.get("new_sample") or {}
            name = (ns.get("name") or "").strip()
            if not name:
                raise EngineError("新样品必须填写 Sample Name")
            sample_id = self.db.insert(
                """INSERT INTO samples
                   (project_id, name, temperature, duration, material, wavelength)
                   VALUES (?,?,?,?,?,?)""",
                (
                    project_id, name,
                    str(ns.get("temperature", "")),
                    str(ns.get("duration", "")),
                    str(ns.get("material", "")),
                    float(ns.get("wavelength", 1.5418)),
                ),
            )
        else:
            if self.db.query_one(
                "SELECT id FROM samples WHERE id = ? AND deleted_at IS NULL",
                (sample_id,),
            ) is None:
                raise EngineError("目标 Sample 不存在")

        imported: list[dict] = []
        duplicates: list[dict] = []
        need_columns: list[dict] = []
        failures: list[dict] = []

        for path in paths:
            result = load_file(
                path,
                x_axis_type=p.get("x_axis_type", "two_theta"),
                wavelength=float(p.get("wavelength", 1.5418)),
                x_column=p.get("x_column"),
                y_column=p.get("y_column"),
            )

            if isinstance(result, ColumnMappingRequired):
                need_columns.append({
                    "path": result.path,
                    "reason": result.reason,
                    "previewRows": result.preview_rows,
                    "columnCount": result.column_count,
                    "suggestedX": result.suggested_x,
                    "suggestedY": result.suggested_y,
                })
                continue
            if isinstance(result, LoadFailure):
                failures.append({"path": result.path, "reason": result.reason})
                continue

            ds = result
            file_hash = sha256_of_file(Path(path))

            # 查重(§8.4)
            dup = self.db.find_duplicate_by_hash(file_hash)
            if dup is not None and not p.get("import_anyway"):
                duplicates.append({
                    "path": str(path),
                    "existing": {
                        "projectName": dup["project_name"],
                        "sampleName": dup["sample_name"],
                        "datasetName": dup["name"],
                    },
                })
                continue

            stored, _ = self.store.import_file(path, {
                "project_id": project_id,
                "sample_id": sample_id,
            })
            name = Path(path).name
            dataset_id = self.db.insert(
                """INSERT INTO datasets
                   (sample_id, name, original_filename, source_path, stored_path,
                    file_hash, x_axis_type, x_min, x_max, step, point_count, grid)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    sample_id, name, name, str(Path(path).resolve()), stored,
                    file_hash, ds.curve.x_axis_type,
                    float(ds.curve.x[0]), float(ds.curve.x[-1]),
                    float(np.median(np.diff(ds.curve.x))), int(ds.curve.x.size),
                    ds.original_grid,
                ),
            )
            self.db.insert(
                "INSERT INTO analyses (dataset_id, pipeline_json) VALUES (?,?)",
                (dataset_id, "{}"),
            )
            imported.append({"id": dataset_id, "name": name})

        return {
            "imported": imported,
            "duplicates": duplicates,
            "needColumns": need_columns,
            "failures": failures,
            "sampleId": sample_id,
        }

    def _cmd_datasets_curve(self, p: dict) -> dict:
        """返回原始曲线(未处理)。payload: {id}"""
        dataset_id = p.get("id")
        row = self.db.query_one(
            "SELECT * FROM datasets WHERE id = ? AND deleted_at IS NULL",
            (dataset_id,),
        )
        if row is None:
            raise EngineError("Dataset 不存在")
        path = self.store.abs_path(row["stored_path"])
        result = load_file(path, x_axis_type=row["x_axis_type"])
        if not hasattr(result, "curve"):
            raise EngineError("无法读取已存储的数据文件", str(result))
        return curve_payload(result.curve)

    # ---------- analysis ----------

    def _active_analysis(self, dataset_id: int):
        row = self.db.query_one(
            "SELECT * FROM analyses WHERE dataset_id = ? AND is_active = 1",
            (dataset_id,),
        )
        if row is None:
            analysis_id = self.db.insert(
                "INSERT INTO analyses (dataset_id, pipeline_json) VALUES (?,?)",
                (dataset_id, "{}"),
            )
            row = self.db.query_one("SELECT * FROM analyses WHERE id = ?", (analysis_id,))
        return row

    def _load_dataset_curve(self, dataset_id: int):
        row = self.db.query_one(
            "SELECT * FROM datasets WHERE id = ? AND deleted_at IS NULL",
            (dataset_id,),
        )
        if row is None:
            raise EngineError("Dataset 不存在")
        path = self.store.abs_path(row["stored_path"])
        result = load_file(path, x_axis_type=row["x_axis_type"])
        if not hasattr(result, "curve"):
            raise EngineError("无法读取数据文件", str(result))
        return result.curve

    def _cmd_analysis_preview(self, p: dict) -> dict:
        """按参数求值处理链但**不落库**(§10.3 previewing 态)。

        payload: {dataset_id, pipeline: {...}}
        """
        curve = self._load_dataset_curve(p["dataset_id"])
        params = params_from_json(p.get("pipeline") or {})
        report = validate_params(curve, params)
        if not report.ok:
            raise EngineError(";".join(report.errors))
        result = run_pipeline(curve, params)
        return {
            "curve": curve_payload(result.curve, result.background),
            "peaks": [pk.to_dict() for pk in result.peaks],
            "smoothingAffectsMetrics": result.metrics_affected_by_smoothing,
            "warnings": result.warnings,
        }

    def _cmd_analysis_commit(self, p: dict) -> dict:
        """把 preview 满意后的参数落库(§10.3 committing 态)。"""
        dataset_id = p["dataset_id"]
        analysis = self._active_analysis(dataset_id)
        self.db.execute(
            "UPDATE analyses SET pipeline_json = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(p.get("pipeline") or {}, ensure_ascii=False), analysis["id"]),
        )
        return {"analysisId": analysis["id"]}

    def _cmd_peaks_detect(self, p: dict) -> dict:
        """对已保存的 analysis 正式跑一遍寻峰并把结果写入 peaks 表。"""
        analysis = self.db.query_one(
            "SELECT * FROM analyses WHERE id = ?", (p["analysis_id"],)
        )
        if analysis is None:
            raise EngineError("Analysis 不存在")
        curve = self._load_dataset_curve(analysis["dataset_id"])
        params = params_from_json(json.loads(analysis["pipeline_json"]))
        result = run_pipeline(curve, params)
        self.db.execute("DELETE FROM peaks WHERE analysis_id = ?", (analysis["id"],))
        for pk in result.peaks:
            self.db.insert(
                """INSERT INTO peaks
                   (analysis_id, position, intensity, fwhm, area,
                    left_bound, right_bound, manual_override)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    analysis["id"], pk.position, pk.intensity, pk.fwhm,
                    pk.area, pk.left_bound, pk.right_bound,
                    int(pk.manual_override),
                ),
            )
        return {"peaks": [pk.to_dict() for pk in result.peaks]}

    def _cmd_peaks_list(self, p: dict) -> dict:
        rows = self.db.query_all(
            "SELECT * FROM peaks WHERE analysis_id = ? ORDER BY position",
            (p["analysis_id"],),
        )
        return {
            "peaks": [
                {
                    "id": r["id"], "position": r["position"],
                    "intensity": r["intensity"], "fwhm": r["fwhm"],
                    "area": r["area"], "left_bound": r["left_bound"],
                    "right_bound": r["right_bound"],
                    "manual_override": bool(r["manual_override"]),
                }
                for r in rows
            ]
        }

    def _cmd_peaks_update_bounds(self, p: dict) -> dict:
        """用户编辑峰边界 → 重算 Area(§38)。"""
        row = self.db.query_one(
            "SELECT * FROM peaks WHERE id = ?", (p["peak_id"],),
        )
        if row is None:
            raise EngineError("Peak 不存在")
        analysis = self.db.query_one(
            "SELECT * FROM analyses WHERE id = ?", (row["analysis_id"],),
        )
        curve = self._load_dataset_curve(analysis["dataset_id"])
        params = params_from_json(json.loads(analysis["pipeline_json"]))
        result = run_pipeline(curve, params)
        y_metric = result.metric_curve_y
        peak = Peak(
            index=0, position=row["position"], intensity=row["intensity"],
            fwhm=row["fwhm"], left_bound=float(p["left"]), right_bound=float(p["right"]),
            manual_override=True,
        )
        from ..peaks.metrics import recompute_peak_after_bound_edit

        peak = recompute_peak_after_bound_edit(
            result.curve.x, y_metric, peak, float(p["left"]), float(p["right"])
        )
        self.db.execute(
            """UPDATE peaks SET left_bound=?, right_bound=?, area=?,
                      manual_override=1, updated_at=datetime('now')
               WHERE id=?""",
            (peak.left_bound, peak.right_bound, peak.area, row["id"]),
        )
        return {"peak": peak.to_dict()}

    # ---------- presets(§8.2 支撑场景 S5)----------

    def _cmd_presets_save(self, p: dict) -> dict:
        preset_id = self.db.insert(
            "INSERT INTO analysis_presets (project_id, name, pipeline_json) VALUES (?,?,?)",
            (
                p["project_id"],
                (p.get("name") or "未命名预设").strip(),
                json.dumps(p.get("pipeline") or {}, ensure_ascii=False),
            ),
        )
        return {"presetId": preset_id}

    def _cmd_presets_list(self, p: dict) -> dict:
        rows = self.db.query_all(
            "SELECT * FROM analysis_presets WHERE project_id = ?",
            (p["project_id"],),
        )
        return {
            "presets": [
                {"id": r["id"], "name": r["name"],
                 "pipeline": json.loads(r["pipeline_json"])}
                for r in rows
            ]
        }

    def _cmd_presets_apply(self, p: dict) -> dict:
        """把预设批量应用到指定 datasets(S5)。"""
        preset = self.db.query_one(
            "SELECT * FROM analysis_presets WHERE id = ?", (p["preset_id"],),
        )
        if preset is None:
            raise EngineError("Preset 不存在")
        dataset_ids = p.get("dataset_ids") or []
        applied = 0
        for ds_id in dataset_ids:
            analysis = self._active_analysis(ds_id)
            self.db.execute(
                """UPDATE analyses SET pipeline_json = ?, preset_id = ?,
                       updated_at = datetime('now') WHERE id = ?""",
                (preset["pipeline_json"], preset["id"], analysis["id"]),
            )
            applied += 1
        return {"applied": applied}

    # ---------- library meta ----------

    def _cmd_projects_list(self, p: dict) -> dict:
        rows = self.db.query_all(
            "SELECT * FROM projects WHERE deleted_at IS NULL ORDER BY name"
        )
        return {"projects": [dict(r) for r in rows]}

    def _cmd_projects_create(self, p: dict) -> dict:
        name = (p.get("name") or "").strip()
        if not name:
            raise EngineError("Project 名称不能为空")
        pid = self.db.insert(
            "INSERT INTO projects (name, description) VALUES (?,?)",
            (name, p.get("description", "")),
        )
        return {"projectId": pid}

    def _cmd_library_tree(self, p: dict) -> dict:
        rows = self.db.get_library_tree()
        datasets = self.db.query_all(
            """SELECT id, sample_id, name FROM datasets
               WHERE deleted_at IS NULL ORDER BY name"""
        )
        by_sample: dict[int, list] = {}
        for d in datasets:
            by_sample.setdefault(d["sample_id"], []).append(
                {"id": d["id"], "name": d["name"]}
            )
        out = []
        for r in rows:
            item = dict(r)
            item["datasets"] = by_sample.get(r["id"], [])
            out.append(item)
        return {"samples": out}

    def _cmd_meta_rename(self, p: dict) -> dict:
        table = p["table"]
        if table not in ("projects", "samples", "datasets"):
            raise EngineError("不支持重命名的对象")
        self.db.execute(
            f"UPDATE {table} SET name = ?, updated_at = datetime('now') WHERE id = ?",
            ((p.get("name") or "").strip(), p["id"]),
        )
        return {"id": p["id"]}

    def _cmd_meta_soft_delete(self, p: dict) -> dict:
        self.db.soft_delete(p["table"], p["id"])
        return {"id": p["id"]}

    def close(self) -> None:
        self.db.close()


def _fail(exc) -> dict:
    return {
        "ok": False,
        "error": getattr(exc, "reason", str(exc)),
        "detail": getattr(exc, "detail", ""),
    }
