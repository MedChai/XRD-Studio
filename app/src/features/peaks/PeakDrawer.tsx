/** 底部 Peak Table(§34/§35):默认收起,与图双向联动。 */

import { ChevronDown, ChevronUp } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  useAnalysisStore,
  useSelectionStore,
  useUiStore,
} from "../../stores";
import { engineCommand } from "../../services/engine";
import { axisShortLabel } from "../../services/coords";
import { cn } from "../../components/cn";
import { t } from "../../i18n";
import type { PeakResult } from "../../types/engine";

/** 晶面单元格的悬停提示:把归属依据摊开,用户可核对而不必信标签。 */
function hklTooltip(p: PeakResult): string {
  const cand = (c: { label: string | null; dominance?: number | null }) =>
    `${c.label ?? "—"}(${c.dominance != null ? (c.dominance * 100).toFixed(0) + "%" : "—"})`;
  if (p.hkl_confidence === "ambiguous") {
    const alts = (p.hkl_candidates ?? []).map(cand).join(" / ");
    return alts ? t.peaks.hkl.ambiguousAlts(alts) : t.peaks.hkl.ambiguous;
  }
  if (!p.hkl) {
    // dominance 在 none 时是"最强的那个反射也只占这么多" —— 低于阈值
    // 才判 none,报出来能让用户看出是"完全没反射"还是"有几个都不占优"。
    // candidates 现在对 none 也保留(引擎 assign_peaks 改动),近邻弱匹配
    // 一并展示 —— 用户能据此判断"没标 = 真没有"还是"有但不占优(疑似杂相)"。
    const share = p.dominance;
    const weak = (p.hkl_candidates ?? [])
      .map((c) => c.label)
      .filter(Boolean)
      .join(" / ");
    const base = weak ? t.peaks.hkl.noneWeak(weak) : t.peaks.hkl.none;
    return share != null && share > 0
      ? t.peaks.hkl.noneShare(base, (share * 100).toFixed(0))
      : base;
  }
  const parts = [t.peaks.hkl.dominance(((p.dominance ?? 0) * 100).toFixed(0))];
  if (p.hkl_candidates && p.hkl_candidates.length > 1) {
    parts.push(t.peaks.hkl.others(p.hkl_candidates.slice(1).map(cand).join(" / ")));
  }
  return parts.join(";");
}

