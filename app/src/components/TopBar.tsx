/** 顶栏(§16):52px,只有 + Import 是 Primary Button,其余 icon 化。
 *  选中电化学 cell 时「+」改开电化学导入(LAND xlsx)——不然 XRD
 *  导入面板在电化学语境里必然选错文件类型。 */

import { Plus, Search, Settings, TrendingUp } from "lucide-react";
import { Button, IconButton } from "../components/controls";
import { ProjectSwitcher } from "./ProjectSwitcher";
import { useSelectionStore, useUiStore } from "../stores";
import { t } from "../i18n";

export function TopBar() {
  const { setImportOpen, setEcImportOpen, setPaletteOpen, setSettingsOpen, setTrendOpen } =
    useUiStore();
  const cellId = useSelectionStore((s) => s.cellId);

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
        onClick={() => (cellId != null ? setEcImportOpen(true) : setImportOpen(true))}
      >
        <Plus size={14} /> {t.topbar.import}
      </Button>
    </header>
  );
}
