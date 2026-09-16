/** Plot Header(§21):标题 + 少量工具,不做 20 个图标的 toolbar。 */

import { Crosshair, Layers } from "lucide-react";
import { IconButton, Segmented } from "../../components/controls";
import { useAnalysisStore, usePlotStore } from "../../stores";
import { t } from "../../i18n";

export function PlotHeader() {
  const curve = useAnalysisStore((s) => s.curve);
  const { mode, setMode, crosshair, setCrosshair } = usePlotStore();

  return (
    <div className="flex h-9 shrink-0 items-center gap-2 px-3">
      <span className="truncate text-[13px] font-medium text-[var(--text-primary)]">
        {curve ? t.plot.current : t.plot.placeholder}
      </span>
      <span className="flex-1" />
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
      <IconButton
        title={t.plot.crosshair}
        active={crosshair}
        onClick={() => setCrosshair(!crosshair)}
      >
        <Crosshair size={15} />
      </IconButton>
      <IconButton
        title={t.plot.toggleMode}
        onClick={() => setMode(mode === "overlay" ? "stacked" : "overlay")}
      >
        <Layers size={15} />
      </IconButton>
    </div>
  );
}
