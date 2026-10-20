/** 引擎调用客户端(§10.4)。
 *
 * 生产路径:invoke("engine_command", {cmd, payload}) → Rust → sidecar。
 * 开发兜底:纯 vite dev(非 Tauri 窗口)时使用确定性 mock 曲线,
 * 让 Milestone 1 的 UI 不依赖 sidecar 也能完整驱动。
 */

import type {
  ScanResult,
  TrendResult,
  AnnotationContext,
  CurvePayload,
  EngineResponse,
  HklCandidate,
  ImportResult,
  MetaFilePathResult,
  PeakResult,
  PreviewResult,
  HighNiResult,
  MaterialProfile,
  ProjectNode,
  RestoreResult,
  SampleNode,
  SoftDeleteResult,
  TrashItem,
  TrashKind,
  TrashListResult,
  EcAttributionResult,
  EcAttributionState,
  EcCellOverview,
  EcCellSummary,
  EcCurvePayload,
  EcDqdvComputeResult,
  EcDqdvParams,
  EcDqdvResult,
  EcDqdvState,
  EcExportResult,
  EcImportResult,
  EcPreviewResult,
  EcTrackRow,
  EcWaterfallPayload,
} from "../types/engine";

/**
 * 单次显示最多传给前端的点数。引擎仍用全量数据计算峰和指标，
 * 这里只限制主图与范围概览的显示负载。
 */
export const DISPLAY_MAX_POINTS = 5000;

/** 对比曲线的配色。**刻意避开 --accent 与 --plot-line** —— 那两条是当前
 *  分析对象的颜色,参考曲线用了会让用户分不清哪条才是正在分析的数据。 */
export const COMPARE_COLORS = [
  "#e8833a", "#3aa76d", "#a855c7", "#c74b6a", "#4a90d9", "#8a8f3a",
];

const isTauri = "__TAURI_INTERNALS__" in window;

/** 拆 Rust 返回的字节包,并把曲线信封还原成 Float32Array。
 *
 *  **不做静默降级。** 长度对不上就抛错 —— 截断的曲线画出来是"谱图少了
 *  后半段",用户完全看不出是传输出了问题,只会以为数据本身有问题。
 */
function unwrapResponse<T>(raw: ArrayBuffer | number[]): EngineResponse<T> {
  const buf = raw instanceof ArrayBuffer ? new Uint8Array(raw) : Uint8Array.from(raw);
  if (buf.length < 4) throw new Error("引擎响应帧过短");
  const jsonLen =
    (buf[0] << 24) | (buf[1] << 16) | (buf[2] << 8) | buf[3];
  if (jsonLen < 0 || 4 + jsonLen > buf.length) throw new Error("引擎响应帧长度不符");
  const head = JSON.parse(
    new TextDecoder().decode(buf.subarray(4, 4 + jsonLen)),
  ) as EngineResponse<T>;
  const body = buf.subarray(4 + jsonLen);
  if (head.ok && head.data) attachBinary(head.data, body);
  return head;
}

/** 把二进制体按 __bin__.layout 切成 Float32Array,挂回 data 上。
 *
 *  **递归**:`datasets.curve` 的信封在 `data` 顶层,而
 *  `analysis.preview` 在 `data.curve` 里。只查顶层的话,preview 的曲线
 *  永远解不出来 —— 而且不会报错,只是图上没有曲线(所有点都是
 *  undefined),极难排查。
 *
 *  layout 是有序的:第 k 个字段对应第 k 段连续 f32[n]。切片顺序错了
 *  x 会读到 y 的值 —— 图能画出来,但完全是错的,所以这里严格按
 *  layout 顺序走,并校验总长度。 */
function attachBinary(data: unknown, body: Uint8Array): void {
  if (typeof data !== "object" || data === null) return;
  const holder = data as Record<string, unknown>;
  const meta = holder.__bin__ as
    | { bytes: number; n: number; layout: string[] }
    | undefined;
  if (!meta || !meta.layout || meta.n <= 0) {
    // 本层没有信封 —— 往下一层找(preview 的曲线嵌在 data.curve 里)
    for (const value of Object.values(holder)) {
      if (typeof value === "object" && value !== null) {
        attachBinary(value, body);
      }
    }
    return;
  }
  const expected = meta.n * meta.layout.length * 4;
  if (body.length !== expected) {
    throw new Error(
      `曲线数据长度不符:期望 ${expected} 字节,实际 ${body.length}`,
    );
  }
  const stride = meta.n * 4;
  meta.layout.forEach((name, i) => {
    // 必须 slice 而不是 subarray:subarray 与整个 body 共享底层
    // ArrayBuffer,而 body 之后可能还被别处引用;slice 出一份独立副本,
    // 生命周期清晰,且长度正好是 4n 字节(满足 Float32Array 的对齐要求)。
    const chunk = body.slice(i * stride, (i + 1) * stride);
    holder[name] = new Float32Array(chunk.buffer);
  });
  delete holder.__bin__;
}

export async function engineCommand<T>(
  cmd: string,
  payload: Record<string, unknown> = {},
): Promise<EngineResponse<T>> {
  if (isTauri) {
    const { invoke } = await import("@tauri-apps/api/core");
    // Rust 侧返回的是原始字节,约定与引擎帧一致(§9.1):
    //   [u32 BE jsonLen][json header][binary body]
    // 用 ipc::Response 而不是 Value 是为了让曲线二进制体走原始字节通道;
    // 若走 Value,serde 会把它转成 JSON 数组(每个字节一个数字),比
    // 原来的 number[] 还慢。
    const raw = await invoke<ArrayBuffer | number[]>("engine_command", {
      cmd,
      payload: JSON.stringify(payload),
    });
    return unwrapResponse<T>(raw);
  }
  return mockCommand(cmd, payload) as Promise<EngineResponse<T>>;
}


// ============================================================
// 开发期 mock —— 数学形状与引擎一致(§4.3 LFP 示例谱)
// ============================================================

function fakeLfpCurve(offset = 0, scale = 1): CurvePayload {
  const peaks: [number, number, number][] = [
    [17.15, 3154, 0.16], [20.8, 6939, 0.17], [25.58, 11040, 0.17],
    [29.72, 17350, 0.18], [32.21, 13248, 0.18], [34.39, 31542, 0.18],
    [35.62, 19241, 0.18], [36.85, 6308, 0.19], [37.9, 4731, 0.19],
    [39.22, 4100, 0.19], [42.05, 2839, 0.2], [45.6, 2208, 0.2],
    [47.8, 1577, 0.21], [52.55, 5047, 0.21], [55.9, 1892, 0.22],
    [58.2, 3785, 0.22], [61.4, 1577, 0.23], [63.9, 1262, 0.23],
    [66.3, 946, 0.24], [70.1, 1262, 0.24], [74.5, 946, 0.25],
  ];
  const x: number[] = [];
  const y: number[] = [];
  // 确定性伪随机,模拟计数噪声
  let seedState = offset + 1;
  const rand = () => {
    seedState = (seedState * 1103515245 + 12345) & 0x7fffffff;
    return seedState / 0x7fffffff - 0.5;
  };
  for (let i = 0; i < 3501; i++) {
    const xi = 10 + i * 0.02;
    x.push(xi);
    let v = 120 + 850 * Math.exp(-(xi - 10) / 12);
    for (const [c, a, w] of peaks) {
      v += a * scale * Math.exp(-((xi - c) ** 2) / (2 * (w / 2.355) ** 2));
    }
    y.push(Math.max(0, Math.round(v + rand() * 80)));
  }
  // mock 也必须返回 Float32Array —— 形状与真实引擎不一致的话,
  // 浏览器开发模式会验证到一个现实里不存在的行为。
  return {
    x: Float32Array.from(x),
    y: Float32Array.from(y),
    xAxisType: "two_theta",
    wavelength: 1.5418,
    pointCount: 3501,
    sourcePointCount: 3501,
    downsampled: false,
  };
}

