/** 引擎调用客户端(§10.4)。
 *
 * 生产路径:invoke("engine_command", {cmd, payload}) → Rust → sidecar。
 * 开发兜底:纯 vite dev(非 Tauri 窗口)时使用确定性 mock 曲线,
 * 让 Milestone 1 的 UI 不依赖 sidecar 也能完整驱动。
 */

import type {
  CurvePayload,
  EngineResponse,
  ImportResult,
  PeakResult,
  PreviewResult,
  ProjectNode,
  SampleNode,
} from "../types/engine";

const isTauri = "__TAURI_INTERNALS__" in window;

export async function engineCommand<T>(
  cmd: string,
  payload: Record<string, unknown> = {},
): Promise<EngineResponse<T>> {
  if (isTauri) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<EngineResponse<T>>("engine_command", {
      cmd,
      payload: JSON.stringify(payload),
    });
  }
  return mockCommand(cmd, payload) as Promise<EngineResponse<T>>;
}

/** 引擎错误的用户可见呈现约定:engine 保证 error 是人话(§68)。 */
export function describeError(res: { error: string; detail?: string }): string {
  return res.error;
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
  return {
    x, y, xAxisType: "two_theta", wavelength: 1.5418, pointCount: 3501,
  };
}

function fakePeaks(curve: CurvePayload, sensitivity: string): PeakResult[] {
  const { x, y } = curve;
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

let mockCurveCache: CurvePayload | null = null;

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
      };
      const peaks = fakePeaks(
        mockCurveCache,
        pipeline.peaks?.sensitivity ?? "medium",
      );
      return ok({
        curve: mockCurveCache,
        peaks,
        smoothingAffectsMetrics: !!pipeline.smoothing?.enabled,
        warnings: [],
      } satisfies PreviewResult);
    }
    case "peaks.detect": {
      if (!mockCurveCache) mockCurveCache = fakeLfpCurve();
      return ok({
        peaks: fakePeaks(mockCurveCache, "medium"),
      });
    }
    case "analysis.commit":
      return ok({ analysisId: 1 });
    case "projects.list":
      return ok({
        projects: [{ id: 1, name: "LFP Study", description: "" }],
      } satisfies { projects: ProjectNode[] });
    case "projects.create":
      return ok({ projectId: 2 });
    case "library.tree":
      return ok({
        samples: SAMPLES.map((name, i) => ({
          id: i + 1,
          project_id: 1,
          name,
          temperature: name.startsWith("LFP") ? name.slice(4, 7) : "",
          duration: "2 h",
          dataset_count: 1,
          datasets: [{ id: i + 1, name: "scan-01.xy" }],
        })),
      } satisfies { samples: SampleNode[] });
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
    case "presets.save":
      return ok({ presetId: 1 });
    case "presets.list":
      return ok({ presets: [] });
    case "presets.apply":
      return ok({ applied: Number((payload.dataset_ids as unknown[])?.length ?? 0) });
    case "meta.rename":
    case "meta.soft_delete":
      return ok({ id: payload.id });
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
