/** 峰轨迹页签:轨迹列表 + 峰位/峰面积 vs 圈数 + 截断峰统计。
 *
 *  明细随 ec.dqdv_compute 固化到 ec_analyses,打开 cell 时由 store 经
 *  ec.dqdv_result 读回 —— 重启软件后本页直接可用,无需重算。UI 参数被
 *  store 对齐到读回的快照,「参数已修改」徽标只在用户改动后出现。
 */

import { useMemo, useState } from "react";
import { EcLineChart, type EcSeries } from "./EcLineChart";
import { useEcStore } from "../../stores";
import { t } from "../../i18n";
import { cn } from "../../components/cn";

const POSITION_COLOR = "#4a90d9";
const AREA_COLOR = "#e8833a";

export function TracksTab() {
  const result = useEcStore((s) => s.dqdvResult);
  const computed = useEcStore((s) => s.dqdvComputed);
  const overview = useEcStore((s) => s.overview);
  const [selectedTrack, setSelectedTrack] = useState<number | null>(null);

  const tracks = result?.tracks ?? [];
  const track = useMemo(
    () => tracks.find((x) => x.trackId === (selectedTrack ?? tracks[0]?.trackId)) ?? null,
    [tracks, selectedTrack],
  );

  const trackPeaks = useMemo(
    () => (result && track ? result.peaks.filter((p) => p.trackId === track.trackId) : []),
    [result, track],
  );

  const positionSeries = useMemo<EcSeries[]>(
    () => [
      {
        name: `${track?.label ?? ""} · ${directionLabel(track?.direction)}`,
        data: trackPeaks.map((p) => [p.cycle, p.position]),
        color: POSITION_COLOR,
      },
    ],
    [trackPeaks, track],
  );
  const areaSeries = useMemo<EcSeries[]>(
    () => [{ name: t.echem.trackArea, data: trackPeaks.map((p) => [p.cycle, p.area]), color: AREA_COLOR }],
    [trackPeaks],
  );

  const nTruncated = result?.nTruncated ?? 0;
  const chargeH3Drift = useMemo(() => {
    if (!result) return null;
    const h3 = result.peaks.filter((p) => p.direction === "charge" && p.window === "H2H3");
    if (h3.length < 2) return null;
    return Math.max(...h3.map((p) => p.position)) - Math.min(...h3.map((p) => p.position));
  }, [result]);

  if (!computed || !result) {
    return (
      <div className="flex h-full items-center justify-center px-8 text-center text-[13px] text-[var(--text-tertiary)]">
        {t.echem.tracksNotComputed}
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 gap-3 px-4 pb-4">
      {/* 轨迹列表 */}
      <div className="flex w-64 shrink-0 flex-col overflow-y-auto rounded-[var(--radius-panel)] border border-[var(--divider)]">
        <div className="sticky top-0 grid grid-cols-[1fr_56px] gap-1 border-b border-[var(--divider)] bg-[var(--bg-panel)] px-2 py-1.5 text-[11px] font-medium text-[var(--text-tertiary)]">
          <span>{t.echem.colWindow}</span>
          <span className="text-right">{t.echem.colCount}</span>
        </div>
        {tracks.length === 0 && (
          <div className="px-3 py-4 text-[12px] text-[var(--text-tertiary)]">{t.echem.tracksNeedCompute}</div>
        )}
        {tracks.map((tr) => (
          <button
            key={tr.trackId}
            className={cn(
              "tr grid grid-cols-[1fr_56px] gap-1 border-b border-[var(--divider)] px-2 py-1.5 text-left text-[12px] hover:bg-[var(--hover)]",
              track?.trackId === tr.trackId && "bg-[var(--selected)]",
            )}
            onClick={() => setSelectedTrack(tr.trackId)}
          >
            <span className="min-w-0">
              <span className="block truncate text-[var(--text-primary)]">
                {tr.label} · {directionLabel(tr.direction)}
              </span>
              <span className="text-[11px] text-[var(--text-tertiary)]">
                {tr.firstCycle}–{tr.lastCycle} · {t.echem.colContinuity}{" "}
                {(tr.continuity * 100).toFixed(0)}%
              </span>
            </span>
            <span className="tabular self-center text-right text-[var(--text-secondary)]">
              {tr.nPeaks}
            </span>
          </button>
        ))}
      </div>

      {/* 双图 + 截断统计 */}
      <div className="flex min-w-0 flex-1 flex-col overflow-y-auto">
        {track && trackPeaks.length > 0 ? (
          <>
            <EcLineChart
              series={positionSeries}
              xName={t.echem.cycleAxis}
              y1Name={t.echem.trackPosition}
              height={230}
              exportName={`${overview?.name ?? "cell"}_峰位`}
            />
            <EcLineChart
              series={areaSeries}
              xName={t.echem.cycleAxis}
              y1Name={t.echem.trackArea}
              height={230}
              exportName={`${overview?.name ?? "cell"}_峰面积`}
            />
          </>
        ) : (
          <div className="flex flex-1 items-center justify-center text-[12px] text-[var(--text-tertiary)]">
            {t.echem.tracksNeedCompute}
          </div>
        )}
        <div className="mt-2 text-[11px] text-[var(--text-tertiary)]">
          {t.echem.truncatedStats(nTruncated, result.nPeaks - nTruncated)}
          {chargeH3Drift != null && chargeH3Drift > 0.05 && (
            <span className="ml-2 text-[var(--warning)]">
              {t.echem.truncatedEvidence(chargeH3Drift)}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function directionLabel(d: "charge" | "discharge" | undefined): string {
  return d === "discharge" ? t.echem.directionDischarge : t.echem.directionCharge;
}