/** 模拟 AsLS 背景:一条随角度缓慢衰减的基线。
 *
 * mock 必须在这里返回背景曲线 —— 真实引擎只在 background.enabled 时返回
 * 它,前端「显示背景」开关依赖这个数组是否存在。mock 不返回的话,这个开关
 * 在纯浏览器下永远无法验证(点了只会弹「请先启用背景扣除」)。 */
function fakeBackground(curve: CurvePayload): Float32Array {
  const { x, y } = curve;
  const minY = Math.min(...y);
  const out = new Float32Array(x.length);
  for (let i = 0; i < x.length; i++) {
    const decay = 900 * Math.exp(-(x[i] - 10) / 14);
    out[i] = Math.max(0, Math.min(y[i] - 1, minY + decay));
  }
  return out;
}

function fakePeaks(curve: CurvePayload, sensitivity: string): PeakResult[] {  const { x, y } = curve;
  const range = Math.max(...y) - Math.min(...y);
  const factor = sensitivity === "high" ? 0.015 : sensitivity === "low" ? 0.15 : 0.05;
  const threshold = range * factor;
  const minDist = 5; // 0.1° / 0.02 步长
  const peaks: PeakResult[] = [];
  let lastIdx = -minDist;
  for (let i = 1; i < x.length - 1; i++) {
    if (y[i] > y[i - 1] && y[i] >= y[i + 1] && y[i] - Math.min(y[0], 200) > threshold && i - lastIdx >= minDist) {
      const half = y[i] / 2;
      let li = i; while (li > 0 && y[li] > half) li--;
      let ri = i; while (ri < y.length - 1 && y[ri] > half) ri++;
      const fwhm = x[ri] - x[li];
      peaks.push({
        index: peaks.length,
        position: x[i],
        intensity: y[i],
        fwhm,
        area: (y[i] - 200) * fwhm * 1.06,
        left_bound: x[li],
        right_bound: x[ri],
        manual_override: false,
        prominence: y[i] - Math.min(y[0], 200),
      });
      lastIdx = i;
    }
  }
  return peaks;
}

const SAMPLES = ["LFP-500C", "LFP-450C", "LFP-400C", "NCM811-03"];
/** samples.create 的自增 id(模拟 DB 主键)。 */
let mockSampleSeq = 100;

/** mock 的 R-3m 允许反射表(消光 -h+k+l ≡ 0 mod 3)。
 *
 * 双峰第二个分量对外用**文献名 (102)/(108)** —— 与引擎
 * `indexing._LITERATURE_HKL` 一致(同一物理反射,d 相同)。位置按
 * a=2.878 / c=14.240 / Cu Kα1 反算,与引擎口径一致。 */
const MOCK_REFLECTIONS: { label: string; t2: number }[] = [
  { label: "(003)", t2: 18.703 }, { label: "(101)", t2: 36.639 },
  { label: "(006)", t2: 37.951 }, { label: "(102)", t2: 38.310 },
  { label: "(104)", t2: 44.371 }, { label: "(015)", t2: 48.560 },
  { label: "(107)", t2: 58.549 }, { label: "(108)", t2: 64.250 },
  { label: "(110)", t2: 64.831 }, { label: "(113)", t2: 68.079 },
  { label: "(116)", t2: 77.615 }, { label: "(202)", t2: 77.844 },
  { label: "(024)", t2: 81.750 },
];

function nearestAllowed(position: number): { label: string; t2: number } | null {
  let best: { label: string; t2: number } | null = null;
  let bestD = 0.15;
  for (const ref of MOCK_REFLECTIONS) {
    const d = Math.abs(position - ref.t2);
    if (d <= bestD) {
      bestD = d;
      best = ref;
    }
  }
  return best;
}

/** mock 晶面归属:与引擎同为三态 reliable/ambiguous/none。
 *
 * 双峰物理上未分开时必须报 ambiguous 并给候选 —— 只给 nearest 的两态
 * 会让浏览器开发模式验证不到「未分开」的斜体/候选列表 UI。
 * 判据:两个允许反射都在邻域内且间距 < 0.45°(如 (006)/(102) 间距 0.36°)。 */
function annotateMock(position: number): {
  hkl: string | null;
  hkl_confidence: "reliable" | "ambiguous" | "none";
  dominance: number | null;
  hkl_candidates: HklCandidate[];
} {
  const within = MOCK_REFLECTIONS.filter((r) => Math.abs(position - r.t2) <= 0.22).sort(
    (a, b) => Math.abs(position - a.t2) - Math.abs(position - b.t2),
  );
  const cand = (r: { label: string; t2: number }, dom: number | null): HklCandidate => ({
    hkl: null,
    label: r.label,
    position: r.t2,
    dominance: dom,
  });
  if (within.length >= 2 && within[1].t2 - within[0].t2 < 0.45) {
    const share = 0.5;
    return {
      hkl: null,
      hkl_confidence: "ambiguous",
      dominance: share,
      hkl_candidates: within.slice(0, 2).map((r) => cand(r, share)),
    };
  }
  const best = nearestAllowed(position);
  return {
    hkl: best?.label ?? null,
    hkl_confidence: best ? "reliable" : "none",
    dominance: best ? 1 : null,
    hkl_candidates: best ? [cand(best, 1)] : [],
  };
}

const MOCK_ANNOTATION = {
  phase: "R-3m 层状",
  a: 2.878,
  c: 14.24,
  zeroShift: 0,
  fwhm0: 0.12,
  fwhmSlope: 0.003,
  rwp: 0.09,
};

/** mock 的 pipeline 指纹(与引擎 sha1 不同值,但同一 mock 内自洽即可 ——
 *  前端只比较 annotation.pipelineHash 与 preview/state 回传的 hash 是否相等)。 */
function hashPipeline(p: unknown): string {
  const s = JSON.stringify(p ?? {});
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return (h >>> 0).toString(16).padStart(8, "0");
}

/** 模拟 analyses.annotation_json:按 dataset 存标注快照(context+labels+hash)。
 *  与引擎同语义:打开图读回、commit 不删、annotate 覆盖。 */
const mockAnnotations = new Map<number, AnnotationContext>();
/** 模拟 peaks 表的固化状态:detect 写入、pipeline 变化的 commit 删除。
 *  与引擎一致 —— 只改 mockPipeline 不清这个的话,浏览器模式下改参数→
 *  commit→重开仍报 peaksStored:true,验证到真机不存在的假行为。 */
