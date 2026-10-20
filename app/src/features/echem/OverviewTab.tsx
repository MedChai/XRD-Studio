/** 循环总览页签:容量/CE vs 圈数 + 测试信息折叠区。
 *
 *  容量按活性物质质量归一到 mAh/g(质量缺失时退回 mAh 并在轴名标明)。
 *  倍率组用不同颜色区分(蓝电"倍率充电 0.2*C"会随容量重标定电流,
 *  组归属由引擎按圈中位电流聚类);不完整末圈画成灰色空心点、不连线。
 *  充电容量默认不画,勾选「充电容量」后按组叠加(同色虚线,图例带 ·放/·充)。
 */

import { useMemo, useState } from "react";
import { EcLineChart, type EcSeries } from "./EcLineChart";
import type { EcCellOverview } from "../../types/engine";
import { t } from "../../i18n";
import { cn } from "../../components/cn";

/** 系列配色。ECharts 的 series 颜色必须是具体色值(不接受 CSS 变量
 *  字符串),模块常量拿不到 computed style —— 沿用 services/engine.ts
 *  COMPARE_COLORS 的既定做法:系列色用 hex,不用 var()。 */
const COLOR_MAIN = "#4a90d9";
const COLOR_SLOW = "#3aa76d";
const COLOR_CE = "#8a8f3a";
const COLOR_INCOMPLETE = "#9aa0a6";

