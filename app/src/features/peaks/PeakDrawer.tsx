/** 底部 Peak Table(§34/§35):默认收起,与图双向联动。 */

import { ChevronDown, ChevronUp } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useAnalysisStore, useSelectionStore } from "../../stores";
import { cn } from "../../components/cn";
import { t } from "../../i18n";

export function PeakDrawer() {
  const peaks = useAnalysisStore((s) => s.peaks);
  const { selectedPeakIndex, setSelectedPeak } = useSelectionStore();
  const [open, setOpen] = useState(false);
  const rowRefs = useRef(new Map<number, HTMLTableRowElement>());

  // 选中峰变化时把表格滚到该行(§6.7「点图内峰 → 表滚动选中」)。
  // 抽屉收起时 scrollIntoView 无效,所以顺带展开 —— 否则从图上点峰会"没反应"。
  useEffect(() => {
    if (selectedPeakIndex == null) return;
    setOpen(true);
    rowRefs.current
      .get(selectedPeakIndex)
      // 必须用 "nearest":用 "center" 会让用户点表格自己的行时页面上下跳动
      ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selectedPeakIndex]);

  return (
    <div className="shrink-0 border-t border-[var(--divider)] bg-[var(--bg-panel)]">
      <button
        className="tr flex h-8 w-full items-center gap-2 px-3 text-[12px] font-medium text-[var(--text-secondary)] hover:bg-[var(--hover)]"
        onClick={() => setOpen(!open)}
      >
        {t.peaks.title}
        <span className="tabular rounded-full bg-[var(--selected)] px-1.5 text-[11px] text-[var(--accent)]">
          {peaks.length}
        </span>
        <span className="flex-1" />
        {open ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
      </button>

      {open && (
        <div className="max-h-[40vh] overflow-y-auto">
          <table className="w-full text-[12px]">
            <thead className="sticky top-0 bg-[var(--bg-panel)]">
              <tr className="border-b border-[var(--divider)] text-left text-[11px] text-[var(--text-tertiary)]">
                <th className="px-3 py-1 font-medium">{t.peaks.colIndex}</th>
                <th className="px-3 py-1 font-medium">{t.peaks.colPosition}</th>
                <th className="px-3 py-1 text-right font-medium">{t.peaks.colIntensity}</th>
                <th className="px-3 py-1 text-right font-medium">{t.peaks.colFwhm}</th>
                <th className="px-3 py-1 text-right font-medium">{t.peaks.colArea}</th>
              </tr>
            </thead>
            <tbody>
              {peaks.map((p) => (
                <tr
                  key={p.index}
                  ref={(el) => {
                    if (el) rowRefs.current.set(p.index, el);
                    else rowRefs.current.delete(p.index);
                  }}
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
                    {t.peaks.empty}
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