const mockStoredPeaks = new Set<number>();
/** mock 当前分析的 pipeline(preview/commit 时更新,annotate 时取指纹)。 */
let mockPipeline: Record<string, unknown> = {};
/** 预设列表(按项目过滤),与 analysis_presets 表同构。 */
const mockPresets: {
  id: number;
  project_id: number;
  name: string;
  pipeline: Record<string, unknown>;
}[] = [];
let mockPresetSeq = 1;

/** 把 labels 快照按位置合回峰(引擎 _merge_annotation_labels 的同款逻辑)。 */
function mergeMockLabels(peaks: PeakResult[], ann: AnnotationContext | undefined): PeakResult[] {
  const labels = ann?.labels ?? [];
  if (!labels.length) return peaks;
  const step = peaks.length > 1
    ? Math.abs(peaks[1].position - peaks[0].position)
    : 0.02;
  const tol = Math.max(1.5 * step, 0.02);
  return peaks.map((p) => {
    let best: (typeof labels)[number] | null = null;
    let bestD = Infinity;
    for (const lb of labels) {
      const d = Math.abs(lb.position - p.position);
      if (d < bestD) { bestD = d; best = lb; }
    }
    if (!best || bestD > tol) return p;
    return {
      ...p,
      hkl: best.hkl ?? null,
      hkl_confidence: (best.hklConfidence as PeakResult["hkl_confidence"]) ?? null,
      dominance: best.dominance ?? null,
      hkl_candidates: best.candidates ?? null,
    };
  });
}

let mockCurveCache: CurvePayload | null = null;

/** 开发期 mock 的回收站。让「删除 → 出现在回收站 → 恢复 → 消失」这条链
 * 在纯浏览器 `npm run dev` 下也能完整走通,否则回收站 UI 没法验证。 */
const mockTrash: TrashItem[] = [];
/** 上次 domain.high_ni.analyze 的结果,按 dataset_id 存 —— 供
 *  domain.high_ni.get 读回,行为与引擎的 domain_results 表一致。 */
const mockDomainCache = new Map<number, HighNiResult>();
const mockRenamed = new Map<string, string>();

// ---------- 电化学 mock 状态(与 ec_* 表同语义) ----------
/** mock 电池:挂在 sample 1 下,id 固定 1。形状与引擎严格一致。 */
const MOCK_EC_CELL: EcCellSummary = {
  id: 1,
  name: "LAND-0.2C循环示例",
  filename: "1 0.2 循环170214_005_7.xlsx",
  massMg: 1.77,
  format: "xlsx",
  cycleCount: 0, // 由 mockEcCycles() 推导
  lastCycle: null,
  importedAt: "2026-10-06 10:00:00",
};
/** 当前(或上次)dqdv 计算的参数指纹;null = 从未计算。 */
let mockEcDqdvHash: string | null = null;
/** 上次计算的入参快照(ec.dqdv_result 读回用,与引擎 params_json 同语义)。 */
let mockEcDqdvParams: EcDqdvParams = {};
/** 上次归因所依据的 dqdv 指纹(引擎存 sourceDqdvHash,stale 由此判定)。 */
let mockEcAttributionSource: string | null = null;

/** mock 圈序列:确定性生成,不随机 —— mock 的价值在于形状稳定可断言。 */
function mockEcCycles() {
  const cycles: {
    cycle: number; qCharge: number; qDischarge: number; ce: number;
    iMedCharge: number; vMin: number; vMax: number;
    rateGroup: "main" | "slow" | "fast"; incomplete: boolean;
  }[] = [];
  const n = 120;
  for (let i = 1; i <= n; i++) {
    const fade = 1 - 0.35 * (i / n) ** 1.2;            // 140 → 91 mAh/g
    const qd = Math.round(140 * fade * 1000) / 1000;
    const qc = Math.round(qd / 0.985 * 1000) / 1000;
    cycles.push({
      cycle: i,
      qCharge: qc,
      qDischarge: qd,
      ce: 0.985,
      iMedCharge: i <= 3 ? 0.032 : 0.068,
      vMin: 2.7,
      vMax: 4.3,
      rateGroup: i <= 3 ? "slow" : "main",
      incomplete: false,
    });
  }
  return cycles;
}

/** mock 单圈 dQ/dV 曲线(充电方向模板,放电镜像 + 峰位下移)。
 *  形状与引擎 ec.dqdv_curve 严格一致:Float32Array + 二进制信封已由
 *  engineCommand 之上的路径处理,mock 只需构造同样的载荷对象。 */
function mockEcCurve(cycle: number, direction: "charge" | "discharge") {
  const dv = 0.002;
  const v: number[] = [];
  const y: number[] = [];
  const fade = 1 - 0.3 * (cycle / 120);
  const shift = direction === "discharge" ? 0.0003 * cycle : 0;
  for (let x = 2.9; x <= 4.32 + 1e-9; x += dv) {
    const g = (c: number, h: number, s: number) =>
      h * Math.exp(-0.5 * ((x - (c - shift)) / s) ** 2);
    v.push(Math.round(x * 1000) / 1000);
    y.push(
      g(3.67, 320 * fade, 0.055) + g(4.18, 160 * fade, 0.045) + 8,
    );
  }
  return {
    cycle,
    direction,
    pointCount: v.length,
    paramsHash: mockEcDqdvHash ?? "mock",
    v: Float32Array.from(v),
    dqdv: Float32Array.from(y),
  } satisfies EcCurvePayload;
}

/** mock 峰/轨迹:每圈每方向 H1H2 一个非截断峰;充电 H2H3 末端圈截断。 */
function mockEcPeaksAndTracks() {
  const cycles = mockEcCycles();
  const peaks: {
    cycle: number; direction: "charge" | "discharge"; window: string;
    position: number; height: number; area: number; fwhm: number;
    prominence: number; onset: number | null; truncated: boolean;
    trackId: number | null;
  }[] = [];
  for (const c of cycles) {
    const fade = 1 - 0.3 * (c.cycle / 120);
    const h = 320 * fade;
    peaks.push({
      cycle: c.cycle, direction: "charge", window: "H1H2",
      position: 3.67, height: h, area: h * 0.055 * 2.5 * 0.64,
      fwhm: 0.13, prominence: h, onset: 3.55, truncated: false, trackId: 1,
    });
    peaks.push({
      cycle: c.cycle, direction: "discharge", window: "H1H2",
      position: 3.67 - 0.0003 * c.cycle, height: h, area: h * 0.055 * 2.5 * 0.64,
      fwhm: 0.13, prominence: h, onset: 3.55 - 0.0003 * c.cycle,
      truncated: false, trackId: 2,
    });
    if (c.cycle > 60) {
      // 充电 H2→H3 贴截止线被截断(方法学演示:截断峰不算面积)
      peaks.push({
        cycle: c.cycle, direction: "charge", window: "H2H3",
        position: 4.28 + 0.0002 * (c.cycle - 60), height: 90 * fade,
        area: 0, fwhm: 0.0, prominence: 90 * fade, onset: 4.1,
        truncated: true, trackId: null,
      });
    }
  }
  const tracks: EcTrackRow[] = [
    { trackId: 1, direction: "charge", window: "H1H2", label: "H1→H2",
      firstCycle: 1, lastCycle: 120, nPeaks: 120, continuity: 1 },
    { trackId: 2, direction: "discharge", window: "H1H2", label: "H1→H2",
      firstCycle: 1, lastCycle: 120, nPeaks: 120, continuity: 1 },
  ];
  return { peaks, tracks };
}
const mockProfiles: MaterialProfile[] = [
  {
    id: 1,
    project_id: 1,
    name: "NCM811 / 高镍层状默认档案",
    family: "high_ni_layered",
    formula: "LiNi0.8Co0.1Mn0.1O2",
    ni_fraction: 0.8,
    li_tm_ratio: 1,
    dopants: {},
    wavelength: 1.5418,
    instrument_name: "",
    reference_dataset_id: null,
    thresholds: { i003_i104_area_low: 1.2, i003_i104_area_good: 1.3 },
  },
];

