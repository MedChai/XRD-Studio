/** Zustand stores(§10.2,每个 store 单一职责,避免 giant global store)。 */

import { create } from "zustand";
import type {
  CurvePayload,
  PeakResult,
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
} from "../types/engine";
import { engineCommand } from "../services/engine";

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
    const n = r.data.cascadedDatasets;
    useUiStore.getState().showToast(
      "success",
      n > 0 ? `已移入回收站(含 ${n} 个数据集)` : "已移入回收站",
    );
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
    const anc = r.data.restoredAncestors;
    useUiStore.getState().showToast(
      "success",
      anc.length > 0
        ? `已恢复(并一并恢复其所属 ${anc.map((a) => a.name).join(" / ")})`
        : "已恢复",
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
    useUiStore.getState().showToast("success", "已彻底删除");
    return true;
  },

  purgeAll: async () => {
    const r = await engineCommand("meta.purge", { all: true });
    if (!r.ok) {
      useUiStore.getState().showToast("error", r.error);
      return false;
    }
    await get().loadTrash();
    useUiStore.getState().showToast("success", "回收站已清空");
    return true;
  },
}));

// ---------------- selectionStore ----------------

interface SelectionState {
  sampleId: number | null;
  datasetId: number | null;
  /** "ds:<id>" 形式的曲线显隐集合;null 表示全选 */
  hiddenCurves: Set<string> | null;
  /** 联动中枢(§35):点击表格行 ↔ 点击图中峰 */
  selectedPeakIndex: number | null;
  /** 传 null 表示清空选择(数据集被删除时) */
  selectDataset: (sampleId: number | null, datasetId: number | null) => void;
  toggleCurve: (key: string) => void;
  setSelectedPeak: (index: number | null) => void;
}

export const useSelectionStore = create<SelectionState>((set) => ({
  sampleId: null,
  datasetId: null,
  hiddenCurves: null,
  selectedPeakIndex: null,
  selectDataset: (sampleId, datasetId) =>
    set({ sampleId, datasetId, selectedPeakIndex: null }),
  toggleCurve: (key) =>
    set((s) => {
      const cur = new Set(s.hiddenCurves ?? []);
      if (cur.has(key)) cur.delete(key);
      else cur.add(key);
      return { hiddenCurves: cur };
    }),
  setSelectedPeak: (selectedPeakIndex) => set({ selectedPeakIndex }),
}));

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
  setMode: (m: PlotState["mode"]) => void;
  setCrosshair: (v: boolean) => void;
  setShowLabels: (v: boolean) => void;
  setOffset: (v: number) => void;
  setCursor: (c: PlotState["cursor"]) => void;
  setXDomain: (d: PlotState["xDomain"]) => void;
  setZoom: (z: PlotState["zoom"]) => void;
  setShowBackground: (v: boolean) => void;
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
  setMode: (mode) => set({ mode }),
  setCrosshair: (crosshair) => set({ crosshair }),
  setShowLabels: (showLabels) => set({ showLabels }),
  setOffset: (offset) => set({ offset }),
  setCursor: (cursor) => set({ cursor }),
  setXDomain: (xDomain) => set({ xDomain }),
  setZoom: (zoom) => set({ zoom }),
  setShowBackground: (showBackground) => set({ showBackground }),
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
  status: "idle" | "previewing" | "committing";
  /** Undo/Redo 有界快照栈(§10.5),仅参数类操作 */
  undoStack: PipelineParams[];
  redoStack: PipelineParams[];
  loadedDatasetId: number | null;
  /** 峰是否与磁盘上的 peaks 表一致。patchPipeline 会把它置 true(参数变了,
   * 内存里的峰已经不是固化过的那批),peaks.detect 成功后置 false。
   * 没有这个标记,用户点完「检测峰」看不出到底有没有生效。 */
  peaksDirty: boolean;
  /** 滑块拖动节流:cancel 旧请求语义由序号丢弃实现 */
  _previewSeq: number;
  _commitTimer: number | null;

  loadDataset: (datasetId: number) => Promise<void>;
  /** 清空当前曲线与峰(数据集被删除时用) */
  clearDataset: () => void;
  patchPipeline: (patch: Partial<PipelineParams>) => void;
  /** peaks.detect 成功后调用:把固化后的峰写回并清 dirty */
  markPeaksCommitted: (peaks: PeakResult[]) => void;
  undo: () => void;
  redo: () => void;
}

