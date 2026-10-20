/** 衰减归因页签:三分量堆叠面积图 + CE 曲线 + 结论文本。
 *
 *  stale 语义:归因所依据的 dQ/dV 参数(引擎存 sourceDqdvHash)与当前
 *  活跃 dQ/dV 计算的指纹不一致 → 保留旧图,亮徽标提示重新运行 ——
 *  与 XRD 标注的「已过期」同一交互语言。
 */

import { useMemo } from "react";
import { EcLineChart, type EcSeries } from "./EcLineChart";
import { ATTRIBUTION_COLORS } from "./OverviewTab";
import { Button } from "../../components/controls";
import { useEcStore } from "../../stores";
import { t } from "../../i18n";

const CE_COLOR = "#8a8f3a";

export function AttributionTab() {
  const attribution = useEcStore((s) => s.attribution);
  const running = useEcStore((s) => s.attributionRunning);
  const runAttribution = useEcStore((s) => s.runAttribution);
  const dqdvComputed = useEcStore((s) => s.dqdvComputed);
  const overview = useEcStore((s) => s.overview);

  const series = useMemo<EcSeries[]>(() => {
    if (!attribution.computed) return [];
    const by = attribution.cycles;
    return [
      {
        name: t.echem.compPolarization,
        data: by.map((c) => [c.cycle, c.polarization]),
        color: ATTRIBUTION_COLORS.polarization,
        kind: "area",
      },
      {
        name: t.echem.compMaterial,
        data: by.map((c) => [c.cycle, c.material]),
        color: ATTRIBUTION_COLORS.material,
        kind: "area",
      },
      {
        name: t.echem.compCutoff,
        data: by.map((c) => [c.cycle, c.cutoff]),
        color: ATTRIBUTION_COLORS.cutoff,
        kind: "area",
      },
      {
        name: t.echem.compTotal,
        data: by.map((c) => [c.cycle, c.total]),
        color: ATTRIBUTION_COLORS.total,
        dashed: true,
      },
      {
        name: t.echem.ceAxis,
        data: attribution.ceSeries.map(([n, v]) => [n, v]),
        color: CE_COLOR,
        yAxisIndex: 1,
        dashed: true,
      },
    ];
  }, [attribution]);

  if (!attribution.computed) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 px-8 text-center">
        <div className="text-[13px] text-[var(--text-tertiary)]">
          {dqdvComputed ? t.echem.attributionNotRun : t.echem.attributionNeedDqdv}
        </div>
        <Button
          kind="primary"
          disabled={running || !dqdvComputed}
          onClick={() => void runAttribution()}
        >
          {running ? t.echem.attributionRunning : t.echem.attributionRun}
        </Button>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto px-4 pb-4">
      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        <span className="rounded-full border border-[var(--divider)] px-2 py-0.5 text-[11px] text-[var(--text-secondary)]">
          {attribution.method === "A" ? t.echem.methodA : t.echem.methodB}
        </span>
        <span
          className={
            attribution.confidence === "high"
              ? "rounded-full border border-[var(--success)] px-2 py-0.5 text-[11px] text-[var(--success)]"
              : "rounded-full border border-[var(--warning)] px-2 py-0.5 text-[11px] text-[var(--warning)]"
          }
        >
          {attribution.confidence === "high" ? t.echem.confidenceHigh : t.echem.confidenceLow}
        </span>
        {attribution.stale && (
          <span className="rounded-full border border-[var(--warning)] px-2 py-0.5 text-[11px] text-[var(--warning)]">
            {t.echem.attributionStale}
          </span>
        )}
        <span className="flex-1" />
        <Button disabled={running} onClick={() => void runAttribution()}>
          {running ? t.echem.attributionRunning : t.echem.attributionRun}
        </Button>
      </div>

      <div className="mt-3">
        <EcLineChart
          series={series}
          xName={t.echem.cycleAxis}
          y1Name="mAh/g"
          y2Name={t.echem.ceAxis}
          y2Percent
          height={300}
          exportName={`${overview?.name ?? "cell"}_归因`}
        />
      </div>

      {/* 结论:归因的最终产出,显著排版 */}
      <div className="mt-3 rounded-[var(--radius-panel)] border border-[var(--divider)] bg-[var(--bg-elevated)] p-3">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
          {t.echem.conclusionTitle}
        </div>
        <div className="mt-1.5 text-[13px] leading-relaxed text-[var(--text-primary)]">
          {attribution.conclusion}
        </div>
      </div>

      {attribution.notes.length > 0 && (
        <div className="mt-2 text-[11px] leading-relaxed text-[var(--text-tertiary)]">
          {attribution.notes.map((n, i) => (
            <div key={i}>· {n}</div>
          ))}
        </div>
      )}
    </div>
  );
}
