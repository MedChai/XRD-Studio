import { useEffect, useRef, useState } from "react";
import * as Popover from "@radix-ui/react-popover";
import { BatteryCharging, ChevronDown, ChevronRight, Folder, FolderOpen, MoreHorizontal, Plus, RefreshCw } from "lucide-react";
import { cn } from "../../../components/cn";
import { Button } from "../../../components/controls";
import { useSelectionStore, useUiStore } from "../../../stores";
import { cexCommand, errorText } from "./model";
import { openCexFiles, useCexSession } from "./session";

interface FolderEntry {
  path: string;
  name: string;
  kind: "folder" | "cex";
}

interface FolderAccess {
  root: string;
  activePath: string | undefined;
  showMenu: (path: string, x: number, y: number) => void;
}

const rowClass = "tr flex min-w-0 items-center gap-1.5 rounded-[var(--radius-input)] px-2 py-1 text-left text-[12px] hover:bg-[var(--hover)]";

export function CexFolderSidebar({ active }: { active: boolean }) {
  const [directory, setDirectory] = useState<{ path: string; revision: number }>();
  const [picking, setPicking] = useState(false);
  const [menuPath, setMenuPath] = useState<string | null>(null);
  const anchor = useRef<{ getBoundingClientRect: () => DOMRect } | null>(null);
  const cexActive = useSelectionStore((s) => s.cexActive);
  const documentPath = useCexSession((s) => s.documents.find((doc) => doc.id === s.active)?.path);

  useEffect(() => {
    if (!active) setMenuPath(null);
  }, [active]);

  function showMenu(path: string, x: number, y: number) {
    anchor.current = { getBoundingClientRect: () => new DOMRect(x, y, 0, 0) };
    setMenuPath(path);
  }

  async function pickFolder() {
    const notify = useUiStore.getState().showToast;
    if (!("__TAURI_INTERNALS__" in window)) {
      notify("error", "请在 Windows 桌面版选择蓝电数据文件夹");
      return;
    }
    setPicking(true);
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const path = await open({ directory: true, multiple: false, title: "选择蓝电数据文件夹" });
      if (path) setDirectory((old) => ({ path, revision: (old?.revision ?? 0) + 1 }));
    } catch (error) {
      notify("error", errorText(error));
    } finally {
      setPicking(false);
    }
  }

  return (
    <div className={cn("min-h-0 flex-1 flex-col", active ? "flex" : "hidden")}>
      <div className="flex items-center justify-between gap-1 px-3 pb-2">
        <Button disabled={picking} onClick={() => void pickFolder()}>
          <FolderOpen size={14} /> 选择文件夹
        </Button>
        {directory && (
          <button type="button" aria-label="刷新蓝电文件夹" title="刷新蓝电文件夹"
            className="tr rounded-[var(--radius-control)] p-1.5 text-[var(--text-secondary)] hover:bg-[var(--hover)]"
            onClick={() => setDirectory({ ...directory, revision: directory.revision + 1 })}>
            <RefreshCw size={14} />
          </button>
        )}
      </div>
      {directory && (
        <>
          <div title={directory.path} className="flex items-center gap-1.5 border-b border-[var(--divider)] px-3 py-2 text-[12px] font-medium">
            <FolderOpen size={14} className="shrink-0 text-[var(--text-secondary)]" />
            <span className="truncate">{directory.path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || directory.path}</span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-2 py-1" onScroll={() => setMenuPath(null)}>
            <FolderContents key={directory.revision} root={directory.path} path={directory.path}
              activePath={cexActive ? documentPath : undefined} showMenu={showMenu} />
          </div>
        </>
      )}
      <Popover.Root open={active && menuPath != null} onOpenChange={(open) => { if (!open) setMenuPath(null); }}>
        <Popover.Anchor virtualRef={anchor} />
        <Popover.Portal>
          <Popover.Content align="start" sideOffset={3} collisionPadding={6} aria-label="蓝电文件操作"
            onCloseAutoFocus={(event) => event.preventDefault()}
            className="z-50 w-36 rounded-[var(--radius-panel)] border border-[var(--divider)] bg-[var(--bg-elevated)] p-1 shadow-[var(--shadow-dialog)]">
            <button type="button" className={cn(rowClass, "w-full")} onClick={() => {
              if (menuPath) {
                setMenuPath(null);
                void openCexFiles([menuPath], useSelectionStore.getState().sampleId);
              }
            }}>
              <Plus size={14} /> 添加到样品
            </button>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
    </div>
  );
}

