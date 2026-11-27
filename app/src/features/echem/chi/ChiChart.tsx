import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts";
import { ImageDown, Maximize2 } from "lucide-react";
import { IconButton } from "../../../components/controls";
import { exportEcFigure } from "../../../services/exports";
import { useAppStore, useUiStore } from "../../../stores";
import { numberLabel, type EisFile } from "./model";

export type View = "cv" | "nyquist" | "bode" | "residual";
export type Trace = { name: string; data: [number, number, number?][]; color: string;
  scatter?: boolean; axis?: 0 | 1; faded?: boolean };

function theme(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function plotBounds(traces: Trace[], width: number, height: number) {
  let xMin = 0, xMax = 0, yMin = 0, yMax = 0;
  for (const { data } of traces) {
    for (const [x, y] of data) {
      xMin = Math.min(xMin, x);
      xMax = Math.max(xMax, x);
      yMin = Math.min(yMin, y);
      yMax = Math.max(yMax, y);
    }
  }
  const gridWidth = Math.max(1, width - 119);
  const gridHeight = Math.max(1, height - 87);
  const widthNeeded = Math.max(xMax - xMin, 0.18 * (yMax - yMin), 1e-9);
  const heightNeeded = Math.max(yMax - yMin, 0.18 * (xMax - xMin), 1e-9);
  const unitsPerPixel = 1.13 * Math.max(widthNeeded / gridWidth, heightNeeded / gridHeight);
  const usedWidth = Math.min(gridWidth, 1.13 * widthNeeded / unitsPerPixel);
  const usedHeight = Math.min(gridHeight, 1.13 * heightNeeded / unitsPerPixel);
  const spanX = usedWidth * unitsPerPixel;
  const spanY = usedHeight * unitsPerPixel;
  const minX = xMin === 0 ? 0 : (xMin + xMax - spanX) / 2;
  const minY = yMin === 0 ? 0 : (yMin + yMax - spanY) / 2;
  return {
    x: { min: minX, max: minX + spanX },
    y: { min: minY, max: minY + spanY },
    grid: { left: 83 + (gridWidth - usedWidth) / 2, right: 36 + (gridWidth - usedWidth) / 2,
      top: 34 + (gridHeight - usedHeight) / 2, bottom: 53 + (gridHeight - usedHeight) / 2 },
  };
}

function makeOption(view: View, traces: Trace[], width: number, height: number): echarts.EChartsOption {
  const bode = view === "bode";
  const nyquist = view === "nyquist";
  const cv = view === "cv";
  const bounds = nyquist ? plotBounds(traces, width, height) : null;
  const xName = cv ? "电位 (V)" : nyquist ? "Z′ (Ω)" : "频率 (Hz)";
  const yName = cv ? "电流 (mA)" : nyquist ? "−Z″ (Ω)" : bode ? "|Z| (Ω)" : "残差 (Ω)";
  const label = { color: theme("--text-secondary"), fontSize: 11, formatter: (n: number) => numberLabel(n, 3) };
  const nameTextStyle = { color: theme("--text-secondary"), fontSize: 12 };
  const splitLine = { show: true, lineStyle: { color: theme("--plot-grid") } };
  const commonAxis = { axisLabel: label, nameTextStyle, splitLine, axisLine: { onZero: false } };
  const extent = (values: { min: number; max: number } | undefined) => values ?? {};
  const xAxis = {
    ...commonAxis, type: cv || nyquist ? "value" as const : "log" as const, scale: !nyquist,
    name: xName, nameLocation: "middle" as const, nameGap: 28, ...extent(bounds?.x),
  };
  const yAxis = {
    ...commonAxis, type: bode ? "log" as const : "value" as const, scale: !nyquist,
    name: yName, nameLocation: "middle" as const, nameGap: 48, ...extent(bounds?.y),
  };
  const xZoom = { type: "inside" as const, xAxisIndex: bode ? [0, 1] : [0], filterMode: "none" as const };
  return {
    animation: false,
    backgroundColor: theme("--plot-bg"),
    grid: bode
      ? [{ top: 34, height: "32%", left: 83, right: 34 }, { top: "51%", bottom: 53, left: 83, right: 34 }]
      : bounds?.grid ?? { top: 34, bottom: 53, left: 83, right: 36 },
    xAxis: bode ? [{ ...xAxis, name: "", axisLabel: { show: false } }, { ...xAxis, gridIndex: 1 }] : xAxis,
    yAxis: bode ? [yAxis, { ...yAxis, type: "value", name: "相位 (°)", gridIndex: 1 }] : yAxis,
    dataZoom: nyquist ? [{ ...xZoom, yAxisIndex: [0] }] : [xZoom],
    tooltip: {
      trigger: "item", renderMode: "richText", confine: true,
      backgroundColor: theme("--bg-panel"), borderColor: theme("--divider"),
      textStyle: { color: theme("--text-primary") },
      formatter: (item) => {
        const point = Array.isArray(item) ? item[0] : item;
        const [x, y, frequency] = point.value as [number, number, number?];
        const ordinate = bode && point.seriesIndex != null && traces[point.seriesIndex]?.axis === 1 ? "相位 (°)" : yName;
        return [point.seriesName, `${xName}   ${numberLabel(x)}`, `${ordinate}   ${numberLabel(y)}`,
          frequency == null ? "" : `频率 (Hz)   ${numberLabel(frequency)}`].filter(Boolean).join("\n");
      },
    },
    series: traces.map((trace): echarts.LineSeriesOption | echarts.ScatterSeriesOption => {
      const style = { color: theme(trace.color), opacity: trace.faded ? 0.38 : 1 };
      const series = {
        name: trace.name, data: trace.data, xAxisIndex: trace.axis ?? 0, yAxisIndex: trace.axis ?? 0,
        itemStyle: style, encode: { x: 0, y: 1 }, clip: true,
      };
      return trace.scatter
        ? { ...series, type: "scatter", symbolSize: 6, z: 3 }
        : { ...series, type: "line", showSymbol: false, lineStyle: { ...style, width: 2 }, z: 4 };
    }),
  };
}

export function eisTraces(file: EisFile, view: View, rawVisible: boolean, fitVisible: boolean): Trace[] {
  const { fit } = file;
  if (view === "residual") {
    const component = (imaginary: boolean): Trace["data"] => (fit?.matched ?? [])
      .map(([index, real, imag]): [number, number] => {
        const [frequency, observedReal, observedImag] = file.points[index];
        return [frequency, imaginary ? imag - observedImag : real - observedReal];
      }).sort((a, b) => a[0] - b[0]);
    return [
      { name: "实部残差 ΔZ′", data: component(false), color: "--chi-raw", scatter: true },
      { name: "虚部残差 ΔZ″", data: component(true), color: "--chi-fit", scatter: true },
    ];
  }
  const traces: Trace[] = [];
  const included = fit && fitVisible ? new Set(fit.matched.map(([index]) => index)) : null;
  const measurements = file.points.map(([f, real, imag, magnitude, phase]) => ({
    f, xy: [real, -imag, f] as [number, number, number], magnitude, phase,
  }));
  if (rawVisible) {
    for (const faded of included ? [false, true] : [false]) {
      const group = measurements.filter((_, index) => !included || included.has(index) !== faded);
      if (!group.length) continue;
      const common = { name: faded ? "原始点 · 拟合频段外" : "原始点", color: "--chi-raw", scatter: true, faded };
      if (view === "nyquist") traces.push({ ...common, data: group.map(({ xy }) => xy) });
      else traces.push(
        { ...common, data: group.filter(({ magnitude }) => magnitude > 0).map(({ f, magnitude }) => [f, magnitude]) },
        { ...common, data: group.map(({ f, phase }) => [f, phase]), axis: 1 },
      );
    }
  }
  if (fit && fitVisible) {
    const common = { name: "拟合曲线", color: "--chi-fit" };
    if (view === "nyquist") traces.push({ ...common, data: fit.curve.map(([f, r, i]) => [r, -i, f]) });
    else traces.push(
      { ...common, data: fit.curve.map(([f, r, i]) => [f, Math.hypot(r, i)]) },
      { ...common, data: fit.curve.map(([f, r, i]) => [f, Math.atan2(i, r) * 180 / Math.PI]), axis: 1 },
    );
  }
  return traces;
}

export function ChiChart({ view, traces, filename }: { view: View; traces: Trace[]; filename: string }) {
  const element = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts>(null);
  const [size, setSize] = useState<[number, number]>([1, 1]);
  const themeMode = useAppStore((s) => s.theme);
  const [themeRevision, setThemeRevision] = useState(0);
  const showToast = useUiStore((s) => s.showToast);
  const reset = () => chart.current?.dispatchAction({ type: "dataZoom", start: 0, end: 100 });

  useEffect(() => {
    if (!element.current) return;
    const instance = echarts.init(element.current, undefined, { renderer: "canvas" });
    chart.current = instance;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      instance.resize();
      setSize([width, height]);
    });
    observer.observe(element.current);
    instance.getZr().on("dblclick", () => instance.dispatchAction({ type: "dataZoom", start: 0, end: 100 }));
    const themeObserver = new MutationObserver(() => setThemeRevision((value) => value + 1));
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => {
      observer.disconnect();
      themeObserver.disconnect();
      instance.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(makeOption(view, traces, ...size), { notMerge: true });
  }, [view, traces, size, themeMode, themeRevision]);

  async function saveFigure() {
    if (!chart.current) return;
    try {
      const url = chart.current.getDataURL({ type: "png", pixelRatio: 2 });
      const result = await exportEcFigure(filename + ".png", url);
      if (!result.cancelled) showToast(result.ok ? "success" : "error", result.ok ? "已导出 PNG" : result.error ?? "导出失败");
    } catch (error) {
      showToast("error", String(error));
    }
  }

  return (
    <div className="chi-chart">
      <div ref={element} className="chi-chart-canvas" role="img" aria-label={`${view.toUpperCase()} 曲线图`} />
      <div className="chi-chart-buttons">
        <IconButton title="复位缩放" onClick={reset}><Maximize2 size={15} /></IconButton>
        <IconButton title="导出图像 PNG" onClick={() => void saveFigure()}><ImageDown size={16} /></IconButton>
      </div>
    </div>
  );
}
