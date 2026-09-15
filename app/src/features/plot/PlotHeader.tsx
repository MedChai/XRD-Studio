/** Plot Header(§21):标题 + 少量工具,不做 20 个图标的 toolbar。 */

import { Crosshair, Layers } from "lucide-react";
import { IconButton, Segmented } from "../../components/controls";
import { useAnalysisStore, usePlotStore } from "../../stores";

export function PlotHeader() {
  const curve = useAnalysisStore((s) => s.curve);
  const { mode, setMode, crosshair, setCrosshair } = usePlotStore();

  return (
    <div className="flex h-9 shrink-0 items-center gap-2 px-3">
      <span className="truncate text-[13px] font-medium text-[var(--text-primary)]">
        {curve ? "当前谱图" : "XRD Plot"}
      </span>
      <span className="flex-1" />
      {curve && (
        <Segmented
          options={[
            { value: "overlay", label: "Overlay" },
            { value: "stacked", label: "Stacked" },
          ]}
          value={mode}
          onChange={setMode}
        />
      )}
      <IconButton title="十字光标" active={crosshair} onClick={() => setCrosshair(!crosshair)}>
        <Crosshair size={15} />
      </IconButton>
      <IconButton title="叠加模式切换" onClick={() => setMode(mode === "overlay" ? "stacked" : "overlay")}>
        <Layers size={15} />
      </IconButton>
    </div>
  );
}
