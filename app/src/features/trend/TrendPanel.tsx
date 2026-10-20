/** 结构趋势面板(docs/DEVELOPMENT_HIGH_NI.md §11.4,阶段 H)。

 *  把同一 Project 下各数据集的 a / c / c·a⁻¹ / I(003)/I(104) / FWHM
 *  按煅烧温度(或批次、导入顺序)排成一条线,看结构随条件的演变。
 *
 *  **两个刻意的设计约束**
 *
 *  1. 没算过的点画成空心,不连线、不插值。直接跳过会让曲线在缺失处
 *     看起来是连续的 —— 用户会以为那里有数据。插值更糟:它是编的。
 *  2. 面板顶部固定显示数据来源。晶胞参数在本仓库有两条算法路径
 *     (全谱精修 / 少峰最小二乘),混画会把"换了算法"显示成"结构变了"。
 *     这不是理论风险 —— 两条路径在同一张谱上确实给出不同的 a/c。
 */

import { useEffect, useMemo, useState } from "react";
import * as echarts from "echarts";
import { X } from "lucide-react";
import { engineCommand } from "../../services/engine";
import type { TrendResult } from "../../types/engine";
import { useProjectStore, useSelectionStore, useUiStore } from "../../stores";
import { useAnalysisStore } from "../../stores";
import { t } from "../../i18n";

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** y 轴指标的中文名。保留 a / c / c·a⁻¹ 这些符号本身 —— 它们是领域记号。 */
const METRIC_LABEL: Record<string, string> = t.trend.metrics;

const AXIS_LABEL: Record<string, string> = t.trend.axes;

