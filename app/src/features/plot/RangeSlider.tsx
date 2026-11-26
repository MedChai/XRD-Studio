import { useEffect, useRef } from "react";
import * as echarts from "echarts";

export type SliderRange = { from: number; to: number };
export type SliderPoint = [number, number | null];
const defaultInsets = { left: 8, right: 8 };

function sameRange(left: SliderRange | null, right: SliderRange) {
  return left?.from === right.from && left.to === right.to;
}

function showRange(chart: echarts.ECharts, range: SliderRange) {
  chart.dispatchAction({ type: "dataZoom", startValue: range.from, endValue: range.to }, { silent: true });
}

export function RangeSlider({
  min, max, range, points, onChange, onCommit, wholeNumbers = false, compact = false, insets = defaultInsets,
}: {
  min: number;
  max: number;
  range: SliderRange;
  points: SliderPoint[];
  onChange: (range: SliderRange) => void;
  onCommit?: (range: SliderRange) => void;
  wholeNumbers?: boolean;
  compact?: boolean;
  insets?: { left: number; right: number };
}) {
  const element = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const applied = useRef<SliderRange | null>(null);
  const settings = useRef({ range, onChange, onCommit, wholeNumbers });

  useEffect(() => {
    settings.current = { range, onChange, onCommit, wholeNumbers };
  }, [range, onChange, onCommit, wholeNumbers]);

  useEffect(() => {
    const node = element.current;
    if (!node) return;
    const chart = echarts.init(node, undefined, { renderer: "canvas" });
    chartRef.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(node);

    let frame = 0;
    let pointer: number | null = null;
    let changedDuringDrag = false;
    const update = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        const option = chart.getOption() as { dataZoom?: { startValue?: number; endValue?: number }[] };
        const { startValue, endValue } = option.dataZoom?.[0] ?? {};
        if (startValue == null || endValue == null) return;
        const raw = { from: startValue, to: endValue };
        const { wholeNumbers, onChange, onCommit } = settings.current;
        const next = wholeNumbers ? { from: Math.round(raw.from), to: Math.round(raw.to) } : raw;
        const changed = !sameRange(applied.current, next);
        if (changed) {
          applied.current = next;
          onChange(next);
        }
        if (pointer != null) {
          changedDuringDrag ||= changed;
          return;
        }
        // Keep the handle moving freely while dragging, then snap to the selected cycle.
        if (!sameRange(raw, next)) showRange(chart, next);
        if (changed || changedDuringDrag) onCommit?.(next);
        changedDuringDrag = false;
      });
    };
    const start = (event: PointerEvent) => {
      if (event.button === 0 && pointer == null) pointer = event.pointerId;
    };
    const finish = () => {
      if (pointer == null) return;
      pointer = null;
      update();
    };
    const end = (event: PointerEvent) => { if (event.pointerId === pointer) finish(); };
    chart.on("dataZoom", update);
    node.addEventListener("pointerdown", start);
    window.addEventListener("pointerup", end, true);
    window.addEventListener("pointercancel", end, true);
    window.addEventListener("blur", finish);

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      node.removeEventListener("pointerdown", start);
      window.removeEventListener("pointerup", end, true);
      window.removeEventListener("pointercancel", end, true);
      window.removeEventListener("blur", finish);
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const target = settings.current.range;
    const theme = getComputedStyle(document.documentElement);
    const color = (name: string) => theme.getPropertyValue(name).trim();
    const shadow = {
      lineStyle: { color: color("--text-tertiary"), width: 1, opacity: 0.6 },
      areaStyle: { color: color("--divider"), opacity: 0.5 },
    };
    chart.setOption({
      animation: false,
      backgroundColor: "transparent",
      grid: { top: compact ? 0 : 4, bottom: compact ? 0 : 22, ...insets },
      xAxis: { type: "value", min, max, show: false },
      yAxis: { type: "value", show: false, min: 0, max: 105 },
      dataZoom: [{
        type: "slider",
        xAxisIndex: 0,
        filterMode: "none",
        height: compact ? 16 : 18,
        bottom: compact ? 4 : 2,
        ...insets,
        brushSelect: false,
        realtime: true,
        ...(wholeNumbers ? { throttle: 0 } : {}),
        startValue: target.from,
        endValue: target.to,
        labelPrecision: wholeNumbers ? 0 : undefined,
        showDataShadow: true,
        dataBackground: shadow,
        ...(compact ? { selectedDataBackground: shadow, borderRadius: 4, backgroundColor: "transparent" } : {}),
        borderColor: color("--divider"),
        fillerColor: color("--selected"),
        handleIcon: "path://M-2 0H2Q4 0 4 2V16Q4 18 2 18H-2Q-4 18 -4 16V2Q-4 0 -2 0Z",
        handleStyle: { color: color("--bg-panel"), borderColor: color(compact ? "--text-tertiary" : "--accent") },
        emphasis: { handleStyle: { borderColor: color("--accent") } },
        moveHandleStyle: { color: color("--accent"), opacity: 0.35 },
        textStyle: { color: color("--text-tertiary"), fontSize: 10 },
      }],
      series: [{
        type: "line",
        showSymbol: false,
        sampling: "lttb",
        large: true,
        largeThreshold: 1000,
        silent: true,
        lineStyle: { color: color("--plot-axis"), width: 1, opacity: compact ? 0 : 1 },
        areaStyle: { color: color("--divider"), opacity: compact ? 0 : 0.6 },
        data: points,
      }],
    }, { notMerge: true });
    applied.current = target;
  }, [min, max, points, wholeNumbers, compact, insets]);

  useEffect(() => {
    const chart = chartRef.current;
    if (chart && !sameRange(applied.current, range)) {
      applied.current = range;
      showRange(chart, range);
    }
  }, [range]);

  return <div ref={element} className="h-full w-full" />;
}
