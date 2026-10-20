/** Plot Header(§21):标题 + 少量工具,不做 20 个图标的 toolbar。 */

import { Crosshair, Maximize2, X } from "lucide-react";
import { IconButton, Segmented } from "../../components/controls";
import { useAnalysisStore, usePlotStore } from "../../stores";
import { t } from "../../i18n";

export function PlotHeader() {
  const curve = useAnalysisStore((s) => s.curve);
  // 逐字段选择器(§15):全量解构会让 plotStore 任意字段变化都重渲染
  const mode = usePlotStore((s) => s.mode);
  const setMode = usePlotStore((s) => s.setMode);
  const crosshair = usePlotStore((s) => s.crosshair);
  const setCrosshair = usePlotStore((s) => s.setCrosshair);
  const fitAll = usePlotStore((s) => s.fitAll);
  const zoom = usePlotStore((s) => s.zoom);
  const compareCurves = usePlotStore((s) => s.compareCurves);
  const removeCompareCurve = usePlotStore((s) => s.removeCompareCurve);
  // 已经全域时禁用,给用户一个"当前就是全览"的反馈
  const isFullView = zoom == null;

  return (
    <div className="flex h-9 shrink-0 items-center gap-2 px-3">
      <span className="truncate text-[13px] font-medium text-[var(--text-primary)]">
        {curve ? t.plot.current : t.plot.placeholder}
      </span>
      <span className="flex-1" />
      {/* 对比 chip 条:一眼看到"现在叠了哪几条",点 × 移除。
          没有它的话,用户加了曲线之后没有任何地方能看出加了什么。 */}
      {compareCurves.length > 0 && (
        <div className="flex min-w-0 items-center gap-1">
          {compareCurves.map((c) => (
            <button
              key={c.datasetId}
              title={t.library.removeFromCompare}
              onClick={() => removeCompareCurve(c.datasetId)}
              className="tr flex max-w-[140px] items-center gap-1 rounded-full border border-[var(--divider)] bg-[var(--bg-elevated)] px-2 py-0.5 text-[11px] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
            >
              <span
                className="h-2 w-2 shrink-0 rounded-full"
                style={{ background: c.color }}
              />
              <span className="truncate">{c.name}</span>
              <X size={10} className="shrink-0 opacity-60" />
            </button>
          ))}
        </div>
      )}
      {curve && (
        <Segmented
          options={[
            { value: "overlay", label: t.plot.overlay },
            { value: "stacked", label: t.plot.stacked },
          ]}
          value={mode}
          onChange={setMode}
        />
      )}
      <IconButton title={t.plot.fitAll} onClick={fitAll} active={false}>
        <Maximize2 size={15} className={isFullView ? "opacity-40" : undefined} />
      </IconButton>
      <IconButton
        title={t.plot.crosshair}
        active={crosshair}
        onClick={() => setCrosshair(!crosshair)}
      >
        <Crosshair size={15} />
      </IconButton>
    </div>
  );
}
