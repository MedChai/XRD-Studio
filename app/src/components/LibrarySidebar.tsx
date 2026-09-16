/** 左侧 Library(§17/§18):248px,Sample 条目卡片式,选中淡蓝不亮白。
 *
 * 每一层(项目 / 样品 / 数据集)都有 hover 才出现的「⋯」菜单,提供重命名与
 * 移到回收站。菜单用 Radix Popover,与顶栏 ProjectSwitcher 同一范式。
 */

import { useState } from "react";
import * as Popover from "@radix-ui/react-popover";
import { ChevronDown, ChevronRight, MoreHorizontal, Plus, Trash2 } from "lucide-react";
import {
  useProjectStore,
  useSelectionStore,
  useAnalysisStore,
  useUiStore,
} from "../stores";
import type { DatasetNode, ProjectNode, TrashKind } from "../types/engine";
import { cn } from "./cn";
import { RowMenuItems } from "../features/library/TrashDialog";
import { t } from "../i18n";

export function LibrarySidebar() {
  const samples = useProjectStore((s) => s.samples);
  const projects = useProjectStore((s) => s.projects);
  const datasetId = useSelectionStore((s) => s.datasetId);
  const selectDataset = useSelectionStore((s) => s.selectDataset);
  const loadDataset = useAnalysisStore((s) => s.loadDataset);
  // 选择器订阅:uiStore 里的 toast 计时器每次变化都会触发重渲染,
  // 全量解构会让整个侧栏跟着抖
  const setImportOpen = useUiStore((s) => s.setImportOpen);
  const setTrashOpen = useUiStore((s) => s.setTrashOpen);
  // 取 length 而非整个数组:选择器返回原始值,不会因数组引用变化而重渲染
  const trashCount = useProjectStore((s) => s.trash.length);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [query, setQuery] = useState("");

  const byProject = projects.map((p) => ({
    project: p,
    samples: samples.filter(
      (s) =>
        s.project_id === p.id &&
        (query === "" || s.name.toLowerCase().includes(query.toLowerCase())),
    ),
  }));

  return (
    <aside
      className="blur-surface flex shrink-0 flex-col border-r border-[var(--divider)] bg-[var(--bg-sidebar)]"
      style={{ width: "var(--sidebar-w)" }}
    >
      <div className="px-3 pt-3 pb-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
        {t.library.title}
      </div>
      <div className="px-3 pb-2">
        <div className="flex items-center gap-1.5 rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-2 py-1">
          <span className="text-[12px] text-[var(--text-tertiary)]">⌕</span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t.library.search}
            className="w-full bg-transparent text-[12px] text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] focus:outline-none"
          />
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto pb-3">
        {byProject.map(({ project, samples: ss }) => (
          <ProjectGroup
            key={project.id}
            project={project}
            samples={ss}
            expanded={expanded}
            toggle={(id) =>
              setExpanded((prev) => {
                const next = new Set(prev);
                next.has(id) ? next.delete(id) : next.add(id);
                return next;
              })
            }
            selectedDatasetId={datasetId}
            onSelect={(sampleId, dsId) => {
              selectDataset(sampleId, dsId);
              void loadDataset(dsId);
            }}
          />
        ))}
        {samples.length === 0 && (
          <div className="px-4 py-6 text-[12px] text-[var(--text-tertiary)]">
            {t.library.empty}
          </div>
        )}
      </div>

      <div className="flex flex-col border-t border-[var(--divider)]">
        <button
          className="tr flex items-center gap-1.5 px-4 py-1.5 text-[12px] text-[var(--text-secondary)] hover:bg-[var(--hover)] hover:text-[var(--text-primary)]"
          onClick={() => useUiStore.getState().setNewProjectOpen(true)}
        >
          <Plus size={13} /> {t.library.newProject}
        </button>
        <button
          className="tr flex items-center gap-1.5 px-4 py-1.5 text-[12px] text-[var(--text-secondary)] hover:bg-[var(--hover)] hover:text-[var(--text-primary)]"
          onClick={() => setImportOpen(true)}
        >
          <Plus size={13} /> {t.library.importNewSample}
        </button>
        <button
          className="tr flex items-center gap-1.5 px-4 py-1.5 text-[12px] text-[var(--text-secondary)] hover:bg-[var(--hover)] hover:text-[var(--text-primary)]"
          onClick={() => setTrashOpen(true)}
        >
          <Trash2 size={13} /> {t.library.trash}
          {trashCount > 0 && (
            <span className="tabular rounded-full bg-[var(--selected)] px-1.5 text-[11px] text-[var(--accent)]">
              {trashCount}
            </span>
          )}
        </button>
      </div>
    </aside>
  );
}

