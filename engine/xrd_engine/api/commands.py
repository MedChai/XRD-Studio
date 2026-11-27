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

import base64
import contextlib
import csv
import hashlib
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from ..coords import DEFAULT_WAVELENGTH, convert
from ..database.db import ALLOWED_TABLES, Database
from ..electrochem.cex_native import CexError
from ..electrochem.cex_viewer import CexViewer
from ..electrochem.chi import ChiError
from ..electrochem.chi_workspace import ChiWorkspace
from ..high_ni import HighNiProfile, analyze_high_ni, default_profile
from ..high_ni.analysis import as_two_theta
from ..importers.loader import (
    ColumnMappingRequired,
    LoadFailure,
    load_file,
)
from ..models.types import (
    BackgroundParams,
    CropParams,
    Curve,
    NormalizationParams,
    Peak,
    PeakDetectionParams,
    PipelineParams,
    ProcessingResult,
    SmoothingParams,
)
from ..processing.downsample import select_minmax_indices
from ..processing.pipeline import PipelineError, run_pipeline, validate_params
from ..services.data_store import DataStore, sha256_of_file
from .envelope import BIN_KEY, pack_arrays

ENGINE_VERSION = "0.4.5"


class EngineError(Exception):
    """携带用户可见中文原因的引擎错误。"""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def _valid_wavelength(value) -> float:
    try:
        wavelength = float(value)
    except (ValueError, TypeError) as exc:
        raise EngineError("波长必须是数字") from exc
    if not 0.3 <= wavelength <= 3.0:
        raise EngineError("波长超出合理范围(0.3–3.0 Å)")
    return wavelength


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


def _dump_candidates(candidates: list[dict] | None) -> str | None:
    """归属候选列表 → JSON 文本,供 peaks 表存储。"""
    if not candidates:
        return None
    return json.dumps(candidates, ensure_ascii=False)


def _stale_domain(result, dataset):
    fingerprint = result.get("pipelineHash")
    if fingerprint:
        if fingerprint != _pipeline_hash(json.loads(dataset["pipeline_json"])):
            return True
    elif dataset["preset_id"] is not None:
        return True
    if result.get("wavelength", DEFAULT_WAVELENGTH) != dataset["wavelength"]:
        return True
    return not result.get("axisConversionVersion") and (
        dataset["x_axis_type"] != "two_theta" or dataset["wavelength"] != DEFAULT_WAVELENGTH
    )


def _trend_metrics(result: dict) -> dict[str, Any]:
    """从一份高镍分析结果里抽出趋势图要用的标量指标。

    **a/c 只认 annotate 那条路径**(全谱精修 + 零点校正)。同一份结果里
    `lattice.a` 走的是 `_lattice_result` 的 3–7 峰最小二乘、零点恒为 0,
    两者数值不同 —— 混用会让趋势线里混进"换了算法"这个伪变化。这里优先
    取全谱精修的结果,取不到就返回 None,由 UI 显示为空心点。
    """
    out: dict[str, Any] = {}
    full = result.get("fullCell") or {}
    if full:
        out["a"] = full.get("a")
        out["c"] = full.get("c")
        if full.get("a") and full.get("c"):
            out["c_over_a"] = full["c"] / full["a"]
        out["rwp"] = full.get("rwp")
        out["source"] = "refine_cell"
    lattice = result.get("lattice") or {}
    if "cell_volume" not in out and lattice.get("cell_volume") is not None:
        out["cell_volume"] = lattice["cell_volume"]
    mixing = result.get("mixing") or {}
    if mixing.get("i003_i104_height_ratio") is not None:
        out["i003_i104"] = mixing["i003_i104_height_ratio"]
    return out


