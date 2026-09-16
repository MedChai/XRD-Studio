/** 底部通栏范围框(用户要求:下方加入滑块,使其能滑动看峰)。
 *
 * 形态:上半是整条谱的缩略预览,下半是可拖动的范围框(ECharts 的 slider
 * 型 dataZoom)。拖动范围框 → 主图跟随;主图滚轮缩放 → 这里跟随。
 *
 * 为什么用独立的 ECharts 实例而不是 ECharts 内置的 slider dataZoom:
 * 内置的那个画在图表 grid **内部**,只能跨图表宽度;用户要的是跨整个窗口
 * 下方的通栏控件。独立实例既拿到通栏的位置自由,又能继续借用 ECharts
 * 自己那套 dataZoom 语义。
 *
 * 同步与防环:
 *   plotStore.zoom 是唯一真相(数据坐标)。本组件只做两件事 —— ①把 zoom
 *   回放到范围框;②把用户拖动产生的 zoom 写回 store。**绝不在 dataZoom
 *   事件里回读主图状态**,环就不存在。值相等短路 + rAF 合并处理连续事件。
 */

import { memo, useEffect, useMemo, useRef } from "react";
import * as echarts from "echarts";
import { useAnalysisStore, usePlotStore } from "../../stores";

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function RangeOverviewInner() {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const appliedZoom = useRef<string>("");
  const curve = useAnalysisStore((s) => s.curve);
  const { zoom, xDomain } = usePlotStore();

  /** 缩略图**不加 offset、不做 offset 平移** —— 它表达的是"整条谱长什么样",
   * 不该跟着 Display 的纵向偏移一起动。 */
  const thumbData = useMemo<[number, number][] | null>(() => {
    if (!curve) return null;
    const { x, y } = curve;
    const maxY = y.reduce((m, v) => (v > m ? v : m), 0) || 1;
    const out: [number, number][] = new Array(x.length);
    for (let i = 0; i < x.length; i++) out[i] = [x[i], (y[i] / maxY) * 100];
    return out;
  }, [curve]);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    chartRef.current = chart;

    let raf = 0;
    chart.on("dataZoom", () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        const opt = chart.getOption() as {
          dataZoom?: { startValue?: number; endValue?: number }[];
        };
        const dz = opt.dataZoom?.[0];
        if (dz?.startValue == null || dz?.endValue == null) return;
        const next = { from: dz.startValue, to: dz.endValue };
        appliedZoom.current = JSON.stringify(next);
        usePlotStore.getState().setZoom(next);
      });
    });

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !thumbData) return;
    const d = xDomain ?? [thumbData[0][0], thumbData[thumbData.length - 1][0]];
    chart.setOption(
      {
        animation: false,
        backgroundColor: "transparent",
        grid: { top: 4, bottom: 22, left: 2, right: 2 },
        xAxis: {
          type: "value",
          min: d[0],
          max: d[1],
          show: false,
        },
        yAxis: { type: "value", show: false, min: 0, max: 105 },
        dataZoom: [
          {
            type: "slider",
            xAxisIndex: 0,
            filterMode: "none",
            height: 18,
            bottom: 2,
            brushSelect: false,
            // 缩略轨迹:一条细线,不喧宾夺主
            showDataShadow: true,
            dataBackground: {
              lineStyle: { color: cssVar("--text-tertiary"), width: 1, opacity: 0.6 },
              areaStyle: { color: cssVar("--divider"), opacity: 0.5 },
            },
            borderColor: cssVar("--divider"),
            fillerColor: "color-mix(in srgb, var(--accent) 12%, transparent)",
            handleStyle: {
              color: cssVar("--bg-panel"),
              borderColor: cssVar("--accent"),
            },
            moveHandleStyle: { color: cssVar("--accent"), opacity: 0.35 },
            textStyle: { color: cssVar("--text-tertiary"), fontSize: 10 },
          },
        ],
        series: [
          {
            type: "line",
            showSymbol: false,
            sampling: "lttb",
            large: true,
            largeThreshold: 1000,
            silent: true,
            lineStyle: { color: cssVar("--plot-axis"), width: 1 },
            areaStyle: { color: cssVar("--divider"), opacity: 0.6 },
            data: thumbData,
          },
        ],
      },
      { notMerge: true, lazyUpdate: true },
    );
    // 首次渲染后把当前 zoom 回放上去
    const target = zoom ?? { from: d[0], to: d[1] };
    appliedZoom.current = JSON.stringify(target);
    chart.dispatchAction({
      type: "dataZoom",
      startValue: target.from,
      endValue: target.to,
    });
    // zoom 变化时只回放,不重建 option(避免拖动时整图重绘)
  }, [thumbData, xDomain]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const chart = chartRef.current;
    const d = usePlotStore.getState().xDomain;
    if (!chart || !d) return;
    const target = zoom ?? { from: d[0], to: d[1] };
    const key = JSON.stringify(target);
    if (key === appliedZoom.current) return;
    appliedZoom.current = key;
    chart.dispatchAction({
      type: "dataZoom",
      startValue: target.from,
      endValue: target.to,
    });
  }, [zoom]);

  // **始终渲染容器 div,不要在没有曲线时 return null** —— 本组件的初始化
  // effect 依赖为空数组,只在挂载时跑一次。若首次挂载时因为没曲线而没有
  // 渲染出 div,ref.current 就是 null,effect 直接返回且永远不会重试,
  // 范围框会永久不出现。是否占位由父组件决定。
  return <div ref={ref} className="h-full w-full" />;
}

export const RangeOverview = memo(RangeOverviewInner);