const clone = (p: PipelineParams): PipelineParams => structuredClone(p);

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
  _previewSeq: 0,
  _commitTimer: null,

  loadDataset: async (datasetId) => {
    const r = await engineCommand<CurvePayload>("datasets.curve", {
      id: datasetId,
    });
    if (r.ok) {
      set({
        curve: r.data,
        loadedDatasetId: datasetId,
        peaks: [],
        peaksDirty: false,
        status: "idle",
      });
      // 新曲线的值域 + 清空缩放窗口。**不清 zoom 会残留上一条曲线的 2θ
      // 窗口**,切到值域不同的曲线时表现为一张空图。
      // 用首尾点而非 Math.min(...x):15k 点展开成参数会爆栈;曲线保证升序。
      const x = r.data.x;
      usePlotStore.getState().setXDomain(x.length ? [x[0], x[x.length - 1]] : null);
      usePlotStore.getState().setZoom(null);
      // 载入即做一次默认寻峰预览
      get().patchPipeline({});
    } else {
      set({ status: "idle" });
    }
  },

  clearDataset: () => {
    set({
      curve: null,
      peaks: [],
      loadedDatasetId: null,
      peaksDirty: false,
      status: "idle",
      smoothingAffectsMetrics: false,
      undoStack: [],
      redoStack: [],
    });
    usePlotStore.getState().setXDomain(null);
    usePlotStore.getState().setZoom(null);
  },

  markPeaksCommitted: (peaks) => set({ peaks, peaksDirty: false }),

  patchPipeline: (patch) => {
    const s = get();
    const next = { ...clone(s.pipeline), ...patch };
    set((st) => ({
      pipeline: next,
      status: "previewing",
      // 参数变了 → 内存里的峰已不是固化过的那批
      peaksDirty: true,
      undoStack: [...st.undoStack.slice(-49), st.pipeline],
      redoStack: [],
    }));

    // 300ms 无新输入 → committing(§10.3)
    if (s._commitTimer) window.clearTimeout(s._commitTimer);
    const seq = ++get()._previewSeq;

    // 引擎类参数:节流发送,乱序丢弃
    const dsId = s.loadedDatasetId;
    if (dsId != null) {
      window.setTimeout(async () => {
        if (seq !== get()._previewSeq) return; // cancel 语义
        const r = await engineCommand<PreviewResult>("analysis.preview", {
          dataset_id: dsId,
          pipeline: get().pipeline,
        });
        if (seq !== get()._previewSeq) return;
        if (r.ok) {
          set({
            curve: r.data.curve,
            peaks: r.data.peaks,
            smoothingAffectsMetrics: r.data.smoothingAffectsMetrics,
          });
        }
      }, 30);
    }

    set({
      _commitTimer: window.setTimeout(async () => {
        if (dsId != null) {
          set({ status: "committing" });
          await engineCommand("analysis.commit", {
            dataset_id: dsId,
            pipeline: get().pipeline,
          });
          set({ status: "idle" });
        }
      }, 300),
    });
  },

  undo: () => {
    const s = get();
    const prev = s.undoStack[s.undoStack.length - 1];
    if (!prev) return;
    set((st) => ({
      pipeline: prev,
      undoStack: st.undoStack.slice(0, -1),
      redoStack: [...st.redoStack, st.pipeline],
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
      }).then((r) => {
        if (seq !== get()._previewSeq) return;
        if (r.ok) {
          set({
            curve: r.data.curve,
            peaks: r.data.peaks,
            smoothingAffectsMetrics: r.data.smoothingAffectsMetrics,
          });
        }
      });
    }
  },

  redo: () => {
    const s = get();
    const nxt = s.redoStack[s.redoStack.length - 1];
    if (!nxt) return;
    set((st) => ({
      pipeline: nxt,
      redoStack: st.redoStack.slice(0, -1),
      undoStack: [...st.undoStack, st.pipeline],
    }));
    if (s.loadedDatasetId != null) {
      const seq = ++get()._previewSeq;
      void engineCommand<PreviewResult>("analysis.preview", {
        dataset_id: s.loadedDatasetId,
        pipeline: nxt,
      }).then((r) => {
        if (seq !== get()._previewSeq) return;
        if (r.ok) {
          set({
            curve: r.data.curve,
            peaks: r.data.peaks,
            smoothingAffectsMetrics: r.data.smoothingAffectsMetrics,
          });
        }
      });
    }
  },
}));

// ---------------- uiStore ----------------

interface UiState {
  importOpen: boolean;
  paletteOpen: boolean;
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
