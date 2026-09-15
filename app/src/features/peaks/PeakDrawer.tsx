/** 底部 Peak Table(§34/§35):默认收起,与图双向联动。 */

import { ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";
import { useAnalysisStore, useSelectionStore } from "../../stores";
import { cn } from "../../components/cn";

export function PeakDrawer() {
  const peaks = useAnalysisStore((s) => s.peaks);
  const { selectedPeakIndex, setSelectedPeak } = useSelectionStore();
  const [open, setOpen] = useState(false);

  return (
    <div className="border-t border-[var(--divider)] bg-[var(--bg-panel)]">
      <button
        className="tr flex h-8 w-full items-center gap-2 px-3 text-[12px] font-medium text-[var(--text-secondary)] hover:bg-[var(--hover)]"
        onClick={() => setOpen(!open)}
      >
        Peaks
        <span className="tabular rounded-full bg-[var(--selected)] px-1.5 text-[11px] text-[var(--accent)]">
          {peaks.length}
        </span>
        <span className="flex-1" />
        {open ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
      </button>

      {open && (
        <div className="max-h-52 overflow-y-auto">
          <table className="w-full text-[12px]">
            <thead className="sticky top-0 bg-[var(--bg-panel)]">
              <tr className="border-b border-[var(--divider)] text-left text-[11px] text-[var(--text-tertiary)]">
                <th className="px-3 py-1 font-medium">#</th>
                <th className="px-3 py-1 font-medium">2θ</th>
                <th className="px-3 py-1 text-right font-medium">Intensity</th>
                <th className="px-3 py-1 text-right font-medium">FWHM</th>
                <th className="px-3 py-1 text-right font-medium">Area</th>
              </tr>
            </thead>
            <tbody>
              {peaks.map((p) => (
                <tr
                  key={p.index}
                  className={cn(
                    "tr cursor-pointer border-b border-[var(--divider)] last:border-0 hover:bg-[var(--hover)]",
                    selectedPeakIndex === p.index && "bg-[var(--selected)]",
                  )}
                  onClick={() => setSelectedPeak(p.index)}
                >
                  <td className="tabular px-3 py-1 text-[var(--text-tertiary)]">{p.index + 1}</td>
                  <td className="tabular px-3 py-1">{p.position.toFixed(3)}°</td>
                  <td className="tabular px-3 py-1 text-right">
                    {Math.round(p.intensity).toLocaleString()}
                  </td>
                  <td className="tabular px-3 py-1 text-right">
                    {p.fwhm != null ? p.fwhm.toFixed(3) : "—"}
                  </td>
                  <td className="tabular px-3 py-1 text-right">
                    {p.area != null ? Math.round(p.area).toLocaleString() : "—"}
                  </td>
                </tr>
              ))}
              {peaks.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-3 py-4 text-center text-[var(--text-tertiary)]">
                    未检出峰
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