def _load_candidates(raw: str | None) -> list[dict]:
    """peaks 表里的候选列表 JSON → list。坏数据当作"没有候选"。"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _pipeline_hash(pipeline: dict) -> str:
    """pipeline 参数的稳定指纹,用于判断标注是否已过期(stale)。

    只用于比对,不进任何计算缓存键 —— 缓存语义由各自缓存的指纹负责。
    """
    raw = json.dumps(pipeline or {}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _curve_step(curve) -> float:
    """曲线的典型 x 步长(度),用于按位置合并标注标签的容差。"""
    x = np.asarray(curve.x, dtype=np.float64)
    if x.size < 2:
        return 0.02
    return float(np.median(np.diff(x)))


def _merge_annotation_labels(
    peaks: list[dict], annotation: dict | None, step: float
) -> list[dict]:
    """把 annotation_json 里的 labels 快照按位置合回峰列表。

    commit 在参数变更时会删掉 peaks 行,state/preview 拿到的是处理链新算
    的裸峰 —— 不合并的话打开图/拖滑块时标签就消失了。位置对不上(超过
    1.5×步长)的峰保持无标签:宁缺毋滥,错标比不标更糟。
    """
    if not annotation or not peaks:
        return peaks
    labels = annotation.get("labels") or []
    if not labels:
        return peaks
    tol = max(1.5 * abs(step), 0.02)
    for pk in peaks:
        pos = float(pk.get("position") or 0.0)
        best = min(
            labels,
            key=lambda lb: abs(float(lb.get("position") or 0.0) - pos),
        )
        if abs(float(best.get("position") or 0.0) - pos) <= tol:
            pk["hkl"] = best.get("hkl")
            pk["hkl_confidence"] = best.get("hklConfidence")
            pk["dominance"] = best.get("dominance")
            pk["hkl_candidates"] = best.get("candidates") or []
    return peaks


def curve_payload(
    curve,
    background=None,
    *,
    max_points: int | None = None,
    x_min: float | None = None,
    x_max: float | None = None,
) -> dict:
    """Curve → JSON 负载。

    max_points/x_min/x_max 只控制显示数据，不影响处理链和峰指标。返回的
    原始点数与 downsampled 标记供前端判断当前曲线是否为 preview。
    """
    indices = select_minmax_indices(curve.x, curve.y, max_points, x_min, x_max)
    x = curve.x[indices]
    y = curve.y[indices]
    out: dict[str, Any] = {
        "xAxisType": curve.x_axis_type,
        "wavelength": curve.wavelength,
        "pointCount": int(indices.size),
        "sourcePointCount": int(curve.x.size),
        "downsampled": bool(indices.size != curve.x.size),
    }

    # 曲线数组走二进制信封(§9.1),不再走 JSON number[]。
    #
    # 这里是**唯一的曲线出口**(datasets.curve / analysis.preview /
    # datasets.curves 全走它),所以只改这一处就够。背景曲线与主曲线
    # 等长,一并放进同一个信封,省掉第二次序列化。
    arrays: list[tuple[str, np.ndarray]] = [("x", x), ("y", y)]
    if background is not None:
        background_arr = np.asarray(background, dtype=np.float64).ravel()
        if background_arr.size != curve.x.size:
            raise ValueError("background 与曲线长度不一致")
        arrays.append(("background", background_arr[indices]))

    header, body = pack_arrays(arrays)
    out[BIN_KEY] = header
    # 二进制体挂在保留键上,由 write_frame 取出后拼到帧尾。
    # 用 _binary 而不是直接返回 bytes:curve_payload 的返回值要能被
    # json.dumps 序列化(测试与 dev_server 都直接 dumps 它)。
    out["_binary"] = body
    return out


class Engine:
    """持有 Database + DataStore 的引擎实例。"""

    def __init__(self, data_root: str | Path, *, log_dir: str | Path | None = None):
        self.store = DataStore(data_root)
        self.log_dir = Path(log_dir) if log_dir else self.store.root / "logs"
        self.db = Database(self.store.db_path)
        self.cex = CexViewer(self.store, self.db)
        self.chi = ChiWorkspace(self.store, self.db)
        # 已解析曲线缓存(§9.3)。见 _load_dataset_curve 的说明。
        self._curve_cache: dict[int, tuple[tuple, Curve]] = {}

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
        except (CexError, ChiError) as exc:
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
                  batch_id?, batch_name?, x_column?, y_column?,
                  x_axis_type?, wavelength?, import_anyway?: bool}

        **归属三选一**(用户反馈的核心):复用已有 sample_id、新建
        new_sample、或直接挂到项目下。此前只有"新建样品"一条路,于是每导
        一次就多出一个样品文件夹,用户没有任何办法把一批文件归到一起。

        batch_id / batch_name 是可选的第三层:同一次制样、同一批送样的
        多个数据集归一组。两者都不给时不建批次,数据集直接挂样品 ——
        不强迫用户维护一个他不需要的层级。
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

        supplied = p.get("wavelength")
        acquisition_wavelength = None if supplied is None else _valid_wavelength(supplied)
        sample_id = p.get("sample_id")
        if sample_id is None:
            ns = p.get("new_sample") or {}
            sample_wavelength = _valid_wavelength(ns.get("wavelength", DEFAULT_WAVELENGTH))
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
                    sample_wavelength,
                ),
            )
        else:
            sample = self.db.query_one(
                "SELECT wavelength FROM samples WHERE id = ? AND project_id = ? AND deleted_at IS NULL",
                (sample_id, project_id),
            )
            if sample is None:
                raise EngineError("目标 Sample 不存在")
            sample_wavelength = sample["wavelength"] or DEFAULT_WAVELENGTH

        # 批次:给了 id 就复用,给了 name 就新建,都没给则不分组。
        batch_id = p.get("batch_id")
        if batch_id is not None:
            if self.db.query_one(
                "SELECT id FROM batches WHERE id = ? AND sample_id = ? AND deleted_at IS NULL",
                (batch_id, sample_id),
            ) is None:
                raise EngineError("目标批次不存在")
        else:
            batch_name = (p.get("batch_name") or "").strip()
            if batch_name:
                # 同名批次复用。**不能每次导入都新建**:用户分两次把同一批
                # 文件导进来(或第一次有文件需要手选列、第二次才成功)时,
                # 会得到两个同名批次,而界面上完全看不出区别 —— 数据看似
                # 归好了组,实际被劈成两半。
                existing = self.db.query_one(
                    """SELECT id FROM batches
                       WHERE sample_id = ? AND name = ? AND deleted_at IS NULL""",
                    (sample_id, batch_name),
                )
                if existing is not None:
                    batch_id = existing["id"]
                else:
                    batch_id = self.db.insert(
                        "INSERT INTO batches (sample_id, name, source_path) "
                        "VALUES (?,?,?)",
                        (sample_id, batch_name, str(p.get("batch_source_path") or "")),
                    )

        imported: list[dict] = []
        duplicates: list[dict] = []
        need_columns: list[dict] = []
        failures: list[dict] = []

        for path in paths:
            result = load_file(
                path,
                x_axis_type=p.get("x_axis_type", "two_theta"),
                wavelength=acquisition_wavelength or sample_wavelength,
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

            # A v7 wide table cannot be reopened until its owner supplies the columns again.
            if p.get("x_column") is not None and p.get("y_column") is not None and not p.get("import_anyway"):
                legacy = self.db.query_one(
                    """SELECT id, name, stored_path, x_axis_type FROM datasets
                       WHERE file_hash = ? AND sample_id = ? AND x_column IS NULL
                         AND y_column IS NULL AND deleted_at IS NULL ORDER BY id LIMIT 1""",
                    (file_hash, sample_id),
                )
                if legacy and isinstance(load_file(self.store.abs_path(legacy["stored_path"])), ColumnMappingRequired):
                    self.db.execute(
                        """UPDATE datasets SET x_column=?, y_column=?, x_min=?, x_max=?, step=?,
                           point_count=?, grid=? WHERE id=?""",
                        (*ds.source_columns, float(ds.curve.x[0]), float(ds.curve.x[-1]),
                         float(np.median(np.diff(ds.curve.x))), ds.curve.point_count, ds.original_grid, legacy["id"]),
                    )
                    imported.append({"id": legacy["id"], "name": legacy["name"]})
                    continue

            # 查重(§8.4)
            dup = self.db.find_duplicate_by_hash(file_hash)
            if dup is not None and not p.get("import_anyway"):
                duplicates.append({
                    "path": str(path),
                    "existing": {
                        # datasetId 让前端能提供「打开已有」而不是只能放弃
                        "datasetId": dup["id"],
                        "projectName": dup["project_name"],
                        "sampleName": dup["sample_name"],
                        "datasetName": dup["name"],
                    },
                })
                continue

            stored, copied_hash = self.store.import_file(path, {
                "project_id": project_id,
                "sample_id": sample_id,
            }, project_folder=self.db.project_storage_folder(project_id))
            if copied_hash != file_hash:
                self.store.remove_stored(stored)
                failures.append({"path": str(path), "reason": "复制期间源文件已更改，请重新导入"})
                continue
            name = Path(path).name
            try:
                with self.db.transaction():
                    dataset_id = self.db.insert(
                        """INSERT INTO datasets
                           (sample_id, name, original_filename, source_path, stored_path,
                            file_hash, x_axis_type, x_min, x_max, step, point_count, grid,
                            batch_id, x_column, y_column, acquisition_wavelength)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            sample_id, name, name, str(Path(path).resolve()), stored,
                            file_hash, ds.curve.x_axis_type,
                            float(ds.curve.x[0]), float(ds.curve.x[-1]),
                            float(np.median(np.diff(ds.curve.x))), int(ds.curve.x.size),
                            ds.original_grid, batch_id, *ds.source_columns, acquisition_wavelength,
                        ),
                    )
                    self.db.insert(
                        "INSERT INTO analyses (dataset_id, pipeline_json) VALUES (?,?)",
                        (dataset_id, "{}"),
                    )
            except Exception:
                self.store.remove_stored(stored)
                raise
            imported.append({"id": dataset_id, "name": name})

        return {
            "imported": imported,
            "duplicates": duplicates,
            "needColumns": need_columns,
            "failures": failures,
            "sampleId": sample_id,
            "batchId": batch_id,
        }

    def _cmd_datasets_scan_folder(self, p: dict) -> dict:
        """扫描一个文件夹并按文件名给出**建议的**实验分组(不落库)。

        用户的真实数据按文件夹组织(一个文件夹 = 一次实验)。这里只做建议:
        分组结果由前端展示、允许改名与拖动,用户确认后才调 datasets.import。
        **不在这个命令里直接导入** —— 自动分组猜错的代价是数据已经落库、
        用户很难发现哪几条归错了,所以确认步骤必须存在。

        payload: {folder}。返回 {folder, files, groups:[{name, paths}]}。
        """
        from ..importers.grouping import group_paths, scan_folder
        from ..importers.loader import SUPPORTED_EXTENSIONS

        folder = str(p.get("folder") or "").strip()
        if not folder:
            raise EngineError("没有指定文件夹")
        files = scan_folder(folder, SUPPORTED_EXTENSIONS)
        if not files:
            raise EngineError(
                "该文件夹下没有可导入的数据文件",
                detail=f"支持的扩展名:{', '.join(sorted(SUPPORTED_EXTENSIONS))}",
            )
        return {
            "folder": folder,
            "files": files,
            "groups": [g.as_dict() for g in group_paths(files)],
        }

    def _cmd_datasets_curve(self, p: dict) -> dict:
        """返回原始曲线(未处理)。

        payload 可选 {max_points, x_min, x_max}，只影响显示返回，不改变原始数据。
        """
        dataset_id = p.get("id")
        if dataset_id is None:
            raise EngineError("必须提供数据集 id")
        # 走 _load_dataset_curve 而不是自己读盘:那条路径有 §9.3 的解析缓存,
        # 且波长处理只有一份实现。此前这里重复了一遍读盘逻辑,两处一旦
        # 分叉(比如一边记得传波长一边忘了)就会静默给出不同的曲线。
        curve = self._load_dataset_curve(int(dataset_id))
        return curve_payload(
            curve,
            max_points=p.get("max_points"),
            x_min=p.get("x_min"),
            x_max=p.get("x_max"),
        )

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
        # **先查缓存(§9.3)。** 每调一次 preview 都要重新读盘 + 重新解析
        # 文本 —— 4751 点的文件约 10 ms,看似不多,但前端拖滑块时每秒数十
        # 次往返,而且 datasets.curve 与 analysis.preview 各读一遍。
        #
        # 指纹用 (stored_path, file_hash, x_axis_type, wavelength) 而不是
        # 只用 id:数据集被删后 id 可能复用,只按 id 缓存会拿到上一条曲线
        # (与 annotate._FIT_CACHE 同一个理由)。
        wavelength = self._dataset_wavelength(int(dataset_id))
        fingerprint = (
            row["stored_path"],
            row["file_hash"],
            row["x_axis_type"],
            wavelength, row["x_column"], row["y_column"],
        )
        hit = self._curve_cache.get(int(dataset_id))
        if hit is not None and hit[0] == fingerprint:
            return hit[1]

        path = self.store.abs_path(row["stored_path"])
        # 波长必须传:Co/Fe 靶数据按 Cu 处理会把所有反射位置整体带偏
        # ((003) 在 Co 靶下是 21.7° 而非 18.7°),而且它是 x 轴换算
        # (2θ↔d↔Q)与高镍归属的共同基准。此前这两个读取路径都只传了
        # x_axis_type,波长静默退回默认的 1.5418。
        result = load_file(
            path,
            x_axis_type=row["x_axis_type"], wavelength=wavelength,
            x_column=row["x_column"], y_column=row["y_column"],
        )
        if isinstance(result, ColumnMappingRequired):
            raise EngineError("旧数据集缺少列映射，请重新导入原文件并指定角度列和强度列", result.reason)
        if not hasattr(result, "curve"):
            raise EngineError("无法读取数据文件", str(result))

        # 有界缓存:32 条曲线足够覆盖"来回切换几个数据集"的实际用法,
        # 而每条 4751 点的曲线约 76 KB,总量可控。FIFO 淘汰。
        if len(self._curve_cache) >= 32:
            self._curve_cache.pop(next(iter(self._curve_cache)))
        self._curve_cache[int(dataset_id)] = (fingerprint, result.curve)
        return result.curve

    def _cmd_analysis_preview(self, p: dict) -> dict:
        """按参数求值处理链但**不落库**(§10.3 previewing 态)。

        payload: {dataset_id, pipeline: {...}, annotate?: bool,
                  restoreAnnotation?: bool}

        普通滑块预览关闭高镍晶面标注(annotate=false):晶面标注需要全谱
        晶胞精修,不该阻塞拖动。`restoreAnnotation=true` 时把已持久化的
        annotation_json.labels 按位置合回返回的峰 —— 纯查库+数组匹配,
        无任何拟合计算,标签不会在拖滑块时消失。
        """
        curve = self._load_dataset_curve(p["dataset_id"])
        params = params_from_json(p.get("pipeline") or {})
        report = validate_params(curve, params)
        if not report.ok:
            raise EngineError(";".join(report.errors))
        # 拖动中前端传 compute_peak_metrics=false:跳过 FWHM/Area,只保
        # 曲线形状跟手。收尾 preview 再传 true 把度量补回来(§9.2)。
        result = run_pipeline(
            curve,
            params,
            compute_peak_metrics=bool(p.get("compute_peak_metrics", True)),
        )
        annotation = None
        if p.get("annotate", False):
            annotation = self._annotate_pipeline_peaks(result, int(p["dataset_id"]))
        peaks = [pk.to_dict() for pk in result.peaks]
        if not p.get("annotate", False) and p.get("restoreAnnotation"):
            analysis = self._active_analysis(int(p["dataset_id"]))
            stored_annotation = self._load_annotation(analysis["id"])
            _merge_annotation_labels(peaks, stored_annotation, _curve_step(curve))
        return {
            "curve": curve_payload(
                result.curve,
                result.background,
                max_points=p.get("max_points"),
                x_min=p.get("x_min"),
                x_max=p.get("x_max"),
            ),
            "peaks": peaks,
            "smoothingAffectsMetrics": result.metrics_affected_by_smoothing,
            "warnings": result.warnings,
            "annotation": annotation,
            "pipelineHash": _pipeline_hash(p.get("pipeline") or {}),
        }

    def _cmd_analysis_commit(self, p: dict) -> dict:
        """把 preview 满意后的参数落库(§10.3 committing 态)。"""
        dataset_id = p["dataset_id"]
        analysis = self._active_analysis(dataset_id)
        pipeline = p.get("pipeline") or {}
        with self.db.transaction() as conn:
            self._write_pipeline(conn, analysis, pipeline, analysis["preset_id"])
        return {"analysisId": analysis["id"]}

    def _write_pipeline(self, conn, analysis, pipeline, preset_id):
        analysis_id = analysis["id"]
        if json.loads(analysis["pipeline_json"] or "{}") != pipeline:
            conn.execute("DELETE FROM peaks WHERE analysis_id = ?", (analysis_id,))
            conn.execute("DELETE FROM domain_results WHERE analysis_id = ?", (analysis_id,))
        conn.execute(
            """UPDATE analyses SET pipeline_json = ?, preset_id = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (json.dumps(pipeline, ensure_ascii=False), preset_id, analysis_id),
        )

    def _cmd_analysis_state(self, p: dict) -> dict:
        """读取并快速重建一个数据集的已保存分析状态。

        这是切换数据集的专用路径:恢复 pipeline 与已固化峰,同时只跑处理链,
        不做高镍晶胞精修。精修仍由 peaks.detect / peaks.annotate / domain
        分析显式触发,所以不会牺牲正式结果的精度。
        """
        dataset_id = p.get("dataset_id")
        if dataset_id is None:
            raise EngineError("必须提供 dataset_id")
        analysis = self._active_analysis(int(dataset_id))
        try:
            pipeline = json.loads(analysis["pipeline_json"] or "{}")
        except json.JSONDecodeError:
            pipeline = {}
        if not isinstance(pipeline, dict):
            pipeline = {}

        curve = self._load_dataset_curve(int(dataset_id))
        params = params_from_json(pipeline)
        report = validate_params(curve, params)
        if not report.ok:
            raise EngineError(";".join(report.errors))
        result = run_pipeline(curve, params)
        stored = self._cmd_peaks_list({"analysis_id": analysis["id"]})["peaks"]
        annotation = self._load_annotation(analysis["id"])
        peaks = stored or [pk.to_dict() for pk in result.peaks]
        if not stored:
            # peaks 行被 commit 删过(参数变更)或从未 detect —— 用
            # annotation_json 的 labels 快照按位置合回,打开图即见标签。
            _merge_annotation_labels(peaks, annotation, _curve_step(curve))
        return {
            "analysisId": analysis["id"],
            "pipeline": pipeline,
            "curve": curve_payload(
                result.curve,
                result.background,
                max_points=p.get("max_points"),
                x_min=p.get("x_min"),
                x_max=p.get("x_max"),
            ),
            "peaks": peaks,
            "peaksStored": bool(stored),
            "annotation": annotation,
            "pipelineHash": _pipeline_hash(pipeline),
            "smoothingAffectsMetrics": result.metrics_affected_by_smoothing,
            "warnings": result.warnings,
        }

    def _load_annotation(self, analysis_id: int) -> dict | None:
        """读 analyses.annotation_json;无/坏数据返回 None。"""
        row = self.db.query_one(
            "SELECT dataset_id, annotation_json FROM analyses WHERE id = ?", (analysis_id,)
        )
        if row is None or not row["annotation_json"]:
            return None
        try:
            data = json.loads(row["annotation_json"])
        except (TypeError, json.JSONDecodeError):
            return None
        if (isinstance(data, dict) and data.get("model") == "high_ni_layered"
                and data.get("wavelength") == self._dataset_wavelength(row["dataset_id"])):
            return data
        return None

    def _save_annotation(
        self, analysis_id: int, context: dict | None, pipeline: dict, peaks: list
    ) -> dict | None:
        """把一次标注的上下文 + labels 快照写入 analyses.annotation_json。

        存 labels 快照而非只存 context:commit 会删 peaks 行,之后打开图
        靠这份快照按位置把 hkl 合回新检出的峰,标注才不会蒸发。
        """
        if not context:
            self.db.execute("UPDATE analyses SET annotation_json = NULL WHERE id = ?", (analysis_id,))
            return None
        payload = dict(context, model="high_ni_layered")
        payload["pipelineHash"] = _pipeline_hash(pipeline)
        payload["annotatedAt"] = datetime.now().isoformat(timespec="seconds")
        payload["labels"] = [
            {
                "position": pk.position,
                "hkl": pk.hkl,
                "hklConfidence": pk.hkl_confidence,
                "dominance": pk.dominance,
                "candidates": pk.hkl_candidates or [],
            }
            for pk in peaks
        ]
        self.db.execute(
            "UPDATE analyses SET annotation_json = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), analysis_id),
        )
        return payload

    def _peak_geometry(self, analysis):
        dataset = self.db.query_one("SELECT x_axis_type FROM datasets WHERE id = ?", (analysis["dataset_id"],))
        axis = dataset["x_axis_type"]
        signature = {"pipeline": json.loads(analysis["pipeline_json"]), "axis": axis}
        if axis in ("d", "q"):
            signature["wavelength"] = self._dataset_wavelength(analysis["dataset_id"])
        return axis, _pipeline_hash(signature)

    def _peaks_current(self, analysis):
        axis, current = self._peak_geometry(analysis)
        if not analysis["peaks_pipeline_hash"]:
            return analysis["preset_id"] is None and axis not in ("d", "q")
        return analysis["peaks_pipeline_hash"] == current

    def _insert_peaks(self, analysis_id: int, peaks: list) -> None:
        """全量写入(检测路径与空表时的标注路径共用)。

        记住每个峰写入后的主键:后续 peaks.annotate 按主键更新,
        不再用 position 做浮点相等匹配(见 _cmd_peaks_annotate 的说明)。
        """
        for pk in peaks:
            pk.row_id = self.db.insert(
                """INSERT INTO peaks
                   (analysis_id, position, intensity, fwhm, area,
                    left_bound, right_bound, manual_override, hkl, hkl_confidence,
                    dominance, hkl_candidates)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    analysis_id, pk.position, pk.intensity, pk.fwhm,
                    pk.area, pk.left_bound, pk.right_bound,
                    int(pk.manual_override), pk.hkl, pk.hkl_confidence,
                    pk.dominance, _dump_candidates(pk.hkl_candidates),
                ),
            )

        analysis = self.db.query_one("SELECT * FROM analyses WHERE id = ?", (analysis_id,))
        self.db.execute("UPDATE analyses SET peaks_pipeline_hash = ? WHERE id = ?",
                        (self._peak_geometry(analysis)[1], analysis_id))

    def _annotate_pipeline_peaks(self, result: ProcessingResult, dataset_id: int) -> dict | None:
        """就地给 pipeline 输出的峰打上晶面标注,返回本次拟合上下文。

        走高镍层状路径:对本张谱精修晶胞后按强度贡献归属(见
        peaks/annotate.py 的模块说明)。波长取自数据集所属样品的设置 ——
        用错波长会把所有反射位置整体带偏(Co 靶下 (003) 在 21.7° 而非
        18.7°),旧实现固定用 Cu Kα,非 Cu 数据全部标错。

        **三态都要写回**:reliable / ambiguous / none。旧实现只在
        hkl_label 为真时赋值,导致 ambiguous 与 none 的峰 hkl 保持 NULL,
        与"尚未标注"无法区分,UI 只能显示 "—"。
        """
        from ..peaks.annotate import annotate_high_ni

        wavelength = self._dataset_wavelength(dataset_id)
        curve = self._load_dataset_curve(dataset_id)
        x, y, _warnings = as_two_theta(curve)
        if curve.x_axis_type == "generic":
            raise EngineError("晶面标注需要 2θ、d 或 Q 采集轴")
        angular_peaks = result.peaks
        if curve.x_axis_type != "two_theta":
            with np.errstate(divide="ignore", invalid="ignore"):
                positions = convert([pk.position for pk in result.peaks], curve.x_axis_type, "two_theta", wavelength)
            angular_peaks = [replace(pk, position=float(pos)) for pk, pos in zip(result.peaks, positions, strict=True)
                             if np.isfinite(pos) and pos > 0]
        annotated, context = annotate_high_ni(
            x, y, angular_peaks,
            wavelength=wavelength, dataset_id=dataset_id,
        )
        by_index = {a.index: a for a in annotated}
        for peak in result.peaks:
            ann = by_index.get(peak.index)
            if ann is None:
                continue
            peak.hkl = ann.hkl_label
            peak.hkl_confidence = ann.hkl_confidence
            # 归属依据也要一路带到 UI。annotate 里算好的 dominance 与
            # candidates 此前在这一步被丢掉,前端只能显示"贡献 0%"。
            peak.dominance = ann.dominance
            peak.hkl_candidates = ann.candidates or None
        return dict(context.as_dict(), wavelength=wavelength) if context else None

    def _dataset_wavelength(self, dataset_id: int) -> float:
        """数据集所属样品的波长,缺省 Cu Kα。"""
        row = self.db.query_one(
            """SELECT COALESCE(d.acquisition_wavelength, s.wavelength) AS wavelength FROM datasets d
               JOIN samples s ON d.sample_id = s.id
               WHERE d.id = ?""",
            (dataset_id,),
        )
        if row is None or not row["wavelength"]:
            return 1.5418
        return float(row["wavelength"])

    def _cmd_peaks_detect(self, p: dict) -> dict:
        """对已保存的 analysis 正式跑一遍寻峰并把结果写入 peaks 表。

        payload 接受 `analysis_id` **或** `dataset_id` 二选一:前端手里通常
        只有 dataset_id(它不关心 analysis 的内部 id),支持 dataset_id 能
        省掉一次多余往返。
        """
        analysis_id = p.get("analysis_id")
        if analysis_id is None:
            dataset_id = p.get("dataset_id")
            if dataset_id is None:
                raise EngineError("必须提供 analysis_id 或 dataset_id")
            analysis = self._active_analysis(dataset_id)
        else:
            analysis = self.db.query_one(
                "SELECT * FROM analyses WHERE id = ?", (analysis_id,)
            )
            if analysis is None:
                raise EngineError("Analysis 不存在")
        curve = self._load_dataset_curve(analysis["dataset_id"])
        params = params_from_json(json.loads(analysis["pipeline_json"]))
        result = run_pipeline(curve, params)
        annotation = None
        if self._load_annotation(analysis["id"]) or p.get("annotate", False):
            annotation = self._annotate_pipeline_peaks(result, int(analysis["dataset_id"]))
        with self.db.transaction():
            self.db.execute("DELETE FROM peaks WHERE analysis_id = ?", (analysis["id"],))
            self._insert_peaks(analysis["id"], result.peaks)
            # 检测也是一次完整标注 —— 同步固化 annotation_json,否则只有
            # detect 过的谱重启后有 hkl 却没有"标注依据"卡(context 丢失)。
            saved = self._save_annotation(
                analysis["id"], annotation,
                json.loads(analysis["pipeline_json"] or "{}"), result.peaks,
            )
        return {
            "peaks": [pk.to_dict() for pk in result.peaks],
            "annotation": saved,
        }

    def _cmd_peaks_list(self, p: dict) -> dict:
        analysis = self.db.query_one("SELECT * FROM analyses WHERE id = ?", (p["analysis_id"],))
        if analysis is None or not self._peaks_current(analysis):
            return {"peaks": []}
        rows = self.db.query_all(
            "SELECT * FROM peaks WHERE analysis_id = ? ORDER BY position",
            (p["analysis_id"],),
        )
        verified = self._load_annotation(p["analysis_id"]) is not None
        return {
            "peaks": [
                {
                    "index": i,
                    "id": r["id"], "position": r["position"],
                    "intensity": r["intensity"], "fwhm": r["fwhm"],
                    "area": r["area"], "left_bound": r["left_bound"],
                    "right_bound": r["right_bound"],
                    "manual_override": bool(r["manual_override"]),
                    "hkl": r["hkl"] if verified else None,
                    "hkl_confidence": r["hkl_confidence"] if verified else None,
                    "dominance": r["dominance"] if verified else None,
                    "hkl_candidates": _load_candidates(r["hkl_candidates"]) if verified else [],
                }
                for i, r in enumerate(rows)
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

    def _cmd_peaks_annotate(self, p: dict) -> dict:
        """对已保存的峰做晶面(hkl)标注,并写回 peaks 表。

        payload: {dataset_id} 或 {analysis_id}。
        每次调用都重跑 pipeline + 标注,保证与当前峰一致。
        """
        analysis_id = p.get("analysis_id")
        if analysis_id is None:
            dataset_id = p.get("dataset_id")
            if dataset_id is None:
                raise EngineError("必须提供 analysis_id 或 dataset_id")
            analysis = self._active_analysis(dataset_id)
        else:
            analysis = self.db.query_one(
                "SELECT * FROM analyses WHERE id = ?", (analysis_id,)
            )
            if analysis is None:
                raise EngineError("Analysis 不存在")

        curve = self._load_dataset_curve(analysis["dataset_id"])
        params = params_from_json(json.loads(analysis["pipeline_json"] or "{}"))
        result = run_pipeline(curve, params)
        context = self._annotate_pipeline_peaks(result, int(analysis["dataset_id"]))

        # 按**主键**写回,不按 position。
        #
        # 旧实现用 `WHERE analysis_id = ? AND position = ?` 且比对的是
        # round(position, 6),而 peaks 表里存的是未舍入的双精度值。
        # SQLite 的 REAL 比较是精确的 IEEE-754 比较:实测
        # position=34.385664000000006 与 round(...,6)=34.385664 不相等,
        # UPDATE 匹配 0 行**且不报错** —— 命令仍然返回 count>0,前端仍
        # 从返回值里合并出正确的标签,只有重新加载 / 导出 CSV 时才暴露出
        # 数据库里根本没有写进去。用主键就不存在这个问题。
        with self.db.transaction():
            existing = self.db.query_all(
                "SELECT id, position FROM peaks WHERE analysis_id = ? ORDER BY id",
                (analysis["id"],),
            )
            if existing and not self._peaks_current(analysis):
                self.db.execute("DELETE FROM peaks WHERE analysis_id = ?", (analysis["id"],))
                existing = []
            if not existing:
                # peaks 表为空(用户没先点"检测峰",或参数变更后行被 commit
                # 删除):只 UPDATE 会一行都写不进去,标注从不落库。这里改为
                # 与 detect 相同的全量 INSERT,保证 annotate 单独调用也能存。
                self._insert_peaks(analysis["id"], result.peaks)
                updated = len(result.peaks)
            else:
                # 同一 analysis 下 peaks 表与本次 pipeline 结果一一对应(pipeline
                # 是确定性的,同一参数必然得到同一批峰)。按位置排序后配对,避免
                # 依赖 id 与 index 恰好相等。
                by_position = sorted(existing, key=lambda r: r["position"])
                ordered_peaks = sorted(result.peaks, key=lambda pk: pk.position)
                updated = 0
                for row, peak in zip(by_position, ordered_peaks, strict=False):
                    self.db.execute(
                        """UPDATE peaks SET hkl = ?, hkl_confidence = ?,
                                  dominance = ?, hkl_candidates = ?,
                                  updated_at = datetime('now')
                           WHERE id = ?""",
                        (
                            peak.hkl, peak.hkl_confidence,
                            peak.dominance, _dump_candidates(peak.hkl_candidates),
                            row["id"],
                        ),
                    )
                    updated += 1

            # 点「标注晶面」= 权威刷新:固化 annotation_json(覆盖旧快照,
            # pipelineHash 随之更新,前端 stale 徽标自然清除)。
            try:
                pipeline = json.loads(analysis["pipeline_json"] or "{}")
            except json.JSONDecodeError:
                pipeline = {}
            saved = self._save_annotation(
                analysis["id"], context, pipeline, result.peaks
            )
        annotated = [
            {
                "index": pk.index,
                "position": pk.position,
                "hkl_label": pk.hkl,
                "hkl_confidence": pk.hkl_confidence,
                "dominance": pk.dominance,
                "candidates": pk.hkl_candidates or [],
            }
            for pk in result.peaks
        ]
        return {
            "annotated": annotated,
            "peaks": self._cmd_peaks_list({"analysis_id": analysis["id"]})["peaks"],
            "count": sum(1 for pk in result.peaks if pk.hkl),
            "updated": updated,
            "annotation": saved if saved is not None else context,
        }

    # ---------- exports ----------

    def _export_target(
        self,
        requested: str | None,
        default_filename: str,
        overwrite: bool = False,
    ) -> Path:
        """Resolve an export target without silently overwriting a file.

        A path supplied by the Tauri save dialog is intentionally allowed to
        live outside DataStore.  When no path is supplied, exports stay in the
        managed ``exports`` directory.
        """
        raw = str(requested or "").strip()
        target = Path(raw).expanduser() if raw else self.store.export_path(default_filename)
        if not target.suffix:
            target = target.with_suffix(Path(default_filename).suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        if overwrite:
            return target

        candidate = target
        index = 1
        while candidate.exists():
            candidate = target.with_name(
                f"{target.stem}_{index}{target.suffix}"
            )
            index += 1
        return candidate

    def _export_context(self, p: dict):
        dataset_id = p.get("dataset_id")
        analysis_id = p.get("analysis_id")
        if dataset_id is None and analysis_id is None:
            raise EngineError("导出必须提供 dataset_id 或 analysis_id")

        if analysis_id is not None:
            analysis = self.db.query_one(
                "SELECT * FROM analyses WHERE id = ?", (analysis_id,)
            )
            if analysis is None:
                raise EngineError("Analysis 不存在")
            dataset_id = analysis["dataset_id"]
        else:
            analysis = self._active_analysis(int(dataset_id))

        dataset = self.db.query_one(
               """SELECT d.*, s.name AS sample_name,
                         COALESCE(d.acquisition_wavelength, s.wavelength, 1.5418) AS wavelength,
                         p.name AS project_name
               FROM datasets d
               JOIN samples s ON d.sample_id = s.id
               JOIN projects p ON s.project_id = p.id
               WHERE d.id = ? AND d.deleted_at IS NULL
                 AND s.deleted_at IS NULL AND p.deleted_at IS NULL""",
            (dataset_id,),
        )
        if dataset is None:
            raise EngineError("Dataset 不存在或已在回收站中")
        return dataset, analysis

    def _export_metadata(self, dataset, analysis, params: PipelineParams) -> list[str]:
        exported_at = datetime.now().astimezone().isoformat(timespec="seconds")
        return [
            f"# XRD Studio version: {ENGINE_VERSION}",
            f"# exported_at: {exported_at}",
            f"# dataset_id: {dataset['id']}",
            f"# dataset_name: {dataset['name']}",
            f"# sample_name: {dataset['sample_name']}",
            f"# project_name: {dataset['project_name']}",
            f"# original_filename: {dataset['original_filename']}",
            f"# source_file_hash_sha256: {dataset['file_hash']}",
            f"# x_axis_type: {dataset['x_axis_type']}",
            f"# wavelength_angstrom: {dataset['wavelength']}",
            f"# analysis_id: {analysis['id']}",
            "# pipeline_json: " + json.dumps(
                params.to_snapshot(), ensure_ascii=False, separators=(",", ":")
            ),
        ]

    def _cmd_export_peaks(self, p: dict) -> dict:
        """Export the active analysis peak table as an auditable CSV."""
        dataset, analysis = self._export_context(p)
        raw_curve = self._load_dataset_curve(dataset["id"])
        params = params_from_json(json.loads(analysis["pipeline_json"] or "{}"))
        result = run_pipeline(raw_curve, params)

        computed_by_position = {
            round(float(pk.position), 8): pk.prominence for pk in result.peaks
        }
        rows = self.db.query_all(
            "SELECT * FROM peaks WHERE analysis_id = ? ORDER BY position",
            (analysis["id"],),
        )
        if rows and self._peaks_current(analysis):
            verified = self._load_annotation(analysis["id"]) is not None
            peaks = [
                {
                    "peak_index": i,
                    "position": r["position"],
                    "intensity": r["intensity"],
                    "fwhm": r["fwhm"],
                    "area": r["area"],
                    "left_bound": r["left_bound"],
                    "right_bound": r["right_bound"],
                    "prominence": computed_by_position.get(
                        round(float(r["position"]), 8)
                    ),
                    "manual_override": bool(r["manual_override"]),
                    "hkl": r["hkl"] if verified else None,
                }
                for i, r in enumerate(rows)
            ]
        else:
            peaks = [
                {
                    "peak_index": pk.index,
                    "position": pk.position,
                    "intensity": pk.intensity,
                    "fwhm": pk.fwhm,
                    "area": pk.area,
                    "left_bound": pk.left_bound,
                    "right_bound": pk.right_bound,
                    "prominence": pk.prominence,
                    "manual_override": pk.manual_override,
                    "hkl": pk.hkl,
                }
                for pk in result.peaks
            ]

        target = self._export_target(
            p.get("path"),
            f"{Path(dataset['name']).stem}_peaks.csv",
            bool(p.get("overwrite", False)),
        )
        with target.open("w", encoding="utf-8-sig", newline="") as stream:
            stream.write("\n".join(self._export_metadata(dataset, analysis, params)))
            stream.write("\n")
            writer = csv.DictWriter(
                stream,
                fieldnames=[
                    "dataset_id", "sample_name", "peak_index", "position",
                    "intensity", "fwhm", "area", "left_bound", "right_bound",
                    "prominence", "manual_override", "hkl",
                ],
            )
            writer.writeheader()
            for peak in peaks:
                writer.writerow({
                    "dataset_id": dataset["id"],
                    "sample_name": dataset["sample_name"],
                    **peak,
                })
        return {"path": str(target), "analysisId": analysis["id"], "count": len(peaks)}

    def _cmd_export_data(self, p: dict) -> dict:
        """Export raw, background and processed values row-by-row as CSV."""
        dataset, analysis = self._export_context(p)
        raw_curve = self._load_dataset_curve(dataset["id"])
        params = params_from_json(json.loads(analysis["pipeline_json"] or "{}"))
        result = run_pipeline(raw_curve, params)
        raw_y = np.interp(result.curve.x, raw_curve.x, raw_curve.y)
        background = result.background
        target = self._export_target(
            p.get("path"),
            f"{Path(dataset['name']).stem}_processed.csv",
            bool(p.get("overwrite", False)),
        )
        with target.open("w", encoding="utf-8-sig", newline="") as stream:
            stream.write("\n".join(self._export_metadata(dataset, analysis, params)))
            stream.write("\n")
            writer = csv.writer(stream)
            writer.writerow(["x", "raw_y", "background", "processed_y"])
            for i, x_value in enumerate(result.curve.x):
                writer.writerow([
                    float(x_value),
                    float(raw_y[i]),
                    "" if background is None else float(background[i]),
                    float(result.curve.y[i]),
                ])
        return {
            "path": str(target),
            "analysisId": analysis["id"],
            "count": int(result.curve.x.size),
            "warnings": result.warnings,
        }

    def _cmd_export_figure(self, p: dict) -> dict:
        """把前端图表生成的 base64 PNG 落盘。

        PNG 的像素由前端 ECharts 渲出，这里只负责写文件 —— 但**溯源信息不能省**：
        CSV 导出带 dataset/hash/pipeline，PNG 不带的话，一张图放在报告里就再也
        说不清是哪条曲线、什么参数画的。此前这个命令完全不调 `_export_context`，
        前端传的 dataset_id 被静默丢弃（导出因此没有任何溯源头）。
        """
        encoded = str(p.get("png_base64") or "")
        if not encoded:
            raise EngineError("没有可导出的图像数据")
        try:
            data = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise EngineError("图像数据格式错误", detail=str(exc)) from exc
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise EngineError("仅支持 PNG 图像导出")

        # 有 dataset_id / analysis_id 时补一份旁车溯源文件（同目录同名 .txt）。
        # 都没有时不阻断导出 —— 将来的模板图可能不属于任何数据集。
        provenance: list[str] = []
        if p.get("dataset_id") is not None or p.get("analysis_id") is not None:
            dataset, analysis = self._export_context(p)
            params = params_from_json(json.loads(analysis["pipeline_json"] or "{}"))
            provenance = self._export_metadata(dataset, analysis, params)
        elif isinstance(p.get("cex_dqdv"), dict):
            provenance = [json.dumps(p["cex_dqdv"], ensure_ascii=False, indent=2)]

        target = self._export_target(
            p.get("path"),
            str(p.get("filename") or "xrd_figure.png"),
            bool(p.get("overwrite", False)),
        )
        target.write_bytes(data)
        out: dict = {"path": str(target), "bytes": len(data)}
        if provenance:
            sidecar = target.with_suffix(target.suffix + ".txt")
            sidecar.write_text("\n".join(provenance) + "\n", encoding="utf-8")
            out["metadataPath"] = str(sidecar)
        return out

    # ---------- high-Ni profiles and domain analysis ----------

    def _profile_from_row(self, row) -> HighNiProfile:
        try:
            thresholds = json.loads(row["thresholds_json"] or "{}")
        except json.JSONDecodeError:
            thresholds = {}
        # 旧档案只存了面积比阈值 → 补上新默认的高度比阈值,保证分级口径
        # 与新版一致(面积比阈值保留供参考,不被覆盖)。
        merged = dict(thresholds)
        merged.setdefault("i003_i104_height_low", 1.20)
        merged.setdefault("i003_i104_height_good", 1.30)
        return HighNiProfile(
            name=row["name"],
            family=row["family"],
            formula=row["formula"],
            ni_fraction=row["ni_fraction"],
            li_tm_ratio=row["li_tm_ratio"] or 1.0,
            wavelength=row["wavelength"] or 1.5418,
            instrument_name=row["instrument_name"],
            thresholds=merged,
        )

    def _cmd_profiles_create(self, p: dict) -> dict:
        project_id = p.get("project_id")
        if self.db.query_one(
            "SELECT id FROM projects WHERE id = ? AND deleted_at IS NULL",
            (project_id,),
        ) is None:
            raise EngineError("目标 Project 不存在")
        name = (p.get("name") or "").strip()
        if not name:
            raise EngineError("材料档案名称不能为空")
        profile = default_profile(float(p.get("wavelength", 1.5418)))
        thresholds = p.get("thresholds")
        profile_id = self.db.insert(
            """INSERT INTO material_profiles
               (project_id, name, family, formula, ni_fraction, li_tm_ratio,
                dopants_json, wavelength, instrument_name, reference_dataset_id,
                thresholds_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                project_id,
                name,
                p.get("family", profile.family),
                p.get("formula", profile.formula),
                p.get("ni_fraction", profile.ni_fraction),
                p.get("li_tm_ratio", profile.li_tm_ratio),
                json.dumps(p.get("dopants") or {}, ensure_ascii=False),
                float(p.get("wavelength", profile.wavelength)),
                p.get("instrument_name", ""),
                p.get("reference_dataset_id"),
                json.dumps(
                    thresholds if thresholds is not None else profile.thresholds,
                    ensure_ascii=False,
                ),
            ),
        )
        return {"profileId": profile_id}

    def _cmd_profiles_list(self, p: dict) -> dict:
        project_id = p.get("project_id")
        rows = self.db.query_all(
            "SELECT * FROM material_profiles WHERE project_id = ? ORDER BY name",
            (project_id,),
        )
        profiles = []
        for row in rows:
            item = dict(row)
            for field, fallback in (("dopants_json", {}), ("thresholds_json", {})):
                try:
                    item[field.removesuffix("_json")] = json.loads(item[field] or "{}")
                except json.JSONDecodeError:
                    item[field.removesuffix("_json")] = fallback
                del item[field]
            profiles.append(item)
        return {"profiles": profiles}

    def _cmd_domain_high_ni_get(self, p: dict) -> dict:
        """读回已保存的高镍分析结果（§7.4 结果溯源）。

        `domain_results` 此前是**只写表**：全仓库对它只有 INSERT / DELETE，
        没有任何 SELECT。后果是用户切走再切回同一个数据集时，前端只能重新
        调 `domain.high_ni.analyze` 重算 —— 单次全谱精修实测 1.5–4.5 秒，
        而这期间界面是空的。库里那份 result_json + source_hash 永远不会被读到。

        payload: {dataset_id} 或 {analysis_id}。
        返回 {found: false} 表示这个数据集还没算过（不是错误）。
        `stale: true` 表示结果是对**上一版文件**算的（source_hash 不匹配），
        用户重新导入/替换过数据 —— 此时仍返回旧结果，但 UI 应提示需要重算。
        """
        analysis_id = p.get("analysis_id")
        if analysis_id is None:
            dataset_id = p.get("dataset_id")
            if dataset_id is None:
                raise EngineError("必须提供 analysis_id 或 dataset_id")
            analysis = self._active_analysis(int(dataset_id))
        else:
            analysis = self.db.query_one(
                "SELECT * FROM analyses WHERE id = ?", (analysis_id,)
            )
            if analysis is None:
                raise EngineError("Analysis 不存在")

        row = self.db.query_one(
            """SELECT dr.*, d.file_hash AS current_hash, d.x_axis_type, a.pipeline_json, a.preset_id,
                      COALESCE(d.acquisition_wavelength, s.wavelength, 1.5418) AS wavelength
               FROM domain_results dr
               JOIN analyses a ON dr.analysis_id = a.id
               JOIN datasets d ON a.dataset_id = d.id
               JOIN samples s ON s.id = d.sample_id
               WHERE dr.analysis_id = ? AND dr.domain = ?
               ORDER BY dr.id DESC LIMIT 1""",
            (analysis["id"], "high_ni_layered"),
        )
        if row is None:
            return {"found": False, "result": None, "stale": False}

        try:
            result = json.loads(row["result_json"] or "{}")
        except json.JSONDecodeError:
            # 坏行当作没有 —— 让用户重算，而不是把 JSONDecodeError 抛给
            # 兜底分支变成「引擎内部错误」。
            return {"found": False, "result": None, "stale": False}

        stored_hash = row["source_hash"] or ""
        current_hash = row["current_hash"] or ""
        stale = bool(stored_hash and current_hash and stored_hash != current_hash) or _stale_domain(result, row)
        return {"found": True, "result": result, "stale": stale}

    def _cmd_domain_high_ni_analyze(self, p: dict) -> dict:
        dataset, analysis = self._export_context(p)
        profile_id = p.get("profile_id")
        if profile_id is None:
            profile = default_profile(dataset["wavelength"])
        else:
            row = self.db.query_one(
                "SELECT * FROM material_profiles WHERE id = ?", (profile_id,)
            )
            if row is None:
                raise EngineError("材料档案不存在")
            profile = replace(self._profile_from_row(row), wavelength=dataset["wavelength"])

        raw_curve = self._load_dataset_curve(dataset["id"])
        saved_pipeline = json.loads(analysis["pipeline_json"] or "{}")
        params = params_from_json(p.get("pipeline") or saved_pipeline)
        processed = run_pipeline(raw_curve, params)

        # 先取全谱精修晶胞(与标注路径共享 _FIT_CACHE,同数据集不重复冷跑),
        # 再把 (a, c) 传进 domain 分析 —— 双峰配名/理论位置一律用精修值,
        # 不再写死 2.878/14.216(写死值与实测胞差 0.06 Å 时 102 分量理论
        # 位置偏约 0.1°,(006)/(102) 配名可能整体翻转)。
        try:
            from ..peaks.annotate import _cached_fit

            x, y, _warnings = as_two_theta(raw_curve)
            fit = _cached_fit(x, y, profile.wavelength, int(dataset["id"])) if x.size >= 16 else None
        except Exception:  # 精修失败不能拖垮整个分析
            fit = None

        # 反序胞(c²≤24a²)不传给 domain:双峰理论角会对调,单峰窗口里
        # 高角分量会被配成 "108"(见 indexing.doublet_order_ok)。
        from ..high_ni.indexing import doublet_order_ok

        cell = (
            (fit.a, fit.c)
            if fit is not None and doublet_order_ok(fit.a, fit.c)
            else None
        )
        result = analyze_high_ni(
            processed.curve,
            profile,
            modules=p.get("modules") or ("mixing", "layered", "lattice"),
            fit=bool(p.get("fit", True)),
            dataset_id=dataset["id"],
            profile_id=profile_id,
            source_hash=dataset["file_hash"],
            cell=cell,
        )
        result["wavelength"] = profile.wavelength
        result["pipelineHash"] = _pipeline_hash(p.get("pipeline") or saved_pipeline)
        result["axisConversionVersion"] = 1
        result["analysisId"] = analysis["id"]
        result["processingWarnings"] = processed.warnings

        # 附上**全谱精修**的晶胞(带零点校正),供结构趋势图使用。
        #
        # 为什么不直接用 result["lattice"]:那条路径只取 3–7 个峰做最小二乘、
        # 零点恒为 0,与晶面标注用的 refine_cell 数值不同。趋势图混用两条
        # 路径,会把"换了算法"画成"样品结构变了" —— 看起来非常像一个结论。
        # 这里显式并列存两份,UI 与导出都能看出用的是哪一个。
        if fit is not None:
            result["fullCell"] = {
                "a": fit.a,
                "c": fit.c,
                "c_over_a": fit.c / fit.a if fit.a else None,
                "zero_shift": fit.zero_shift,
                "rwp": fit.rwp,
                "source": "refine_cell",
            }

        with self.db.transaction():
            self.db.execute(
                "DELETE FROM domain_results WHERE analysis_id = ? AND domain = ?",
                (analysis["id"], "high_ni_layered"),
            )
            self.db.insert(
                """INSERT INTO domain_results
                   (analysis_id, profile_id, domain, result_json, warnings_json, source_hash)
                   VALUES (?,?,?,?,?,?)""",
                (
                    analysis["id"],
                    profile_id,
                    "high_ni_layered",
                    json.dumps(result, ensure_ascii=False),
                    json.dumps(result.get("warnings", []), ensure_ascii=False),
                    dataset["file_hash"],
                ),
            )
        return result

    # ---------- presets(§8.2 支撑场景 S5)----------

    def _cmd_presets_save(self, p: dict) -> dict:
        # 校验顺序与 _cmd_profiles_create 一致:缺 key 或 project 不存在时
        # 必须给出人话,而不是让 KeyError / IntegrityError 被兜底成
        # "引擎内部错误,请重试或查看日志"(§68)。
        project_id = p.get("project_id")
        if self.db.query_one(
            "SELECT id FROM projects WHERE id = ? AND deleted_at IS NULL",
            (project_id,),
        ) is None:
            raise EngineError("目标 Project 不存在")
        name = (p.get("name") or "").strip()
        if not name:
            raise EngineError("预设名称不能为空")
        preset_id = self.db.insert(
            "INSERT INTO analysis_presets (project_id, name, pipeline_json) VALUES (?,?,?)",
            (
                project_id,
                name,
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
        dataset_ids = list(dict.fromkeys(p.get("dataset_ids") or []))
        pipeline = json.loads(preset["pipeline_json"])
        params = params_from_json(pipeline)
        with self.db.transaction() as conn:
            for ds_id in dataset_ids:
                analysis = conn.execute(
                    """SELECT a.* FROM analyses a JOIN datasets d ON d.id = a.dataset_id
                       JOIN samples s ON s.id = d.sample_id
                       WHERE a.dataset_id = ? AND a.is_active = 1 AND s.project_id = ?
                         AND d.deleted_at IS NULL AND s.deleted_at IS NULL""",
                    (ds_id, preset["project_id"]),
                ).fetchone()
                if analysis is None:
                    raise EngineError("所选数据集不属于当前项目或已删除")
                report = validate_params(self._load_dataset_curve(ds_id), params)
                if not report.ok:
                    raise EngineError(f"数据集 {ds_id} 无法应用预设", "；".join(report.errors))
                self._write_pipeline(conn, analysis, pipeline, preset["id"])
        return {"applied": len(dataset_ids)}

    # ---------- library meta ----------

    def _cmd_samples_create(self, p: dict) -> dict:
        """新建一个实验(样品),不导入数据。

        **为什么需要它**:此前样品只能作为导入的副作用被创建,于是用户想先
        建好实验再往里导数据是做不到的 —— 而导完才发现归错组时,唯一的手段
        是删掉重来。侧栏的「新建实验」按钮走这条路。

        payload: {project_id, name, temperature?, duration?, material?,
                  batch?, wavelength?, notes?}。
        """
        return {"sampleId": self._create_sample(p)}

    def _create_sample(self, p: dict) -> int:
        """校验并插入一个 sample,返回新 id。import 与 samples.create 共用。"""
        project_id = p.get("project_id")
        if self.db.query_one(
            "SELECT id FROM projects WHERE id = ? AND deleted_at IS NULL",
            (project_id,),
        ) is None:
            raise EngineError("目标 Project 不存在")
        name = (p.get("name") or "").strip()
        if not name:
            raise EngineError("实验名称不能为空")
        return self.db.insert(
            """INSERT INTO samples
               (project_id, name, temperature, duration, material, batch,
                wavelength, notes)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                project_id, name,
                str(p.get("temperature") or ""),
                str(p.get("duration") or ""),
                str(p.get("material") or ""),
                str(p.get("batch") or ""),
                float(p.get("wavelength") or 1.5418),
                str(p.get("notes") or ""),
            ),
        )

    def _cmd_samples_update(self, p: dict) -> dict:
        """更新实验的元数据(温度/时长/材料/批次/波长/备注)。

        这些字段是结构趋势图的自变量(docs/DEVELOPMENT_HIGH_NI.md §11.4),
        此前没有任何写入路径 —— 用户即使想填也无处可填,趋势图因此永远
        只能按导入顺序排。

        **只更新显式给出的字段**:前端可以只改温度而不动波长。
        """
        sample_id = p.get("id")
        if self.db.query_one(
            "SELECT id FROM samples WHERE id = ? AND deleted_at IS NULL",
            (sample_id,),
        ) is None:
            raise EngineError("实验不存在")

        fields: list[str] = []
        values: list[Any] = []
        for key in ("temperature", "duration", "material", "batch", "notes"):
            if key in p:
                fields.append(f"{key} = ?")
                values.append(str(p.get(key) or ""))
        if "wavelength" in p:
            try:
                lam = float(p["wavelength"])
            except (TypeError, ValueError) as exc:
                raise EngineError("波长必须是数字") from exc
            if not 0.3 <= lam <= 3.0:
                raise EngineError("波长超出合理范围(0.3–3.0 Å)")
            fields.append("wavelength = ?")
            values.append(lam)
        if "name" in p:
            name = (p.get("name") or "").strip()
            if not name:
                raise EngineError("实验名称不能为空")
            fields.append("name = ?")
            values.append(name)

        if not fields:
            return {"updated": 0}
        fields.append("updated_at = datetime('now')")
        values.append(sample_id)
        cur = self.db.execute(
            f"UPDATE samples SET {', '.join(fields)} WHERE id = ?",
            tuple(values),
        )
        return {"updated": cur.rowcount}


        rows = self.db.query_all(
            "SELECT * FROM projects WHERE deleted_at IS NULL ORDER BY name"
        )
        return {"projects": [dict(r) for r in rows]}

    def _cmd_trend_series(self, p: dict) -> dict:
        """把一个 Project 下的结构指标汇成趋势序列(§11.4)。

        **指标只从 domain_results 读,不在这个命令里重算。** 一次高镍分析
        要做全谱精修(1.5–4.5 s),一个 Project 十几个数据集就是几十秒 ——
        而趋势图要的是「扫一眼」。没算过的点返回 value=null,UI 画成空心,
        用户点了才去算那一条。

        payload: {project_id, x_axis?, metric?}。
        """
        from ..high_ni.trend import build_trend

        project_id = p.get("project_id")
        if self.db.query_one(
            "SELECT id FROM projects WHERE id = ? AND deleted_at IS NULL",
            (project_id,),
        ) is None:
            raise EngineError("目标 Project 不存在")

        rows = self.db.query_all(
            """SELECT d.id, d.name, s.name AS sample_name,
                      s.temperature, s.batch, d.x_axis_type, a.pipeline_json, a.preset_id,
                      COALESCE(d.acquisition_wavelength, s.wavelength, 1.5418) AS wavelength,
                      dr.result_json
               FROM datasets d
               JOIN samples s ON d.sample_id = s.id
               JOIN analyses a ON a.dataset_id = d.id AND a.is_active = 1
               LEFT JOIN domain_results dr
                 ON dr.analysis_id = a.id AND dr.domain = 'high_ni_layered'
               WHERE s.project_id = ?
                 AND d.deleted_at IS NULL AND s.deleted_at IS NULL
               ORDER BY s.name, d.name""",
            (project_id,),
        )

        datasets: list[dict[str, Any]] = []
        for row in rows:
            metrics = None
            if row["result_json"]:
                try:
                    stored = json.loads(row["result_json"])
                except json.JSONDecodeError:
                    stored = None
                if isinstance(stored, dict) and not _stale_domain(stored, row):
                    metrics = _trend_metrics(stored)
            datasets.append({
                "id": row["id"],
                "name": row["name"],
                "sample_name": row["sample_name"],
                "temperature": row["temperature"],
                "batch": row["batch"],
                "metrics": metrics,
            })

        # build_trend 对未知指标/横轴抛 ValueError —— 必须翻译成人话,
        # 否则会掉进 handle() 的兜底分支变成"引擎内部错误"(§68),
        # 而用户只是传了个下拉里不存在的字段。
        try:
            return build_trend(
                datasets,
                x_axis=str(p.get("x_axis") or "temperature"),
                metric=str(p.get("metric") or "a"),
            )
        except ValueError as exc:
            raise EngineError("不支持的趋势指标或横轴", detail=str(exc)) from exc

    # ---------- projects / library ----------

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
        # 一并判父级删除状态:与 get_library_tree 的过滤条件保持一致。
        # 只判 d.deleted_at 时,被删样品下的 dataset 仍会进 by_sample 字典
        # (只是因为没有对应的 sample 行而不会被输出),功能上无害,但多读
        # 了无用行,且两处条件不一致容易在后续改动中真的出错。
        datasets = self.db.query_all(
            """SELECT d.id, d.sample_id, d.batch_id, d.name, d.point_count
               FROM datasets d
               JOIN samples s ON d.sample_id = s.id
               JOIN projects p ON s.project_id = p.id
               WHERE d.deleted_at IS NULL
                 AND s.deleted_at IS NULL AND p.deleted_at IS NULL
               ORDER BY d.name"""
        )
        by_sample: dict[int, list] = {}
        by_batch: dict[int, list] = {}
        for d in datasets:
            entry = {
                "id": d["id"],
                "name": d["name"],
                "batch_id": d["batch_id"],
                "point_count": d["point_count"],
            }
            by_sample.setdefault(d["sample_id"], []).append(entry)
            if d["batch_id"] is not None:
                by_batch.setdefault(d["batch_id"], []).append(entry)

        batches = []
        for b in self.db.get_batches():
            item = dict(b)
            item["datasets"] = by_batch.get(b["id"], [])
            batches.append(item)

        out = []
        for r in rows:
            item = dict(r)
            item["datasets"] = by_sample.get(r["id"], [])
            item["batches"] = [b for b in batches if b["sample_id"] == r["id"]]
            out.append(item)
        return {"samples": out, "batches": batches}

    def _cmd_meta_rename(self, p: dict) -> dict:
        table = p.get("table")
        if table not in ALLOWED_TABLES:
            raise EngineError("不支持重命名的对象")
        name = (p.get("name") or "").strip()
        if not name:
            raise EngineError("名称不能为空")
        if len(name) > 200:
            raise EngineError("名称过长(最多 200 字)")
        row_id = p.get("id")
        # 校验存在性与软删状态:否则空名/已删对象会静默"重命名成功"
        if self.db.query_one(
            f"SELECT id FROM {table} WHERE id = ? AND deleted_at IS NULL",
            (row_id,),
        ) is None:
            raise EngineError("对象不存在或已在回收站中")
        self.db.execute(
            f"UPDATE {table} SET name = ?, updated_at = datetime('now') WHERE id = ?",
            (name, row_id),
        )
        return {"id": row_id, "name": name}

    def _cmd_meta_file_path(self, p: dict) -> dict:
        """文件型对象的实际磁盘路径(前端右键"在资源管理器中显示")。

        明确指定 stored 或 source 时只定位该份文件。省略 kind 保留旧客户端行为。
        """
        table = p.get("table")
        if table not in ("datasets", "ec_cells"):
            raise EngineError("只有数据集和电池有文件位置")
        row = self.db.query_one(
            f"SELECT source_path, stored_path FROM {table} WHERE id = ?",
            (p.get("id"),),
        )
        if row is None:
            raise EngineError("对象不存在")
        src = (row["source_path"] or "").strip()
        kind = p.get("kind")
        if kind not in (None, "source", "stored"):
            raise EngineError("未知的文件位置")
        if kind != "stored":
            if src and Path(src).is_file():
                return {"path": src, "kind": "source"}
            if kind == "source":
                raise EngineError(f"找不到原始文件，导入时的路径为：{src}" if src else "没有记录原始导入路径")
        stored_rel = (row["stored_path"] or "").strip()
        if stored_rel:
            stored = self.store.abs_path(stored_rel)
            if stored.is_file():
                return {"path": str(stored), "kind": "stored"}
        if kind == "stored":
            raise EngineError("找不到项目副本，请检查项目库中的文件")
        raise EngineError("文件已找不到(原文件与库内副本都不在)")

    def _cmd_meta_paths(self, p: dict) -> dict:
        """数据目录/日志文件路径(Settings 的 Data 页,README §8.3)。

        用户装完软件第一个问题就是"数据存在哪了/崩溃了看什么",
        不能让人自己去翻 %LOCALAPPDATA%。
        """
        data_root = Path(self.store.root)
        log = self.log_dir / "engine.log"
        return {
            "dataRoot": str(data_root),
            "logFile": str(log),
            "logExists": log.exists(),
        }

    def _cmd_meta_soft_delete(self, p: dict) -> dict:
        """把对象移入回收站(Trash 语义,§10.5)。

        软删除是**隐式级联**的:只标记被直接删除的这一行,子对象因为父级
        不可见而一并从 Library 消失;恢复时子对象自动回来,正是因为它们
        从未被写过 deleted_at。详见 Database.soft_delete 的注释。
        """
        table = p.get("table")
        # 白名单在命令层显式校验,不依赖 db 层的断言 —— 否则非法表名会被
        # 兜底分支伪装成"引擎内部错误",用户看不到人话
        if table not in ALLOWED_TABLES:
            raise EngineError("不支持删除的对象")
        row_id = p.get("id")
        if self.db.query_one(
            f"SELECT id FROM {table} WHERE id = ?", (row_id,)
        ) is None:
            raise EngineError("对象不存在")
        # 必须在删除**之前**统计:删完父级就不可见了,计数会恒为 0
        cascaded = self.db.descendant_dataset_count(table, row_id)
        if not self.db.soft_delete(table, row_id):
            raise EngineError("该对象已在回收站中")
        return {
            "id": row_id,
            "kind": table,
            # 隐式随之下线的 dataset 数,供前端提示"已移入回收站(含 N 个数据集)"
            "cascadedDatasets": cascaded,
        }

    # ---------- trash(回收站)----------

    def _cmd_trash_list(self, p: dict) -> dict:
        rows = self.db.trash_rows(limit=int(p.get("limit", 500)))
        return {
            "items": [
                {
                    "kind": r["kind"],
                    "id": r["id"],
                    "name": r["name"],
                    "deletedAt": r["deleted_at"],
                    "projectName": r["project_name"],
                    "sampleName": r["sample_name"],
                    "childCount": r["child_count"],
                }
                for r in rows
            ],
            "total": len(rows),
        }

    def _cmd_meta_restore(self, p: dict) -> dict:
        """从回收站恢复。父级仍被删除时**一并向上恢复父级**。

        级联只向上、不向下:
          - 向上:恢复一个 dataset 而其 sample/project 还在回收站里时,
            单恢复 dataset 它依然不可见,用户会觉得"恢复没生效"。
          - 向下:恢复一个 project 时不主动恢复其 sample/dataset —— 它们
            从未被写过 deleted_at(隐式级联),恢复 project 后自然全部可见。
            这个不对称是隐式级联带来的直接好处,不是遗漏。
        """
        table = p.get("table")
        if table not in ALLOWED_TABLES:
            raise EngineError("不支持恢复的对象")
        row_id = p.get("id")
        row = self.db.query_one(f"SELECT * FROM {table} WHERE id = ?", (row_id,))
        if row is None:
            raise EngineError("对象不存在")
        if row["deleted_at"] is None:
            raise EngineError("该对象不在回收站中")

        ancestors: list[dict] = []
        cascade = p.get("cascade_parent", True)

        if cascade and table == "datasets":
            sample = self.db.query_one(
                "SELECT * FROM samples WHERE id = ?", (row["sample_id"],)
            )
            if sample is not None and sample["deleted_at"] is not None:
                self.db.restore("samples", sample["id"])
                ancestors.append({"kind": "samples", "name": sample["name"]})
                project = self.db.query_one(
                    "SELECT * FROM projects WHERE id = ?", (sample["project_id"],)
                )
                if project is not None and project["deleted_at"] is not None:
                    self.db.restore("projects", project["id"])
                    ancestors.append({"kind": "projects", "name": project["name"]})
        elif cascade and table == "samples":
            project = self.db.query_one(
                "SELECT * FROM projects WHERE id = ?", (row["project_id"],)
            )
            if project is not None and project["deleted_at"] is not None:
                self.db.restore("projects", project["id"])
                ancestors.append({"kind": "projects", "name": project["name"]})

        self.db.restore(table, row_id)
        return {"id": row_id, "kind": table, "restoredAncestors": ancestors}

    def _cmd_meta_purge(self, p: dict) -> dict:
        """**永久删除**(不可恢复),并清理磁盘副本。

        这是唯一真正删文件的入口 —— 软删除刻意不碰磁盘,以便恢复时数据
        仍然完整。所以 purge 必须在 UI 上做二次确认。
        """
        if p.get("all"):
            return self._purge_all()

        table = p.get("table")
        if table not in ALLOWED_TABLES:
            raise EngineError("不支持删除的对象")
        row_id = p.get("id")
        row = self.db.query_one(
            f"SELECT id, deleted_at FROM {table} WHERE id = ?", (row_id,)
        )
        if row is None:
            raise EngineError("对象不存在")
        if row["deleted_at"] is None:
            raise EngineError("只能彻底删除回收站中的对象")

        removed = self._purge_entry(table, row_id)
        return {"id": row_id, "kind": table, "removedFiles": removed}

    def _purge_all(self) -> dict:
        """清空回收站。必须由深到浅处理,否则删了父级就找不到子级了。"""
        rows = self.db.trash_rows(limit=100000)
        order = {"datasets": 0, "ec_cells": 0, "batches": 0, "samples": 1, "projects": 2}
        ordered = sorted(rows, key=lambda r: order[r["kind"]])
        removed = sum(self._purge_entry(r["kind"], r["id"]) for r in ordered)
        return {"purged": len(ordered), "removedFiles": removed}

    def _purge_entry(self, table: str, row_id: int) -> int:
        with self.db.transaction() as conn:
            ds_rows = self.db.dataset_ids_of(table, row_id) if table != "ec_cells" else []
            ds_ids = [row["id"] for row in ds_rows]
            if table == "ec_cells":
                stored = self.db.purge_ec_cell(row_id)
            elif table == "samples":
                stored = self.db.purge_sample(row_id)
            elif table == "projects":
                stored = self.db.purge_project(row_id)
            else:
                stored = [path for ds_id in ds_ids if (path := self.db.purge_dataset(ds_id)) is not None]
                if table == "batches":
                    conn.execute("DELETE FROM batches WHERE id=?", (row_id,))
        return self._remove_files(stored, ds_ids)

    def _remove_files(self, stored: list[str], ds_ids: list[int]) -> int:
        """删磁盘副本。**失败只忽略,不抛异常** —— 此时 DB 行已经删了,
        抛异常只会让用户以为整件事失败了,而实际状态是"行没了、文件残留",
        这是可以接受的(留点垃圾,不是数据丢失)。"""
        removed = 0
        for path in stored:
            with contextlib.suppress(Exception):
                if self.store.remove_stored(path):
                    removed += 1
        # 数据集没了,它的晶胞精修缓存也必须清 —— 否则 id 被复用时会
        # 拿上一条曲线的晶胞去标注,标签会静默错位。
        from ..peaks.annotate import invalidate_fit_cache
        from ..processing.pipeline import clear_stage_cache

        for ds_id in ds_ids:
            invalidate_fit_cache(ds_id)
            self._curve_cache.pop(int(ds_id), None)
        # 阶段缓存按曲线指纹分组,删数据集本身不会命中错键,但清掉可以
        # 释放内存并避免同指纹新数据意外复用(P6)。
        clear_stage_cache()
        return removed

    # ---------- CHI 工作站 ----------

    def _cmd_chi_list(self, p: dict) -> dict:
        return self.chi.list(p)

    def _cmd_chi_import(self, p: dict) -> dict:
        return self.chi.import_files(p)

    def _cmd_chi_open(self, p: dict) -> dict:
        return self.chi.open(p)

    def _cmd_chi_fit(self, p: dict) -> dict:
        return self.chi.fit(p)

    def _cmd_chi_export(self, p: dict) -> dict:
        target = self._export_target(p.get("path"), "chi_data.csv")
        return self.chi.export(p["id"], target)

    # ---------- cex(蓝电原始数据)----------

    def _cmd_cex_scan_folder(self, p: dict) -> dict:
        return self.cex.scan_folder(p)

    def _cmd_cex_open(self, p: dict) -> dict:
        return self.cex.open(p)

    def _cmd_cex_close(self, p: dict) -> dict:
        self.cex.close_document(p["id"])
        return {}

    def _cmd_cex_page(self, p: dict) -> dict:
        return self.cex.page(p)

    def _cmd_cex_locate(self, p: dict) -> dict:
        return self.cex.locate(p)

    def _cmd_cex_plot(self, p: dict) -> dict:
        return self.cex.plot(p)

    def _cmd_cex_compare(self, p: dict) -> dict:
        return self.cex.compare(p)

    def _cmd_cex_archive(self, p: dict) -> dict:
        return self.cex.archive(p)

    def _cmd_cex_export(self, p: dict) -> dict:
        return self.cex.export(p)

    def _cmd_cex_dqdv(self, p: dict) -> dict:
        return self.cex.dqdv(p)

    def _cmd_cex_dqdv_source(self, p: dict) -> dict:
        return self.cex.dqdv_source(p)

    def _cmd_cex_dqdv_export(self, p: dict) -> dict:
        target = self._export_target(p.get("path"), "cex_dqdv.csv", bool(p.get("overwrite")))
        return self.cex.export_dqdv(target, p.get("columns"), p.get("rows"))

    def _cmd_cex_dqdv_export_curves(self, p: dict) -> dict:
        target = self._export_target(p.get("path"), "cex_dqdv_curves.csv", bool(p.get("overwrite")))
        return self.cex.export_dqdv_curves(p, target)

    def _cmd_cex_cells(self, p: dict) -> dict:
        return self.cex.cells(p)

    def _cmd_cex_backup_legacy(self, p: dict) -> dict:
        target = self._export_target(p.get("path"), "cex_legacy_backup.zip", bool(p.get("overwrite")))
        return self.cex.backup_legacy(p.get("sample_id"), target)

    def close(self) -> None:
        try:
            self.cex.close()
        finally:
            self.db.close()


def _fail(exc) -> dict:
    return {
        "ok": False,
        "error": getattr(exc, "reason", str(exc)),
        "detail": getattr(exc, "detail", ""),
    }
