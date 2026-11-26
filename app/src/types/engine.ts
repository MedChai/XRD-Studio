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
  /** peaks 表主键。落库(detect/annotate)后才有值;preview 临时峰为
   *  null —— 编辑 bounds 必须有它,拿不到就提示先「检测峰」固化。 */
  id?: number | null;
  index: number;
  position: number;
  intensity: number;
  fwhm: number | null;
  area: number | null;
  left_bound: number | null;
  right_bound: number | null;
  manual_override: boolean;
  prominence: number | null;
  /** 晶面归属,由引擎 peaks.annotate / 预览填充。
   *  为 null 表示"本物相没有对应反射";若同时 hkl_confidence === "ambiguous",
   *  表示有反射匹配但与他人未分开 —— 两者语义不同,UI 应区别呈现。 */
  hkl?: string | null;
  hkl_confidence?: "reliable" | "ambiguous" | "none" | null;
  /** 该晶面在该峰邻域内贡献的强度占比(0–1)。归属的可核对依据。 */
  dominance?: number | null;
  /** 竞争候选,即使判为 reliable 也可能非空(双峰区)。 */
  hkl_candidates?: HklCandidate[] | null;
}

export interface HklCandidate {
  hkl: [number, number, number] | null;
  label: string | null;
  position: number | null;
  dominance: number | null;
}

/** 晶面标注所用的拟合上下文,供 UI 展示"标签是怎么来的"。
 *  持久化形态(analyses.annotation_json)另带 pipelineHash / labels 快照:
 *  前端用 pipelineHash 判断标注是否已过期(stale),用 labels 在 peaks 表
 *  被 commit 删除后按位置把标签合回新峰。 */
export interface AnnotationContext {
  phase: string;
  a: number;
  c: number;
  zeroShift: number;
  fwhm0: number;
  fwhmSlope: number;
  rwp: number;
  /** 标注时的 pipeline 指纹(引擎 sha1 前 16 位)。与当前 pipeline 的
   *  hash 不一致 → 标注过期,UI 显示 stale 提示但保留旧标签。 */
  pipelineHash?: string;
  annotatedAt?: string;
  labels?: AnnotationLabel[];
}

export interface AnnotationLabel {
  position: number;
  hkl?: string | null;
  hklConfidence?: string | null;
  dominance?: number | null;
  candidates?: HklCandidate[] | null;
}

/** peaks.annotate 的返回体(与引擎 _cmd_peaks_annotate 对齐)。 */
export interface PeaksAnnotateResult {
  peaks: PeakResult[];
  annotated: {
    index: number;
    position: number;
    hkl_label?: string | null;
    hkl_confidence?: "reliable" | "ambiguous" | "none" | null;
    dominance?: number | null;
    candidates?: HklCandidate[];
  }[];
  count: number;
  updated?: number;
  annotation?: AnnotationContext | null;
}

/** 曲线载荷(§9.1)。
 *
 *  x / y / background 是 **Float32Array**,不是 number[] —— 引擎侧经
 *  二进制信封传输,前端零解析直接喂 ECharts。mock 路径也返回
 *  Float32Array,保证浏览器开发模式与真实引擎的形状一致。
 *
 *  注意:它是 TypedArray,`.reduce` / `.map` 可用,但 `Array.isArray()`
 *  为 false,且没有 `toSorted` 之类的数组方法 —— 用到的地方要留意。 */
export interface CurvePayload {
  x: Float32Array;
  y: Float32Array;
  xAxisType: AxisType;
  wavelength: number;
  pointCount: number;
  sourcePointCount?: number;
  downsampled?: boolean;
  background?: Float32Array;
}

export interface PreviewResult {
  curve: CurvePayload;
  peaks: PeakResult[];
  smoothingAffectsMetrics: boolean;
  warnings: string[];
  /** 本次晶面标注所用的拟合结果(晶胞/零点/Rwp),供 UI 展示依据。 */
  annotation?: AnnotationContext | null;
  /** 本次 preview 所用 pipeline 的指纹,与 annotation.pipelineHash 比对
   *  得出 stale(annotate=false 时引擎也返回)。 */
  pipelineHash?: string;
}

/** 已保存分析状态,用于切换数据集时快速恢复而不是从默认参数重算。 */
export interface AnalysisStateResult {
  analysisId: number;
  pipeline: Partial<PipelineParams>;
  curve: CurvePayload;
  peaks: PeakResult[];
  peaksStored: boolean;
  smoothingAffectsMetrics: boolean;
  warnings: string[];
  /** 已持久化的晶面标注(analyses.annotation_json);从未标注过为 null。 */
  annotation?: AnnotationContext | null;
  /** 当前落库 pipeline 的指纹(与 annotation.pipelineHash 比对)。 */
  pipelineHash?: string;
}

// ---------- 高镍层状正极分析 ----------

export interface MaterialProfile {
  id: number;
  project_id: number;
  name: string;
  family: string;
  formula: string;
  ni_fraction: number | null;
  li_tm_ratio: number | null;
  dopants: Record<string, unknown>;
  wavelength: number | null;
  instrument_name: string;
  reference_dataset_id: number | null;
  thresholds: Record<string, number>;
}

