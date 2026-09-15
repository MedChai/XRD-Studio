/** 顶栏(§16):52px,只有 + Import 是 Primary Button,其余 icon 化。 */

import { ChevronDown, Plus, Search, Settings } from "lucide-react";
import { Button, IconButton } from "../components/controls";
import { useProjectStore, useUiStore } from "../stores";

export function TopBar() {
  const { projects, currentProjectId } = useProjectStore();
  const { setImportOpen, setPaletteOpen, setSettingsOpen } = useUiStore();
  const current = projects.find((p) => p.id === currentProjectId);

  return (
    <header
      className="blur-surface flex items-center gap-2 border-b border-[var(--divider)] bg-[var(--bg-sidebar)] px-3"
      style={{ height: "var(--topbar-h)" }}
    >
      <span className="text-[13px] font-semibold text-[var(--text-primary)]">
        XRD Studio
      </span>
      <button className="tr flex items-center gap-1 rounded-[var(--radius-control)] px-2 py-1 text-[13px] text-[var(--text-secondary)] hover:bg-[var(--hover)]">
        {current?.name ?? "No Project"}
        <ChevronDown size={13} />
      </button>
      <div className="flex-1" />
      <IconButton title="搜索 (Ctrl+K)" onClick={() => setPaletteOpen(true)}>
        <Search size={16} />
      </IconButton>
      <IconButton title="设置" onClick={() => setSettingsOpen(true)}>
        <Settings size={16} />
      </IconButton>
      <Button kind="primary" onClick={() => setImportOpen(true)}>
        <Plus size={14} /> Import
      </Button>
    </header>
  );
}
