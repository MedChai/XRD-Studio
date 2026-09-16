/** 引擎 command 接口的类型定义(§10.4,与 Python 端保持一致)。 */

export type AxisType = "two_theta" | "d" | "q" | "generic";
export type Sensitivity = "low" | "medium" | "high";
export type NormalizationMode = "raw" | "max1" | "max100";
export type ThemeMode = "system" | "light" | "dark";

// ---------- pipeline 参数(与 models/types.py 一一对应) ----------

export interface CropParams {
  enabled: boolean;
  x_min: number | null;
  x_max: number | null;
}

export interface BackgroundParams {
  enabled: boolean;
  method: "asls" | "polynomial";
  smoothness: number;
  asymmetry: number;
  iterations: number;
  order: number;
}

export interface SmoothingParams {
  enabled: boolean;
  method: "savgol";
  window_length: number;
  polyorder: number;
}

export interface NormalizationParams {
  mode: NormalizationMode;
}

export interface PeakDetectionParams {
  sensitivity: Sensitivity;
  min_height_pct: number;
  min_distance_deg: number;
  prominence: number | null;
  width: number | null;
  rel_height: number;
}

export interface PipelineParams {
  crop: CropParams;
  background: BackgroundParams;
  smoothing: SmoothingParams;
  normalization: NormalizationParams;
  peaks: PeakDetectionParams;
}

// ---------- 结果 ----------

export interface PeakResult {
  index: number;
  position: number;
  intensity: number;
  fwhm: number | null;
  area: number | null;
  left_bound: number | null;
  right_bound: number | null;
  manual_override: boolean;
  prominence: number | null;
}

export interface PeakRow extends PeakResult {
  id: number;
}

export interface CurvePayload {
  x: number[];
  y: number[];
  xAxisType: AxisType;
  wavelength: number;
  pointCount: number;
  background?: number[];
}

export interface PreviewResult {
  curve: CurvePayload;
  peaks: PeakResult[];
  smoothingAffectsMetrics: boolean;
  warnings: string[];
}

// ---------- Library ----------

export interface DatasetNode {
  id: number;
  name: string;
}

export interface SampleNode {
  id: number;
  project_id: number;
  name: string;
  temperature: string;
  duration: string;
  dataset_count: number;
  datasets: DatasetNode[];
}

export interface ProjectNode {
  id: number;
  name: string;
  description: string;
}

export interface ImportResult {
  imported: { id: number; name: string }[];
  duplicates: {
    path: string;
    existing: {
      /** 已存在的那条 dataset,供「打开已有」直接定位 */
      datasetId: number;
      projectName: string;
      sampleName: string;
      datasetName: string;
    };
  }[];
  needColumns: {
    path: string;
    reason: string;
    previewRows: string[][];
    columnCount: number;
    suggestedX: number;
    suggestedY: number;
  }[];
  failures: { path: string; reason: string }[];
  sampleId: number;
}

// ---------- 回收站 ----------

/** 可删除/重命名的对象类型。用**表名**,与引擎命令参数一致,可直接透传。 */
export type TrashKind = "projects" | "samples" | "datasets";

export interface TrashItem {
  kind: TrashKind;
  id: number;
  name: string;
  deletedAt: string | null;
  /** 层级路径,dataset 有全部两级,sample 只有 projectName */
  projectName: string | null;
  sampleName: string | null;
  /** 隐式随之下线的子对象数,用于提示影响范围 */
  childCount: number;
}

export interface TrashListResult {
  items: TrashItem[];
  total: number;
}

export interface RestoreResult {
  id: number;
  kind: TrashKind;
  /** 一并向上恢复的祖先(父级此前也在回收站里时) */
  restoredAncestors: { kind: TrashKind; name: string }[];
}

export interface SoftDeleteResult {
  id: number;
  kind: TrashKind;
  /** 隐式随之下线的 dataset 数 */
  cascadedDatasets: number;
}

// ---------- 响应封装 ----------

export type EngineResponse<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; detail: string };
