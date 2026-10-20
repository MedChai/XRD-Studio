/** 电化学 cell 页面:主工作区在选中 cell 时替换 XRD 谱图区。
 *
 *  页签:循环总览 / dQ/dV / 峰轨迹 / 衰减归因。
 *  顶部:cell 名 + 文件名 + 质量 + 圈数,右侧三枚 CSV 导出按钮。
 *  所有重计算都由各页签的显式按钮触发,这里只做装配。
 */

import { useState } from "react";
import { Segmented } from "../../components/controls";
import { Button } from "../../components/controls";
import { ecExport } from "../../services/engine";
import { useEcStore, useUiStore } from "../../stores";
import { t } from "../../i18n";
import { OverviewTab } from "./OverviewTab";
import { DqdvTab } from "./DqdvTab";
import { TracksTab } from "./TracksTab";
import { AttributionTab } from "./AttributionTab";

type Tab = "overview" | "dqdv" | "tracks" | "attribution";

export function CellPage() {
  const overview = useEcStore((s) => s.overview);
  const loading = useEcStore((s) => s.loadingCell);
  const cellId = useEcStore((s) => s.loadedCellId);
  const showToast = useUiStore((s) => s.showToast);
  const [tab, setTab] = useState<Tab>("overview");
  const [exporting, setExporting] = useState(false);

  async function doExport(kind: "cycles" | "peaks" | "attribution") {
    if (cellId == null || exporting) return;
    setExporting(true);
    try {
      const r = await ecExport(cellId, kind);
      if (!r.ok) {
        showToast("error", r.error);
        return;
      }
      showToast("success", t.echem.exportDone(r.data.path));
    } finally {
      setExporting(false);
    }
  }

  if (loading || (!overview && cellId != null)) {
    return (
      <div className="flex h-full items-center justify-center text-[13px] text-[var(--text-tertiary)]">
        {t.echem.loading}
      </div>
    );
  }
  if (!overview) return null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* 头部:标识 + 页签 + 导出 */}
      <div className="flex flex-wrap items-center gap-3 border-b border-[var(--divider)] px-4 py-2">
        <div className="min-w-0">
          <div className="truncate text-[15px] font-semibold text-[var(--text-primary)]">
            {overview.name}
          </div>
          <div className="truncate text-[11px] text-[var(--text-tertiary)]">
            {overview.filename}
            {overview.massMg != null && ` · ${overview.massMg.toFixed(2)} mg`}
            {` · ${t.echem.cyclesBadge(overview.cycles.length)}`}
          </div>
        </div>
        <span className="flex-1" />
        <Segmented
          options={[
            { value: "overview", label: t.echem.tabOverview },
            { value: "dqdv", label: t.echem.tabDqdv },
            { value: "tracks", label: t.echem.tabTracks },
            { value: "attribution", label: t.echem.tabAttribution },
          ]}
          value={tab}
          onChange={(v) => setTab(v as Tab)}
        />
        <Button disabled={exporting} onClick={() => void doExport("cycles")}>
          {t.echem.exportCycles}
        </Button>
        <Button disabled={exporting} onClick={() => void doExport("peaks")}>
          {t.echem.exportPeaks}
        </Button>
        <Button disabled={exporting} onClick={() => void doExport("attribution")}>
          {t.echem.exportAttribution}
        </Button>
      </div>

      {/* 页签内容。key 绑 cellId:切 cell 时重挂载,页签内局部状态
          (选中圈号/轨迹等)不会把旧 cell 的值带到新 cell。 */}
      <div className="min-h-0 flex-1 pt-2" key={cellId ?? "none"}>
        {tab === "overview" && <OverviewTab overview={overview} />}
        {tab === "dqdv" && <DqdvTab overview={overview} />}
        {tab === "tracks" && <TracksTab />}
        {tab === "attribution" && <AttributionTab />}
      </div>
    </div>
  );
}
