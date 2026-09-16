/** Zustand stores(§10.2,每个 store 单一职责,避免 giant global store)。 */

import { create } from "zustand";
import type {
  CurvePayload,
  PeakResult,
  PreviewResult,
  PipelineParams,
  ProjectNode,
  SampleNode,
  ThemeMode,
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
  load: () => Promise<void>;
  setCurrentProject: (id: number) => void;
  createProject: (name: string, description?: string) => Promise<number | null>;
}

export const useProjectStore = create<ProjectState>((set, get) => ({
  projects: [],
  samples: [],
  currentProjectId: null,
  load: async () => {
    const [p, t] = await Promise.all([
      engineCommand<{ projects: ProjectNode[] }>("projects.list"),
      engineCommand<{ samples: SampleNode[] }>("library.tree"),
    ]);
    if (p.ok) {
      set({
        projects: p.data.projects,
        currentProjectId:
          get().currentProjectId ?? p.data.projects[0]?.id ?? null,
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
}));

// ---------------- selectionStore ----------------

interface SelectionState {
  sampleId: number | null;
  datasetId: number | null;
  /** "ds:<id>" 形式的曲线显隐集合;null 表示全选 */
  hiddenCurves: Set<string> | null;
  /** 联动中枢(§35):点击表格行 ↔ 点击图中峰 */
  selectedPeakIndex: number | null;
  selectDataset: (sampleId: number, datasetId: number) => void;
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
  setMode: (m: PlotState["mode"]) => void;
  setCrosshair: (v: boolean) => void;
  setShowLabels: (v: boolean) => void;
  setOffset: (v: number) => void;
  setCursor: (c: PlotState["cursor"]) => void;
}

export const usePlotStore = create<PlotState>((set) => ({
  mode: "overlay",
  crosshair: false,
  showLabels: true,
  offset: 0,
  cursor: null,
  setMode: (mode) => set({ mode }),
  setCrosshair: (crosshair) => set({ crosshair }),
  setShowLabels: (showLabels) => set({ showLabels }),
  setOffset: (offset) => set({ offset }),
  setCursor: (cursor) => set({ cursor }),
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
  /** 滑块拖动节流:cancel 旧请求语义由序号丢弃实现 */
  _previewSeq: number;
  _commitTimer: number | null;

  loadDataset: (datasetId: number) => Promise<void>;
  patchPipeline: (patch: Partial<PipelineParams>) => void;
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
        status: "idle",
      });
      // 载入即做一次默认寻峰预览
      get().patchPipeline({});
    } else {
      set({ status: "idle" });
    }
  },

  patchPipeline: (patch) => {
    const s = get();
    const next = { ...clone(s.pipeline), ...patch };
    set((st) => ({
      pipeline: next,
      status: "previewing",
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
      // Undo 复用 preview→commit 通路(§10.5),不走旁门
      void engineCommand<PreviewResult>("analysis.preview", {
        dataset_id: s.loadedDatasetId,
        pipeline: prev,
      }).then((r) => {
        if (r.ok) set({ curve: r.data.curve, peaks: r.data.peaks });
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
      void engineCommand<PreviewResult>("analysis.preview", {
        dataset_id: s.loadedDatasetId,
        pipeline: nxt,
      }).then((r) => {
        if (r.ok) set({ curve: r.data.curve, peaks: r.data.peaks });
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
  toast: { kind: "success" | "error"; text: string } | null;
  /** 拖放进来的文件路径,由 AppShell 写入、ImportSheet 消费 */
  pendingDropPaths: string[] | null;
  /** 上次导入归属的 sample(列映射往返时用) */
  lastSampleId: number | null;
  setImportOpen: (v: boolean) => void;
  setPaletteOpen: (v: boolean) => void;
  setSettingsOpen: (v: boolean) => void;
  setNewProjectOpen: (v: boolean) => void;
  setPendingDropPaths: (v: string[] | null) => void;
  setLastSampleId: (v: number | null) => void;
  showToast: (kind: "success" | "error", text: string) => void;
  dismissToast: () => void;
}

export const useUiStore = create<UiState>((set) => ({
  importOpen: false,
  paletteOpen: false,
  settingsOpen: false,
  newProjectOpen: false,
  toast: null,
  pendingDropPaths: null,
  lastSampleId: null,
  setImportOpen: (importOpen) => set({ importOpen }),
  setPaletteOpen: (paletteOpen) => set({ paletteOpen }),
  setSettingsOpen: (settingsOpen) => set({ settingsOpen }),
  setNewProjectOpen: (newProjectOpen) => set({ newProjectOpen }),
  setPendingDropPaths: (pendingDropPaths) => set({ pendingDropPaths }),
  setLastSampleId: (lastSampleId) => set({ lastSampleId }),
  showToast: (kind, text) => {
    set({ toast: { kind, text } });
    window.setTimeout(() => set({ toast: null }), 3200);
  },
  dismissToast: () => set({ toast: null }),
}));
