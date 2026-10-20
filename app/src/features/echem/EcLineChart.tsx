/** 电化学折线/堆叠面积图(与 TrendPanel 同一做法:ECharts + cssVar 主题)。
 *
 *  这里不复用 XRDChart —— 那是谱图专用(x/y 信封、缩放、十字光标全绑
 *  在 XRD 数据模型上);电化学需要的是"几条趋势线 + 双 y 轴 + 堆叠面积",
 *  一个受控的轻量封装更直接。主题色仍走设计 token(cssVar),不硬编码。
 *
 *  交互(与 XRDChart 手感对齐):滚轮在光标处缩放横纵轴,拖拽平移
 *  (横向拖动平移 x、纵向拖动平移 y),双击复位到全范围(= Fit All)。
 */

import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import { Download } from "lucide-react";
import { exportEcFigure } from "../../services/exports";
import { useUiStore } from "../../stores";
import { t } from "../../i18n";

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export interface EcSeries {
  name: string;
  /** [x, y] 点列;y 为 null 的点不连线(缺失 ≠ 0)。 */
  data: [number, number | null][];
  color?: string;
  yAxisIndex?: 0 | 1;
  /** area = 堆叠面积(归因三分量用);line = 普通折线。 */
  kind?: "line" | "area";
  dashed?: boolean;
  /** 空心点(不完整末圈等异常标记)。 */
  hollow?: boolean;
}