export function OverviewTab({ overview }: { overview: EcCellOverview }) {
  const [metaOpen, setMetaOpen] = useState(false);
  const [showCharge, setShowCharge] = useState(false);
  const mass = overview.massMg;
  const scale = mass ? 1000 / mass : 1;
  const unit = mass ? "mAh/g" : "mAh";

  const series = useMemo<EcSeries[]>(() => {
    const main: [number, number | null][] = [];
    const slow: [number, number | null][] = [];
    const incomplete: [number, number | null][] = [];
    const mainC: [number, number | null][] = [];
    const slowC: [number, number | null][] = [];
    const incompleteC: [number, number | null][] = [];
    for (const c of overview.cycles) {
      const pt: [number, number | null] = [c.cycle, c.qDischarge * scale];
      const ptC: [number, number | null] = [c.cycle, c.qCharge * scale];
      if (c.incomplete) {
        incomplete.push(pt);
        if (showCharge) incompleteC.push(ptC);
      } else if (c.rateGroup === "slow") {
        slow.push(pt);
        if (showCharge) slowC.push(ptC);
      } else {
        main.push(pt);
        if (showCharge) mainC.push(ptC);
      }
    }
    const ce: [number, number | null][] = overview.cycles
      .filter((c) => !c.incomplete && c.ce > 0)
      .map((c) => [c.cycle, c.ce]);
    // 充电叠加时放电系列加 ·放 后缀,否则保持原名(不打扰既有习惯)
    const sD = showCharge ? t.echem.legendDischargeSuffix : "";
    const out: EcSeries[] = [
      { name: `${t.echem.rateMain}${sD}`, data: main, color: COLOR_MAIN },
    ];
    if (showCharge)
      out.push({ name: `${t.echem.rateMain}${t.echem.legendChargeSuffix}`, data: mainC, color: COLOR_MAIN, dashed: true });
    out.push({ name: `${t.echem.rateSlow}${sD}`, data: slow, color: COLOR_SLOW });
    if (showCharge)
      out.push({ name: `${t.echem.rateSlow}${t.echem.legendChargeSuffix}`, data: slowC, color: COLOR_SLOW, dashed: true });
    out.push({
      name: `${t.echem.incompleteCycle}${sD}`,
      data: incomplete,
      color: COLOR_INCOMPLETE,
      hollow: true,
    });
    if (showCharge)
      out.push({
        name: `${t.echem.incompleteCycle}${t.echem.legendChargeSuffix}`,
        data: incompleteC,
        color: COLOR_INCOMPLETE,
        dashed: true,
        hollow: true,
      });
    out.push({ name: t.echem.ceAxis, data: ce, color: COLOR_CE, yAxisIndex: 1, dashed: true });
    return out;
  }, [overview, scale, showCharge]);

  const qa = overview.qa as Record<string, unknown>;
  const qaLines = useMemo(() => {
    const out: string[] = [];
    if (qa.n_records != null) out.push(t.echem.qaRecords(Number(qa.n_records)));
    if (qa.backwards != null) out.push(t.echem.qaBackwards(Number(qa.backwards)));
    if (qa.interval_med != null) out.push(t.echem.qaInterval(Number(qa.interval_med)));
    if (qa.capacity_dev_med != null) out.push(t.echem.qaCapacityDev(Number(qa.capacity_dev_med)));
    return out;
  }, [qa]);

  return (
    <div className="flex h-full flex-col overflow-y-auto px-4 pb-4">
      <div className="flex items-center justify-end pb-0.5 text-[12px] text-[var(--text-secondary)]">
        <label className="flex cursor-pointer items-center gap-1.5">
          <input
            type="checkbox"
            checked={showCharge}
            onChange={(e) => setShowCharge(e.target.checked)}
            className="accent-[var(--accent)]"
          />
          {t.echem.showCharge}
        </label>
      </div>
      <EcLineChart
        series={series}
        xName={t.echem.cycleAxis}
        y1Name={`${(showCharge ? t.echem.capacityAxisAny : t.echem.capacityAxis).replace(
          "(mAh/g)",
          "",
        )} (${unit})`}
        y2Name={t.echem.ceAxis}
        y2Percent
        height={300}
        exportName={`${overview.name}${t.echem.exportOverviewSuffix}`}
      />

      {/* 测试信息折叠区:程序与 QA 是"数据可信度"的依据,默认收起,
          需要核对时展开 —— 常驻会挤压图表。 */}
      <div className="mt-3 rounded-[var(--radius-panel)] border border-[var(--divider)]">
        <button
          className="tr flex w-full items-center gap-2 px-3 py-2 text-[12px] font-medium text-[var(--text-secondary)] hover:bg-[var(--hover)]"
          onClick={() => setMetaOpen(!metaOpen)}
        >
          <span className={cn("transition-transform", metaOpen && "rotate-90")}>▸</span>
          {t.echem.metaTitle}
          <span className="ml-2 truncate text-[11px] text-[var(--text-tertiary)]">
            {overview.session.test_name}
            {overview.session.channel && ` · ${t.echem.channelLabel} ${overview.session.channel}`}
          </span>
        </button>
        {metaOpen && (
          <div className="border-t border-[var(--divider)] px-4 py-3 text-[12px] leading-relaxed text-[var(--text-secondary)]">
            <div className="flex flex-wrap gap-x-6 gap-y-1">
              <span>
                {t.echem.metaTitle}: {overview.session.test_name || "—"}
              </span>
              <span>{t.echem.channelColon(overview.session.channel || "—")}</span>
              <span>{overview.session.start_time}</span>
              <span>→ {overview.session.end_time}</span>
              {overview.massMg != null && <span>m = {overview.massMg.toFixed(2)} mg</span>}
            </div>
            <div className="mt-2 text-[11px] text-[var(--text-tertiary)]">{t.echem.programTitle}</div>
            <div className="mt-1 max-h-40 overflow-y-auto rounded-[var(--radius-input)] bg-[var(--bg-elevated)] p-2 font-mono text-[11px] text-[var(--text-secondary)]">
              {overview.session.program.map((line, i) => (
                <div key={i}>{line}</div>
              ))}
            </div>
            {qaLines.length > 0 && (
              <>
                <div className="mt-2 text-[11px] text-[var(--text-tertiary)]">{t.echem.qaTitle}</div>
                <div className="mt-1 flex flex-wrap gap-x-4 text-[11px]">{qaLines.join(" · ")}</div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/** 归因页的配色与轴名在这里导出,避免两个文件各自硬编码。 */
export const ATTRIBUTION_COLORS = {
  polarization: "#4a90d9",
  material: "#e8833a",
  cutoff: "#c74b6a",
  total: "#5f6368",
};
