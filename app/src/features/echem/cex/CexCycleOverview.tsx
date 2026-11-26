import { useEffect, useState } from "react";
import { Button } from "../../../components/controls";
import { RangeSlider, type SliderPoint } from "../../plot/RangeSlider";
import { cexCommand, type CexSeries } from "./model";

export function CexCycleOverview({ id, total, first, last, preview, apply }: {
  id: string;
  total: number;
  first: number;
  last: number;
  preview: (first: number, last: number) => void;
  apply: (first: number, last: number) => void;
}) {
  const [points, setPoints] = useState<SliderPoint[]>([]);

  useEffect(() => {
    if (total <= 1) return;
    let current = true;
    void cexCommand<{ series: CexSeries[] }>("plot", { id, kind: "cycles", first: 1, last: total }).then(({ series }) => {
      if (!current) return;
      const discharge = series.find((curve) => curve.name === "放电" && curve.points.some((point) => point[1] != null));
      const source = (discharge ?? series.find((curve) => curve.name === "充电"))?.points ?? [];
      const max = source.reduce((peak, [, value]) => Math.max(peak, value ?? 0), 0) || 1;
      setPoints(source.map(([cycle, value]) => [cycle, value == null ? null : (value / max) * 100]));
    }, () => { /* The range remains usable without a capacity preview. */ });
    return () => { current = false; };
  }, [id, total]);

  return (
    <div role="group" aria-label="圈数范围轴" className="shrink-0 border-t border-[var(--divider)] px-3 pt-1 pb-0.5">
      <div className="flex h-6 items-center justify-between gap-2 text-[11px] text-[var(--text-secondary)]">
        <span>圈数范围</span>
        <span className="tabular ml-auto">{first}–{last} / {total} 圈</span>
        <Button kind="ghost" className="h-5 px-1.5 text-[11px]" disabled={first === 1 && last === total}
          onClick={() => apply(1, total)}>全部</Button>
      </div>
      <div className="h-8">
        {total > 1 ? (
          <RangeSlider min={1} max={total} range={{ from: first, to: last }} points={points}
            wholeNumbers onChange={({ from, to }) => preview(from, to)} onCommit={({ from, to }) => apply(from, to)} />
        ) : (
          <div className="mt-1.5 h-[18px] rounded border border-[var(--divider)] bg-[var(--selected)]" />
        )}
      </div>
    </div>
  );
}