export function EcLineChart({
  series,
  xName,
  y1Name,
  y2Name,
  height = 240,
  y2Percent,
  exportName,
}: {
  series: EcSeries[];
  xName: string;
  y1Name: string;
  y2Name?: string;
  height?: number;
  /** 第二轴是 0–1 比例(CE)时按百分比格式化。 */
  y2Percent?: boolean;
  /** 给了就在图右上角渲染 PNG 导出按钮,值为文件名片段(不含扩展名)。 */
  exportName?: string;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  /** 当前 echarts 实例 —— 导出按钮要拿它 getDataURL(effect 局部变量拿不到)。 */
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const chart = echarts.init(el, undefined, { renderer: "canvas" });
    chartRef.current = chart;
    chart.setOption(
      {
        animation: false,
        backgroundColor: "transparent",
        grid: { top: 28, bottom: 40, left: 64, right: y2Name ? 64 : 24 },
        legend: {
          show: series.length > 1,
          top: 0,
          itemWidth: 14,
          itemHeight: 8,
          textStyle: { color: cssVar("--text-secondary"), fontSize: 11 },
        },
        tooltip: {
          trigger: "axis",
          backgroundColor: cssVar("--bg-elevated"),
          borderColor: cssVar("--divider"),
          textStyle: { color: cssVar("--text-primary"), fontSize: 11 },
          valueFormatter: (v: unknown) =>
            typeof v === "number" ? (Math.abs(v) >= 100 ? v.toFixed(1) : v.toPrecision(4)) : "—",
        },
        // 两个 inside dataZoom(x/y 各一)共享一个 RoamController:滚轮在
        // 光标处缩放两轴(区域缩放)、拖拽按各自轴向平移,双击复位
        // (见下方 zr dblclick)。filterMode none —— 缩放一条轴不引起
        // 另一条轴按可见数据重算范围,横纵都保持手调值。
        dataZoom: [
          { type: "inside", xAxisIndex: 0, filterMode: "none" },
          { type: "inside", yAxisIndex: 0, filterMode: "none" },
        ],
        xAxis: {
          type: "value",
          // scale:true 不强制含 0:电压/圈数轴从数据范围起,
          // 否则横轴总是从 0 开始(与 y 轴行为一致)。
          scale: true,
          name: xName,
          nameLocation: "middle",
          nameGap: 26,
          nameTextStyle: { color: cssVar("--text-tertiary"), fontSize: 10 },
          axisLine: { lineStyle: { color: cssVar("--plot-axis") } },
          axisTick: { show: false },
          axisLabel: { color: cssVar("--text-secondary"), fontSize: 10 },
          splitLine: { show: false },
        },
        yAxis: [
          {
            type: "value",
            scale: true,
            name: y1Name,
            nameTextStyle: { color: cssVar("--text-tertiary"), fontSize: 10 },
            axisLine: { show: false },
            axisTick: { show: false },
            splitLine: { lineStyle: { color: cssVar("--divider") } },
            axisLabel: {
              color: cssVar("--text-secondary"),
              fontSize: 10,
              formatter: (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : String(v)),
            },
          },
          ...(y2Name
            ? [
                {
                  type: "value" as const,
                  scale: true,
                  name: y2Name,
                  nameTextStyle: { color: cssVar("--text-tertiary"), fontSize: 10 },
                  axisLine: { show: false },
                  axisTick: { show: false },
                  splitLine: { show: false },
                  axisLabel: {
                    color: cssVar("--text-secondary"),
                    fontSize: 10,
                    ...(y2Percent
                      ? { formatter: (v: number) => `${(v * 100).toFixed(0)}%` }
                      : {}),
                  },
                },
              ]
            : []),
        ],
        series: series.map((s) => ({
          name: s.name,
          type: "line" as const,
          yAxisIndex: s.yAxisIndex ?? 0,
          data: s.data,
          connectNulls: false,
          symbol: s.hollow ? "emptyCircle" : "none",
          showSymbol: s.hollow ? true : false,
          symbolSize: s.hollow ? 7 : 0,
          step: false,
          smooth: false,
          lineStyle: {
            color: s.color ?? cssVar("--accent"),
            width: s.dashed ? 1.2 : 1.6,
            type: s.dashed ? ("dashed" as const) : ("solid" as const),
          },
          itemStyle: {
            color: s.hollow ? "transparent" : (s.color ?? cssVar("--accent")),
            borderColor: s.color ?? cssVar("--text-tertiary"),
            borderWidth: 1.5,
          },
          ...(s.kind === "area"
            ? {
                stack: "total",
                areaStyle: { opacity: 0.55 },
                emphasis: { focus: "series" as const },
              }
            : {}),
        })),
      },
      { notMerge: true },
    );
    // 双击复位(= XRDChart 的 Fit All):两个 dataZoom 各自拉回 0–100%。
    // 监听挂 zrender 上;dispose 时随 chart 一起回收,无需单独解绑。
    chart.getZr().on("dblclick", () => {
      chart.dispatchAction({ type: "dataZoom", dataZoomIndex: 0, start: 0, end: 100 });
      chart.dispatchAction({ type: "dataZoom", dataZoomIndex: 1, start: 0, end: 100 });
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el);
    return () => {
      ro.disconnect();
      chartRef.current = null;
      chart.dispose();
    };
  }, [series, xName, y1Name, y2Name, y2Percent]);

  async function handleExport() {
    const chart = chartRef.current;
    if (!chart || !exportName) return;
    const dataUrl = chart.getDataURL({
      type: "png",
      pixelRatio: 2,
      backgroundColor: cssVar("--bg-panel"),
    });
    const r = await exportEcFigure(exportName, dataUrl);
    if (r.cancelled) return;
    useUiStore
      .getState()
      .showToast(
        r.ok ? "success" : "error",
        r.ok ? t.command.exportSuccess : (r.error ?? t.command.exportPending),
      );
  }

  return (
    // 外层只做定位(按钮要压在图上);echarts.init 独占内层 ref div,
    // 不和 React 管理的按钮节点混在同一个容器里。
    <div className="relative w-full" style={{ height }}>
      <div ref={ref} className="h-full w-full" />
      {exportName && (
        <button
          className="absolute right-1 top-1 z-10 rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)]/85 p-1 text-[var(--text-secondary)] hover:bg-[var(--hover)]"
          title={t.echem.exportFigure}
          onClick={() => void handleExport()}
        >
          <Download size={13} />
        </button>
      )}
    </div>
  );
}