function mockKey(table: string, id: number): string {
  return `${table}:${id}`;
}

function isTrashed(table: string, id: number): boolean {
  return mockTrash.some((x) => x.kind === table && x.id === id);
}

async function mockCommand(
  cmd: string,
  payload: Record<string, unknown>,
): Promise<EngineResponse<unknown>> {
  await new Promise((r) => setTimeout(r, 15)); // 模拟引擎往返
  switch (cmd) {
    case "datasets.curve": {
      const dsId = Number(payload.id ?? 1);
      mockCurveCache = fakeLfpCurve(dsId);
      return ok(mockCurveCache);
    }
    case "analysis.preview": {
      if (!mockCurveCache) mockCurveCache = fakeLfpCurve();
      const pipeline = (payload.pipeline ?? {}) as {
        peaks?: { sensitivity?: string };
        smoothing?: { enabled?: boolean };
        background?: { enabled?: boolean };
      };
      mockPipeline = pipeline as Record<string, unknown>;
      const doAnnotate = payload.annotate !== false;
      const restore = payload.restoreAnnotation === true;
      const dsId = Number(payload.dataset_id ?? 1);
      let peaks = fakePeaks(
        mockCurveCache,
        pipeline.peaks?.sensitivity ?? "medium",
      );
      // 与引擎一致:compute_peak_metrics=false 时只给位置/强度,
      // FWHM/Area/边界为 null(拖动预览跳过度量)。
      if (payload.compute_peak_metrics === false) {
        peaks = peaks.map((p) => ({
          ...p,
          fwhm: null,
          area: null,
          left_bound: null,
          right_bound: null,
        }));
      }
      // 与引擎一致:annotate=false 时不带晶面;restoreAnnotation=true 时
      // 把 annotation_json 的 labels 快照按位置合回(不重算)。
      if (doAnnotate) {
        for (const p of peaks) {
          const a = annotateMock(p.position);
          p.hkl = a.hkl;
          p.hkl_confidence = a.hkl_confidence;
          p.dominance = a.dominance;
          p.hkl_candidates = a.hkl_candidates;
        }
      } else if (restore) {
        peaks = mergeMockLabels(peaks, mockAnnotations.get(dsId));
      }
      const bgOn = !!pipeline.background?.enabled;
      // 与引擎一致:只在 background.enabled 时返回 background 数组
      const curve: CurvePayload = bgOn
        ? { ...mockCurveCache, background: fakeBackground(mockCurveCache) }
        : mockCurveCache;
      return ok({
        curve,
        peaks,
        smoothingAffectsMetrics: !!pipeline.smoothing?.enabled,
        warnings: [],
        annotation: doAnnotate ? MOCK_ANNOTATION : null,
        pipelineHash: hashPipeline(pipeline),
      } satisfies PreviewResult);
    }
    case "analysis.state": {
      const dsId = Number(payload.dataset_id ?? 1);
      if (!mockCurveCache) mockCurveCache = fakeLfpCurve(dsId);
      const stored = mockAnnotations.get(dsId);
      let peaks = fakePeaks(mockCurveCache, "medium");
      if (stored) peaks = mergeMockLabels(peaks, stored);
      return ok({
        analysisId: 1,
        pipeline: {},
        curve: mockCurveCache,
        peaks,
        peaksStored: mockStoredPeaks.has(dsId),
        smoothingAffectsMetrics: false,
        warnings: [],
        annotation: stored ?? null,
        pipelineHash: hashPipeline({}),
      });
    }
    case "peaks.detect": {
      if (!mockCurveCache) mockCurveCache = fakeLfpCurve();
      const dsId = Number(payload.dataset_id ?? 1);
      const peaks = fakePeaks(mockCurveCache, "medium");
      for (const p of peaks) {
        const a = annotateMock(p.position);
        p.hkl = a.hkl;
        p.hkl_confidence = a.hkl_confidence;
        p.dominance = a.dominance;
        p.hkl_candidates = a.hkl_candidates;
      }
      const labels = peaks.map((p) => ({
        position: p.position,
        hkl: p.hkl ?? null,
        hklConfidence: p.hkl_confidence ?? null,
        dominance: p.dominance ?? null,
        candidates: (p.hkl_candidates ?? []) as HklCandidate[],
      }));
      mockAnnotations.set(dsId, {
        ...MOCK_ANNOTATION,
        pipelineHash: hashPipeline(mockPipeline),
        annotatedAt: new Date().toISOString(),
        labels,
      });
      mockStoredPeaks.add(dsId);
      return ok({
        peaks,
        annotation: mockAnnotations.get(dsId),
      });
    }
    case "peaks.annotate": {
      if (!mockCurveCache) mockCurveCache = fakeLfpCurve();
      const dsId = Number(payload.dataset_id ?? 1);
      const peaks = fakePeaks(mockCurveCache, "medium");
      // 与引擎的 annotate_high_ni 同口径:按 R-3m 允许反射表归属(三态)。
      // 归属依据也要给:引擎返回 dominance / candidates,前端峰表 tooltip
      // 靠它显示"该反射贡献 X%"。mock 缺这两个字段会让浏览器开发模式
      // 下 tooltip 恒显示 0%,与真实引擎行为不符。
      const annotated = peaks.map((p) => {
        const a = annotateMock(p.position);
        return {
          index: p.index,
          position: p.position,
          hkl_label: a.hkl,
          hkl_confidence: a.hkl_confidence,
          dominance: a.dominance,
          candidates: a.hkl_candidates,
        };
      });
      // 与引擎一致:annotate = 权威刷新,覆盖 annotation_json 快照。
      const saved: AnnotationContext = {
        ...MOCK_ANNOTATION,
        pipelineHash: hashPipeline(mockPipeline),
        annotatedAt: new Date().toISOString(),
        labels: annotated.map((a) => ({
          position: a.position,
          hkl: a.hkl_label ?? null,
          hklConfidence: a.hkl_confidence ?? null,
          dominance: a.dominance ?? null,
          candidates: a.candidates ?? [],
        })),
      };
      mockAnnotations.set(dsId, saved);
      return ok({
        annotated,
        count: annotated.filter((a) => a.hkl_label).length,
        updated: annotated.length,
        annotation: saved,
      });
    }
    case "analysis.commit": {
      const next = (payload.pipeline ?? {}) as Record<string, unknown>;
      // 与引擎一致:参数变化的 commit 删 peaks 行(annotation 保留)
      if (hashPipeline(next) !== hashPipeline(mockPipeline)) {
        mockStoredPeaks.delete(Number(payload.dataset_id ?? 1));
      }
      mockPipeline = next;
      return ok({ analysisId: 1 });
    }
    case "profiles.list":
      return ok({
        profiles: mockProfiles.filter(
          (profile) => profile.project_id === Number(payload.project_id ?? 1),
        ),
      });
    case "profiles.create": {
      const profile: MaterialProfile = {
        id: mockProfiles.length + 1,
        project_id: Number(payload.project_id ?? 1),
        name: String(payload.name ?? "高镍层状默认档案"),
        family: "high_ni_layered",
        formula: String(payload.formula ?? "LiNi0.90Mn0.05Al0.05O2"),
        ni_fraction: Number(payload.ni_fraction ?? 0.9),
        li_tm_ratio: Number(payload.li_tm_ratio ?? 1),
        dopants: {},
        wavelength: Number(payload.wavelength ?? 1.5418),
        instrument_name: "",
        reference_dataset_id: null,
        thresholds: { i003_i104_area_low: 1.2, i003_i104_area_good: 1.3 },
      };
      mockProfiles.push(profile);
      return ok({ profileId: profile.id });
    }
    case "domain.high_ni.get": {
      // 与引擎同语义:没算过返回 found:false,算过就原样读回。
      const key = Number(payload.dataset_id ?? 1);
      const cached = mockDomainCache.get(key);
      return ok({
        found: cached != null,
        result: cached ?? null,
        stale: false,
      });
    }

    case "domain.high_ni.analyze": {
      const profileId = Number(payload.profile_id ?? 1);
      const result: HighNiResult = {
        domain: "high_ni_layered",
        analysisVersion: "high-ni-0.1",
        analysisId: 1,
        datasetId: Number(payload.dataset_id ?? 1),
        profileId,
        sourceHash: "mock-source-hash",
        confidence: "low",
        warnings: ["浏览器开发模式使用模拟结果,正式结果由 Python 引擎计算"],
        mixing: {
          i003_i104_area_ratio: 1.28,
          i003_i104_height_ratio: 1.19,
          r_order: 0.44,
          risk: "medium",
          referenceMode: "literature_hint",
          confidence: "low",
          evidence: ["003/104 面积比", "006/102 双峰结果"],
          warnings: ["经验阈值仅用于筛查,不是 Li/Ni 占位率标定"],
          peaks_area_bounds: {
            "003": [18.4, 19.0],
            "104": [44.1, 44.8],
            "101": [36.4, 37.0],
          },
        },
        layered: {
          layeredOrder: "acceptable",
          split006_102: {
            peaks: {}, peakSeparation: 0.62, valleyRatio: 0.71,
            singleFitError: 10, doubleFitError: 5, fitImprovement: 0.5,
            splitStatus: "clear", fit: true,
          },
          split018_110: {
            peaks: {}, peakSeparation: 0.44, valleyRatio: 0.89,
            singleFitError: 10, doubleFitError: 8, fitImprovement: 0.2,
            splitStatus: "weak", fit: true,
          },
          evidence: ["003/101/104 主峰顺序可识别", "006/102 clear", "108/110 weak"],
          confidence: "medium",
          warnings: ["R-3m 结论是粉末 XRD 层状特征筛查"],
        },
        lattice: {
          a: 2.86, c: 14.25, c_over_a: 4.98, cell_volume: 100.8,
          fit_rms: 0.0004, zero_shift: 0, uncertainty_a: 0.01,
          uncertainty_c: 0.03, used_peaks: ["003", "101", "104"],
          confidence: "low",
          warnings: ["零点校正尚未拟合"],
        },
      };
      mockDomainCache.set(Number(payload.dataset_id ?? 1), result);
      return ok(result);
    }
    case "projects.list": {
      const projects: ProjectNode[] = [
        { id: 1, name: mockRenamed.get("projects:1") ?? "LFP Study", description: "" },
        { id: 2, name: mockRenamed.get("projects:2") ?? "NCM Study", description: "" },
      ];
      return ok({
        projects: projects.filter((p) => !isTrashed("projects", p.id)),
      });
    }
    case "projects.create":
      return ok({ projectId: 2 });
    case "library.tree": {
      const samples: SampleNode[] = SAMPLES.map((name, i) => ({
        id: i + 1,
        project_id: 1,
        name: mockRenamed.get(`samples:${i + 1}`) ?? name,
        temperature: name.startsWith("LFP") ? name.slice(4, 7) : "",
        duration: "2 h",
        material: name.startsWith("LFP") ? "LiFePO4" : "NCM811",
        batch: "",
        wavelength: 1.5418,
        notes: "",
        dataset_count: 1,
        datasets: [
          {
            id: i + 1,
            name: mockRenamed.get(`datasets:${i + 1}`) ?? "scan-01.xy",
            batch_id: null,
            point_count: 3501,
          },
        ],
        batches: [],
      }));
      // 删项目 → 里面样品一并不可见(隐式级联);删样品同理
      const visible = isTrashed("projects", 1)
        ? []
        : samples
            .filter((s) => !isTrashed("samples", s.id))
            .map((s) => ({
              ...s,
              datasets: s.datasets.filter((d) => !isTrashed("datasets", d.id)),
            }));
      return ok({ samples: visible } satisfies { samples: SampleNode[] });
    }
    case "trend.series": {
      // mock:两个点,其中一个"尚未分析" —— 浏览器开发模式下也要能看到
      // 空心点的画法,否则这条分支的真实行为无从验证。
      const points = [
        { datasetId: 1, datasetName: "scan-01.xy", sampleName: "LFP-500C",
          xValue: 500, xLabel: "500", value: 2.8781, source: "refine_cell" },
        { datasetId: 2, datasetName: "scan-02.xy", sampleName: "LFP-550C",
          xValue: 550, xLabel: "550", value: 2.8794, source: "refine_cell" },
        { datasetId: 3, datasetName: "scan-03.xy", sampleName: "LFP-600C",
          xValue: 600, xLabel: "600", value: null, source: null },
      ];
      return ok({
        xAxis: String(payload.x_axis ?? "temperature"),
        metric: String(payload.metric ?? "a"),
        points,
        availableMetrics: ["a", "c", "c_over_a", "cell_volume", "i003_i104", "fwhm_003", "rwp"],
        availableAxes: ["temperature", "batch", "index"],
      } satisfies TrendResult);
    }
    case "datasets.scan_folder": {
      // mock 里没有真实文件系统,给一组固定的示例分组。
      const folder = String(payload.folder ?? "C:/data");
      return ok({
        folder,
        files: ["GENBB-1-14.txt", "GENBB-1-15.txt", "GENBB-2-06.txt"],
        groups: [
          { name: "GENBB-1", paths: [`${folder}/GENBB-1-14.txt`, `${folder}/GENBB-1-15.txt`] },
          { name: "GENBB-2", paths: [`${folder}/GENBB-2-06.txt`] },
        ],
      } satisfies ScanResult);
    }
    case "datasets.import": {
      const first = Array.isArray(payload.paths) ? String(payload.paths[0]) : "file.xy";
      return ok({
        imported: [{ id: 99, name: first.split(/[\\/]/).pop() ?? first }],
        duplicates: [],
        needColumns: [],
        failures: [],
        sampleId: Number(payload.sample_id ?? 1),
      } satisfies ImportResult);
    }
    // 预设按项目存内存,浏览器开发模式下保存/列出/应用可完整走通。
    // apply 把 mockPipeline 换成预设内容 —— 与真引擎"更新 analyses 后
    // loadDataset 读回"的可见效果一致。
    case "presets.save": {
      const name = String(payload.name ?? "").trim();
      if (!name) return fail("预设名称不能为空");
      const preset: (typeof mockPresets)[number] = {
        id: mockPresetSeq++,
        project_id: Number(payload.project_id ?? 1),
        name,
        pipeline: (payload.pipeline ?? {}) as Record<string, unknown>,
      };
      mockPresets.push(preset);
      return ok({ presetId: preset.id });
    }
    case "presets.list": {
      const pid = Number(payload.project_id ?? 1);
      return ok({
        presets: mockPresets
          .filter((x) => x.project_id === pid)
          .map(({ id, name, pipeline }) => ({ id, name, pipeline })),
      });
    }
    case "presets.apply": {
      const preset = mockPresets.find(
        (x) => x.id === Number(payload.preset_id),
      );
      if (!preset) return fail("Preset 不存在");
      mockPipeline = { ...preset.pipeline };
      const ids = (payload.dataset_ids as unknown[]) ?? [];
      return ok({ applied: ids.length });
    }
    case "meta.rename": {
      const name = String(payload.name ?? "").trim();
      if (!name) return fail("名称不能为空");
      mockRenamed.set(mockKey(String(payload.table), Number(payload.id)), name);
      return ok({ id: payload.id, name });
    }
    case "meta.file_path": {
      const table = String(payload.table);
      if (table !== "datasets" && table !== "ec_cells") {
        return fail("只有数据集和电池有文件位置");
      }
      // 浏览器 dev 没有真实库,给个可复制的演示路径,走通 UI 流程即可
      return ok({
        path: table === "datasets" ? "D:\\demo\\scan-01.xy" : "D:\\demo\\cell.xlsx",
        kind: "source",
      } satisfies MetaFilePathResult);
    }
    case "meta.soft_delete": {
      const table = String(payload.table) as TrashItem["kind"];
      const id = Number(payload.id);
      // 与引擎 ALLOWED_TABLES 一致:projects/samples/batches/datasets/ec_cells
      if (!["projects", "samples", "batches", "datasets", "ec_cells"].includes(table)) {
        return fail("不支持删除的对象");
      }
      if (mockTrash.some((x) => x.kind === table && x.id === id)) {
        return fail("该对象已在回收站中");
      }
      if (table === "ec_cells") {
        mockTrash.unshift({
          kind: "ec_cells",
          id,
          name: mockRenamed.get(`ec_cells:${id}`) ?? MOCK_EC_CELL.name,
          deletedAt: new Date().toISOString().slice(0, 19).replace("T", " "),
          projectName: "LFP Study",
          sampleName: mockRenamed.get("samples:1") ?? SAMPLES[0],
          childCount: 0,
        });
        return ok({ id, kind: table, cascadedDatasets: 0 } satisfies SoftDeleteResult);
      }
      const projectName = mockRenamed.get("projects:1") ?? "LFP Study";
      const sampleName =
        mockRenamed.get(`samples:${id}`) ?? SAMPLES[id - 1] ?? "LFP-500C";
      const name =
        table === "projects"
          ? (mockRenamed.get(`projects:${id}`) ?? projectName)
          : table === "samples"
            ? sampleName
            : (mockRenamed.get(`datasets:${id}`) ?? "scan-01.xy");
      mockTrash.unshift({
        kind: table,
        id,
        name,
        deletedAt: new Date().toISOString().slice(0, 19).replace("T", " "),
        projectName: table === "projects" ? null : projectName,
        sampleName: table === "datasets" ? sampleName : null,
        childCount: table === "projects" ? SAMPLES.length : table === "samples" ? 1 : 0,
      });
      return ok({
        id,
        kind: table,
        cascadedDatasets: table === "projects" ? SAMPLES.length : table === "samples" ? 1 : 0,
      } satisfies SoftDeleteResult);
    }
    case "trash.list":
      return ok({ items: [...mockTrash], total: mockTrash.length } satisfies TrashListResult);
    case "meta.restore": {
      const table = String(payload.table) as TrashItem["kind"];
      const id = Number(payload.id);
      const i = mockTrash.findIndex((x) => x.kind === table && x.id === id);
      if (i < 0) return fail("该对象不在回收站中");
      mockTrash.splice(i, 1);
      return ok({ id, kind: table, restoredAncestors: [] } satisfies RestoreResult);
    }
    case "meta.purge": {
      if (payload.all) {
        const n = mockTrash.length;
        mockTrash.length = 0;
        return ok({ purged: n, removedFiles: n });
      }
      const table = String(payload.table) as TrashItem["kind"];
      const id = Number(payload.id);
      const i = mockTrash.findIndex((x) => x.kind === table && x.id === id);
      if (i < 0) return fail("只能彻底删除回收站中的对象");
      mockTrash.splice(i, 1);
      return ok({ id, kind: table, removedFiles: 1 });
    }
    case "samples.create": {
      // 与引擎同口径:名称必填、波长可校验。此前 mock 缺这条,
      // 浏览器开发模式「新建实验」必失败(CLAUDE.md §2:加了引擎命令必须同步 mock)
      const name = String(payload.name ?? "").trim();
      if (!name) return fail("实验名称不能为空");
      if (payload.wavelength != null) {
        const lam = Number(payload.wavelength);
        if (!Number.isFinite(lam) || lam < 0.3 || lam > 3.0) {
          return fail("波长超出合理范围(0.3–3.0 Å)");
        }
      }
      mockSampleSeq += 1;
      return ok({ sampleId: mockSampleSeq });
    }
    case "samples.update": {
      // 与引擎同口径:只更新给出的字段;波长校验 0.3–3.0。
      const id = Number(payload.id ?? 0);
      if (payload.wavelength != null) {
        const lam = Number(payload.wavelength);
        if (!Number.isFinite(lam) || lam < 0.3 || lam > 3.0) {
          return fail("波长超出合理范围(0.3–3.0 Å)");
        }
      }
      if (payload.name != null && !String(payload.name).trim()) {
        return fail("实验名称不能为空");
      }
      return ok({ updated: 1, id });
    }
    case "peaks.list": {
      if (!mockCurveCache) mockCurveCache = fakeLfpCurve();
      const stored = mockAnnotations.get(Number(payload.dataset_id ?? 1));
      const peaks = mergeMockLabels(fakePeaks(mockCurveCache, "medium"), stored);
      return ok({ peaks });
    }
    case "peaks.update_bounds": {
      const left = Number(payload.left ?? 0);
      const right = Number(payload.right ?? 0);
      if (!(right > left)) return fail("积分边界不合法:右边界必须大于左边界");
      return ok({
        peak: {
          index: 0, position: (left + right) / 2, intensity: 0,
          fwhm: null, area: Math.abs(right - left) * 100,
          left_bound: left, right_bound: right, manual_override: true,
          prominence: null,
        } satisfies PeakResult,
      });
    }
    // ---------- 电化学(mock 与引擎形状严格一致) ----------
    case "ec.preview": {
      const path = String(payload.path ?? "land.xlsx");
      const cycles = mockEcCycles();
      return ok({
        file: path.split(/[\\/]/).pop() ?? path,
        session: {
          test_name: "fixture-0.2C循环",
          channel: "005_7",
          start_time: "2026/09/01 08:00:00",
          end_time: "2026/09/30 17:08:43",
          program: [
            "工步1: 静置 | 直到: 单步时间≥48:01:00",
            "工步2: 倍率充电: 0.1*C | 直到: 电压*≥4.3V",
            "工步3: 倍率放电: 0.1*C | 直到: 电压*≤2.7V",
            "工步4: 倍率充电: 0.2*C | 直到: 电压*≥4.3V",
            "测试结束",
          ],
        },
        qa: { n_records: 386061, backwards: 0, interval_med: 10 },
        n_cycles: cycles.length,
        cycles_preview: cycles.slice(0, 8).map((c) => ({
          index: c.cycle, q_charge: c.qCharge, q_discharge: c.qDischarge,
          ce: c.ce, rate_group: c.rateGroup, incomplete: c.incomplete,
        })),
        mass_mg_estimate: 1.77,
      } satisfies EcPreviewResult);
    }
    case "ec.import": {
      const first = Array.isArray(payload.paths) ? String(payload.paths[0]) : "land.xlsx";
      return ok({
        imported: [{ id: 1, name: first.split(/[\\/]/).pop() ?? first, n_cycles: 120 }],
        duplicates: [],
        failures: [],
        sampleId: Number(payload.sample_id ?? 1),
      } satisfies EcImportResult);
    }
    case "ec.cells": {
      const sid = Number(payload.sample_id ?? 1);
      // mock 只有一颗电池且挂在 sample 1 下(与 mock library.tree 对齐)
      if (sid !== 1 || isTrashed("ec_cells", MOCK_EC_CELL.id)) {
        return ok({ cells: [] } satisfies { cells: EcCellSummary[] });
      }
      return ok({
        cells: [{ ...MOCK_EC_CELL, cycleCount: 120, lastCycle: 120 }],
      } satisfies { cells: EcCellSummary[] });
    }
    case "ec.cell_overview": {
      const id = Number(payload.cell_id ?? 1);
      if (id !== MOCK_EC_CELL.id || isTrashed("ec_cells", id)) {
        return fail("电化学数据(电池)不存在或已在回收站中");
      }
      return ok({
        id,
        name: mockRenamed.get(`ec_cells:${id}`) ?? MOCK_EC_CELL.name,
        filename: MOCK_EC_CELL.filename,
        massMg: MOCK_EC_CELL.massMg,
        session: {
          test_name: "陈钰婷-0.2C循环",
          channel: "005_7",
          start_time: "2017/02/14 13:43:37",
          end_time: "2026/09/19 17:08:43",
          program: [
            "工步1: 静置 | 直到: 单步时间≥48:01:00",
            "工步3: 倍率充电: 0.1*C | 直到: 电压*≥4.3V",
            "工步5: 倍率放电: 0.1*C | 直到: 电压*≤2.7V",
            "工步8: 倍率充电: 0.2*C | 直到: 电压*≥4.3V",
            "测试结束",
          ],
        },
        qa: { n_records: 386061, backwards: 0, dup_time: 759, interval_med: 10 },
        cycles: mockEcCycles(),
      } satisfies EcCellOverview);
    }
    case "ec.dqdv_compute": {
      const id = Number(payload.cell_id ?? 1);
      if (id !== MOCK_EC_CELL.id) return fail("电化学数据(电池)不存在或已在回收站中");
      const params = (payload.params ?? {}) as EcDqdvParams;
      mockEcDqdvHash = hashPipeline(params);
      mockEcDqdvParams = params;
      const { peaks, tracks } = mockEcPeaksAndTracks();
      return ok({
        curveFile: `dqdv_${mockEcDqdvHash}.npz`,
        nCurves: 240,
        nPeaks: peaks.length,
        nTruncated: peaks.filter((p) => p.truncated).length,
        peaks,
        tracks,
        paramsHash: mockEcDqdvHash,
      } satisfies EcDqdvComputeResult);
    }
    case "ec.dqdv_result": {
      // 与引擎同语义:没算过 → {computed:false};算过 → 原样读回明细+参数。
      if (mockEcDqdvHash == null) return ok({ computed: false } satisfies EcDqdvResult);
      const id = Number(payload.cell_id ?? 1);
      if (id !== MOCK_EC_CELL.id) return fail("电化学数据(电池)不存在或已在回收站中");
      const { peaks, tracks } = mockEcPeaksAndTracks();
      return ok({
        computed: true,
        paramsHash: mockEcDqdvHash,
        params: { dv_mv: 2.0, sg_window: 11, sg_poly: 3, v_min: 2.9, v_max: 4.32, ...mockEcDqdvParams },
        updatedAt: new Date().toISOString().slice(0, 19).replace("T", " "),
        nCurves: 240,
        nPeaks: peaks.length,
        nTruncated: peaks.filter((p) => p.truncated).length,
        peaks,
        tracks,
      } satisfies EcDqdvResult);
    }
    case "ec.dqdv_state": {
      if (mockEcDqdvHash == null) return ok({ computed: false } satisfies EcDqdvState);
      return ok({
        computed: true,
        paramsHash: mockEcDqdvHash,
        updatedAt: new Date().toISOString().slice(0, 19).replace("T", " "),
      } satisfies EcDqdvState);
    }
    case "ec.dqdv_curve": {
      if (mockEcDqdvHash == null) return fail("尚未计算dQ/dV,请先执行计算");
      const cycle = Number(payload.cycle ?? 1);
      const direction = (payload.direction === "discharge"
        ? "discharge"
        : "charge") as "charge" | "discharge";
      return ok(mockEcCurve(cycle, direction));
    }
    case "ec.dqdv_waterfall": {
      if (mockEcDqdvHash == null) return fail("尚未计算dQ/dV,请先执行计算");
      const direction = (payload.direction === "discharge"
        ? "discharge"
        : "charge") as "charge" | "discharge";
      const step = Math.max(1, Number(payload.step ?? 10));
      const cycles = mockEcCycles();
      const sampled = cycles.filter((_, i) => i % step === 0).map((c) => c.cycle);
      // 与引擎同一电压网格(0.004 步长 → 356 点)
      const v: number[] = [];
      for (let x = 2.9; x <= 4.32 + 1e-9; x += 0.004) v.push(Math.round(x * 1000) / 1000);
      const matrix = new Float32Array(sampled.length * v.length);
      sampled.forEach((cy, r) => {
        // 复用单圈曲线重采样到公共网格(与引擎行为一致)
        const curve = mockEcCurve(cy, direction);
        for (let i = 0; i < v.length; i++) {
          const idx = Math.round((v[i] - curve.v[0]) / 0.002);
          matrix[r * v.length + i] = curve.dqdv[idx] ?? Number.NaN;
        }
      });
      return ok({
        cycles: sampled,
        direction,
        step,
        shape: [sampled.length, v.length],
        v,
        matrix,
        paramsHash: mockEcDqdvHash,
      } satisfies EcWaterfallPayload);
    }
    case "ec.attribution_run": {
      if (mockEcDqdvHash == null) return fail("尚未计算dQ/dV,请先执行计算");
      const cycles = mockEcCycles();
      const baseline = cycles[3].qDischarge; // 主倍率基线
      const attCycles = cycles.slice(3).map((c) => {
        const total = Math.round((baseline - c.qDischarge) * 1000) / 1000;
        return {
          cycle: c.cycle,
          total,
          polarization: Math.round(total * 0.62 * 1000) / 1000,
          material: Math.round(total * 0.13 * 1000) / 1000,
          cutoff: Math.round(total * 0.25 * 1000) / 1000,
        };
      });
      mockEcAttributionSource = mockEcDqdvHash;
      return ok({
        method: "B",
        confidence: "low",
        baseline: Math.round(baseline * 1000) / 1000,
        nCycles: attCycles.length,
        lastCycle: attCycles[attCycles.length - 1].cycle,
        lastTotal: attCycles[attCycles.length - 1].total,
        cycles: attCycles,
        ceSeries: cycles.map((c) => [c.cycle, c.ce] as [number, number]),
        cumulativeIrreversible: cycles.map((c) => [
          c.cycle,
          Math.round(c.qCharge * (1 - c.ce) * c.cycle * 100) / 100,
        ] as [number, number]),
        truncatedEvidence: {
          available: true,
          nTruncatedCycles: 60,
          nFullCycles: 0,
          firstPosition: 4.28,
          lastPosition: 4.3,
          driftV: 0.02,
        },
        notes: ["mock:未检出低倍率回访圈,极化与活性拆分为近似值(方法 B)。"],
        conclusion:
          "第 120 圈累计衰减 40.9 mAh/g(基线 137.8),主导分量为极化型占 62%。分量:极化 25.4 / 活性 5.3 / 截断 10.2 mAh/g。注:方法 B 为近似拆分,分量比例仅供参考;方向判断可靠。",
        sourceDqdvHash: mockEcDqdvHash,
      } satisfies EcAttributionResult);
    }
    case "ec.attribution_get": {
      if (mockEcAttributionSource == null) {
        return ok({ computed: false } satisfies EcAttributionState);
      }
      // get 是只读:内容用确定性生成复用,但 sourceDqdvHash/stale 必须反映
      // **上次归因时的**指纹 —— run 的递归调用会覆盖它,先存后还原。
      const source = mockEcAttributionSource;
      const run = (await mockCommand("ec.attribution_run", {
        cell_id: 1,
      })) as EngineResponse<EcAttributionResult>;
      if (!run.ok) return run;
      mockEcAttributionSource = source;
      return ok({
        ...run.data,
        sourceDqdvHash: source,
        computed: true,
        stale: mockEcDqdvHash !== source,
        updatedAt: new Date().toISOString().slice(0, 19).replace("T", " "),
      } satisfies EcAttributionState);
    }
    case "ec.export": {
      const kind = String(payload.kind ?? "cycles");
      const rows = kind === "cycles" ? 120 : kind === "peaks" ? 360 : 117;
      return ok({
        path: `C:/Users/dev/Exports/ec_${kind}.csv`,
        rows,
      } satisfies EcExportResult);
    }
    default:
      return fail(`mock: 未实现命令 ${cmd}`);
  }
}

