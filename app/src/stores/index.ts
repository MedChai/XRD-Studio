/** Zustand stores(§10.2,每个 store 单一职责,避免 giant global store)。 */

import { create } from "zustand";
import type {
  AnnotationContext,
  CurvePayload,
  AnalysisStateResult,
  PeakResult,
  PeaksAnnotateResult,
  PreviewResult,
  PipelineParams,
  ProjectNode,
  RestoreResult,
  SampleNode,
  SoftDeleteResult,
  ThemeMode,
  TrashItem,
  TrashKind,
  TrashListResult,
  CexLibraryListing,
} from "../types/engine";
import { DISPLAY_MAX_POINTS, engineCommand } from "../services/engine";
import type { XView } from "../services/coords";
import { t } from "../i18n";

// 与 models/types.py 的 dataclass 默认值严格一致(§7.4 快照语义)
export const DEFAULT_PIPELINE: PipelineParams = {
  crop: { enabled: false, x_min: null, x_max: null },
  background: {
    enabled: false, method: "asls",
    smoothness: 1e5, asymmetry: 0.01, iterations: 10, order: 3,
  },
  smoothing: { enabled: false, method: "savgol", window_length: 7, polyorder: 2 },
  normalization: { mode: "max100" },
  peaks: {
    sensitivity: "medium", min_height_pct: 5, min_distance_deg: 0.1,
    prominence: null, width: null, rel_height: 0.5,
  },
};

// ---------------- appStore ----------------

interface AppState {
  theme: ThemeMode;
  setTheme: (t: ThemeMode) => void;
}

export const useAppStore = create<AppState>((set) => ({
  theme: (localStorage.getItem("xrd-theme") as ThemeMode) ?? "system",
  setTheme: (theme) => {
    localStorage.setItem("xrd-theme", theme);
    applyTheme(theme);
    set({ theme });
  },
}));

