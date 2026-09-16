/** AppShell(§15):TopBar / Sidebar / Workspace(Plot+PeakDrawer) /
 * Inspector / StatusBar 主布局,加:
 *  - 全局键盘(Ctrl+K、Ctrl+Z/Shift+Z、双击图 Fit All 在 chart 内)
 *  - Tauri 拖放事件(§10.6,经 tauri-plugin 的 onDragDropEvent 拿真实路径)
 *  - 空状态(§67)
 */

import { useCallback, useEffect, useState } from "react";
import { TopBar } from "./components/TopBar";
import { LibrarySidebar } from "./components/LibrarySidebar";
import { StatusBar } from "./components/StatusBar";
import { Toast } from "./components/Toast";
import { NewProjectDialog } from "./components/ProjectSwitcher";
import { RenameDialog } from "./features/library/RenameDialog";
import { TrashDialog } from "./features/library/TrashDialog";
import { PlotHeader } from "./features/plot/PlotHeader";
import { XRDChart } from "./features/plot/XRDChart";
import { RangeOverview } from "./features/plot/RangeOverview";
import { PeakDrawer } from "./features/peaks/PeakDrawer";
import { Inspector } from "./features/processing/Inspector";
import { ImportSheet } from "./features/import/ImportSheet";
import { CommandPalette } from "./features/settings/CommandPalette";
import { SettingsDialog } from "./features/settings/SettingsDialog";
import {
  applyTheme,
  useAnalysisStore,
  useAppStore,
  usePlotStore,
  useProjectStore,
  useSelectionStore,
  useUiStore,
} from "./stores";
import { Button } from "./components/controls";
import { t } from "./i18n";

const isTauri = "__TAURI_INTERNALS__" in window;

/** 底部范围框高度。只在有曲线时才占位,否则空状态下方会多一条空白带。 */
const rangeOverviewHeight = 56;

export default function App() {
  const theme = useAppStore((s) => s.theme);
  const loadLibrary = useProjectStore((s) => s.load);
  const { curve, loadedDatasetId } = useAnalysisStore();
  const [dragging, setDragging] = useState(false);

  // 主题初始化
  useEffect(() => {
    applyTheme(theme);
    if (theme === "system") {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      const h = () => applyTheme("system");
      mq.addEventListener("change", h);
      return () => mq.removeEventListener("change", h);
    }
  }, [theme]);

  // Library 加载 + 默认选中第一个数据集
  useEffect(() => {
    void loadLibrary().then(() => {
      const { samples } = useProjectStore.getState();
      const { datasetId } = useSelectionStore.getState();
      const first = samples[0]?.datasets[0];
      if (first && datasetId == null) {
        useSelectionStore.getState().selectDataset(samples[0].id, first.id);
        void useAnalysisStore.getState().loadDataset(first.id);
      }
    });
    // 回收站徽标要在启动时就有数
    void useProjectStore.getState().loadTrash();
  }, [loadLibrary]);

  // 全局键盘(§44/§73)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "k") {
        e.preventDefault();
        useUiStore.getState().setPaletteOpen(!useUiStore.getState().paletteOpen);
      } else if (mod && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) useAnalysisStore.getState().redo();
        else useAnalysisStore.getState().undo();
      } else if (mod && e.key === "i") {
        e.preventDefault();
        useUiStore.getState().setImportOpen(true);
      } else if (mod && e.key === "0") {
        // Ctrl+0 = Fit All(与 macOS 惯例一致)
        e.preventDefault();
        usePlotStore.getState().fitAll();
      } else if (e.key === "Escape") {
        useUiStore.getState().setPaletteOpen(false);
        useUiStore.getState().setTrashOpen(false);
        useUiStore.getState().setRenaming(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // 拖放:Tauri 下用 webview 拖放事件拿真实路径(§10.6)
  const registerDragDrop = useCallback(async () => {
    if (!isTauri) return;
    const { getCurrentWebview } = await import("@tauri-apps/api/webview");
    return getCurrentWebview().onDragDropEvent((event) => {
      if (event.payload.type === "enter" || event.payload.type === "over") {
        setDragging(true);
      } else if (event.payload.type === "drop") {
        setDragging(false);
        useUiStore.getState().setPendingDropPaths(event.payload.paths);
      } else {
        setDragging(false);
      }
    });
  }, []);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    void registerDragDrop().then((u) => {
      unlisten = u;
    });
    return () => unlisten?.();
  }, [registerDragDrop]);

  const empty = !curve && loadedDatasetId == null;

  return (
    <div className="flex h-full flex-col">
      <TopBar />
      <div className="flex min-h-0 flex-1">
        <LibrarySidebar />
        <main className="flex min-w-0 flex-1 flex-col">
          <PlotHeader />
          <div className="relative min-h-0 flex-1">
            {empty ? (
              <EmptyState />
            ) : (
              <XRDChart />
            )}
            {dragging && <DropOverlay />}
          </div>
          <PeakDrawer />
        </main>
        <Inspector />
      </div>
      {/* 底部通栏范围框:横跨 Sidebar + 绘图区 + Inspector。
          内部左右留出与两栏等宽的内边距,使缩略图与绘图区对齐,
          而容器底色保持通栏 —— 视觉上是"通栏分隔带 + 居中的缩略图"。
          没有曲线时不占位,免得空状态下方多一条空白带。 */}
      {!empty && (
        <div
          className="shrink-0 border-t border-[var(--divider)] bg-[var(--bg-panel)]"
          style={{ height: rangeOverviewHeight }}
        >
          <div
            className="h-full"
            style={{ paddingLeft: "var(--sidebar-w)", paddingRight: "var(--inspector-w)" }}
          >
            <RangeOverview />
          </div>
        </div>
      )}
      <StatusBar />

      <ImportSheet />
      <NewProjectDialog />
      <RenameDialog />
      <TrashDialog />
      <CommandPalette />
      <SettingsDialog />
      <Toast />
    </div>
  );
}

/** 空状态(§67):无大插画,一句话一个按钮。 */
function EmptyState() {
  const setImportOpen = useUiStore((s) => s.setImportOpen);
  return (
    <div className="flex h-full flex-col items-center justify-center gap-5">
      <div className="text-[22px] font-semibold text-[var(--text-primary)]">{t.app.name}</div>
      <div className="text-[13px] text-[var(--text-secondary)]">
        {t.empty.hint}
      </div>
      <Button kind="primary" onClick={() => setImportOpen(true)}>
        {t.empty.importButton}
      </Button>
      <div className="text-[11px] text-[var(--text-tertiary)]">{t.import.formats}</div>
    </div>
  );
}

/** 拖放态(§43):仅淡蓝 overlay。 */
function DropOverlay() {
  return (
    <div className="absolute inset-2 z-30 flex flex-col items-center justify-center rounded-[var(--radius-panel)] border-2 border-dashed border-[var(--accent)] bg-[color-mix(in_srgb,var(--accent)_8%,transparent)]">
      <div className="text-[15px] font-medium text-[var(--accent)]">{t.empty.dropHere}</div>
      <div className="mt-1 text-[12px] text-[var(--text-secondary)]">{t.import.formats}</div>
    </div>
  );
}
