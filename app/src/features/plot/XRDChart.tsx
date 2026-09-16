/** 图谱组件(ECharts,§6.7 + §9.1)。
 *
 * 性能约束(§9):
 *  - large: true + 渐进渲染,3.5k–15k 点 <300ms
 *  - crosshair tooltip 自绘 axisPointer,禁用 ECharts 动画
 *  - 峰标签只在 showLabels 时画 markPoint,且普通态只有 2θ(§33)
 *
 * 视口(缩放窗口)的唯一真相在 plotStore.zoom,单位是**数据坐标**(2θ)。
 * 本组件只做两件事:①把 zoom 回放到图上;②把用户操作产生的 zoom 写回
 * store。**绝不在 dataZoom 事件里回读另一个图的状态** —— 那是死循环的
 * 根源。dispatchAction 不会重新触发 dataZoom 事件,链路到这里就终止了。
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

/** 背景曲线的 series 名。tooltip 与点击命中都要靠它区分主曲线与背景线。 */
const BG_SERIES = "__background__";

function XRDChartInner({ onPeakClick }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  /** 上一次回放到图上的 zoom,用于值相等短路(防环第二层) */
  const appliedZoom = useRef<string>("");
  const curve = useAnalysisStore((s) => s.curve);
  const peaks = useAnalysisStore((s) => s.peaks);
  const selectedPeak = useSelectionStore((s) => s.selectedPeakIndex);
  const selectPeak = useSelectionStore((s) => s.setSelectedPeak);
  const { mode, crosshair, showLabels, offset, showBackground, zoom } = usePlotStore();

  /** 归一化到 100 的 y 标度。曲线、背景线、峰标记三者必须用**同一个**
   * maxY,否则一拖 Offset 滑块它们就互相错位。 */
  const maxY = useMemo(() => {
    if (!curve) return 1;
    return curve.y.reduce((m, v) => (v > m ? v : m), 0) || 1;
  }, [curve]);

  // 数据只转一次;normalization/offset 纯前端(§9.2)
  const seriesData = useMemo<[number, number][] | null>(() => {
    if (!curve) return null;
    const { x, y } = curve;
    const out: [number, number][] = new Array(x.length);
    for (let i = 0; i < x.length; i++) {
      out[i] = [x[i], (y[i] / maxY) * 100 + offset];
    }
    return out;
  }, [curve, offset, maxY]);

  /** 背景曲线(§7.3 Show Background)。引擎只在 background.enabled 时
   * 才返回它,所以关闭背景扣除后这个开关是无效的 —— Inspector 会提示。 */
  const bgData = useMemo<[number, number][] | null>(() => {
    if (!curve?.background) return null;
    const { x, background } = curve;
    if (background.length !== x.length) return null;
    const out: [number, number][] = new Array(x.length);
    for (let i = 0; i < x.length; i++) {
      out[i] = [x[i], (background[i] / maxY) * 100 + offset];
    }
    return out;
  }, [curve, offset, maxY]);

  const markData = useMemo(() => {
    if (!curve || !showLabels || peaks.length === 0) return undefined;
    return peaks.map((p: PeakResult) => ({
      name: String(p.index),
      // offset 必须与 seriesData 一致:否则拖 Display 的 Offset 滑块时
      // 曲线上下移动而圆点/标签原地不动,视觉上就是一堆悬空的点
      coord: [p.position, (p.intensity / maxY) * 100 + offset],
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
  }, [curve, peaks, showLabels, selectedPeak, offset, maxY]);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    chartRef.current = chart;

    // 点击图中峰 ↔ 表格联动(§35)。
    // **peaks 必须从 store 现取**:这个 effect 只在挂载时跑一次,闭包捕获
    // 的 peaks 永远是首渲染的空数组,用闭包变量会让点击永远选不中。
    chart.on("click", (params) => {
      const current = useAnalysisStore.getState().peaks;
      if (current.length === 0) return;

      // ① 点在峰标记圆点上。这是用户最自然的操作 —— 圆点就是"峰"的视觉
      //    代表。markPoint 的 componentType 是 "markPoint" 而不是 "series",
      //    只判 series 会让点圆点完全没反应。
      if (params.componentType === "markPoint") {
        const idx = Number(params.name);
        if (Number.isInteger(idx)) {
          selectPeak(idx);
          onPeakClick?.(idx);
        }
        return;
      }

      // ② 点在曲线上:按 x 就近匹配峰位
      if (params.componentType !== "series") return;
      if (params.seriesName === BG_SERIES) return; // 背景线不参与选峰
      if (!Array.isArray(params.value)) return;
      const xClick = params.value[0] as number;
      let best = -1;
      let bestD = Infinity;
      for (const p of current) {
        const d = Math.abs(p.position - xClick);
        if (d < bestD) { bestD = d; best = p.index; }
      }
      if (best >= 0 && bestD < 1.0) {
        selectPeak(best);
        onPeakClick?.(best);
      }
    });

    // 双击 Fit All(§6.7)。
    // **必须用 zrender 的 dblclick,不能用 chart.on("dblclick")**:平移用的
    // 是 moveOnMouseMove,那一动会被当成 pan,ECharts 层级的 dblclick 往往
    // 不派发。
    const onDblClick = () => usePlotStore.getState().fitAll();
    const zr = chart.getZr();
    zr.on("dblclick", onDblClick);

    // 状态栏光标读数:用 zrender 全局事件 + convertFromPixel 反算数据坐标
    const onMove = (e: { offsetX: number; offsetY: number }) => {
      const store = usePlotStore.getState();
      if (!store.crosshair) return;
      const point = chart.convertFromPixel({ seriesIndex: 0 }, [e.offsetX, e.offsetY]);
      if (point) store.setCursor({ x: point[0], y: point[1] });
    };
    const onLeave = () => usePlotStore.getState().setCursor(null);
    zr.on("mousemove", onMove);
    zr.on("globalout", onLeave);

    // 用户操作(滚轮 / 拖动)产生的缩放 → 写回 store,底部范围框据此跟随。
    // 写回时用 rAF 合并:一次滚轮会连续触发多个 dataZoom 事件。
    let raf = 0;
    const onZoom = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        const opt = chart.getOption() as {
          dataZoom?: { startValue?: number; endValue?: number }[];
        };
        const dz = opt.dataZoom?.[0];
        if (dz?.startValue == null || dz?.endValue == null) return;
        const next = { from: dz.startValue, to: dz.endValue };
        appliedZoom.current = JSON.stringify(next); // 标记为本图自己产生的
        usePlotStore.getState().setZoom(next);
      });
    };
    chart.on("dataZoom", onZoom);

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
      zr.off("dblclick", onDblClick);
      zr.off("mousemove", onMove);
      zr.off("globalout", onLeave);
      chart.dispose();
      chartRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const xDomain = usePlotStore((s) => s.xDomain);

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
          formatter: (ps: { seriesName?: string; value: [number, number] }[]) => {
            // 背景线开着时 axis 触发会给出两条,取主曲线那条 —— 否则
            // crosshair 读数会显示背景值,与用户读到的东西对不上
            const main = ps.find((p) => p.seriesName !== BG_SERIES) ?? ps[0];
            const v = main?.value;
            if (!v) return "";
            return `<span class=tabular>2θ ${v[0].toFixed(3)}°</span><br/>` +
              `<span class=tabular>I ${Math.round(v[1]).toLocaleString()}</span>`;
          },
        },
        xAxis: {
          type: "value",
          // x 值域必须从数据推导。此前硬编码 min:10/max:80,任何不在
          // 10–80° 的实测谱都会被裁掉,而且 Fit All 也无从谈起。
          min: xDomain ? xDomain[0] : undefined,
          max: xDomain ? xDomain[1] : undefined,
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
          ...(bgData && showBackground
            ? [
                {
                  type: "line" as const,
                  name: BG_SERIES,
                  showSymbol: false,
                  sampling: "lttb" as const,
                  large: true,
                  largeThreshold: 2000,
                  // silent:不参与 tooltip 与点击命中。否则点击会选中背景线
                  // 上的点导致选峰错位。
                  silent: true,
                  lineStyle: {
                    color: cssVar("--text-tertiary"),
                    width: 1,
                    type: "dashed" as const,
                    opacity: 0.55,
                  },
                  data: bgData,
                },
              ]
            : []),
          {
            type: "line",
            name: "spectrum",
            showSymbol: false,
            sampling: "lttb",
            large: true,
            largeThreshold: 2000,
            lineStyle: { color: cssVar("--plot-line"), width: 1.5 },
            data: seriesData ?? [],
            // 必须**永远**传对象:setOption 用的是 notMerge:false 合并模式,
            // ECharts 会忽略值为 undefined 的项 —— 传 undefined 清不掉已有的
            // markPoint,取消勾选「峰标签」后旧圆点与标签会残留(幽灵标记)。
            // 用空数组才是真正的"清空"。
            markPoint: {
              symbol: "circle",
              data: (markData ?? []) as never,
              silent: false,
            },
          },
        ],
      },
      { notMerge: true, lazyUpdate: true },
    );
    // curve 必须在依赖里:同一份 LFP mock 在切换数据集时 x/y 完全相同,
    // seriesData 的 useMemo 会返回**同一个引用**(依赖 curve/offset/maxY,
    // maxY 也相同),effect 就不会重跑,图表根本不重绘 —— 而且因为
    // setXDomain/setZoom(null) 让 xDomain 引用变了却仍是相同数值,
    // 看起来就像"切换数据集后缩放窗口残留"。
    // 依赖 curve 本身(对象引用每次 loadDataset 都是新的)才能真正重绘。
  }, [curve, seriesData, bgData, markData, crosshair, mode, peaks, selectPeak, onPeakClick, showBackground, xDomain]);

  // 把 plotStore.zoom 回放到图上。
  // 值相等短路:本图自己产生的 zoom 已经 appliedZoom 标记过,不必再回放,
  // 也避免 dispatchAction → 重渲染 → 再进这里。
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !xDomain) return;
    const target = zoom ?? { from: xDomain[0], to: xDomain[1] };
    const key = JSON.stringify(target);
    if (key === appliedZoom.current) return;
    appliedZoom.current = key;
    chart.dispatchAction({
      type: "dataZoom",
      startValue: target.from,
      endValue: target.to,
    });
  }, [zoom, xDomain]);

  // 选中峰变化:平移视口到峰附近并高亮(§35)。
  // 走 setZoom(数据坐标)而不是直接 dispatchAction,这样底部范围框同步跟随。
  useEffect(() => {
    if (selectedPeak == null) return;
    const peak = peaks.find((p) => p.index === selectedPeak);
    const d = usePlotStore.getState().xDomain;
    if (!peak || !d) return;
    const span = 4;
    usePlotStore.getState().setZoom({
      from: Math.max(d[0], peak.position - span),
      to: Math.min(d[1], peak.position + span),
    });
  }, [selectedPeak, peaks]);

  return <div ref={ref} className="h-full w-full" />;
}

export const XRDChart = memo(XRDChartInner);
