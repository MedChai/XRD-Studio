import { useEffect, useMemo, useRef } from "react";
import * as echarts from "echarts";
import { useAppStore } from "../../../stores";
import { EcChartExport } from "../EcLineChart";
import { directionLabel, type AnalyzedCex, type Direction, type DqdvTrace } from "./dqdv";

function heatmapRows(source: AnalyzedCex, direction: Direction) {
  return source.cycles.flatMap(({ cycle, curves }) => {
    const traces = curves.filter((curve) => curve.direction === direction);
    return traces.length
      ? traces.map((trace) => ({ cycle, trace: trace as DqdvTrace | undefined }))
      : [{ cycle, trace: undefined }];
  });
}

export function DqdvHeatmap({ source, direction, gridMv, scale, absolute, unit, metadata, onCycle }: {
  source: AnalyzedCex;
  direction: Direction;
  gridMv: number;
  scale: number;
  absolute: boolean;
  unit: string;
  metadata: Record<string, unknown>;
  onCycle: (cycle: number) => void;
}) {
  const element = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const theme = useAppStore((s) => s.theme);
  const rows = useMemo(() => heatmapRows(source, direction), [source, direction]);
  const plot = useMemo(() => {
    let low = Infinity;
    let high = -Infinity;
    for (const { trace } of rows) {
      if (!trace) continue;
      low = Math.min(low, trace.startBin);
      high = Math.max(high, trace.startBin + trace.values.length);
    }
    if (!Number.isFinite(low)) return null;
    const signedDischarge = direction === "discharge" && !absolute;
    const maxColumns = Math.max(1, Math.min(1400, Math.floor(180000 / rows.length)));
    const stride = Math.max(1, Math.ceil((high - low) / maxColumns));
    const volts = Array.from({ length: Math.ceil((high - low) / stride) }, (_, i) => {
      const start = low + i * stride;
      return Number(((start + Math.min(high, start + stride)) * gridMv / 2000).toFixed(5));
    });
    const points: [number, number, number][] = [];
    let max = 0;
    for (const [y, { trace }] of rows.entries()) {
      if (!trace) continue;
      const cells = new Map<number, number>();
      trace.values.forEach((value, i) => {
        const x = Math.floor((trace.startBin + i - low) / stride);
        const magnitude = Math.abs(value) * scale;
        cells.set(x, Math.max(cells.get(x) ?? 0, magnitude));
        max = Math.max(max, magnitude);
      });
      for (const [x, magnitude] of cells) points.push([x, y, signedDischarge ? -magnitude : magnitude]);
    }
    return { volts, points, stride, max, signedDischarge };
  }, [rows, gridMv, scale, absolute, direction]);

  useEffect(() => {
    const node = element.current;
    if (!node || !plot) return;
    const chart = echarts.init(node, undefined, { renderer: "canvas" });
    chartRef.current = chart;
    const styles = getComputedStyle(document.documentElement);
    const text = styles.getPropertyValue("--text-secondary").trim();
    const divider = styles.getPropertyValue("--divider").trim();
    const background = styles.getPropertyValue("--bg-panel").trim();
    const colors = direction === "charge" ? ["#e8eef8", "#a6badc", "#244d95"] : ["#faebe4", "#efb69a", "#a54122"];
    const yLabels = rows.map(({ cycle, trace }) => trace
      ? `${cycle} · ${trace.step}${trace.part > 1 ? `/${trace.part}` : ""}` : `${cycle}`);
    chart.setOption({
      animation: false,
      backgroundColor: background,
      grid: { left: 88, right: 87, top: 28, bottom: 67 },
      xAxis: { type: "category", data: plot.volts, name: "电压 (V)", nameLocation: "middle", nameGap: 29,
        axisLabel: { color: text, hideOverlap: true }, nameTextStyle: { color: text } },
      yAxis: { type: "category", data: yLabels, name: "圈 · 工步", axisLabel: { color: text },
        nameTextStyle: { color: text }, splitLine: { show: false } },
      visualMap: { show: true, orient: "vertical", right: 6, top: "middle", itemHeight: 110,
        min: plot.signedDischarge ? -plot.max : 0, max: plot.signedDischarge ? 0 : plot.max,
        calculable: false, dimension: 2, inRange: { color: plot.signedDischarge ? colors.slice().reverse() : colors },
        text: plot.signedDischarge ? ["0", (-plot.max).toPrecision(3)] : [plot.max.toPrecision(3), "0"],
        textStyle: { color: text }, textGap: 3, precision: 2 },
      dataZoom: [
        { type: "inside", xAxisIndex: 0, filterMode: "filter" },
        { type: "slider", xAxisIndex: 0, height: 14, bottom: 8, textStyle: { color: text }, borderColor: divider },
        { type: "inside", yAxisIndex: 0, filterMode: "filter", end: Math.min(100, 4000 / rows.length) },
      ],
      tooltip: { position: "top", formatter: (item: unknown) => {
        const [x, y, value] = (item as { data: [number, number, number] }).data;
        const row = rows[y];
        return `圈 ${row.cycle} · 工步 ${row.trace?.step ?? "—"} · ${directionLabel[direction]}`
          + `<br>${plot.volts[x]} V · ${Number(value.toPrecision(5))} ${unit}`;
      } },
      series: [{ type: "heatmap", data: plot.points, progressive: 6000, emphasis: { itemStyle: { borderWidth: 1 } } }],
    });
    chart.on("click", (params) => {
      const data = params.data as [number, number, number] | undefined;
      if (data) onCycle(rows[data[1]].cycle);
    });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(node);
    return () => { observer.disconnect(); chartRef.current = null; chart.dispose(); };
  }, [plot, rows, onCycle, direction, unit, theme]);

  if (!plot) return <div role="status" className="m-auto p-4 text-sm">这个方向没有可绘制的曲线</div>;
  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div ref={element} className="min-h-0 flex-1" />
      <EcChartExport chartRef={chartRef} name="CEX-dQdV-热图" metadata={{ ...metadata, heatmapBinStride: plot.stride }} />
      {plot.stride > 1 && <div className="px-3 text-[11px] text-[var(--text-secondary)]">
        热图每 {plot.stride} 个电压网格显示绝对值最大点。CSV 使用完整网格。
      </div>}
    </div>
  );
}
