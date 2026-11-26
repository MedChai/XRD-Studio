/** Command Palette(§44):Ctrl+K。命令 + 样品混合搜索。 */

import { useEffect, useMemo, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  Download,
  FolderPlus,
  Maximize2,
  Moon,
  Pencil,
  Search,
  Sun,
  TrendingUp,
  Trash2,
  Upload,
  Wand2,
} from "lucide-react";
import {
  useAnalysisStore,
  useAppStore,
  usePlotStore,
  useProjectStore,
  useUiStore,
} from "../../stores";
import { useSelectionStore } from "../../stores";
import { t } from "../../i18n";
import {
  exportData,
  exportFigure,
  exportFigureSvg,
  exportPeaks,
  exportBatch,
} from "../../services/exports";

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
  const selectDataset = useSelectionStore((s) => s.selectDataset);
  const loadDataset = useAnalysisStore((s) => s.loadDataset);
  const curve = useAnalysisStore((s) => s.curve);
  const peaks = useAnalysisStore((s) => s.peaks);
  const figureExporter = usePlotStore((s) => s.figureExporter);
  const figureSvgExporter = usePlotStore((s) => s.figureSvgExporter);
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
    const notifyExport = (result: { ok: boolean; cancelled?: boolean; error?: string }) => {
      if (result.cancelled) return;
      useUiStore.getState().showToast(
        result.ok ? "success" : "error",
        result.ok ? t.command.exportSuccess : (result.error ?? t.command.exportPending),
      );
    };
    const currentDataset = () => {
      const { datasetId, sampleId } = useSelectionStore.getState();
      const sample = samples.find((item) => item.id === sampleId);
      const dataset = sample?.datasets.find((item) => item.id === datasetId);
      return datasetId == null || dataset == null
        ? null
        : { id: datasetId, name: dataset.name };
    };
    const cmds: Entry[] = [
      { id: "import", label: t.command.importData, icon: <Upload size={14} />, group: t.command.groupCommands, run: () => useUiStore.getState().setImportOpen(true) },
      { id: "newProject", label: t.command.newProject, icon: <FolderPlus size={14} />, group: t.command.groupCommands, run: () => useUiStore.getState().setNewProjectOpen(true) },
      { id: "detect", label: t.command.detectPeaks, icon: <Wand2 size={14} />, group: t.command.groupCommands, run: () => { void useAnalysisStore.getState().detectPeaks(); } },
      { id: "fitAll", label: t.command.fitAll, icon: <Maximize2 size={14} />, group: t.command.groupCommands, run: () => usePlotStore.getState().fitAll() },
      {
        id: "exportPeaks",
        label: t.command.exportPeaks,
        icon: <Download size={14} />,
        group: t.command.groupCommands,
        run: () => {
          const dataset = currentDataset();
          if (!dataset) {
            useUiStore.getState().showToast("error", t.command.exportPending);
            return;
          }
          void exportPeaks(dataset.id, dataset.name, peaks).then(notifyExport);
        },
      },
      {
        id: "exportData",
        label: t.command.exportData,
        icon: <Download size={14} />,
        group: t.command.groupCommands,
        run: () => {
          const dataset = currentDataset();
          if (!dataset || !curve) {
            useUiStore.getState().showToast("error", t.command.exportPending);
            return;
          }
          void exportData(dataset.id, dataset.name, curve).then(notifyExport);
        },
      },
      {
        // S5 收尾:4 个温度点交差要整项目全套表,不该逐条点导出
        id: "exportBatch",
        label: t.command.exportBatch,
        icon: <Download size={14} />,
        group: t.command.groupCommands,
        run: () => {
          const projectId = useProjectStore.getState().currentProjectId;
          const list = samples
            .filter((s) => s.project_id === projectId)
            .flatMap((s) => [
              ...s.datasets.map((d) => ({ id: d.id, name: d.name, sampleName: s.name })),
              ...s.batches.flatMap((b) =>
                b.datasets.map((d) => ({ id: d.id, name: d.name, sampleName: s.name })),
              ),
            ]);
          if (list.length === 0) {
            useUiStore.getState().showToast("error", t.command.exportPending);
            return;
          }
          void exportBatch(list, { peaks: true, data: true }).then((r) => {
            if (r.cancelled) return;
            useUiStore.getState().showToast(
              r.ok ? "success" : "error",
              r.ok
                ? t.command.exportBatchDone(r.exported ?? 0)
                : (r.error ?? t.command.exportPending),
            );
          });
        },
      },
      {
        id: "exportFigure",
        label: t.command.exportFigure,
        icon: <Download size={14} />,
        group: t.command.groupCommands,
        run: () => {
          const dataset = currentDataset();
          const dataUrl = figureExporter?.();
          if (!dataset || !dataUrl) {
            useUiStore.getState().showToast("error", t.command.exportPending);
            return;
          }
          void exportFigure(dataset.id, dataset.name, dataUrl).then(notifyExport);
        },
      },
      {
        id: "exportFigureSvg",
        label: t.command.exportFigureSvg,
        icon: <Download size={14} />,
        group: t.command.groupCommands,
        run: () => {
          const dataset = currentDataset();
          const svg = figureSvgExporter?.();
          if (!dataset || !svg) {
            useUiStore.getState().showToast("error", t.command.exportPending);
            return;
          }
          void exportFigureSvg(dataset.name, svg).then(notifyExport);
        },
      },
      {
        id: "renameDataset",
        label: t.command.renameDataset,
        icon: <Pencil size={14} />,
        group: t.command.groupCommands,
        run: () => {
          const { datasetId, sampleId } = useSelectionStore.getState();
          if (datasetId == null) return;
          const name =
            useProjectStore
              .getState()
              .samples.find((s) => s.id === sampleId)
              ?.datasets.find((d) => d.id === datasetId)?.name ?? "";
          useUiStore.getState().setRenaming({ table: "datasets", id: datasetId, name });
        },
      },
      {
        id: "trashDataset",
        label: t.command.trashDataset,
        icon: <Trash2 size={14} />,
        group: t.command.groupCommands,
        run: () => {
          const { datasetId } = useSelectionStore.getState();
          if (datasetId == null) return;
          void useProjectStore.getState().deleteItem("datasets", datasetId);
        },
      },
      {
        id: "trend",
        label: t.trend.open,
        icon: <TrendingUp size={14} />,
        group: t.command.groupCommands,
        run: () => useUiStore.getState().setTrendOpen(true),
      },
      {
        id: "openTrash",
        label: t.command.openTrash,
        icon: <Trash2 size={14} />,
        group: t.command.groupCommands,
        run: () => useUiStore.getState().setTrashOpen(true),
      },
      {
        id: "theme",
        label:
          // 看**生效**主题而不是设置值:theme="system" 且系统深色时,
          // 按设置值判断会显示"切换到深色",点了却还是深色
          document.documentElement.dataset.theme === "dark"
            ? t.command.switchToLight
            : t.command.switchToDark,
        icon: document.documentElement.dataset.theme === "dark" ? <Sun size={14} /> : <Moon size={14} />,
        group: t.command.groupCommands,
        run: () =>
          setTheme(
            document.documentElement.dataset.theme === "dark" ? "light" : "dark",
          ),
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
  }, [samples, theme, setTheme, selectDataset, loadDataset, curve, peaks, figureExporter, figureSvgExporter]);

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
