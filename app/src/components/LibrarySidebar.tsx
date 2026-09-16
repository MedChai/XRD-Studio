/** 左侧 Library(§17/§18):248px,Sample 条目卡片式,选中淡蓝不亮白。 */

import { useState } from "react";
import { ChevronDown, ChevronRight, Plus } from "lucide-react";
import { useProjectStore, useSelectionStore, useAnalysisStore, useUiStore } from "../stores";
import { cn } from "./cn";

export function LibrarySidebar() {
  const { samples, projects } = useProjectStore();
  const { datasetId, selectDataset } = useSelectionStore();
  const loadDataset = useAnalysisStore((s) => s.loadDataset);
  const { setImportOpen } = useUiStore();
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
        Library
      </div>
      <div className="px-3 pb-2">
        <div className="flex items-center gap-1.5 rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-2 py-1">
          <span className="text-[12px] text-[var(--text-tertiary)]">⌕</span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search"
            className="w-full bg-transparent text-[12px] text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] focus:outline-none"
          />
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto pb-3">
        {byProject.map(({ project, samples: ss }) => (
          <ProjectGroup
            key={project.id}
            name={project.name}
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
            还没有样品。点击 Import 开始。
          </div>
        )}
      </div>

      <div className="flex flex-col border-t border-[var(--divider)]">
        <button
          className="tr flex items-center gap-1.5 px-4 py-1.5 text-[12px] text-[var(--text-secondary)] hover:bg-[var(--hover)] hover:text-[var(--text-primary)]"
          onClick={() => useUiStore.getState().setNewProjectOpen(true)}
        >
          <Plus size={13} /> New Project
        </button>
        <button
          className="tr flex items-center gap-1.5 px-4 py-1.5 text-[12px] text-[var(--text-secondary)] hover:bg-[var(--hover)] hover:text-[var(--text-primary)]"
          onClick={() => setImportOpen(true)}
        >
          <Plus size={13} /> Import / New Sample
        </button>
      </div>
    </aside>
  );
}

function ProjectGroup({
  name,
  samples,
  expanded,
  toggle,
  selectedDatasetId,
  onSelect,
}: {
  name: string;
  samples: ReturnType<typeof useProjectStore.getState>["samples"];
  expanded: Set<number>;
  toggle: (id: number) => void;
  selectedDatasetId: number | null;
  onSelect: (sampleId: number, datasetId: number) => void;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div className="mb-1">
      <button
        className="tr flex w-full items-center gap-1 px-3 py-1 text-[12px] font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
        onClick={() => setOpen(!open)}
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {name}
      </button>
      {open &&
        samples.map((s) => {
          const isOpen = expanded.has(s.id);
          const hasSelected = s.datasets.some((d) => d.id === selectedDatasetId);
          return (
            <div key={s.id}>
              <button
                className={cn(
                  "tr mx-2 my-0.5 flex w-[calc(100%-16px)] items-center gap-1.5 rounded-[var(--radius-card)] px-2 py-1.5 text-left",
                  hasSelected
                    ? "bg-[var(--selected)]"
                    : "hover:bg-[var(--hover)]",
                )}
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
              {isOpen &&
                s.datasets.map((d) => (
                  <button
                    key={d.id}
                    className={cn(
                      "tr block w-full truncate py-1 pl-10 pr-3 text-left text-[12px]",
                      d.id === selectedDatasetId
                        ? "bg-[var(--selected)] text-[var(--accent)]"
                        : "text-[var(--text-secondary)] hover:bg-[var(--hover)]",
                    )}
                    onClick={() => onSelect(s.id, d.id)}
                  >
                    {d.name}
                  </button>
                ))}
            </div>
          );
        })}
    </div>
  );
}