export function TrendPanel({ onClose }: { onClose: () => void }) {
  const projectId = useProjectStore((s) => s.currentProjectId);
  const showToast = useUiStore((s) => s.showToast);
  const [metric, setMetric] = useState("a");
  const [xAxis, setXAxis] = useState("temperature");
  const [data, setData] = useState<TrendResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [chartEl, setChartEl] = useState<HTMLDivElement | null>(null);

  useEffect(() => {
    if (projectId == null) {
      setData(null);
      return;
    }
    let alive = true;
    setBusy(true);
    void engineCommand<TrendResult>("trend.series", {
      project_id: projectId,
      metric,
      x_axis: xAxis,
    }).then((r) => {
      if (!alive) return;
      setBusy(false);
      if (!r.ok) {
        showToast("error", r.error);
        return;
      }
      setData(r.data);
    });
    return () => {
      alive = false;
    };
  }, [projectId, metric, xAxis, showToast]);

  const points = useMemo(() => data?.points ?? [], [data]);

  useEffect(() => {
    if (!chartEl) return;
    const chart = echarts.init(chartEl, undefined, { renderer: "canvas" });
    const missing = points.filter((p) => p.value == null);
    const values = points
      .map((p) => p.value)
      .filter((v): v is number => v != null);
    const lo = values.length ? Math.min(...values) : 0;
    const hi = values.length ? Math.max(...values) : 1;
    // 底部基线:放在最小值的下方一点,避免与真实的低值点重叠。
    const baselineY = lo - Math.max((hi - lo) * 0.08, Math.abs(lo) * 0.002, 1e-9);

    chart.setOption(
      {
        animation: false,
        backgroundColor: "transparent",
        grid: { top: 20, bottom: 46, left: 62, right: 24 },
        tooltip: {
          trigger: "item",
          backgroundColor: cssVar("--bg-elevated"),
          borderColor: cssVar("--divider"),
          textStyle: { color: cssVar("--text-primary"), fontSize: 11 },
          formatter: (p: { data: [number, number, string, string, boolean] }) => {
            const [, y, label, dsName, isMissing] = p.data;
            if (isMissing) return `${dsName}<br/>${label}<br/>${t.trend.notAnalyzed}`;
            return `${dsName}<br/>${label}<br/>${y.toPrecision(5)}`;
          },
        },
        xAxis: {
          type: "category",
          data: points.map((p) => p.xLabel),
          axisLine: { lineStyle: { color: cssVar("--plot-axis") } },
          axisTick: { show: false },
          axisLabel: { color: cssVar("--text-secondary"), fontSize: 10 },
          name: AXIS_LABEL[xAxis] ?? xAxis,
          nameLocation: "middle",
          nameGap: 28,
          nameTextStyle: { color: cssVar("--text-tertiary"), fontSize: 10 },
        },
        yAxis: {
          type: "value",
          scale: true,
          axisLine: { show: false },
          axisTick: { show: false },
          splitLine: { lineStyle: { color: cssVar("--divider") } },
          axisLabel: { color: cssVar("--text-secondary"), fontSize: 10 },
          name: METRIC_LABEL[metric] ?? metric,
          nameTextStyle: { color: cssVar("--text-tertiary"), fontSize: 10 },
        },
        series: [
          {
            type: "line",
            // 只连有值的点。connectNulls 会把缺失段直接跨过去,
            // 视觉上等同于"那里有数据",是误导。
            data: points.map((p, i) => [
              i,
              p.value,
              p.xLabel,
              p.datasetName,
              p.value == null,
              p.datasetId,
            ]) as never,
            connectNulls: false,
            symbolSize: 7,
            lineStyle: { color: cssVar("--accent"), width: 1.6 },
            itemStyle: { color: cssVar("--accent") },
          },
          // 未分析的点:画在绘图区底部,空心。
          //
          // **不能给 scatter 的 y 传 null** —— value 轴上 null 无法定位,
          // 点根本不会渲染,于是"未分析的数据集"在图上彻底消失,正是我
          // 想避免的"用户以为没这条数据"。改用一个具体的 y(轴最小值),
          // 空心 + 灰色,与实心趋势点视觉上明确区分。
          {
            type: "scatter",
            data: missing.map((p) => {
              const i = points.indexOf(p);
              return [i, baselineY, p.xLabel, p.datasetName, true, p.datasetId];
            }) as never,
            symbolSize: 9,
            symbol: "emptyCircle",
            itemStyle: {
              color: "transparent",
              borderColor: cssVar("--text-tertiary"),
              borderWidth: 1.5,
            },
          },
        ],
      },
      { notMerge: true },
    );

    // 点数据点 → 切回该数据集的主图(与侧栏选择同一条联动通路)
    //
    // **datasetId 从 data 里取,不能用 points[params.dataIndex]。**
    // 两条 series 的 dataIndex 是各自数组的下标:scatter 只有"未分析"那些
    // 点,它的 dataIndex=0 对应 missing[0],而 points[0] 很可能是另一个
    // 数据集 —— 点空心点会跳到错误的数据集上。我把 datasetId 放进 data 的
    // 第 6 位,两条 series 都能直接读。
    chart.on("click", (params) => {
      const dsId = (params.data as unknown[] | undefined)?.[5] as number | undefined;
      if (dsId == null) return;
      useSelectionStore.getState().selectDataset(null, dsId);
      void useAnalysisStore.getState().loadDataset(dsId);
      onClose();
    });

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(chartEl);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [chartEl, points, metric, xAxis, onClose]);

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/25 backdrop-blur-[2px]">
      <div className="w-[720px] max-w-[92vw] rounded-[var(--radius-dialog)] bg-[var(--bg-panel)] p-5 shadow-[var(--shadow-dialog)]">
        <div className="flex items-center gap-3">
          <span className="text-[17px] font-semibold text-[var(--text-primary)]">
            {t.trend.title}
          </span>
          <span className="flex-1" />
          <button
            onClick={onClose}
            className="tr rounded-[var(--radius-control)] p-1 text-[var(--text-tertiary)] hover:bg-[var(--hover)] hover:text-[var(--text-primary)]"
          >
            <X size={16} />
          </button>
        </div>

        <div className="mt-3 flex items-center gap-3 text-[12px] text-[var(--text-secondary)]">
          <label className="flex items-center gap-1.5">
            {t.trend.xAxis}
            <select
              value={xAxis}
              onChange={(e) => setXAxis(e.target.value)}
              className="rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-2 py-0.5 text-[12px]"
            >
              {(data?.availableAxes ?? ["temperature"]).map((a) => (
                <option key={a} value={a}>
                  {AXIS_LABEL[a] ?? a}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-1.5">
            {t.trend.metric}
            <select
              value={metric}
              onChange={(e) => setMetric(e.target.value)}
              className="rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-2 py-0.5 text-[12px]"
            >
              {(data?.availableMetrics ?? ["a"]).map((m) => (
                <option key={m} value={m}>
                  {METRIC_LABEL[m] ?? m}
                </option>
              ))}
            </select>
          </label>
          {busy && <span className="text-[var(--text-tertiary)]">{t.trend.loading}</span>}
        </div>

        {points.length === 0 && !busy ? (
          <div className="flex h-[300px] items-center justify-center text-[13px] text-[var(--text-tertiary)]">
            {t.trend.empty}
          </div>
        ) : (
          <div ref={setChartEl} className="mt-2 h-[300px] w-full" />
        )}

        {/* 数据来源必须写出来 —— 两条算法路径混画会显示成"结构变了" */}
        <div className="mt-2 text-[11px] text-[var(--text-tertiary)]">
          {t.trend.sourceHint}
        </div>
      </div>
    </div>
  );
}
