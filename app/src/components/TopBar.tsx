/** 顶栏(§16):52px,只有 + Import 是 Primary Button,其余 icon 化。 */

import { Plus, Search, Settings } from "lucide-react";
import { Button, IconButton } from "../components/controls";
import { ProjectSwitcher } from "./ProjectSwitcher";
import { useUiStore } from "../stores";
import { t } from "../i18n";

export function TopBar() {
  const { setImportOpen, setPaletteOpen, setSettingsOpen } = useUiStore();

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
      <IconButton title={t.topbar.search} onClick={() => setPaletteOpen(true)}>
        <Search size={16} />
      </IconButton>
      <IconButton title={t.topbar.settings} onClick={() => setSettingsOpen(true)}>
        <Settings size={16} />
      </IconButton>
      <Button kind="primary" onClick={() => setImportOpen(true)}>
        <Plus size={14} /> {t.topbar.import}
      </Button>
    </header>
  );
}