function FolderContents({ path, ...access }: FolderAccess & { path: string }) {
  const { root, activePath, showMenu } = access;
  const [listing, setListing] = useState<{ entries: FolderEntry[] } | { error: string }>();
  useEffect(() => {
    let current = true;
    void cexCommand<{ entries: FolderEntry[] }>("scan_folder", { root, folder: path }).then(
      (result) => { if (current) setListing(result); },
      (error: unknown) => { if (current) setListing({ error: errorText(error) }); },
    );
    return () => { current = false; };
  }, [root, path]);

  if (!listing || "error" in listing || !listing.entries.length) {
    const message = !listing ? "正在读取文件夹…" : "error" in listing ? listing.error : "这里没有子文件夹或 CEX 文件";
    return <p role="status" className="break-words px-2 py-2 text-[12px] text-[var(--text-secondary)]">{message}</p>;
  }
  return (
    <ul className="space-y-0.5">
      {listing.entries.map((entry) => entry.kind === "folder" ? (
        <FolderRow key={entry.path} entry={entry} {...access} />
      ) : (
        <li key={entry.path} className="group relative" onContextMenu={(event) => {
          event.preventDefault();
          const rect = event.currentTarget.getBoundingClientRect();
          showMenu(entry.path, event.clientX || rect.left, event.clientY || rect.bottom);
        }}>
          <button type="button" title={entry.path} aria-current={activePath === entry.path ? "true" : undefined}
            className={cn(rowClass, "w-full pr-7", activePath === entry.path && "bg-[var(--selected)] text-[var(--accent)]")}
            onClick={() => void openCexFiles([entry.path])}>
            <BatteryCharging size={14} className="ml-3.5 shrink-0" />
            <span className="truncate">{entry.name}</span>
          </button>
          <button type="button" aria-label={`更多操作 · ${entry.name}`} aria-haspopup="dialog"
            className="absolute right-0.5 top-1/2 -translate-y-1/2 rounded p-1 text-[var(--text-secondary)] opacity-0 hover:bg-[var(--hover)] group-hover:opacity-100 focus-visible:opacity-100"
            onClick={(event) => {
              const rect = event.currentTarget.getBoundingClientRect();
              showMenu(entry.path, rect.left, rect.bottom);
            }}>
            <MoreHorizontal size={14} />
          </button>
        </li>
      ))}
    </ul>
  );
}

function FolderRow({ entry, ...access }: FolderAccess & { entry: FolderEntry }) {
  const [expanded, setExpanded] = useState(false);
  const Chevron = expanded ? ChevronDown : ChevronRight;
  const Icon = expanded ? FolderOpen : Folder;
  return (
    <li>
      <button type="button" title={entry.path} aria-expanded={expanded} className={cn(rowClass, "w-full")}
        onClick={() => setExpanded(!expanded)}>
        <Chevron size={12} className="shrink-0 text-[var(--text-secondary)]" />
        <Icon size={14} className="shrink-0 text-[var(--text-secondary)]" />
        <span className="truncate">{entry.name}</span>
      </button>
      {expanded && (
        <div className="ml-3 border-l border-[var(--divider)] pl-1">
          <FolderContents path={entry.path} {...access} />
        </div>
      )}
    </li>
  );
}
