/** 顶栏根据当前工作区打开谱图导入或蓝电 CEX 文件选择。 */

import { FolderOpen, Plus, Search, Settings, TrendingUp } from "lucide-react";
import { Button, IconButton } from "../components/controls";
import { ProjectSwitcher } from "./ProjectSwitcher";
import { useSelectionStore, useUiStore } from "../stores";
import { t } from "../i18n";
import { chooseCexFiles, useCexSession } from "../features/echem/cex/session";

export function TopBar() {
  const { setImportOpen, setPaletteOpen, setSettingsOpen, setTrendOpen } =
    useUiStore();
  const cexActive = useSelectionStore((s) => s.cexActive);
  const activeCex = useCexSession((s) => s.active);

  return (
    <header
      className="blur-surface flex items-center gap-2 border-b border-[var(--divider)] bg-[var(--bg-sidebar)] px-3"
      style={{ height: "var(--topbar-h)" }}
    >
      <span className="text-[13px] font-semibold text-[var(--text-primary)]">
        {t.app.name}
      </span>
      <ProjectSwitcher />
      <div className="flex-1" />
      <Button kind="ghost" title={!cexActive && activeCex ? "返回蓝电工作区" : "打开蓝电 CEX（Ctrl+O）"}
        onClick={() => {
          if (!cexActive && activeCex) useCexSession.getState().select(activeCex);
          else void chooseCexFiles();
        }}>
        <FolderOpen size={14} /> {!cexActive && activeCex ? "蓝电数据" : "打开 CEX"}
      </Button>
      <IconButton title={t.topbar.trend} onClick={() => setTrendOpen(true)}>
        <TrendingUp size={16} />
      </IconButton>
      <IconButton title={t.topbar.search} onClick={() => setPaletteOpen(true)}>
        <Search size={16} />
      </IconButton>
      <IconButton title={t.topbar.settings} onClick={() => setSettingsOpen(true)}>
        <Settings size={16} />
      </IconButton>
      <Button
        kind="primary"
        onClick={() => {
          if (cexActive) void chooseCexFiles(useSelectionStore.getState().sampleId ?? undefined);
          else setImportOpen(true);
        }}
      >
        <Plus size={14} /> {t.topbar.import}
      </Button>
    </header>
  );
}
