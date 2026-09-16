/** 状态栏(§15):光标读数、点数、保存状态。 */

import { useAnalysisStore, usePlotStore } from "../stores";
import { t } from "../i18n";

export function StatusBar() {
  const curve = useAnalysisStore((s) => s.curve);
  const status = useAnalysisStore((s) => s.status);
  const peaksDirty = useAnalysisStore((s) => s.peaksDirty);
  const crosshair = usePlotStore((s) => s.crosshair);
  const cursor = usePlotStore((s) => s.cursor);

  return (
    <footer
      className="blur-surface flex shrink-0 items-center gap-4 border-t border-[var(--divider)] bg-[var(--bg-sidebar)] px-3 text-[11px] text-[var(--text-secondary)]"
      style={{ height: "var(--statusbar-h)" }}
    >
      {crosshair && cursor ? (
        <>
          <span className="tabular">2θ {cursor.x.toFixed(3)}°</span>
          <span className="tabular">I {Math.max(0, Math.round(cursor.y)).toLocaleString()}</span>
        </>
      ) : (
        <span>—</span>
      )}
      <span className="flex-1" />
      {curve && (
        <span className="tabular">{t.status.points(curve.pointCount.toLocaleString())}</span>
      )}
      {/* 参数改过但还没固化时给出提示 —— 否则用户点完「检测峰」看不出有没有生效 */}
      {curve && peaksDirty && status === "idle" && (
        <span className="text-[var(--warning)]">{t.status.peaksDirty}</span>
      )}
      <span className={status === "idle" ? "text-[var(--success)]" : "text-[var(--warning)]"}>
        {status === "idle"
          ? t.status.saved
          : status === "committing"
            ? t.status.saving
            : t.status.computing}
      </span>
    </footer>
  );
}
