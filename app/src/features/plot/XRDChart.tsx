/** 图谱组件(ECharts,§6.7 + §9.1)。
 *
 * 性能约束(§9):
 *  - large: true + 渐进渲染,3.5k–15k 点 <300ms
 *  - crosshair tooltip 自绘 axisPointer,禁用 ECharts 动画
 *  - 峰标签只在 showLabels 时画 markPoint,且普通态只有 2θ(§33)
 */

import { memo, useEffect, useMemo, useRef } from "react";
import * as echarts from "echarts";
import type { PeakResult } from "../../types/engine";
import {
  useAnalysisStore,
  usePlotStore,
  useSelectionStore,
} from "../../stores";

interface Props {
  onPeakClick?: (index: number) => void;
}

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function XRDChartInner({ onPeakClick }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const curve = useAnalysisStore((s) => s.curve);
  const peaks = useAnalysisStore((s) => s.peaks);
  const selectedPeak = useSelectionStore((s) => s.selectedPeakIndex);
  const selectPeak = useSelectionStore((s) => s.setSelectedPeak);
  const { mode, crosshair, showLabels, offset } = usePlotStore();

  // 数据只转一次;normalization/offset 纯前端(§9.2)
  const seriesData = useMemo<[number, number][] | null>(() => {
    if (!curve) return null;
    const { x, y } = curve;
    const out: [number, number][] = new Array(x.length);
    const maxY = y.reduce((m, v) => (v > m ? v : m), 0) || 1;
    for (let i = 0; i < x.length; i++) {
      let v = (y[i] / maxY) * 100 + offset;
      out[i] = [x[i], v];
    }
    return out;
  }, [curve, offset]);

  const markData = useMemo(() => {
    if (!curve || !showLabels || peaks.length === 0) return undefined;
    const maxY = curve.y.reduce((m, v) => (v > m ? v : m), 1);
    return peaks.map((p: PeakResult) => ({
      name: String(p.index),
      coord: [p.position, (p.intensity / maxY) * 100],
      value: p.position.toFixed(2) + "°",
      itemStyle: {
        color:
          selectedPeak === p.index ? cssVar("--accent") : "transparent",
      },
      label: {
        show: true,
        formatter: p.position.toFixed(2) + "°",
        position: "top" as const,
        distance: 8,
        fontSize: 10,
        color:
          selectedPeak === p.index
            ? cssVar("--accent")
            : cssVar("--text-secondary"),
      },
      symbol: "circle",
      symbolSize: selectedPeak === p.index ? 7 : 4,
    }));
  }, [curve, peaks, showLabels, selectedPeak]);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    chartRef.current = chart;

    // 点击图中峰 ↔ 表格联动(§35):在 grid 坐标上命中测试峰位置
    chart.on("click", (params) => {
      if (params.componentType === "series" && Array.isArray(params.value)) {
        const xClick = params.value[0] as number;
        let best = -1;
        let bestD = Infinity;
        for (const p of peaks) {
          const d = Math.abs(p.position - xClick);
          if (d < bestD) { bestD = d; best = p.index; }
        }
        if (best >= 0 && bestD < 1.0) {
          selectPeak(best);
          onPeakClick?.(best);
        }
      }
    });

    // 状态栏光标读数:用 zrender 全局事件 + convertFromPixel 反算数据坐标
    const zr = chart.getZr();
    const onMove = (e: { offsetX: number; offsetY: number }) => {
      const store = usePlotStore.getState();
      if (!store.crosshair) return;
      const point = chart.convertFromPixel({ seriesIndex: 0 }, [e.offsetX, e.offsetY]);
      if (point) store.setCursor({ x: point[0], y: point[1] });
    };
    const onLeave = () => usePlotStore.getState().setCursor(null);
    zr.on("mousemove", onMove);
    zr.on("globalout", onLeave);

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      zr.off("mousemove", onMove);
      zr.off("globalout", onLeave);
      chart.dispose();
      chartRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // peaks 变化时重新绑 click(peaks 是闭包变量)
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.setOption(
      {
        animation: false,
        backgroundColor: "transparent",
        grid: { top: 28, bottom: 34, left: 52, right: 20 },
        tooltip: {
          show: crosshair,
          trigger: "axis",
          axisPointer: {
            type: "cross",
            lineStyle: { color: cssVar("--text-tertiary"), width: 1, type: "dashed" },
            label: {
              backgroundColor: cssVar("--bg-elevated"),
              color: cssVar("--text-primary"),
              borderColor: cssVar("--divider"),
              borderWidth: 1,
              formatter: (p: { value: number | number[] }) => {
                const v = Array.isArray(p.value) ? p.value : p.value;
                return typeof v === "number" ? v.toFixed(3) : String(v);
              },
            },
          },
          backgroundColor: cssVar("--bg-elevated"),
          borderColor: cssVar("--divider"),
          textStyle: { color: cssVar("--text-primary"), fontSize: 11 },
          formatter: (ps: { value: [number, number] }[]) => {
            const v = ps[0]?.value;
            if (!v) return "";
            return `<span class=tabular>2θ ${v[0].toFixed(3)}°</span><br/>` +
              `<span class=tabular>I ${Math.round(v[1]).toLocaleString()}</span>`;
          },
        },
        xAxis: {
          type: "value",
          min: 10,
          max: 80,
          axisLine: { lineStyle: { color: cssVar("--plot-axis") } },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: { color: cssVar("--text-secondary"), fontSize: 10 },
          name: "2θ (°)",
          nameLocation: "end",
          nameGap: 8,
          nameTextStyle: { color: cssVar("--text-tertiary"), fontSize: 10 },
        },
        yAxis: {
          type: "value",
          axisLine: { show: false },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: { color: cssVar("--text-secondary"), fontSize: 10 },
          name: mode === "stacked" ? "Stacked" : "Int. (norm)",
          nameTextStyle: { color: cssVar("--text-tertiary"), fontSize: 10 },
        },
        dataZoom: [
          { type: "inside", xAxisIndex: 0, filterMode: "none", zoomOnMouseWheel: true, moveOnMouseMove: true },
        ],
        series: [
          {
            type: "line",
            showSymbol: false,
            sampling: "lttb",
            large: true,
            largeThreshold: 2000,
            lineStyle: { color: cssVar("--plot-line"), width: 1.5 },
            data: seriesData ?? [],
            markPoint: markData
              ? { symbol: "circle", data: markData as never, silent: false }
              : undefined,
          },
        ],
      },
      { notMerge: false, lazyUpdate: true },
    );
  }, [seriesData, markData, crosshair, mode, peaks, selectPeak, onPeakClick]);

  // 选中峰变化:平移视口到峰附近并高亮(§35)
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || selectedPeak == null || !curve) return;
    const peak = peaks.find((p) => p.index === selectedPeak);
    if (!peak) return;
    const span = 4;
    chart.dispatchAction({
      type: "dataZoom",
      startValue: Math.max(curve.x[0], peak.position - span),
      endValue: Math.min(curve.x[curve.x.length - 1], peak.position + span),
    });
  }, [selectedPeak, peaks, curve]);

  return <div ref={ref} className="h-full w-full" />;
}

export const XRDChart = memo(XRDChartInner);
