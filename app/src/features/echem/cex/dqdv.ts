import { cexCommand, errorText, type CexDocument } from "./model";

export type Direction = "charge" | "discharge";

export interface DqdvSettings {
  gridMv: number;
  smoothingMv: number;
}

export interface DqdvTrace {
  direction: Direction;
  step: number;
  part: number;
  startBin: number;
  values: number[];
  rawValues: number[] | null;
  records: number[];
  firstRecord: number;
  lastRecord: number;
  count: number;
  currentMa: number;
  capacityMah: number;
  capacitySource: "cex" | "integrated";
  sampleMv: number;
  warnings: string[];
}

export interface DqdvCycle {
  cycle: number;
  paired: boolean;
  cvCapacityMah: number | null;
  issues: string[];
  curves: DqdvTrace[];
}

interface DqdvPage {
  algorithm: string;
  sha256: string;
  total: number;
  results: DqdvCycle[];
}

export interface AnalyzedCex {
  doc: CexDocument;
  sha256: string;
  algorithm: string;
  cycles: DqdvCycle[];
}

export interface DqdvRun {
  settings: DqdvSettings;
  sources: AnalyzedCex[];
  errors: string[];
  cancelled: boolean;
}

export interface TraceRef {
  key: string;
  source: AnalyzedCex;
  cycle: DqdvCycle;
  trace: DqdvTrace;
}

export const directionLabel = { charge: "充电", discharge: "放电" } as const;

export function voltageAt(trace: DqdvTrace, index: number, gridMv: number) {
  return (trace.startBin + index + 0.5) * gridMv / 1000;
}

export function traceName(ref: TraceRef) {
  const { source, cycle, trace } = ref;
  return `${source.doc.name} · ${cycle.cycle} 圈 · ${directionLabel[trace.direction]} · 工步 ${trace.step}`
    + (trace.part > 1 ? `/${trace.part}` : "");
}

export function traceRefs(sources: AnalyzedCex[]): TraceRef[] {
  return sources.flatMap((source) => source.cycles.flatMap((cycle) => cycle.curves.map((trace) => ({
    key: `${source.doc.id}:${cycle.cycle}:${trace.step}:${trace.part}`,
    source, cycle, trace,
  }))));
}

export async function calculateDqdv(
  selections: { doc: CexDocument; expression: string }[],
  settings: DqdvSettings,
  signal: AbortSignal,
  progress: (text: string) => void,
): Promise<DqdvRun> {
  const sources: AnalyzedCex[] = [];
  const errors: string[] = [];
  for (const [index, { doc, expression }] of selections.entries()) {
    if (signal.aborted) break;
    const automatic = !expression.trim();
    const cycles: DqdvCycle[] = [];
    let firstUsable: DqdvCycle | undefined;
    let firstChecked: DqdvCycle | undefined;
    let sha256 = "";
    let algorithm = "";
    let offset = 0;
    try {
      while (!signal.aborted) {
        progress(`${doc.name} · 文件 ${index + 1}/${selections.length} · 已检查 ${offset} 圈`);
        const page = await cexCommand<DqdvPage>("dqdv", {
          id: doc.id, cycles: expression, settings, offset, limit: automatic ? 2 : 4,
        });
        sha256 = page.sha256;
        algorithm = page.algorithm;
        if (!page.results.length) break;
        if (automatic) {
          firstChecked ??= page.results[0];
          firstUsable ??= page.results.find((cycle) => cycle.curves.length > 0);
          const paired = page.results.find((cycle) => cycle.paired
            && new Set(cycle.curves.map((trace) => trace.direction)).size === 2);
          if (paired) {
            cycles.push(paired);
            break;
          }
        } else {
          cycles.push(...page.results);
        }
        offset += page.results.length;
        if (offset >= page.total) break;
      }
    } catch (error) {
      errors.push(doc.filename + " · " + errorText(error));
    }
    if (automatic && !cycles.length) {
      const fallback = firstUsable ?? firstChecked;
      if (fallback) cycles.push(fallback);
    }
    if (cycles.length) sources.push({ doc, sha256, algorithm, cycles });
  }
  return { settings, sources, errors, cancelled: signal.aborted };
}

export interface DqdvPeak {
  voltage: number | null;
  height: number | null;
  width: number | null;
  area: number | null;
  coverage: number;
}

/** Measure a peak above the line through the window edges. Integrate the original
 *  incremental capacity over the same window as a separate measured quantity. */
export function measurePeak(trace: DqdvTrace, gridMv: number, from: number, to: number): DqdvPeak {
  const grid = gridMv / 1000;
  const first = Math.max(0, Math.floor(from / grid - trace.startBin + 1e-9));
  const end = Math.min(trace.values.length, Math.ceil(to / grid - trace.startBin - 1e-9));
  let area = 0;
  let covered = 0;
  for (let i = first; i < end; i++) {
    const edge = (trace.startBin + i) * grid;
    const width = Math.max(0, Math.min(to, edge + grid) - Math.max(from, edge));
    area += width * Math.abs(trace.values[i]);
    covered += width;
  }
  const result: DqdvPeak = {
    voltage: null, height: null, width: null,
    area: covered > 0 ? area : null,
    coverage: Math.min(1, covered / (to - from)),
  };
  if (end - first < 5 || result.coverage < 1 - 1e-8) return result;
  const y = trace.values.slice(first, end).map(Math.abs);
  const baseline = (y[y.length - 1] - y[0]) / (y.length - 1);
  const corrected = y.map((v, index) => v - y[0] - index * baseline);
  const peak = corrected.reduce((best, value, index) => value > corrected[best] ? index : best, 0);
  const numericFloor = y.reduce((maximum, value) => Math.max(maximum, value), 1) * 1e-8;
  if (peak === 0 || peak === corrected.length - 1 || corrected[peak] <= numericFloor) return result;
  const half = corrected[peak] / 2;
  let left = peak;
  let right = peak;
  while (left > 0 && corrected[left] > half) left--;
  while (right < corrected.length - 1 && corrected[right] > half) right++;
  const a = left + (half - corrected[left]) / (corrected[left + 1] - corrected[left]);
  const b = right - (half - corrected[right]) / (corrected[right - 1] - corrected[right]);
  return {
    ...result, voltage: voltageAt(trace, first + peak, gridMv),
    height: corrected[peak], width: (b - a) * grid,
  };
}