export function PeakDrawer() {
  const peaks = useAnalysisStore((s) => s.peaks);
  const curve = useAnalysisStore((s) => s.curve);
  // 逐字段选择器(§15):全量解构 useSelectionStore() 会订阅整个 store
  const selectedPeakIndex = useSelectionStore((s) => s.selectedPeakIndex);
  const setSelectedPeak = useSelectionStore((s) => s.setSelectedPeak);
  const showToast = useUiStore((s) => s.showToast);
  const [open, setOpen] = useState(false);
  const rowRefs = useRef(new Map<number, HTMLTableRowElement>());
  /** 正在编辑的积分边界:{峰 index, 左/右}。双击进入,Enter/失焦提交。 */
  const [editing, setEditing] = useState<{
    index: number;
    side: "left" | "right";
    draft: string;
  } | null>(null);

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

  function beginEdit(p: PeakResult, side: "left" | "right") {
    // bounds 编辑走 peaks.update_bounds,必须有 peaks 表主键。preview 峰
    // 没有 id(未固化)—— 如实告知,不静默失败(M4)。
    if (p.id == null) {
      showToast("error", t.peaks.boundsNeedStore);
      return;
    }
    const cur = side === "left" ? p.left_bound : p.right_bound;
    setEditing({ index: p.index, side, draft: cur != null ? String(Number(cur.toFixed(4))) : "" });
  }

  async function commitEdit() {
    if (!editing) return;
    const p = peaks.find((x) => x.index === editing.index);
    setEditing(null);
    if (!p || p.id == null) return;
    const value = Number(editing.draft);
    if (!Number.isFinite(value)) return;
    const left = editing.side === "left" ? value : (p.left_bound ?? value - 0.1);
    const right = editing.side === "right" ? value : (p.right_bound ?? value + 0.1);
    if (!(right > left)) {
      showToast("error", t.peaks.boundsInvalid);
      return;
    }
    const r = await engineCommand<{ peak: PeakResult }>("peaks.update_bounds", {
      peak_id: p.id,
      left,
      right,
    });
    if (!r.ok) {
      showToast("error", r.error);
      return;
    }
    // 按 index 合并回峰表:面积由引擎重算,manual_override 置位
    const merged = useAnalysisStore.getState().peaks.map((x) =>
      x.index === p.index
        ? {
            ...x,
            left_bound: r.data.peak.left_bound ?? left,
            right_bound: r.data.peak.right_bound ?? right,
            area: r.data.peak.area ?? x.area,
            manual_override: true,
          }
        : x,
    );
    useAnalysisStore.setState({ peaks: merged });
    showToast("success", t.peaks.boundsSaved);
  }

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
                {/* 峰位按**采集轴**存储(README §4.1),列头要标它是什么轴,
                    不是写死 "2θ" —— d/q 采集的数据列头写 2θ 是错的 */}
                <th className="px-3 py-1 font-medium">
                  {curve ? axisShortLabel(curve.xAxisType) : t.peaks.colPosition}
                </th>
                <th className="px-3 py-1 font-medium">{t.peaks.colHkl}</th>
                <th className="px-3 py-1 text-right font-medium">{t.peaks.colIntensity}</th>
                <th className="px-3 py-1 text-right font-medium">{t.peaks.colFwhm}</th>
                <th className="px-3 py-1 text-right font-medium">{t.peaks.colArea}</th>
                <th className="px-3 py-1 text-right font-medium" title={t.peaks.boundsHint}>
                  {t.peaks.colLeft}
                </th>
                <th className="px-3 py-1 text-right font-medium" title={t.peaks.boundsHint}>
                  {t.peaks.colRight}
                </th>
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
                  <td className="px-3 py-1 text-[var(--text-primary)]">
                    {p.hkl ? (
                      <span
                        className={
                          p.hkl_confidence === "ambiguous"
                            ? "italic text-[var(--warning)]"
                            : undefined
                        }
                        title={hklTooltip(p)}
                      >
                        {p.hkl}
                        {p.hkl_confidence === "ambiguous" && " ?"}
                      </span>
                    ) : (
                      // 区分"本物相无对应反射"与"有反射但未分开" —— 旧实现
                      // 两者都显示 —,用户无法知道该不该去调参数
                      <span
                        className="text-[var(--text-tertiary)]"
                        title={hklTooltip(p)}
                      >
                        {p.hkl_confidence === "ambiguous" ? "未分开" : "—"}
                      </span>
                    )}
                  </td>
                  <td className="tabular px-3 py-1 text-right">
                    {Math.round(p.intensity).toLocaleString()}
                  </td>
                  <td className="tabular px-3 py-1 text-right">
                    {p.fwhm != null ? p.fwhm.toFixed(3) : "—"}
                  </td>
                  <td
                    className="tabular px-3 py-1 text-right"
                    title={p.manual_override ? t.peaks.manualBadge : undefined}
                  >
                    {p.area != null ? Math.round(p.area).toLocaleString() : "—"}
                    {p.manual_override && (
                      <span className="ml-0.5 text-[var(--accent)]" aria-label={t.peaks.manualBadge}>
                        ●
                      </span>
                    )}
                  </td>
                  {(["left", "right"] as const).map((side) => {
                    const raw = side === "left" ? p.left_bound : p.right_bound;
                    const active = editing?.index === p.index && editing.side === side;
                    if (active) {
                      return (
                        <td key={side} className="px-1.5 py-0.5 text-right">
                          <input
                            autoFocus
                            type="number"
                            step="0.01"
                            value={editing.draft}
                            onChange={(e) =>
                              setEditing({ ...editing, draft: e.target.value })
                            }
                            onClick={(e) => e.stopPropagation()}
                            onBlur={() => void commitEdit()}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") void commitEdit();
                              if (e.key === "Escape") setEditing(null);
                            }}
                            className="tabular w-16 rounded-[var(--radius-input)] border border-[var(--accent)] bg-[var(--bg-panel)] px-1 py-0.5 text-right text-[12px] focus:outline-none"
                          />
                        </td>
                      );
                    }
                    return (
                      <td
                        key={side}
                        className="tabular cursor-text px-3 py-1 text-right text-[var(--text-secondary)] hover:bg-[var(--hover)]"
                        title={t.peaks.boundsHint}
                        onDoubleClick={(e) => {
                          e.stopPropagation();
                          beginEdit(p, side);
                        }}
                      >
                        {raw != null ? raw.toFixed(3) : "—"}
                      </td>
                    );
                  })}
                </tr>
              ))}
              {peaks.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-3 py-4 text-center text-[var(--text-tertiary)]">
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