function ok<T>(data: T): EngineResponse<T> {
  return { ok: true, data };
}
function fail(error: string): EngineResponse<never> {
  return { ok: false, error, detail: "" };
}

// ============================================================
// 电化学(LAND dQ/dV 归因)命令封装。
// 薄封装只为类型收窄 —— engineCommand 本身是泛型,这里把每条 ec 命令
// 的返回类型钉死,调用方(store/组件)不再手写泛型参数。
// ============================================================

/** dry-run 解析:不落库。大文件(20MB xlsx)约 20 秒,调用方须给等待态。 */
export function ecPreview(path: string) {
  return engineCommand<EcPreviewResult>("ec.preview", { path });
}

export function ecImport(payload: {
  paths: string[];
  project_id: number;
  sample_id?: number;
  new_sample?: { name: string; material?: string };
  active_mass_mg?: number;
  import_anyway?: boolean;
}) {
  return engineCommand<EcImportResult>("ec.import", payload);
}

export function ecCells(sampleId: number) {
  return engineCommand<{ cells: EcCellSummary[] }>("ec.cells", {
    sample_id: sampleId,
  });
}

export function ecCellOverview(cellId: number) {
  return engineCommand<EcCellOverview>("ec.cell_overview", { cell_id: cellId });
}

export function ecDqdvCompute(cellId: number, params: EcDqdvParams) {
  return engineCommand<EcDqdvComputeResult>("ec.dqdv_compute", {
    cell_id: cellId,
    params,
  });
}