export function applyTheme(mode: ThemeMode) {
  const dark =
    mode === "dark" ||
    (mode === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

// ---------------- projectStore ----------------

interface ProjectState {
  projects: ProjectNode[];
  samples: SampleNode[];
  currentProjectId: number | null;
  trash: TrashItem[];
  load: () => Promise<void>;
  setCurrentProject: (id: number) => void;
  createProject: (name: string, description?: string) => Promise<number | null>;
  renameItem: (table: TrashKind, id: number, name: string) => Promise<boolean>;
  deleteItem: (table: TrashKind, id: number) => Promise<boolean>;
  loadTrash: () => Promise<void>;
  restoreItem: (table: TrashKind, id: number) => Promise<boolean>;
  purgeItem: (table: TrashKind, id: number) => Promise<boolean>;
  purgeAll: () => Promise<boolean>;
}

export const useProjectStore = create<ProjectState>((set, get) => ({
  projects: [],
  samples: [],
  currentProjectId: null,
  trash: [],
  load: async () => {
    const [p, t] = await Promise.all([
      engineCommand<{ projects: ProjectNode[] }>("projects.list"),
      engineCommand<{ samples: SampleNode[] }>("library.tree"),
    ]);
    if (p.ok) {
      // currentProjectId 可能指向一个刚被删除的项目 —— 只在它仍存在于
      // 新列表里时才保留,否则回落到第一个。不修这一步,删掉当前项目后
      // 顶栏会显示旧项目名、且 Import 会往一个已删项目里写数据。
      const cur = get().currentProjectId;
      const stillThere = cur != null && p.data.projects.some((x) => x.id === cur);
      set({
        projects: p.data.projects,
        currentProjectId: stillThere ? cur : (p.data.projects[0]?.id ?? null),
      });
    }
    if (t.ok) set({ samples: t.data.samples });
  },
  setCurrentProject: (id) => set({ currentProjectId: id }),
  createProject: async (name, description = "") => {
    const r = await engineCommand<{ projectId: number }>("projects.create", {
      name,
      description,
    });
    if (r.ok) {
      await get().load();
      set({ currentProjectId: r.data.projectId });
      return r.data.projectId;
    }
    useUiStore.getState().showToast("error", r.error);
    return null;
  },

  renameItem: async (table, id, name) => {
    const r = await engineCommand("meta.rename", { table, id, name });
    if (!r.ok) {
      useUiStore.getState().showToast("error", r.error);
      return false;
    }
    await get().load();
    useCexLibraryStore.getState().invalidateCells();
    return true;
  },

  deleteItem: async (table, id) => {
    const r = await engineCommand<SoftDeleteResult>("meta.soft_delete", { table, id });
    if (!r.ok) {
      useUiStore.getState().showToast("error", r.error);
      return false;
    }
    // 回收站列表也要刷新:侧栏的徽标数与回收站对话框的内容都依赖它
    await Promise.all([get().load(), get().loadTrash()]);
    useCexLibraryStore.getState().invalidateCells();
    // 删掉的若是当前选中的数据集,清空视图,避免继续显示一条已不存在的曲线
    const sel = useSelectionStore.getState();
    if (table === "datasets" && sel.datasetId === id) {
      sel.selectDataset(null, null);
      useAnalysisStore.getState().clearDataset();
    } else if (table !== "datasets" && sel.datasetId != null) {
      const stillThere = get().samples.some((s) =>
        s.datasets.some((d) => d.id === sel.datasetId),
      );
      if (!stillThere) {
        sel.selectDataset(null, null);
        useAnalysisStore.getState().clearDataset();
      }
    }
    if (sel.cexActive && ((table === "ec_cells" && sel.cellId === id)
        || (sel.sampleId != null && !get().samples.some((sample) => sample.id === sel.sampleId)))) {
      sel.selectDataset(null, null);
    }
    const n = r.data.cascadedDatasets;
    useUiStore.getState().showToast("success", t.trash.toastDeleted(n));
    return true;
  },

  loadTrash: async () => {
    const r = await engineCommand<TrashListResult>("trash.list");
    if (r.ok) set({ trash: r.data.items });
    else useUiStore.getState().showToast("error", r.error);
  },

  restoreItem: async (table, id) => {
    const r = await engineCommand<RestoreResult>("meta.restore", { table, id });
    if (!r.ok) {
      useUiStore.getState().showToast("error", r.error);
      return false;
    }
    await Promise.all([get().load(), get().loadTrash()]);
    useCexLibraryStore.getState().invalidateCells();
    const anc = r.data.restoredAncestors;
    useUiStore.getState().showToast(
      "success",
      t.trash.toastRestored(anc.map((a) => a.name).join(" / ")),
    );
    return true;
  },

  purgeItem: async (table, id) => {
    const r = await engineCommand("meta.purge", { table, id });
    if (!r.ok) {
      useUiStore.getState().showToast("error", r.error);
      return false;
    }
    await get().loadTrash();
    useUiStore.getState().showToast("success", t.trash.toastPurged);
    return true;
  },

  purgeAll: async () => {
    const r = await engineCommand("meta.purge", { all: true });
    if (!r.ok) {
      useUiStore.getState().showToast("error", r.error);
      return false;
    }
    await get().loadTrash();
    useUiStore.getState().showToast("success", t.trash.toastEmptied);
    return true;
  },
}));

// ---------------- selectionStore ----------------

interface SelectionState {
  sampleId: number | null;
  datasetId: number | null;
  /** 选中的电化学 cell(ec_cells.id)。与 datasetId 互斥 —— 同一时刻
   *  主工作区要么是 XRD 谱图,要么是电化学页面。 */
  cellId: number | null;
  cexActive: boolean;
  /** "ds:<id>" 形式的曲线显隐集合;null 表示全选 */
  hiddenCurves: Set<string> | null;
  /** 联动中枢(§35):点击表格行 ↔ 点击图中峰 */
  selectedPeakIndex: number | null;
  /** 传 null 表示清空选择(数据集被删除时) */
  selectDataset: (sampleId: number | null, datasetId: number | null) => void;
  selectCex: (sampleId?: number | null, cellId?: number | null) => void;
  toggleCurve: (key: string) => void;
  setSelectedPeak: (index: number | null) => void;
}

export const useSelectionStore = create<SelectionState>((set) => ({
  sampleId: null,
  datasetId: null,
  cellId: null,
  cexActive: false,
  hiddenCurves: null,
  selectedPeakIndex: null,
  selectDataset: (sampleId, datasetId) =>
    set(() => {
      // 样本归属自动补全:趋势图点数据点、导入"打开已有"这类旁路只带
      // datasetId。丢掉 sampleId 会让 Inspector 的材料档案/预设拉不到
      // (profileProjectId 依赖 sample),整段功能无声失效。
      let sid = sampleId;
      if (sid == null && datasetId != null) {
        sid =
          useProjectStore
            .getState()
            .samples.find((s) => s.datasets.some((d) => d.id === datasetId))
            ?.id ?? null;
      }
      return { sampleId: sid, datasetId, selectedPeakIndex: null, cellId: null, cexActive: false };
    }),
  selectCex: (sampleId = null, cellId = null) =>
    set({ sampleId, cellId, datasetId: null, selectedPeakIndex: null, hiddenCurves: null, cexActive: true }),
  toggleCurve: (key) =>
    set((s) => {
      const cur = new Set(s.hiddenCurves ?? []);
      if (cur.has(key)) cur.delete(key);
      else cur.add(key);
      return { hiddenCurves: cur };
    }),
  setSelectedPeak: (selectedPeakIndex) => set({ selectedPeakIndex }),
}));

/** 对比曲线(多谱对比 S4)。只带显示所需的最小字段。 */
export interface CompareCurve {
  datasetId: number;
  name: string;
  color: string;
  curve: CurvePayload;
}

// ---------------- plotStore ----------------

interface PlotState {
  mode: "overlay" | "stacked";
  crosshair: boolean;
  showLabels: boolean;
  /** 纯显示层参数,前端就地生效,不走引擎(§9.2) */
  offset: number;
  /** 光标处的数据坐标(状态栏显示,§15 草图) */
  cursor: { x: number; y: number } | null;
  /** 当前曲线的完整 x 值域,作为 Fit All 与底部范围框的共同基准。
   * 由 loadDataset 成功后写入 —— 不能从 XRDChart 内部推,因为底部范围框
   * 也需要它。 */
  xDomain: [number, number] | null;
  /** 缩放窗口(§10.2:plotStore 持有缩放窗口)。
   *
   * **用数据坐标(2θ)而不是百分比**:百分比是相对于当前曲线值域的,
   * 切换数据集后旧的百分比会落在新曲线的错误位置上,表现为"图是空的"
   * 且极难排查。数据坐标跨曲线切换是无歧义的。
   *
   * null 表示"全域"(未缩放)。 */
  zoom: { from: number; to: number } | null;
  /** 是否叠加显示背景曲线(§7.3 的 Show Background) */
  showBackground: boolean;
  /** X 轴显示坐标(§4.1 视图层 2θ/d/Q 切换)。纯显示态:峰表结果与 zoom
   * 仍按采集轴存储(README §4.1),换算只作用于图上 x 映射与读数。 */
  xView: XView;
  /** 当前 ECharts 实例提供的 PNG 导出函数。图表卸载时清空。 */
  figureExporter: (() => string) | null;
  /** SVG 导出:离屏用 svg renderer 重画同一 option,矢量可进矢量软件。 */
  figureSvgExporter: (() => string) | null;
  setMode: (m: PlotState["mode"]) => void;
  setCrosshair: (v: boolean) => void;
  setShowLabels: (v: boolean) => void;
  setOffset: (v: number) => void;
  setCursor: (c: PlotState["cursor"]) => void;
  setXDomain: (d: PlotState["xDomain"]) => void;
  setZoom: (z: PlotState["zoom"]) => void;
  setShowBackground: (v: boolean) => void;
  setXView: (v: XView) => void;
  setFigureExporter: (fn: (() => string) | null) => void;
  setFigureSvgExporter: (fn: (() => string) | null) => void;
  /** 叠加对比的曲线集合(S4 系列对比)。
   *
   * 与 `analysisStore.curve` 的分工:那条是**当前分析对象**(峰、指标都
   * 挂在它身上);这里只是**显示层的参考曲线**,没有峰、没有处理参数,
   * 唯一作用是画出来给用户横向比。因此不放进 analysisStore —— 放进去
   * 会让"当前数据集"与"对比曲线"两个概念混在一起。
   *
   * 每项带自己的颜色与显示名(取 dataset 名),避免在图表里再去查库。 */
  compareCurves: CompareCurve[];
  addCompareCurve: (c: CompareCurve) => void;
  removeCompareCurve: (datasetId: number) => void;
  clearCompareCurves: () => void;
  /** 每条曲线的垂直偏移(stacked 模式用;overlay 模式下为 0)。
   * 不放进 CompareCurve 里是因为它随模式切换整体重算。 */
  compareOffsets: Record<number, number>;
  /** Fit All:把缩放窗口设回全域。zoom 是唯一真相,两个图都订阅它自动跟随,
   * 所以不需要额外的"请求序号"来通知谁去执行。 */
  fitAll: () => void;
}

export const usePlotStore = create<PlotState>((set, get) => ({
  mode: "overlay",
  crosshair: false,
  showLabels: true,
  offset: 0,
  cursor: null,
  xDomain: null,
  zoom: null,
  showBackground: false,
  xView: "two_theta",
  figureExporter: null,
  figureSvgExporter: null,
  compareCurves: [],
  compareOffsets: {} as Record<number, number>,
  setMode: (mode) => set({ mode }),
  setCrosshair: (crosshair) => set({ crosshair }),
  setShowLabels: (showLabels) => set({ showLabels }),
  setOffset: (offset) => set({ offset }),
  setCursor: (cursor) => set({ cursor }),
  setXDomain: (xDomain) => set({ xDomain }),
  setZoom: (zoom) => set({ zoom }),
  addCompareCurve: (c) =>
    set((s) => {
      // 同一个数据集不叠加两次:重复添加会让两条参考曲线完全重合,
      // 图上看不出问题(叠在一起),但图例多一项、删除时也分不清删哪条。
      if (s.compareCurves.some((x) => x.datasetId === c.datasetId)) return s;
      // 上限 6 条:再多颜色就分不清了,ECharts 图例会挤成一团,
      // 而且每条都要占一份内存里的完整曲线。
      if (s.compareCurves.length >= 6) return s;
      return { compareCurves: [...s.compareCurves, c] };
    }),
  removeCompareCurve: (datasetId) =>
    set((s) => {
      const next = { ...s.compareOffsets };
      delete next[datasetId];
      return {
        compareCurves: s.compareCurves.filter((x) => x.datasetId !== datasetId),
        compareOffsets: next,
      };
    }),
  clearCompareCurves: () => set({ compareCurves: [], compareOffsets: {} }),
  setCompareOffsets: (compareOffsets: PlotState["compareOffsets"]) =>
    set({ compareOffsets }),
  setShowBackground: (showBackground) => set({ showBackground }),
  setXView: (xView) => set({ xView }),
  setFigureExporter: (figureExporter) => set({ figureExporter }),
  setFigureSvgExporter: (figureSvgExporter) => set({ figureSvgExporter }),
  fitAll: () => {
    const d = get().xDomain;
    if (d) set({ zoom: { from: d[0], to: d[1] } });
  },
}));

// ---------------- analysisStore ----------------

interface AnalysisState {
  pipeline: PipelineParams;
  curve: CurvePayload | null;
  peaks: PeakResult[];
  smoothingAffectsMetrics: boolean;
  /** preview 状态机(§10.3):idle → previewing → committing */
  status: "idle" | "previewing" | "committing" | "unsaved";
  /** Undo/Redo 有界快照栈(§10.5),仅参数类操作 */
  undoStack: PipelineParams[];
  redoStack: PipelineParams[];
  loadedDatasetId: number | null;
  /** 峰是否与磁盘上的 peaks 表一致。patchPipeline 会把它置 true(参数变了,
   * 内存里的峰已经不是固化过的那批),peaks.detect 成功后置 false。
   * 没有这个标记,用户点完「检测峰」看不出到底有没有生效。 */
  peaksDirty: boolean;
  /** 已持久化的晶面标注(analyses.annotation_json)。打开图即从库恢复,
   *  不重算;点「标注晶面」才刷新。 */
  annotation: AnnotationContext | null;
  /** 当前 pipeline 与标注时不一致 → 保留旧标签但显示"已过期"提示。 */
  annotationStale: boolean;
  /** 正在后台/显式标注(全谱精修 1.5–4.5s,首次)。 */
  annotationLoading: boolean;
  /** peaks.detect 进行中(按钮 loading 与命令面板共用)。 */
  detecting: boolean;
  /** 滑块拖动节流:cancel 旧请求语义由序号丢弃实现 */
  _previewSeq: number;
  _commitTimer: number | null;
  /** 30ms 节流的 preview 定时器 id。新 patch/load/undo 时必须 clear ——
   *  只靠序号丢结果会让废弃请求仍在引擎侧跑完整条 pipeline(排队堆积)。 */
  _previewTimer: number | null;
  /** Undo 帧合并:400ms 窗口内的连续 patch 只压一帧进 undoStack。
   *  拖滑块每 30ms 一帧会把 50 格栈瞬间填满,Ctrl+Z 要按几十次才回到起点。 */
  _lastUndoAt: number;

  flushEdits: () => Promise<boolean>;
  loadDataset: (datasetId: number) => Promise<void>;
  /** 清空当前曲线与峰(数据集被删除时用) */
  clearDataset: () => void;
  patchPipeline: (patch: Partial<PipelineParams>) => void;
  /** peaks.detect 成功后调用:把固化后的峰写回并清 dirty */
  markPeaksCommitted: (peaks: PeakResult[]) => void;
  /** 「检测峰」唯一实现:先 commit 落库参数,再让引擎正式寻峰写 peaks 表。
   *  Inspector 按钮与命令面板共用 —— 此前面板里是空 patchPipeline({}),
   *  语义完全不同(不 detect、不 commit、不清 dirty)。 */
  detectPeaks: () => Promise<void>;
  /** 显式选择层状模型后重新标注当前数据集。 */
  recomputeAnnotation: () => Promise<void>;
  undo: () => void;
  redo: () => void;
}

const clone = (p: PipelineParams): PipelineParams => structuredClone(p);

/** 标注是否已过期:annotation 的 pipelineHash 与当前不一致。
 *  任何一环缺信息(从未标注/旧数据无 hash)都视为不过期 —— 误报 stale
 *  会诱导用户反复点重算。 */
function isAnnotationStale(
  hash: string | undefined,
  annotation: AnnotationContext | null,
): boolean {
  if (!annotation || !hash) return false;
  return (annotation.pipelineHash ?? "") !== hash;
}

/** 把 annotate 返回的标注按 position 合并进当前峰表(容差=网格步长)。 */
function mergePipeline(raw: Partial<PipelineParams>): PipelineParams {
  return {
    crop: { ...DEFAULT_PIPELINE.crop, ...(raw.crop ?? {}) },
    background: { ...DEFAULT_PIPELINE.background, ...(raw.background ?? {}) },
    smoothing: { ...DEFAULT_PIPELINE.smoothing, ...(raw.smoothing ?? {}) },
    normalization: { ...DEFAULT_PIPELINE.normalization, ...(raw.normalization ?? {}) },
    peaks: { ...DEFAULT_PIPELINE.peaks, ...(raw.peaks ?? {}) },
  };
}

function syncPlotDomain(curve: CurvePayload): void {
  if (curve.x.length === 0) {
    usePlotStore.getState().setXDomain(null);
    usePlotStore.getState().setZoom(null);
    return;
  }
  const next: [number, number] = [curve.x[0], curve.x[curve.x.length - 1]];
  const plot = usePlotStore.getState();
  const previous = plot.xDomain;
  plot.setXDomain(next);
  if (plot.zoom && previous && (previous[0] !== next[0] || previous[1] !== next[1])) {
    const from = Math.max(next[0], plot.zoom.from);
    const to = Math.min(next[1], plot.zoom.to);
    plot.setZoom(from < to ? { from, to } : null);
  }
}

// Each queued edit belongs to its dataset even while another spectrum is loading.
const pendingEdits = new Map<number, PipelineParams>();
const saving = new Map<number, Promise<boolean>>();

async function flushDatasetEdits(id: number | null): Promise<boolean> {
  if (id == null) return true;
  const active = saving.get(id);
  if (active && !await active) return false;
  if (saving.has(id)) return flushDatasetEdits(id);
  const pipeline = pendingEdits.get(id);
  if (!pipeline) return true;
  const write = engineCommand("analysis.commit", { dataset_id: id, pipeline })
    .then((r) => {
      if (!r.ok) {
        useUiStore.getState().showToast("error", r.error);
        return false;
      }
      if (pendingEdits.get(id) === pipeline) pendingEdits.delete(id);
      return true;
    })
    .catch((error: unknown) => {
      useUiStore.getState().showToast("error", String(error));
      return false;
    })
    .finally(() => saving.delete(id));
  saving.set(id, write);
  return write;
}

export const useAnalysisStore = create<AnalysisState>((set, get) => ({
  pipeline: clone(DEFAULT_PIPELINE),
  curve: null,
  peaks: [],
  smoothingAffectsMetrics: false,
  status: "idle",
  undoStack: [],
  redoStack: [],
  loadedDatasetId: null,
  peaksDirty: false,
  annotation: null,
  annotationStale: false,
  annotationLoading: false,
  detecting: false,
  _previewSeq: 0,
  _commitTimer: null,
  _previewTimer: null,
  _lastUndoAt: 0,

  flushEdits: () => flushDatasetEdits(get().loadedDatasetId),

  loadDataset: async (datasetId) => {
    const previousId = get().loadedDatasetId;
    const previousSave = flushDatasetEdits(previousId);
    const previousTimer = get()._commitTimer;
    if (previousTimer) window.clearTimeout(previousTimer);
    const previousPreview = get()._previewTimer;
    if (previousPreview) window.clearTimeout(previousPreview);
    const seq = get()._previewSeq + 1;
    set({
      _previewSeq: seq,
      _commitTimer: null,
      _previewTimer: null,
      loadedDatasetId: datasetId,
      _lastUndoAt: 0,
      curve: null,
      peaks: [],
      peaksDirty: false,
      annotation: null,
      annotationStale: false,
      annotationLoading: false,
      status: "previewing",
      undoStack: [],
      redoStack: [],
    });

    await previousSave;
    if (seq !== get()._previewSeq) return;
    if (!await flushDatasetEdits(datasetId)) {
      if (seq === get()._previewSeq) set({ status: "unsaved" });
      return;
    }
    if (seq !== get()._previewSeq) return;
    // 先取原始曲线,让数据集切换在几十到几百毫秒内有可见反馈;
    // analysis.state 随后恢复该数据集自己的 pipeline 与正式峰结果。
    const r = await engineCommand<CurvePayload>("datasets.curve", {
      id: datasetId,
      max_points: DISPLAY_MAX_POINTS,
    });
    if (seq !== get()._previewSeq) return;
    if (!r.ok) {
      set({ status: "idle" });
      useUiStore.getState().showToast("error", r.error);
      return;
    }

    set({ curve: r.data });
    syncPlotDomain(r.data);
    usePlotStore.getState().setZoom(null);

    const state = await engineCommand<AnalysisStateResult>("analysis.state", {
      dataset_id: datasetId,
      max_points: DISPLAY_MAX_POINTS,
    });
    if (seq !== get()._previewSeq) return;
    if (!state.ok) {
      set({ status: "idle" });
      useUiStore.getState().showToast("error", state.error);
      return;
    }

    // 打开图只读库(annotation_json + peaks 表),零精修 —— 亚秒见标签。
    const annotation = state.data.annotation ?? null;
    const stale = isAnnotationStale(state.data.pipelineHash, annotation);
    set({
      pipeline: mergePipeline(state.data.pipeline),
      curve: state.data.curve,
      peaks: state.data.peaks,
      peaksDirty: !state.data.peaksStored,
      smoothingAffectsMetrics: state.data.smoothingAffectsMetrics,
      annotation,
      annotationStale: stale,
      status: "idle",
      undoStack: [],
      redoStack: [],
    });
    syncPlotDomain(state.data.curve);

  },

  clearDataset: () => {
    const s = get();
    // 与 loadDataset 同样清理:不关掉在途 preview/commit 定时器、不递增
    // _previewSeq,删完数据集后残留请求会把曲线写回 UI(数据"复活"),
    // commit 定时器还会对已删 dataset 发保存请求。
    if (s._commitTimer) window.clearTimeout(s._commitTimer);
    if (s._previewTimer) window.clearTimeout(s._previewTimer);
    set({
      curve: null,
      peaks: [],
      loadedDatasetId: null,
      peaksDirty: false,
      annotation: null,
      annotationStale: false,
      annotationLoading: false,
      status: "idle",
      smoothingAffectsMetrics: false,
      undoStack: [],
      redoStack: [],
      _previewSeq: s._previewSeq + 1,
      _commitTimer: null,
      _previewTimer: null,
    });
    usePlotStore.getState().setXDomain(null);
    usePlotStore.getState().setZoom(null);
  },

  markPeaksCommitted: (peaks) => set({ peaks, peaksDirty: false }),

  detectPeaks: async () => {
    const dsId = get().loadedDatasetId;
    if (dsId == null || get().detecting) return;
    // 参数快照:await 期间用户可能切到别的数据集,不能把切走后的
    // get().pipeline(已经是新数据集的参数)commit 到旧数据集头上
    const pipeline = get().pipeline;
    pendingEdits.set(dsId, pipeline);
    set({ detecting: true });
    try {
      // Detect Peaks 是「固化」动作:先把当前参数落库(commit),再让
      // 引擎按落库的参数正式跑一遍寻峰、写进 peaks 表。
      if (!await flushDatasetEdits(dsId)) return;
      const r = await engineCommand<{ peaks: PeakResult[]; annotation: AnnotationContext | null }>("peaks.detect", {
        dataset_id: dsId,
      });
      if (!r.ok) {
        useUiStore.getState().showToast("error", r.error);
        return;
      }
      if (get().loadedDatasetId !== dsId) return;
      if (get().pipeline !== pipeline) return;
      set({ annotation: r.data.annotation, annotationStale: false });
      get().markPeaksCommitted(r.data.peaks);
      useUiStore.getState().showToast("success", t.inspector.detected(r.data.peaks.length));
    } finally {
      set({ detecting: false });
    }
  },

  recomputeAnnotation: async () => {
    const { loadedDatasetId: dsId, annotationLoading, _previewSeq: seq } = get();
    if (dsId == null || annotationLoading) return;
    set({ annotationLoading: true });
    try {
      if (!await flushDatasetEdits(dsId)) return;
      const r = await engineCommand<PeaksAnnotateResult>("peaks.annotate", { dataset_id: dsId });
      if (get().loadedDatasetId !== dsId || get()._previewSeq !== seq) return;
      if (!r.ok) {
        useUiStore.getState().showToast("error", r.error);
        return;
      }
      set({ annotation: r.data.annotation ?? null, annotationStale: false,
        peaks: r.data.peaks, peaksDirty: false });
      useUiStore.getState().showToast("success", t.inspector.annotatedHkl(r.data.count));
    } finally {
      if (get().loadedDatasetId === dsId) set({ annotationLoading: false });
    }
  },

  patchPipeline: (patch) => {
    const s = get();
    const next = { ...clone(s.pipeline), ...patch };
    const dsId = s.loadedDatasetId;
    if (dsId != null) pendingEdits.set(dsId, next);
    // Undo 帧合并(400ms):同一次拖动产生的连续 patch 只压一帧。
    // 首帧总是入栈(_lastUndoAt=0),之后窗口内的 patch 不再入栈 ——
    // 一次 Ctrl+Z 就能回到这次拖动开始前的状态。
    const now = Date.now();
    const coalesce = now - s._lastUndoAt < 400;
    set((st) => ({
      pipeline: next,
      // 无数据集时不置 previewing —— 后面没有通路会把它拉回 idle,
      // StatusBar 会永久卡在"计算中"(undo 栈仍记录,切换数据集前的
      // 参数编辑是合法操作,只是无处可算)
      status: dsId != null ? "previewing" : st.status,
      // 参数变了 → 内存里的峰已不是固化过的那批
      peaksDirty: true,
      undoStack: coalesce
        ? st.undoStack
        : [...st.undoStack.slice(-49), st.pipeline],
      redoStack: [],
      _lastUndoAt: coalesce ? st._lastUndoAt : now,
    }));

    // 300ms 无新输入 → committing(§10.3)
    if (s._commitTimer) window.clearTimeout(s._commitTimer);
    if (s._previewTimer) window.clearTimeout(s._previewTimer);
    const seq = ++get()._previewSeq;

    // 引擎类参数:节流发送,乱序丢弃。拖动中 compute_peak_metrics=false
    // —— 跳过 FWHM/Area(它们是拖动延迟的主要来源之一),只保曲线跟手;
    // commit 前的收尾 preview 再传 true 把度量补全。
    if (dsId != null) {
      const previewTimer = window.setTimeout(async () => {
        if (seq !== get()._previewSeq) return; // cancel 语义
        const r = await engineCommand<PreviewResult>("analysis.preview", {
          dataset_id: dsId,
          pipeline: get().pipeline,
          max_points: DISPLAY_MAX_POINTS,
          annotate: false,
          // 标注不重算,但把 annotation_json 的 labels 快照按位置合回
          // —— 拖滑块时图上的晶面标签不消失(查库合并,亚毫秒)。
          restoreAnnotation: true,
          compute_peak_metrics: false,
        });
        if (seq !== get()._previewSeq) return;
        if (r.ok) {
          set({
            curve: r.data.curve,
            peaks: r.data.peaks,
            smoothingAffectsMetrics: r.data.smoothingAffectsMetrics,
            annotationStale: isAnnotationStale(
              r.data.pipelineHash,
              get().annotation,
            ),
          });
          syncPlotDomain(r.data.curve);
        } else {
          set({ status: "idle" });
          useUiStore.getState().showToast("error", r.error);
        }
      }, 30);
      set({ _previewTimer: previewTimer });
    }

    set({
      _commitTimer: window.setTimeout(async () => {
        if (dsId != null) {
          set({ status: "committing" });
          // 收尾 preview:把拖动中跳过的 FWHM/Area 补回来,再落库。
          // 不补的话峰表会在拖动后一直显示 "—",直到下次「检测峰」。
          const commitSeq = seq;
          const fin = await engineCommand<PreviewResult>("analysis.preview", {
            dataset_id: dsId,
            pipeline: next,
            max_points: DISPLAY_MAX_POINTS,
            annotate: false,
            restoreAnnotation: true,
            compute_peak_metrics: true,
          });
          if (commitSeq === get()._previewSeq) {
            if (fin.ok) {
              set({
                curve: fin.data.curve,
                peaks: fin.data.peaks,
                smoothingAffectsMetrics: fin.data.smoothingAffectsMetrics,
              });
            } else {
              // 收尾失败不能静默:峰表会停在 compute_peak_metrics=false
              // 的空 FWHM/Area,而状态显示"已保存"
              useUiStore.getState().showToast("error", fin.error);
            }
          }
          if (commitSeq !== get()._previewSeq) return;
          const saved = await flushDatasetEdits(dsId);
          // 只有这轮 seq 还没被新 patch 顶掉才收状态;否则新一轮 preview
          // 在途,状态归它管 —— 无条件置 idle 会让"拖动中 commit"显示假"已保存"
          if (commitSeq === get()._previewSeq) {
            set({ status: saved ? "idle" : "unsaved" });
          }
        }
      }, 300),
    });
  },

  undo: () => {
    const s = get();
    const prev = s.undoStack[s.undoStack.length - 1];
    if (!prev) return;
    if (s.loadedDatasetId != null) pendingEdits.set(s.loadedDatasetId, prev);
    // 拖完 300ms 内按 Ctrl+Z:旧 commit/preview 定时器必须清掉,否则它们会用
    // get().pipeline 再跑一次(值可能已变对,但多一次写库与状态抖动)。
    if (s._commitTimer) window.clearTimeout(s._commitTimer);
    if (s._previewTimer) window.clearTimeout(s._previewTimer);
    set((st) => ({
      pipeline: prev,
      undoStack: st.undoStack.slice(0, -1),
      redoStack: [...st.redoStack, st.pipeline],
      peaksDirty: true,
      // 无数据集不置 previewing(没有通路拉回 idle,状态会永久卡住)
      status: s.loadedDatasetId != null ? "previewing" : st.status,
      _commitTimer: null,
      _previewTimer: null,
      // undo 后立即再调参应开新帧,不与刚结束的拖动窗口合并
      _lastUndoAt: 0,
    }));
    if (s.loadedDatasetId != null) {
      // Undo 复用 preview→commit 通路(§10.5),不走旁门。
      // **必须同样递增 _previewSeq**:否则「拖滑块后立刻 Ctrl+Z」时,
      // 先前排队的那次 preview 会在本次之后返回并覆盖 curve/peaks,
      // 把曲线退回旧参数的结果。
      const seq = ++get()._previewSeq;
      void engineCommand<PreviewResult>("analysis.preview", {
        dataset_id: s.loadedDatasetId,
        pipeline: prev,
        max_points: DISPLAY_MAX_POINTS,
        annotate: false,
        restoreAnnotation: true,
      }).then((r) => {
        if (seq !== get()._previewSeq) return;
        if (!r.ok) {
          set({ status: "idle" });
          useUiStore.getState().showToast("error", r.error);
          return;
        }
        set({
          curve: r.data.curve,
          peaks: r.data.peaks,
          smoothingAffectsMetrics: r.data.smoothingAffectsMetrics,
          annotationStale: isAnnotationStale(r.data.pipelineHash, get().annotation),
          status: "committing",
        });
        syncPlotDomain(r.data.curve);
        return flushDatasetEdits(s.loadedDatasetId).then((ok) => {
          if (seq === get()._previewSeq) set({ status: ok ? "idle" : "unsaved" });
        });
      });
    }
  },

  redo: () => {
    const s = get();
    const nxt = s.redoStack[s.redoStack.length - 1];
    if (!nxt) return;
    if (s.loadedDatasetId != null) pendingEdits.set(s.loadedDatasetId, nxt);
    if (s._commitTimer) window.clearTimeout(s._commitTimer);
    if (s._previewTimer) window.clearTimeout(s._previewTimer);
    set((st) => ({
      pipeline: nxt,
      redoStack: st.redoStack.slice(0, -1),
      undoStack: [...st.undoStack, st.pipeline],
      peaksDirty: true,
      status: s.loadedDatasetId != null ? "previewing" : st.status,
      _commitTimer: null,
      _previewTimer: null,
      _lastUndoAt: 0,
    }));
    if (s.loadedDatasetId != null) {
      const seq = ++get()._previewSeq;
      void engineCommand<PreviewResult>("analysis.preview", {
        dataset_id: s.loadedDatasetId,
        pipeline: nxt,
        max_points: DISPLAY_MAX_POINTS,
        annotate: false,
        restoreAnnotation: true,
      }).then((r) => {
        if (seq !== get()._previewSeq) return;
        if (!r.ok) {
          set({ status: "idle" });
          useUiStore.getState().showToast("error", r.error);
          return;
        }
        set({
          curve: r.data.curve,
          peaks: r.data.peaks,
          smoothingAffectsMetrics: r.data.smoothingAffectsMetrics,
          annotationStale: isAnnotationStale(r.data.pipelineHash, get().annotation),
          status: "committing",
        });
        syncPlotDomain(r.data.curve);
        return flushDatasetEdits(s.loadedDatasetId).then((ok) => {
          if (seq === get()._previewSeq) set({ status: ok ? "idle" : "unsaved" });
        });
      });
    }
  },
}));

interface CexLibraryState {
  bySample: Record<number, CexLibraryListing>;
  revision: number;
  loadCells: (sampleId: number, force?: boolean) => Promise<void>;
  invalidateCells: () => void;
}

export const useCexLibraryStore = create<CexLibraryState>((set, get) => ({
  bySample: {},
  revision: 0,
  loadCells: async (sampleId, force = false) => {
    const { bySample, revision } = get();
    if (!force && bySample[sampleId]) return;
    const result = await engineCommand<CexLibraryListing>("cex.cells", { sample_id: sampleId });
    if (revision !== get().revision) return;
    if (result.ok) set((state) => ({ bySample: { ...state.bySample, [sampleId]: result.data } }));
    else useUiStore.getState().showToast("error", result.error);
  },
  invalidateCells: () => set((state) => ({ bySample: {}, revision: state.revision + 1 })),
}));

interface UiState {
  importOpen: boolean;
  paletteOpen: boolean;
  trendOpen: boolean;
  /** 正在编辑信息的实验(样品)。null = 弹窗关闭。 */
  editingSample: { id: number; name: string } | null;
  settingsOpen: boolean;
  newProjectOpen: boolean;
  trashOpen: boolean;
  /** 重命名弹窗的目标;null 表示关闭 */
  renaming: { table: TrashKind; id: number; name: string } | null;
  toast: { kind: "success" | "error"; text: string } | null;
  /** 拖放进来的文件路径,由 AppShell 写入、ImportSheet 消费 */
  pendingDropPaths: string[] | null;
  /** 上次导入归属的 sample(列映射往返时用) */
  lastSampleId: number | null;
  /** toast 的自动消失定时器。存进 store 才能在下次弹出前清掉上一个 ——
   * 否则第二条 toast 会在第一条的定时器到点时被提前清掉 */
  _toastTimer: number | null;
  setImportOpen: (v: boolean) => void;
  setPaletteOpen: (v: boolean) => void;
  setTrendOpen: (v: boolean) => void;
  setEditingSample: (v: { id: number; name: string } | null) => void;
  setSettingsOpen: (v: boolean) => void;
  setNewProjectOpen: (v: boolean) => void;
  setTrashOpen: (v: boolean) => void;
  setRenaming: (v: UiState["renaming"]) => void;
  setPendingDropPaths: (v: string[] | null) => void;
  setLastSampleId: (v: number | null) => void;
  showToast: (kind: "success" | "error", text: string) => void;
  dismissToast: () => void;
}

export const useUiStore = create<UiState>((set, get) => ({
  importOpen: false,
  paletteOpen: false,
  trendOpen: false,
  editingSample: null,
  settingsOpen: false,
  newProjectOpen: false,
  trashOpen: false,
  renaming: null,
  toast: null,
  pendingDropPaths: null,
  lastSampleId: null,
  _toastTimer: null,
  setImportOpen: (importOpen) => set({ importOpen }),
  setPaletteOpen: (paletteOpen) => set({ paletteOpen }),
  setTrendOpen: (trendOpen) => set({ trendOpen }),
  setEditingSample: (editingSample) => set({ editingSample }),
  setSettingsOpen: (settingsOpen) => set({ settingsOpen }),
  setNewProjectOpen: (newProjectOpen) => set({ newProjectOpen }),
  setTrashOpen: (trashOpen) => set({ trashOpen }),
  setRenaming: (renaming) => set({ renaming }),
  setPendingDropPaths: (pendingDropPaths) => set({ pendingDropPaths }),
  setLastSampleId: (lastSampleId) => set({ lastSampleId }),
  showToast: (kind, text) => {
    const prev = get()._toastTimer;
    if (prev != null) window.clearTimeout(prev);
    set({ toast: { kind, text } });
    set({
      _toastTimer: window.setTimeout(
        () => set({ toast: null, _toastTimer: null }),
        3200,
      ),
    });
  },
  dismissToast: () => {
    const prev = get()._toastTimer;
    if (prev != null) window.clearTimeout(prev);
    set({ toast: null, _toastTimer: null });
  },
}));
