import { useMemo, useState } from "react";
import { Button } from "../../../components/controls";
import { COMPARE_COLORS } from "../../../services/engine";
import { EcChartModeSelect, EcLineChart, type EcChartMode, type EcSeries } from "../EcLineChart";
import { directionLabel, measurePeak, type AnalyzedCex, type TraceRef } from "./dqdv";

export interface PeakRow {
  ref: TraceRef;
  voltage: number | null;
  height: number | null;
  width: number | null;
  area: number | null;
  coverage: number;
  shift: number | null;
}

export function peakRows(refs: TraceRef[], gridMv: number, window: [number, number], baseline: string,
  scale: (ref: TraceRef) => number): PeakRow[] {
  const measurements = refs.map((ref) => ({ ref, ...measurePeak(ref.trace, gridMv, ...window) }));
  const references = new Map<string, typeof measurements>();
  for (const measurement of measurements) {
    if (String(measurement.ref.cycle.cycle) !== baseline) continue;
    const key = `${measurement.ref.source.doc.id}:${measurement.ref.trace.direction}`;
    const matches = references.get(key) ?? [];
    matches.push(measurement);
    references.set(key, matches);
  }
  return measurements.map((row) => {
    const key = `${row.ref.source.doc.id}:${row.ref.trace.direction}`;
    const matches = references.get(key);
    const reference = matches?.length === 1 ? matches[0] : null;
    const current = row.ref.trace.currentMa;
    const sameRate = reference && Math.abs(reference.ref.trace.currentMa - current) <= 0.12 * Math.abs(current);
    const factor = scale(row.ref);
    return {
      ...row,
      height: row.height == null ? null : row.height * factor,
      area: row.area == null ? null : row.area * factor,
      shift: sameRate && row.voltage != null && reference?.voltage != null ? (row.voltage - reference.voltage) * 1000 : null,
    };
  });
}

export function displayNumber(value: number | null, digits = 4) {
  return value == null ? "—" : Number(value.toPrecision(digits)).toString();
}

export function DqdvPeaks({ rows, sources, unit, areaUnit, metadata, label, from, to, baseline, onBaseline,
  onLabel, onFrom, onTo, onPick, onExport, exporting }: {
  rows: PeakRow[];
  sources: AnalyzedCex[];
  unit: string;
  areaUnit: string;
  metadata: Record<string, unknown>;
  label: string;
  from: string;
  to: string;
  baseline: string;
  onBaseline: (value: string) => void;
  onLabel: (value: string) => void;
  onFrom: (value: string) => void;
  onTo: (value: string) => void;
  onPick: () => void;
  onExport: () => void;
  exporting: boolean;
}) {
  const [metric, setMetric] = useState<"voltage" | "area">("voltage");
  const [mode, setMode] = useState<EcChartMode>("scatter");
  const series = useMemo<EcSeries[]>(() => {
    const grouped = new Map<string, { title: string; file: string; points: Map<number, number | null> }>();
    for (const row of rows) {
      const { source, trace, cycle } = row.ref;
      const key = `${source.doc.id}:${trace.direction}`;
      const group = grouped.get(key) ?? { title: `${source.doc.name} · ${directionLabel[trace.direction]}`,
        file: source.doc.id, points: new Map() };
      const value = metric === "area" && row.coverage < 1 - 1e-8 ? null : row[metric];
      group.points.set(cycle.cycle, group.points.has(cycle.cycle) ? null : value);
      grouped.set(key, group);
    }
    const cyclesByFile = new Map(sources.map((source) => [source.doc.id, source.cycles.map((cycle) => cycle.cycle)]));
    return [...grouped.values()].map(({ title, file, points }, index) => ({
      name: title,
      color: COMPARE_COLORS[index % COMPARE_COLORS.length],
      data: (cyclesByFile.get(file) ?? []).map<[number, number | null]>((cycle) => [cycle, points.get(cycle) ?? null])
        .sort(([a], [b]) => a - b),
    }));
  }, [rows, sources, metric]);
  return (
    <>
      <div className="flex flex-wrap items-end gap-2 border-b border-[var(--divider)] px-3 py-2 text-[12px]">
        <label className="grid gap-1">峰名（可选）
          <input className="cex-input w-32" value={label} maxLength={80} onChange={(event) => onLabel(event.target.value)} />
        </label>
        <label className="grid gap-1">下限 (V)
          <input className="cex-input w-21" type="number" step="0.001" value={from} onChange={(event) => onFrom(event.target.value)} />
        </label>
        <label className="grid gap-1">上限 (V)
          <input className="cex-input w-21" type="number" step="0.001" value={to} onChange={(event) => onTo(event.target.value)} />
        </label>
        <Button kind="secondary" onClick={onPick}>图上选窗</Button>
        <label className="grid gap-1">基准圈（可选）
          <input className="cex-input w-23" type="number" min="1" step="1" value={baseline} onChange={(event) => onBaseline(event.target.value)} />
        </label>
        <Button kind="secondary" disabled={!rows.length || exporting} onClick={onExport}>峰表 CSV</Button>
      </div>
      {rows.length > 0 ? (
        <>
          <div className="flex flex-wrap items-center gap-2 px-3 pt-2 text-[12px]">
            <label>趋势 <select className="cex-input ml-1" value={metric}
              onChange={(event) => setMetric(event.target.value === "area" ? "area" : "voltage")}>
              <option value="voltage">峰位</option><option value="area">窗口积分</option>
            </select></label>
            <EcChartModeSelect value={mode} onChange={setMode} />
            <span className="text-[var(--text-secondary)]">取窗口内主峰。峰高和半峰宽以窗口两端连线为基线。</span>
          </div>
          <div className="min-h-32 flex-1 px-2">
            <EcLineChart series={series} xName="循环序号" y1Name={metric === "area" ? `窗口积分 (${areaUnit})` : "峰位 (V)"}
              mode={mode} height="100%" xMinInterval={1} exportName="CEX-dQdV-峰趋势" figureMetadata={{ ...metadata, metric }} />
          </div>
          <div className="max-h-[36%] min-h-25 overflow-auto border-t border-[var(--divider)]">
            <table className="w-full text-left text-[11px] tabular-nums">
              <thead className="sticky top-0 bg-[var(--bg-sidebar)] text-[var(--text-secondary)]">
                <tr>{["文件", "圈", "方向", "工步", "峰位 (V)", `峰高 (${unit})`, "半峰宽 (mV)",
                  `窗口积分 (${areaUnit})`, "覆盖", "相对基准 (mV)"].map((title) => <th className="whitespace-nowrap px-2 py-1.5" key={title}>{title}</th>)}</tr>
              </thead>
              <tbody>{rows.map((row) => <tr className="border-t border-[var(--divider)]" key={row.ref.key}>
                {[row.ref.source.doc.name, row.ref.cycle.cycle, directionLabel[row.ref.trace.direction],
                  row.ref.trace.step, displayNumber(row.voltage, 6), displayNumber(row.height),
                  displayNumber(row.width == null ? null : 1000 * row.width), displayNumber(row.area),
                  `${Math.round(100 * row.coverage)}%`, displayNumber(row.shift)].map((value, i) => (
                    <td className="whitespace-nowrap px-2 py-1.5" key={i}>{value}</td>
                ))}
              </tr>)}</tbody>
            </table>
          </div>
        </>
      ) : <div role="status" className="m-auto p-4 text-sm text-[var(--text-secondary)]">
        选择有效的电压窗口，在已计算曲线上测量峰位和面积。
      </div>}
    </>
  );
}
