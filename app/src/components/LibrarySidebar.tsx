/** 左侧 Library(§17/§18):248px,Sample 条目卡片式,选中淡蓝不亮白。
 *
 * 每一层(项目 / 样品 / 数据集)都有 hover 才出现的「⋯」菜单,提供重命名与
 * 移到回收站。菜单用 Radix Popover,与顶栏 ProjectSwitcher 同一范式。
 */

import { useEffect, useState } from "react";
import * as Popover from "@radix-ui/react-popover";
import * as Dialog from "@radix-ui/react-dialog";
import {
  BatteryCharging, Boxes, ChevronDown, ChevronRight, FlaskConical,
  MoreHorizontal, Plus, Trash2,
} from "lucide-react";
import {
  useProjectStore,
  useSelectionStore,
  useAnalysisStore,
  useEcStore,
  useUiStore,
} from "../stores";
import type { DatasetNode, ProjectNode, TrashKind } from "../types/engine";
import { cn } from "./cn";
import { RowMenuItems } from "../features/library/TrashDialog";
import {
  RowContextMenu,
  useRowContextMenu,
  type RowContextOpen,
} from "./RowContextMenu";
import { Button } from "./controls";
import { engineCommand } from "../services/engine";
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
  const [createSampleOpen, setCreateSampleOpen] = useState(false);
  // 行右键菜单(文件行可"在资源管理器中显示")。菜单 portal 到 body,
  // 所以挂在这里的树里任何位置都行 —— 放 aside 尾部,不动现有嵌套。
  const { state: ctxMenu, open: openRowMenu, close: closeRowMenu } =
    useRowContextMenu();

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
            onRowContext={openRowMenu}
            onSelect={(sampleId, dsId) => {
              selectDataset(sampleId, dsId);
              void loadDataset(dsId);
            }}
          />
        ))}
        {samples.length === 0 && projects.length === 0 && (
          <div className="px-4 py-6 text-[12px] text-[var(--text-tertiary)]">
            {t.library.empty}
          </div>
        )}
        {/* 项目已建但一个实验都没有:给出「新建第一个实验」的直接入口。
            只显示「还没有样品」会让用户以为项目本身坏了。 */}
        {samples.length === 0 && projects.length > 0 && (
          <div className="flex flex-col gap-2 px-4 py-6 text-[12px] text-[var(--text-tertiary)]">
            <span>{t.library.noExperiments}</span>
            <button
              className="tr self-start rounded-[var(--radius-button)] border border-[var(--divider)] bg-[var(--bg-elevated)] px-2.5 py-1 text-[12px] font-medium text-[var(--text-primary)] hover:bg-[var(--hover)]"
              onClick={() => setCreateSampleOpen(true)}
            >
              {t.library.createFirst}
            </button>
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
          onClick={() => useUiStore.getState().setEcImportOpen(true)}
        >
          <BatteryCharging size={13} /> {t.echem.importEntry}
        </button>
        <button
          className="tr flex items-center gap-1.5 px-4 py-1.5 text-[12px] text-[var(--text-secondary)] hover:bg-[var(--hover)] hover:text-[var(--text-primary)]"
          onClick={() => setCreateSampleOpen(true)}
        >
          <FlaskConical size={13} /> {t.library.newExperiment}
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
      <NewExperimentDialog open={createSampleOpen} onOpenChange={setCreateSampleOpen} />
      {/* portal 到 body 渲染:祖先的 backdrop-filter 不会把 fixed 菜单困在侧栏里 */}
      <RowContextMenu state={ctxMenu} onClose={closeRowMenu} />
    </aside>
  );
}

/** 新建实验。侧栏此前没有这个入口 —— 样品只能作为导入的副作用产生,
 *  于是"先建好实验再往里导数据"做不到,导完发现归错组也只能删掉重来。 */
function NewExperimentDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const showToast = useUiStore((s) => s.showToast);
  const projects = useProjectStore((s) => s.projects);
  const currentProjectId = useProjectStore((s) => s.currentProjectId);
  const load = useProjectStore((s) => s.load);
  const [name, setName] = useState("");
  const [projectId, setProjectId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const project = projectId ?? currentProjectId ?? projects[0]?.id ?? null;

  async function submit() {
    if (!name.trim() || project == null) return;
    setBusy(true);
    const r = await engineCommand<{ sampleId: number }>("samples.create", {
      project_id: project,
      name: name.trim(),
    });
    setBusy(false);
    if (!r.ok) {
      showToast("error", r.error);
      return;
    }
    showToast("success", t.library.experimentCreated);
    setName("");
    onOpenChange(false);
    void load();
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/25 backdrop-blur-[2px]" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[380px] -translate-x-1/2 -translate-y-1/2 rounded-[var(--radius-dialog)] bg-[var(--bg-panel)] p-5 shadow-[var(--shadow-dialog)]">
          <Dialog.Title className="text-[17px] font-semibold">
            {t.library.newExperiment}
          </Dialog.Title>
          {projects.length === 0 ? (
            <div className="mt-4 text-[12px] text-[var(--text-tertiary)]">
              {t.library.empty}
            </div>
          ) : (
            <div className="mt-4 flex flex-col gap-3">
              <label className="flex items-center justify-between text-[13px] text-[var(--text-secondary)]">
                {t.import.projectLabel}
                <select
                  value={project ?? ""}
                  onChange={(e) => setProjectId(Number(e.target.value))}
                  className="w-52 rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-2 py-1 text-[12px]"
                >
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center justify-between text-[13px] text-[var(--text-secondary)]">
                {t.library.newExperimentName}
                <input
                  autoFocus
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void submit();
                  }}
                  placeholder={t.library.newExperimentPlaceholder}
                  className="w-52 rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-2 py-1 text-[12px] focus:border-[var(--accent)] focus:outline-none"
                />
              </label>
              <div className="mt-1 flex justify-end gap-2">
                <Button onClick={() => onOpenChange(false)}>{t.import.cancel}</Button>
                <Button kind="primary" onClick={() => void submit()} disabled={busy || !name.trim()}>
                  {t.import.import}
                </Button>
              </div>
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
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
  onRowContext,
}: {
  project: ProjectNode;
  samples: ReturnType<typeof useProjectStore.getState>["samples"];
  expanded: Set<number>;
  toggle: (id: number) => void;
  selectedDatasetId: number | null;
  onSelect: (sampleId: number, datasetId: number) => void;
  /** 行右键 → 光标处的上下文菜单(文件行带"在资源管理器中显示") */
  onRowContext: RowContextOpen;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div className="mb-1">
      <div
        className="group flex items-center gap-1 px-3 py-1"
        onContextMenu={(e) =>
          onRowContext(e, { table: "projects", id: project.id, name: project.name })
        }
      >
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
                onContextMenu={(e) =>
                  onRowContext(e, { table: "samples", id: s.id, name: s.name })
                }
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
              {/* 批次:同样品多批送样时才有。没有批次的数据集直接挂在
                  实验下 —— 不强迫用户维护一个他不需要的层级。 */}
              {isOpen &&
                (s.batches ?? []).map((b) => (
                  <div key={`b${b.id}`}>
                    <div
                      className="tr group mx-2 my-0.5 flex w-[calc(100%-16px)] items-center gap-1.5 rounded-[var(--radius-card)] px-2 py-1 pl-6 hover:bg-[var(--hover)]"
                      onContextMenu={(e) =>
                        onRowContext(e, { table: "batches", id: b.id, name: b.name })
                      }
                    >
                      <Boxes size={11} className="shrink-0 text-[var(--text-tertiary)]" />
                      <span className="min-w-0 flex-1 truncate text-[12px] text-[var(--text-secondary)]">
                        {b.name}
                      </span>
                      <span className="tabular text-[11px] text-[var(--text-tertiary)]">
                        {b.datasets.length}
                      </span>
                      <RowMenu table="batches" id={b.id} name={b.name} />
                    </div>
                    {b.datasets.map((d: DatasetNode) => (
                      <div
                        key={`bd${d.id}`}
                        className="tr group mx-2 my-0.5 flex w-[calc(100%-16px)] items-center gap-1 rounded-[var(--radius-card)] px-2 py-1 pl-10 hover:bg-[var(--hover)]"
                        onContextMenu={(e) =>
                          onRowContext(e, { table: "datasets", id: d.id, name: d.name })
                        }
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
                ))}

              {isOpen &&
                s.datasets
                  .filter((d: DatasetNode) => d.batch_id == null)
                  .map((d: DatasetNode) => (
                  <div
                    key={d.id}
                    className={cn(
                      "tr group flex w-full items-center gap-1 pl-8 pr-2",
                      d.id === selectedDatasetId
                        ? "bg-[var(--selected)]"
                        : "hover:bg-[var(--hover)]",
                    )}
                    onContextMenu={(e) =>
                      onRowContext(e, { table: "datasets", id: d.id, name: d.name })
                    }
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

              {/* 电化学 cell:与数据集并列,电池图标 + 圈数徽标区分。
                  懒加载(展开才请求),缓存失效由导入/删除触发。 */}
              {isOpen && (
                <EcCellsRow sampleId={s.id} onRowContext={onRowContext} />
              )}
            </div>
          );
        })}
    </div>
  );
}

/** 样品下的电化学 cell 列表(ec.cells,懒加载)。 */
function EcCellsRow({
  sampleId,
  onRowContext,
}: {
  sampleId: number;
  onRowContext: RowContextOpen;
}) {
  const cells = useEcStore((s) => s.cellsBySample[sampleId]);
  const loadCells = useEcStore((s) => s.loadCells);
  const selectedCellId = useSelectionStore((s) => s.cellId);
  const selectCell = useSelectionStore((s) => s.selectCell);
  const loadCell = useEcStore((s) => s.loadCell);

  useEffect(() => {
    void loadCells(sampleId);
  }, [sampleId, loadCells]);

  if (!cells || cells.length === 0) return null;
  return (
    <div>
      <div className="flex items-center gap-1 px-3 pb-0.5 pt-1.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
        <BatteryCharging size={10} className="shrink-0" />
        {t.echem.cellsSection}
      </div>
      {cells.map((c) => (
        <div
          key={c.id}
          className={cn(
            "tr group mx-2 my-0.5 flex w-[calc(100%-16px)] items-center gap-1 rounded-[var(--radius-card)] px-2 py-1 pl-7",
            c.id === selectedCellId ? "bg-[var(--selected)]" : "hover:bg-[var(--hover)]",
          )}
          onContextMenu={(e) =>
            onRowContext(e, { table: "ec_cells", id: c.id, name: c.name })
          }
        >
          <button
            className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
            onClick={() => {
              selectCell(sampleId, c.id);
              void loadCell(c.id);
            }}
          >
            <BatteryCharging
              size={11}
              className={cn(
                "shrink-0",
                c.id === selectedCellId
                  ? "text-[var(--accent)]"
                  : "text-[var(--text-tertiary)]",
              )}
            />
            <span
              className={cn(
                "min-w-0 flex-1 truncate text-[12px]",
                c.id === selectedCellId
                  ? "text-[var(--accent)]"
                  : "text-[var(--text-secondary)]",
              )}
            >
              {c.name}
            </span>
            <span className="tabular shrink-0 text-[11px] text-[var(--text-tertiary)]">
              {t.echem.cyclesBadge(c.cycleCount)}
            </span>
          </button>
          <RowMenu table="ec_cells" id={c.id} name={c.name} />
        </div>
      ))}
    </div>
  );
}
