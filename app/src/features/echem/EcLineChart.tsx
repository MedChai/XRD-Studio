/** 电化学折线/堆叠面积图(与 TrendPanel 同一做法:ECharts + cssVar 主题)。
 *
 *  这里不复用 XRDChart —— 那是谱图专用(x/y 信封、缩放、十字光标全绑
 *  在 XRD 数据模型上);电化学需要的是"几条趋势线 + 双 y 轴 + 堆叠面积",
 *  一个受控的轻量封装更直接。主题色仍走设计 token(cssVar),不硬编码。
 *
 *  交互(与 XRDChart 手感对齐):滚轮在光标处缩放横纵轴,拖拽平移
 *  (横向拖动平移 x、纵向拖动平移 y),双击复位到全范围(= Fit All)。
 */

import { useEffect, useRef, type RefObject } from "react";
import * as echarts from "echarts";
import { Download } from "lucide-react";
import { exportEcFigure, exportFigureSvg } from "../../services/exports";
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
  recordNumbers?: (number | null)[];
}

export function EcLineChart({
  series,
  xName,
  y1Name,
  y2Name,
  height = 240,
  y2Percent,
  exportName,
  formatX,
  onRecord,
  focusedPoint,
  onX,
  selectedRange,
  figureMetadata,
  xMinInterval,
}: {
  series: EcSeries[];
  xName: string;
  y1Name: string;
  y2Name?: string;
  height?: number | string;
  /** 第二轴是 0–1 比例(CE)时按百分比格式化。 */
  y2Percent?: boolean;
  /** 给了就在图右上角渲染 PNG 导出按钮,值为文件名片段(不含扩展名)。 */
  exportName?: string;
  formatX?: (value: number) => string;
  onRecord?: (number: number, seriesName: string) => void;
  focusedPoint?: { x: number; y: [number | null, (number | null)?] } | null;
  onX?: (x: number) => void;
  selectedRange?: [number, number];
  figureMetadata?: Record<string, unknown>;
  xMinInterval?: number;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  /** 当前 echarts 实例 —— 导出按钮要拿它 getDataURL(effect 局部变量拿不到)。 */
  const chartRef = useRef<echarts.ECharts | null>(null);
  const interaction = useRef({ series, onRecord, onX });

  useEffect(() => {
    interaction.current = { series, onRecord, onX };
  }, [series, onRecord, onX]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const chart = echarts.init(el, undefined, { renderer: "canvas" });
    chartRef.current = chart;

    chart.getZr().on("dblclick", () => {
      chart.dispatchAction({ type: "dataZoom", dataZoomIndex: 0, start: 0, end: 100 });
      chart.dispatchAction({ type: "dataZoom", dataZoomIndex: 1, start: 0, end: 100 });
    });
    chart.getZr().on("click", (event) => {
      const { series, onRecord, onX } = interaction.current;
      const mouse: [number, number] = [event.offsetX, event.offsetY];
      if (!chart.containPixel({ gridIndex: 0 }, mouse)) return;
      if (onX) {
        const [x] = chart.convertFromPixel({ xAxisIndex: 0, yAxisIndex: 0 }, mouse);
        if (Number.isFinite(x)) onX(x);
        return;
      }
      if (!onRecord) return;
      let nearest: { record: number; name: string } | undefined;
      let best = 24 ** 2;
      for (const item of series) {
        if (!item.recordNumbers) continue;
        const axes = { xAxisIndex: 0, yAxisIndex: item.yAxisIndex ?? 0 };
        const [x, y] = chart.convertFromPixel(axes, mouse);
        const [xAtNextPixel, yAtNextPixel] = chart.convertFromPixel(axes, [mouse[0] + 1, mouse[1] + 1]);
        const dx = Math.abs(xAtNextPixel - x);
        const dy = Math.abs(yAtNextPixel - y);
        if (!dx || !dy) continue;
        for (const [index, [px, py]] of item.data.entries()) {
          const record = item.recordNumbers?.[index];
          if (py == null || record == null) continue;
          const distance = ((px - x) / dx) ** 2 + ((py - y) / dy) ** 2;
          if (distance < best) {
            best = distance;
            nearest = { record, name: item.name };
          }
        }
      }
      if (nearest) onRecord(nearest.record, nearest.name);
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el);
    return () => {
      ro.disconnect();
      chartRef.current = null;
      chart.dispose();
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.setOption(
      {
        animation: false,
        backgroundColor: "transparent",
        grid: { top: series.length > 1 ? 48 : 28, bottom: 40, left: 64, right: y2Name ? 64 : 24 },
        legend: {
          show: series.length > 1,
          type: "scroll" as const,
          left: 36,
          right: 20,
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
          ...(formatX ? { axisPointer: {
            label: { formatter: ({ value }: { value: unknown }) => formatX(Number(value)) },
          } } : {}),
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
          minInterval: xMinInterval,
          name: xName,
          nameLocation: "middle",
          nameGap: 26,
          nameTextStyle: { color: cssVar("--text-tertiary"), fontSize: 10 },
          axisLine: { lineStyle: { color: cssVar("--plot-axis") } },
          axisTick: { show: false },
          axisLabel: {
            color: cssVar("--text-secondary"), fontSize: 10,
            ...(formatX ? { formatter: formatX } : {}),
          },
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
        series: series.map((s, index) => ({
          name: s.name,
          type: "line" as const,
          yAxisIndex: s.yAxisIndex ?? 0,
          data: s.data,
          connectNulls: false,
          symbol: s.hollow ? "emptyCircle" : s.data.length === 1 ? "circle" : "none",
          showSymbol: !!s.hollow || s.data.length === 1,
          symbolSize: s.hollow || s.data.length === 1 ? 7 : 0,
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
          ...(index === 0 && selectedRange ? { markArea: {
            silent: true, itemStyle: { color: cssVar("--accent"), opacity: 0.12 },
            data: [[{ xAxis: selectedRange[0] }, { xAxis: selectedRange[1] }]],
          } } : {}),
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
  }, [series, xName, y1Name, y2Name, y2Percent, formatX, selectedRange, xMinInterval]);

  useEffect(() => {
    if (focusedPoint === undefined) return;
    chartRef.current?.setOption({
      series: series.map((s) => {
        const y = focusedPoint?.y[s.yAxisIndex ?? 0];
        return {
          markPoint: {
            symbol: "circle",
            symbolSize: 10,
            label: { show: false },
            itemStyle: { color: s.color ?? cssVar("--accent"), borderColor: cssVar("--bg-panel"), borderWidth: 2 },
            data: focusedPoint && y != null ? [{ coord: [focusedPoint.x, y] }] : [],
          },
        };
      }),
    });
  }, [series, focusedPoint, xName, y1Name, y2Name, y2Percent, formatX, selectedRange, xMinInterval]);

  return (
    // 外层只做定位(按钮要压在图上);echarts.init 独占内层 ref div,
    // 不和 React 管理的按钮节点混在同一个容器里。
    <div className="relative w-full" style={{ height }}>
      <div ref={ref} className="h-full w-full" />
      {exportName && (
        <EcChartExport chartRef={chartRef} name={exportName} metadata={figureMetadata} belowLegend={series.length > 1} />
      )}
    </div>
  );
}

export function EcChartExport({ chartRef, name, metadata, belowLegend }: {
  chartRef: RefObject<echarts.ECharts | null>;
  name: string;
  metadata?: Record<string, unknown>;
  belowLegend?: boolean;
}) {
  async function save(format: "png" | "svg") {
    const chart = chartRef.current;
    if (!chart) return;
    const notify = useUiStore.getState().showToast;
    try {
      const backgroundColor = cssVar("--bg-panel");
      let result;
      if (format === "svg") {
        const svgChart = echarts.init(null, undefined, {
          renderer: "svg", ssr: true, width: chart.getWidth(), height: chart.getHeight(),
        });
        try {
          svgChart.setOption({ ...chart.getOption(), backgroundColor });
          const context = JSON.stringify(metadata ?? {}).replace(/[&<>]/g, (char) => (
            { "&": "&amp;", "<": "&lt;", ">": "&gt;" }[char] ?? char
          ));
          const svg = svgChart.renderToSVGString().replace(/<svg[^>]*>/, (tag) => `${tag}\n<metadata>${context}</metadata>`);
          result = await exportFigureSvg(name, svg);
        } finally {
          svgChart.dispose();
        }
      } else {
        result = await exportEcFigure(name, chart.getDataURL({ type: "png", pixelRatio: 2, backgroundColor }), metadata);
      }
      if (!result.cancelled) {
        notify(result.ok ? "success" : "error", result.ok ? t.command.exportSuccess : (result.error ?? "导出失败"));
      }
    } catch (error) {
      notify("error", error instanceof Error ? error.message : String(error));
    }
  }

  const className = "rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)]/90 px-1.5 py-1 text-[11px] text-[var(--text-secondary)] hover:bg-[var(--hover)]";
  return (
    <div className={`absolute right-1 z-10 flex gap-1 ${belowLegend ? "top-6" : "top-1"}`}>
      <button type="button" className={className} title="导出 PNG" onClick={() => void save("png")}>
        {metadata ? "PNG" : <Download size={13} />}
      </button>
      {metadata && <button type="button" className={className} title="导出 SVG" onClick={() => void save("svg")}>SVG</button>}
    </div>
  );
}