/** 一行的「⋯」菜单。Radix Popover,trigger 只在 hover / 聚焦时显形。 */
function RowMenu({
  table,
  id,
  name,
  className,
}: {
  table: TrashKind;
  id: number;
  name: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button
          className={cn(
            "tr flex h-5 w-5 shrink-0 items-center justify-center rounded-[4px] text-[var(--text-tertiary)] hover:bg-[var(--hover)] hover:text-[var(--text-primary)]",
            !open && "opacity-0 group-hover:opacity-100 focus:opacity-100",
            className,
          )}
          title={t.library.more}
          onClick={(e) => e.stopPropagation()}
        >
          <MoreHorizontal size={13} />
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="start"
          sideOffset={4}
          className="z-50 w-36 rounded-[var(--radius-panel)] border border-[var(--divider)] bg-[var(--bg-elevated)] p-1 shadow-[var(--shadow-dialog)] backdrop-blur-2xl"
        >
          <RowMenuItems
            table={table}
            id={id}
            name={name}
            onClose={() => setOpen(false)}
          />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

function ProjectGroup({
  project,
  samples,
  expanded,
  toggle,
  selectedDatasetId,
  onSelect,
}: {
  project: ProjectNode;
  samples: ReturnType<typeof useProjectStore.getState>["samples"];
  expanded: Set<number>;
  toggle: (id: number) => void;
  selectedDatasetId: number | null;
  onSelect: (sampleId: number, datasetId: number) => void;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div className="mb-1">
      <div className="group flex items-center gap-1 px-3 py-1">
        <button
          className="tr flex min-w-0 flex-1 items-center gap-1 text-[12px] font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
          onClick={() => setOpen(!open)}
        >
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          <span className="truncate">{project.name}</span>
        </button>
        <RowMenu table="projects" id={project.id} name={project.name} />
      </div>
      {open &&
        samples.map((s) => {
          const isOpen = expanded.has(s.id);
          const hasSelected = s.datasets.some((d) => d.id === selectedDatasetId);
          return (
            <div key={s.id}>
              <div
                className={cn(
                  "tr group mx-2 my-0.5 flex w-[calc(100%-16px)] items-center gap-1.5 rounded-[var(--radius-card)] px-2 py-1.5",
                  hasSelected ? "bg-[var(--selected)]" : "hover:bg-[var(--hover)]",
                )}
              >
                <button
                  className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
                  onClick={() => toggle(s.id)}
                >
                  <span
                    className={cn(
                      "h-2 w-2 shrink-0 rounded-full",
                      hasSelected ? "bg-[var(--accent)]" : "bg-[var(--text-tertiary)]",
                    )}
                  />
                  <span className="flex-1 truncate text-[13px] text-[var(--text-primary)]">
                    {s.name}
                  </span>
                  <span className="tabular text-[11px] text-[var(--text-tertiary)]">
                    {s.dataset_count}
                  </span>
                </button>
                <RowMenu table="samples" id={s.id} name={s.name} />
              </div>
              {isOpen &&
                s.datasets.map((d: DatasetNode) => (
                  <div
                    key={d.id}
                    className={cn(
                      "tr group flex w-full items-center gap-1 pl-8 pr-2",
                      d.id === selectedDatasetId
                        ? "bg-[var(--selected)]"
                        : "hover:bg-[var(--hover)]",
                    )}
                  >
                    <button
                      className={cn(
                        "min-w-0 flex-1 truncate py-1 pr-1 text-left text-[12px]",
                        d.id === selectedDatasetId
                          ? "text-[var(--accent)]"
                          : "text-[var(--text-secondary)]",
                      )}
                      onClick={() => onSelect(s.id, d.id)}
                    >
                      {d.name}
                    </button>
                    <RowMenu table="datasets" id={d.id} name={d.name} />
                  </div>
                ))}
            </div>
          );
        })}
    </div>
  );
}