/** 分析参数预设(§1.2 S5,analysis_presets 表)。 */
export interface AnalysisPreset {
  id: number;
  name: string;
  /** 存的是 pipeline 快照;结构与 PipelineParams 一致,允许缺省分段。 */
  pipeline: Partial<PipelineParams> | Record<string, unknown>;
}

export type HighNiRisk = "low" | "medium" | "high" | "unknown";
export type HighNiOrder = "good" | "acceptable" | "degraded" | "unknown";

export interface HighNiPeakObservation {
  position: number;
  height: number;
  area: number;
  fwhm: number | null;
  fit_error: number | null;
  source: string;
}

export interface HighNiSplitResult {
  peaks: Record<string, HighNiPeakObservation>;
  peakSeparation: number | null;
  valleyRatio: number | null;
  singleFitError: number | null;
  doubleFitError: number | null;
  fitImprovement: number | null;
  splitStatus: "clear" | "weak" | "unresolved" | "not_found";
  fit: boolean;
}

export interface HighNiResult {
  domain: "high_ni_layered";
  analysisVersion: string;
  analysisId?: number;
  datasetId: number | null;
  profileId: number | null;
  sourceHash: string;
  confidence: "high" | "medium" | "low";
  warnings: string[];
  mixing?: {
    i003_i104_area_ratio: number | null;
    i003_i104_height_ratio: number | null;
    r_order: number | null;
    risk: HighNiRisk;
    referenceMode: "user_baseline" | "literature_hint" | "none";
    confidence: "high" | "medium" | "low";
    evidence: string[];
    warnings: string[];
    /** 003/104/101 峰本体的积分边界(2θ),供 UI 在图上叠加 */
    peaks_area_bounds?: {
      "003": [number, number] | null;
      "104": [number, number] | null;
      "101": [number, number] | null;
    };
  };
  layered?: {
    layeredOrder: HighNiOrder;
    split006_102: HighNiSplitResult;
    split018_110: HighNiSplitResult;
    evidence: string[];
    confidence: "high" | "medium" | "low";
    warnings: string[];
  };
  lattice?: {
    a: number | null;
    c: number | null;
    c_over_a: number | null;
    cell_volume: number | null;
    fit_rms: number | null;
    zero_shift: number;
    uncertainty_a: number | null;
    uncertainty_c: number | null;
    used_peaks: string[];
    confidence: "high" | "medium" | "low";
    warnings: string[];
  };
}

// ---------- Library ----------

export interface DatasetNode {
  id: number;
  name: string;
  /** 所属批次;未分组时为 null */
  batch_id?: number | null;
  point_count?: number | null;
}

/** 层级:项目 > 实验(sample) > 批次(batch) > 数据(dataset)。
 *  批次是**可选**的一层 —— 没分批次的数据集直接挂在实验下。 */
export interface BatchNode {
  id: number;
  sample_id: number;
  name: string;
  source_path: string;
  notes: string;
  sample_name: string;
  project_name: string;
  dataset_count: number;
  datasets: DatasetNode[];
}

export interface SampleNode {
  id: number;
  project_id: number;
  name: string;
  /** 煅烧温度。结构趋势图的 x 轴自变量,用户可留空。 */
  temperature: string;
  duration: string;
  material: string;
  batch: string;
  wavelength: number | null;
  notes: string;
  dataset_count: number;
  electrochem_count?: number;
  datasets: DatasetNode[];
  batches: BatchNode[];
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
  /** 本次导入归入的批次;未分组时为 null */
  batchId?: number | null;
}

/** 结构趋势(§11.4)。每个点是**一个数据集**在该指标上的取值。
 *  value 为 null 表示这条数据集还没算过结构分析 —— UI 应画成空心点,
 *  而不是当作 0(那会画成"晶胞塌了")。 */
export interface TrendPoint {
  datasetId: number;
  datasetName: string;
  sampleName: string;
  xValue: number | null;
  xLabel: string;
  value: number | null;
  source: string | null;
}

export interface TrendResult {
  xAxis: string;
  metric: string;
  points: TrendPoint[];
  availableMetrics: string[];
  availableAxes: string[];
}

/** 文件夹扫描的建议分组(datasets.scan_folder)。**只是建议** —— 用户
 *  确认后才逐组调 datasets.import。 */
export interface ScanResult {
  folder: string;
  files: string[];
  groups: { name: string; paths: string[] }[];
}

// ---------- 回收站 ----------

/** 可删除/重命名的对象类型。用**表名**,与引擎命令参数一致,可直接透传。
 *  ec_cells(电化学电池数据)与 datasets 同层挂在样品下。 */
export type TrashKind = "projects" | "samples" | "batches" | "datasets" | "ec_cells";

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

/** meta.file_path:文件型对象的实际磁盘路径(右键"在资源管理器中显示")。
 *  source = 用户自己的原文件;stored = 库内副本(原文件已不在时回落)。 */
export interface MetaFilePathResult {
  path: string;
  kind: "source" | "stored";
}

/** 已归档到样品的蓝电 CEX。 */
export interface CexCellSummary {
  id: number;
  name: string;
  filename: string;
  massMg: number | null;
  cycleCount: number;
}

export interface CexLibraryListing {
  cells: CexCellSummary[];
  historyCount: number;
}

// ---------- 响应封装 ----------



export type EngineResponse<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; detail: string };