export function ecDqdvState(cellId: number) {
  return engineCommand<EcDqdvState>("ec.dqdv_state", { cell_id: cellId });
}

/** 读回上次固化的峰/轨迹/参数(打开 cell 时调用,重启后免重算)。 */
export function ecDqdvResult(cellId: number) {
  return engineCommand<EcDqdvResult>("ec.dqdv_result", { cell_id: cellId });
}

export function ecDqdvCurve(
  cellId: number,
  cycle: number,
  direction: "charge" | "discharge",
) {
  return engineCommand<EcCurvePayload>("ec.dqdv_curve", {
    cell_id: cellId,
    cycle,
    direction,
  });
}

export function ecDqdvWaterfall(
  cellId: number,
  direction: "charge" | "discharge",
  step: number,
) {
  return engineCommand<EcWaterfallPayload>("ec.dqdv_waterfall", {
    cell_id: cellId,
    direction,
    step,
  });
}

export function ecAttributionRun(cellId: number) {
  return engineCommand<EcAttributionResult>("ec.attribution_run", {
    cell_id: cellId,
  });
}

export function ecAttributionGet(cellId: number) {
  return engineCommand<EcAttributionState>("ec.attribution_get", {
    cell_id: cellId,
  });
}

export function ecExport(
  cellId: number,
  kind: "cycles" | "peaks" | "attribution",
) {
  return engineCommand<EcExportResult>("ec.export", { cell_id: cellId, kind });
}

