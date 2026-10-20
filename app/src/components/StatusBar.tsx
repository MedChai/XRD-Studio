/** 状态栏(§15):光标读数、点数、保存状态。
 *
 *  电化学模式(cellId 非空)整栏换成模式标签:光标、XRD 点数/
 *  峰固化徽标、谱图管线状态全是谱图语境,selectCell 不清 analysisStore,
 *  留着会显示上一张谱图的残值,只会误导。
 *
 *  光标 x 存的是**显示坐标**(与图上轴一致),读数格式与图表 tooltip
 *  共用 formatAxisReadout —— 两处显示不同读数会让人怀疑哪个是真的。
 */

import { useAnalysisStore, usePlotStore, useSelectionStore } from "../stores";
import { formatAxisReadout, type XDisplay } from "../services/coords";
import { t } from "../i18n";

export function StatusBar() {
  const curve = useAnalysisStore((s) => s.curve);
  const status = useAnalysisStore((s) => s.status);
  const peaksDirty = useAnalysisStore((s) => s.peaksDirty);
  const crosshair = usePlotStore((s) => s.crosshair);
  const cursor = usePlotStore((s) => s.cursor);
  const xView = usePlotStore((s) => s.xView);
  const cellId = useSelectionStore((s) => s.cellId);
  const view: XDisplay = curve && curve.xAxisType === "generic" ? "generic" : xView;

  if (cellId != null) {
    return (
      <footer
        className="blur-surface flex shrink-0 items-center gap-4 border-t border-[var(--divider)] bg-[var(--bg-sidebar)] px-3 text-[11px] text-[var(--text-secondary)]"
        style={{ height: "var(--statusbar-h)" }}
      >
        <span>{t.status.ecMode}</span>
        <span className="flex-1" />
      </footer>
    );
  }

  return (
    <footer
      className="blur-surface flex shrink-0 items-center gap-4 border-t border-[var(--divider)] bg-[var(--bg-sidebar)] px-3 text-[11px] text-[var(--text-secondary)]"
      style={{ height: "var(--statusbar-h)" }}
    >
      {crosshair && cursor ? (
        <>
          <span className="tabular">{formatAxisReadout(cursor.x, view)}</span>
          <span className="tabular">I {Math.max(0, Math.round(cursor.y)).toLocaleString()}</span>
        </>
      ) : (
        <span>—</span>
      )}
      <span className="flex-1" />
      {curve && (
        <span className="tabular">
          {curve.downsampled
            ? t.status.pointsDisplay(
                curve.pointCount.toLocaleString(),
                (curve.sourcePointCount ?? curve.pointCount).toLocaleString(),
              )
            : t.status.points(curve.pointCount.toLocaleString())}
        </span>
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
