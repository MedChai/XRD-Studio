/** Command Palette(§44):Ctrl+K。命令 + 样品混合搜索。 */

import { useEffect, useMemo, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  Download,
  FolderPlus,
  Moon,
  Search,
  Sun,
  Upload,
  Wand2,
} from "lucide-react";
import { useAnalysisStore, useAppStore, useProjectStore, useUiStore } from "../../stores";
import { useSelectionStore } from "../../stores";
import { t } from "../../i18n";

interface Entry {
  id: string;
  label: string;
  icon?: React.ReactNode;
  run: () => void;
  group: string;
}

export function CommandPalette() {
  const open = useUiStore((s) => s.paletteOpen);
  const setOpen = useUiStore((s) => s.setPaletteOpen);
  const { samples } = useProjectStore();
  const { setTheme, theme } = useAppStore();
  const { selectDataset } = useSelectionStore();
  const loadDataset = useAnalysisStore((s) => s.loadDataset);
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQ("");
      setActive(0);
      setTimeout(() => inputRef.current?.focus(), 30);
    }
  }, [open]);

  const entries = useMemo<Entry[]>(() => {
    const cmds: Entry[] = [
      { id: "import", label: t.command.importData, icon: <Upload size={14} />, group: t.command.groupCommands, run: () => useUiStore.getState().setImportOpen(true) },
      { id: "newProject", label: t.command.newProject, icon: <FolderPlus size={14} />, group: t.command.groupCommands, run: () => useUiStore.getState().setNewProjectOpen(true) },
      { id: "detect", label: t.command.detectPeaks, icon: <Wand2 size={14} />, group: t.command.groupCommands, run: () => useAnalysisStore.getState().patchPipeline({}) },
      { id: "export", label: t.command.exportPeaks, icon: <Download size={14} />, group: t.command.groupCommands, run: () => useUiStore.getState().showToast("success", t.command.exportPending) },
      {
        id: "theme",
        label: theme === "dark" ? t.command.switchToLight : t.command.switchToDark,
        icon: theme === "dark" ? <Sun size={14} /> : <Moon size={14} />,
        group: t.command.groupCommands,
        run: () => setTheme(theme === "dark" ? "light" : "dark"),
      },
    ];
    const samples_: Entry[] = samples.map((s) => ({
      id: `s${s.id}`,
      label: s.name,
      group: t.command.groupSamples,
      run: () => {
        const ds = s.datasets[0];
        if (ds) {
          selectDataset(s.id, ds.id);
          void loadDataset(ds.id);
        }
      },
    }));
    return [...cmds, ...samples_];
  }, [samples, theme, setTheme, selectDataset, loadDataset]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return entries;
    return entries.filter((e) => e.label.toLowerCase().includes(needle));
  }, [entries, q]);

  const grouped = useMemo(() => {
    const g = new Map<string, Entry[]>();
    filtered.forEach((e) => {
      if (!g.has(e.group)) g.set(e.group, []);
      g.get(e.group)!.push(e);
    });
    return g;
  }, [filtered]);

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/25 backdrop-blur-[2px]" />
        <Dialog.Content className="fixed left-1/2 top-[22%] z-50 w-[460px] -translate-x-1/2 rounded-[var(--radius-dialog)] bg-[var(--bg-elevated)] shadow-[var(--shadow-dialog)] backdrop-blur-2xl">
          <Dialog.Title className="sr-only">{t.command.title}</Dialog.Title>
          <div className="flex items-center gap-2 border-b border-[var(--divider)] px-4 py-3">
            <Search size={15} className="text-[var(--text-tertiary)]" />
            <input
              ref={inputRef}
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setActive(0);
              }}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, filtered.length - 1)); }
                if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
                if (e.key === "Enter" && filtered[active]) {
                  filtered[active].run();
                  setOpen(false);
                }
              }}
              placeholder={t.command.placeholder}
              className="w-full bg-transparent text-[14px] text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] focus:outline-none"
            />
          </div>
          <div className="max-h-72 overflow-y-auto py-1.5">
            {Array.from(grouped.entries()).map(([group, items]) => (
              <div key={group}>
                <div className="px-4 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
                  {group}
                </div>
                {items.map((e) => {
                  const idx = filtered.indexOf(e);
                  return (
                    <button
                      key={e.id}
                      className={
                        "tr flex w-full items-center gap-2.5 px-4 py-1.5 text-left text-[13px] " +
                        (idx === active
                          ? "bg-[var(--selected)] text-[var(--accent)]"
                          : "text-[var(--text-primary)] hover:bg-[var(--hover)]")
                      }
                      onMouseEnter={() => setActive(idx)}
                      onClick={() => {
                        e.run();
                        setOpen(false);
                      }}
                    >
                      <span className="text-[var(--text-tertiary)]">{e.icon}</span>
                      {e.label}
                    </button>
                  );
                })}
              </div>
            ))}
            {filtered.length === 0 && (
              <div className="px-4 py-6 text-center text-[12px] text-[var(--text-tertiary)]">
                {t.command.noMatch}
              </div>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
