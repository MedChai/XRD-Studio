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
import {
  axisLabel,
  formatAxisReadout,
  formatAxisValue,
  fromDisplayX,
  toDisplayX,
  type XDisplay,
} from "../../services/coords";

interface Props {
  onPeakClick?: (index: number) => void;
}

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** 背景曲线的 series 名。tooltip 与点击命中都要靠它区分主曲线与背景线。 */
const BG_SERIES = "__background__";

/** 当前曲线的显示映射参数。
 *
 * 放模块级供挂载时注册的事件回调使用:那些回调只在 mount 跑一次,捕获
 * 渲染闭包里的 view/wavelength 会永远拿到首帧的值(切换视图后缩放写回
 * 就会错位)。从 store 现取才总是当前的。 */
function currentConv(): {
  view: XDisplay;
  nativeAxis: "two_theta" | "d" | "q" | "generic";
  wavelength: number;
} {
  const cur = useAnalysisStore.getState().curve;
  const { xView } = usePlotStore.getState();
  return {
    view: cur && cur.xAxisType === "generic" ? "generic" : xView,
    nativeAxis: cur?.xAxisType ?? "two_theta",
    wavelength: cur?.wavelength ?? 1.5418,
  };
}

function XRDChartInner({ onPeakClick }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  /** 上一次回放到图上的 zoom,用于值相等短路(防环第二层) */
  const appliedZoom = useRef<string>("");
  /** 上一次因选中峰而自动定位的峰序号。见下面 auto-zoom effect 的说明。 */
  const autoZoomedPeak = useRef<number | null>(null);
  const curve = useAnalysisStore((s) => s.curve);
  const peaks = useAnalysisStore((s) => s.peaks);
  const selectedPeak = useSelectionStore((s) => s.selectedPeakIndex);
  const selectPeak = useSelectionStore((s) => s.setSelectedPeak);
  // 逐字段选择器:全量解构会让 crosshair mousemove 的 cursor 更新
  // 重渲染整个主图(§15 / B1)。
  const mode = usePlotStore((s) => s.mode);
  const crosshair = usePlotStore((s) => s.crosshair);
  const showLabels = usePlotStore((s) => s.showLabels);
  const offset = usePlotStore((s) => s.offset);
  const showBackground = usePlotStore((s) => s.showBackground);
  const zoom = usePlotStore((s) => s.zoom);
  const compareCurves = usePlotStore((s) => s.compareCurves);
  const xView = usePlotStore((s) => s.xView);

  /** 显示层坐标映射(§4.1):xView 是用户选的视图,generic 采集轴无换算
   *  定义,强制原样显示。zoom/xDomain/峰位始终是采集轴,只有画上图的 x
   *  过 toDisplayX —— 峰表结果按采集轴存储,换算只影响显示。 */
  const view: XDisplay = curve && curve.xAxisType === "generic" ? "generic" : xView;
  const nativeAxis = curve?.xAxisType ?? "two_theta";
  const wavelength = curve?.wavelength ?? 1.5418;

  /** 归一化到 100 的 y 标度。曲线、背景线、峰标记三者必须用**同一个**
   * maxY,否则一拖 Offset 滑块它们就互相错位。 */
  const maxY = useMemo(() => {
    if (!curve) return 1;
    return curve.y.reduce((m, v) => (v > m ? v : m), 0) || 1;
  }, [curve]);

  // 数据只转一次;normalization/offset 纯前端(§9.2)
  /** stacked 模式下主曲线的 lane 偏移。
   *
   * 主曲线永远在最上面一条 lane —— 它是当前分析对象,放中间会让用户以为
   * 那也是一条参考曲线。lane 高度与 compareSeries 保持一致(55)。 */
  const mainShift = mode === "stacked" ? compareCurves.length * 55 : 0;

  const seriesData = useMemo<[number, number][] | null>(() => {
    if (!curve) return null;
    const { x, y } = curve;
    const out: [number, number][] = new Array(x.length);
    for (let i = 0; i < x.length; i++) {
      out[i] = [toDisplayX(x[i], view, nativeAxis, wavelength), (y[i] / maxY) * 100 + offset + mainShift];
    }
    return out;
  }, [curve, offset, maxY, mainShift, view, nativeAxis, wavelength]);

  /** 背景曲线(§7.3 Show Background)。引擎只在 background.enabled 时
   * 才返回它,所以关闭背景扣除后这个开关是无效的 —— Inspector 会提示。 */
  const bgData = useMemo<[number, number][] | null>(() => {
    if (!curve?.background) return null;
    const { x, background } = curve;
    if (background.length !== x.length) return null;
    const out: [number, number][] = new Array(x.length);
    for (let i = 0; i < x.length; i++) {
      out[i] = [toDisplayX(x[i], view, nativeAxis, wavelength), (background[i] / maxY) * 100 + offset + mainShift];
    }
    return out;
  }, [curve, offset, maxY, mainShift, view, nativeAxis, wavelength]);

  /** 对比曲线(§S4)。每条**用各自的 maxY 归一化**到同一个 0–100 标度 ——
   * 若共用当前曲线的 maxY,强度差 10 倍的样品在图上看起来一样高,叠加就
   * 失去意义。这是"比较"和"拼接"的区别。
   *
   * stacked 模式下每加一条就整体抬升一个 lane(offsetStep),让曲线彼此
   * 错开不重叠;overlay 模式下全部落在同一条基线上。lane 高度取 55 而非
   * 100:保留重叠余量,否则相邻两条的峰顶会顶到上一条的基线,看着像同一条。 */
  const compareSeries = useMemo(() => {
    return compareCurves.map((c) => {
      const peakY = c.curve.y.reduce((m, v) => (v > m ? v : m), 0) || 1;
      const shift = mode === "stacked" ? compareCurves.indexOf(c) * 55 : 0;
      const data: [number, number][] = new Array(c.curve.x.length);
      // 每条对比曲线用**自己的**采集轴/波长换算到同一显示视图 —— 不同靶
      // 的 λ 不同,共用当前曲线的 λ 会让 d/Q 对齐整体错位。
      for (let i = 0; i < c.curve.x.length; i++) {
        data[i] = [
          toDisplayX(c.curve.x[i], view, c.curve.xAxisType, c.curve.wavelength),
          (c.curve.y[i] / peakY) * 100 + shift,
        ];
      }
      return { id: c.datasetId, name: c.name, color: c.color, data };
    });
  }, [compareCurves, mode, view]);

  const markData = useMemo(() => {
    if (!curve || !showLabels || peaks.length === 0) return undefined;
    return peaks.map((p: PeakResult) => {
      // 峰位存的是采集轴,标到显示视图上要换算;无晶面标注时标签退回
      // 峰位读数,单位随视图(2θ/d/Q),不能写死 "°"。
      const xDisp = toDisplayX(p.position, view, nativeAxis, wavelength);
      const fallbackLabel = formatAxisValue(xDisp, view);
      return {
        name: String(p.index),
        // offset 必须与 seriesData 一致:否则拖 Display 的 Offset 滑块时
        // 曲线上下移动而圆点/标签原地不动,视觉上就是一堆悬空的点
        coord: [xDisp, (p.intensity / maxY) * 100 + offset + mainShift],
        // 有晶面标注优先显示晶面,否则退回峰位读数
        value: p.hkl ?? fallbackLabel,
        itemStyle: {
          color:
            selectedPeak === p.index ? cssVar("--accent") : "transparent",
        },
        label: {
          show: true,
          formatter: () => p.hkl ?? fallbackLabel,
          // 恒在点上方。**不要**对贴顶峰改 bottom —— 标签会落在曲线
          // 上,峰线直接穿过数字。顶部裁切靠 yAxis.max 预留余量解决。
          position: "top" as const,
          distance: 6,
          fontSize: 10,
          // 三态必须**视觉可辨**:reliable 用常规色,ambiguous 用弱化色 +
          // 括号外的问号,未归属用更弱的色。旧实现里三者都是同一个
          // --text-secondary,用户无法区分"这是 (102)"与"这可能是 (006)
          // 或 (102),没分开"。把不确定性画出来,比给一个假确定更有用。
          color:
            selectedPeak === p.index
              ? cssVar("--accent")
              : p.hkl_confidence === "ambiguous"
                ? cssVar("--warning")
                : p.hkl
                  ? cssVar("--text-secondary")
                  : cssVar("--text-tertiary"),
          fontStyle:
            p.hkl_confidence === "ambiguous" ? ("italic" as const) : undefined,
        },
        symbol: "circle",
        symbolSize: selectedPeak === p.index ? 7 : 4,
      };
    });
  }, [curve, peaks, showLabels, selectedPeak, offset, maxY, mainShift, view, nativeAxis, wavelength]);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    chartRef.current = chart;
    usePlotStore.getState().setFigureExporter(() =>
      chart.getDataURL({
        type: "png",
        pixelRatio: 2,
        backgroundColor: cssVar("--bg-panel"),
      }),
    );
    // SVG:canvas renderer 画不出矢量,导出时离屏用 svg renderer
    // 重画同一 option —— 导出是低频操作,多一次 init 可接受(F6)。
    usePlotStore.getState().setFigureSvgExporter(() => {
      const temp = document.createElement("div");
      temp.style.width = `${chart.getWidth()}px`;
      temp.style.height = `${chart.getHeight()}px`;
      temp.style.position = "fixed";
      temp.style.left = "-9999px";
      document.body.appendChild(temp);
      try {
        const svgChart = echarts.init(temp, undefined, { renderer: "svg" });
        svgChart.setOption(chart.getOption());
        const svg = svgChart.renderToSVGString();
        svgChart.dispose();
        return svg;
      } finally {
        temp.remove();
      }
    });

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

      // ② 点在曲线上:按 x 就近匹配峰位。
      //    图上的 x 是显示坐标,峰位存的是采集轴(§4.1)—— 先换回采集轴
      //    再比,否则切到 d/Q 视图后点曲线永远匹配不上(d 值个位数 vs
      //    2θ 十位数,容差 1.0 直接失效)。
      if (params.componentType !== "series") return;
      if (params.seriesName === BG_SERIES) return; // 背景线不参与选峰
      if (!Array.isArray(params.value)) return;
      const { view: v, nativeAxis: nat, wavelength: lam } = currentConv();
      const xClick = fromDisplayX(params.value[0] as number, v, nat, lam);
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
        // dataZoom 的 start/end 是显示坐标;zoom 持采集轴(§4.1),换回再
        // 写 store。key 与 zoom 回放 effect 同构(view 前缀),否则切换视图
        // 后同值短路会失效/误触发。
        const { view: v, nativeAxis: nat, wavelength: lam } = currentConv();
        const next = {
          from: fromDisplayX(dz.startValue, v, nat, lam),
          to: fromDisplayX(dz.endValue, v, nat, lam),
        };
        appliedZoom.current = `${v}:${JSON.stringify(next)}`; // 标记为本图自己产生的
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
      usePlotStore.getState().setFigureExporter(null);
      usePlotStore.getState().setFigureSvgExporter(null);
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
        // top 留 36px:y 轴标题 + 最高峰标签(distance 6 + 字号 10)。
        // 配合 yAxis.max 顶部余量,标签不会再被 container overflow:hidden 裁掉。
        grid: { top: 36, bottom: 34, left: 52, right: 20 },
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
                // 显示坐标读数,精度/单位随视图(§4.4)
                return typeof v === "number" ? formatAxisValue(v, view) : String(v);
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
            // 与状态栏同一条格式(formatAxisReadout),两处读数必须一致
            return `<span class=tabular>${formatAxisReadout(v[0], view)}</span><br/>` +
              `<span class=tabular>I ${Math.round(v[1]).toLocaleString()}</span>`;
          },
        },
        xAxis: {
          type: "value",
          // x 值域必须从数据推导。此前硬编码 min:10/max:80,任何不在
          // 10–80° 的实测谱都会被裁掉,而且 Fit All 也无从谈起。
          // 换算到显示坐标后仍单调,from/to 直接映射到 min/max。
          min: xDomain ? toDisplayX(xDomain[0], view, nativeAxis, wavelength) : undefined,
          max: xDomain ? toDisplayX(xDomain[1], view, nativeAxis, wavelength) : undefined,
          axisLine: { lineStyle: { color: cssVar("--plot-axis") } },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: {
            color: cssVar("--text-secondary"),
            fontSize: 10,
            // d 视图内部映射是负 d(见 services/coords.ts 头注),刻度取
            // 绝对值 —— 用户看到的与文献图一致(大 d 在左)。
            formatter: view === "d" ? (value: number) => Math.abs(value).toFixed(2) : undefined,
          },
          name: axisLabel(view),
          nameLocation: "end",
          nameGap: 8,
          nameTextStyle: { color: cssVar("--text-tertiary"), fontSize: 10 },
        },
        yAxis: {
          type: "value",
          // 归一化后最高峰恒在 y=100。若 max 恰等于数据最大值,点贴
          // grid 顶边,position:"top" 的晶面标签会被裁掉(用户反馈
          // "峰很高时 003 不显示")。预留约 12% 顶部余量,最高点的
          // 标签也画在 grid 内,且仍在点上方、不会压住曲线。
          // 结果**向上取整到整十**:否则轴顶刻度会直接显示
          // 111.34608864131751 这种裸浮点(用户截图里的长串数字)。
          max: (value: { min: number; max: number }) => {
            const span = value.max - value.min;
            const raw = value.max + Math.max(span * 0.12, 8);
            return Math.ceil(raw / 10) * 10;
          },
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
        // 对比曲线画在**主曲线之前**:ECharts 后画的在上层,而当前分析的
        // 那条曲线带峰标记,必须压在最上面 —— 否则它的峰会被参考曲线盖住,
        // 用户点了半天选不中。
        legend: compareSeries.length
          ? {
              show: true,
              top: 0,
              right: 8,
              itemWidth: 10,
              itemHeight: 8,
              textStyle: { color: cssVar("--text-secondary"), fontSize: 10 },
              data: compareSeries.map((c) => c.name),
            }
          : { show: false },
        series: [
          ...compareSeries.map((c) => ({
            type: "line" as const,
            name: c.name,
            showSymbol: false,
            sampling: "lttb" as const,
            large: true,
            largeThreshold: 2000,
            // silent:对比曲线只供目视比较,不参与点击选峰 —— 否则用户点
            // 一条参考曲线的峰,选中的却是当前数据集的峰,张冠李戴。
            silent: true,
            lineStyle: { color: c.color, width: 1, opacity: 0.85 },
            data: c.data,
          })),
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
  }, [curve, seriesData, bgData, markData, crosshair, mode, peaks, selectPeak, onPeakClick, showBackground, xDomain, compareSeries, view, nativeAxis, wavelength]);

  // 把 plotStore.zoom(采集轴)换算成显示坐标后回放到图上。
  // 值相等短路:本图自己产生的 zoom 已经 appliedZoom 标记过,不必再回放,
  // 也避免 dispatchAction → 重渲染 → 再进这里。key 带 view 前缀 ——
  // 切换视图时同一个采集轴窗口要映射到新的显示坐标重放,只比 {from,to}
  // 会被短路掉,缩放窗口残留旧视图的坐标值。
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !xDomain) return;
    const target = zoom ?? { from: xDomain[0], to: xDomain[1] };
    const key = `${view}:${JSON.stringify(target)}`;
    if (key === appliedZoom.current) return;
    appliedZoom.current = key;
    chart.dispatchAction({
      type: "dataZoom",
      startValue: toDisplayX(target.from, view, nativeAxis, wavelength),
      endValue: toDisplayX(target.to, view, nativeAxis, wavelength),
    });
  }, [zoom, xDomain, view, nativeAxis, wavelength]);

  // 选中峰变化:平移视口到峰附近并高亮(§35)。
  // 走 setZoom(数据坐标)而不是直接 dispatchAction,这样底部范围框同步跟随。
  //
  // **只在"选中的峰真的换了"时平移,不能在 peaks 变化时也平移。**
  // 依赖里若带上 peaks(它是每次 preview 都会被整体替换的新数组),
  // 那么拖任何一个滑块、切一次数据集都会把视口重新拽回 ±4° 窗口 ——
  // 用户表现为"图自己跳走了",而且每次 preview 都多一轮 setZoom →
  // dataZoom → 重绘。用 ref 记住上次已定位的峰序号来区分这两种情况。
  useEffect(() => {
    if (selectedPeak == null) {
      autoZoomedPeak.current = null;
      return;
    }
    if (autoZoomedPeak.current === selectedPeak) return;
    const peak = peaks.find((p) => p.index === selectedPeak);
    const d = usePlotStore.getState().xDomain;
    if (!peak || !d) return;
    autoZoomedPeak.current = selectedPeak;
    const span = 4;
    usePlotStore.getState().setZoom({
      from: Math.max(d[0], peak.position - span),
      to: Math.min(d[1], peak.position + span),
    });
  }, [selectedPeak, peaks]);

  return <div ref={ref} className="h-full w-full" />;
}

export const XRDChart = memo(XRDChartInner);