// ============================================================
// 右键"在资源管理器中显示"
// ============================================================

/** 取文件路径并在资源管理器里定位(explorer /select)。
 *
 *  - Tauri:走 tauri-plugin-opener 的 revealItemInDir;
 *  - 浏览器 dev:没有系统 shell,降级为复制路径 + 让调用方 toast 路径 ——
 *    静默失败最糟,用户点了没反应会以为功能坏了。
 *
 *  路径取不到(原文件与库内副本都不在)时返回 ok:false + 引擎的人话错误。
 */
export async function revealInExplorer(
  table: TrashKind,
  id: number,
): Promise<
  | { ok: true; path: string; mode: "reveal" | "degraded" }
  | { ok: false; error: string }
> {
  const r = await engineCommand<MetaFilePathResult>("meta.file_path", {
    table,
    id,
  });
  if (!r.ok) return { ok: false, error: r.error };
  if (!isTauri) {
    // 尽力复制;失败也把路径 toast 出来,信息本身才是关键
    try {
      await navigator.clipboard.writeText(r.data.path);
    } catch {
      /* 剪贴板不可用(权限/非安全上下文)→ 只展示路径 */
    }
    return { ok: true, path: r.data.path, mode: "degraded" };
  }
  try {
    const { revealItemInDir } = await import("@tauri-apps/plugin-opener");
    await revealItemInDir(r.data.path);
    return { ok: true, path: r.data.path, mode: "reveal" };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}
