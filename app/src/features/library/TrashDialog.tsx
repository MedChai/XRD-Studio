/** 回收站(§10.5 Trash 语义)。
 *
 * 三类对象扁平混排,每条标注完整层级路径 —— 回收站的核心任务是「找到那条
 * 被误删的记录」,一条路径字符串比展开一棵树快得多。
 *
 * 「彻底删除」是不可逆的,所以用**内联二次确认**(点一次变「确认删除?」)
 * 而不是再套一层 Dialog:嵌套 Dialog 在 Tauri WebView 里有 z-index 与焦点
 * 陷阱的老问题。
 */

import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useState } from "react";
import { Database, FolderOpen, RotateCcw, Trash2, X } from "lucide-react";
import { Button } from "../../components/controls";
import { usePlotStore, useProjectStore, useUiStore } from "../../stores";
import { COMPARE_COLORS, DISPLAY_MAX_POINTS, engineCommand, revealInExplorer } from "../../services/engine";
import type { CurvePayload } from "../../types/engine";
import type { TrashItem, TrashKind } from "../../types/engine";
import { t } from "../../i18n";
import { cn } from "../../components/cn";

const KIND_LABEL: Record<TrashKind, string> = {
  projects: t.trash.kindProjects,
  samples: t.trash.kindSamples,
  batches: t.trash.kindBatches,
  datasets: t.trash.kindDatasets,
  ec_cells: t.trash.kindEcCells,
};

function pathOf(item: TrashItem): string {
  const parts: string[] = [];
  if (item.projectName) parts.push(item.projectName);
  if (item.sampleName) parts.push(item.sampleName);
  if (item.kind === "datasets" || item.kind === "ec_cells") parts.push(item.name);
  return parts.join(" / ");
}

export function TrashDialog() {
  const open = useUiStore((s) => s.trashOpen);
  const setOpen = useUiStore((s) => s.setTrashOpen);
  const { trash, loadTrash, restoreItem, purgeItem, purgeAll } = useProjectStore();
  const [confirming, setConfirming] = useState<string | null>(null);
  const [confirmAll, setConfirmAll] = useState(false);
  const [busy, setBusy] = useState(false);

  // 打开时拉一次最新列表。
  // **必须用 useEffect 而不是 Dialog 的 onOpenChange**:open 是由 uiStore
  // 驱动的受控 prop,从外部把 open 置 true 时 Radix 不会回调 onOpenChange,
  // 放在那里的加载逻辑永远不会执行(回收站会显示成空的)。
  useEffect(() => {
    if (open) {
      void loadTrash();
      setConfirming(null);
      setConfirmAll(false);
    }
  }, [open, loadTrash]);

  // onOpenChange 只处理"用户关闭"(Escape / 点遮罩 / 点关闭按钮)
  const onOpenChange = (o: boolean) => {
    if (!o) setOpen(false);
  };

  async function run(fn: () => Promise<boolean>) {
    setBusy(true);
    await fn();
    setBusy(false);
    setConfirming(null);
    setConfirmAll(false);
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/25 backdrop-blur-[2px]" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 flex max-h-[60vh] w-[520px] -translate-x-1/2 -translate-y-1/2 flex-col rounded-[var(--radius-dialog)] bg-[var(--bg-panel)] p-5 shadow-[var(--shadow-dialog)]">
          <Dialog.Title className="flex items-center gap-2 text-[17px] font-semibold">
            <Trash2 size={16} className="text-[var(--text-secondary)]" />
            {t.trash.title}
            {trash.length > 0 && (
              <span className="tabular rounded-full bg-[var(--selected)] px-1.5 text-[11px] text-[var(--accent)]">
                {trash.length}
              </span>
            )}
          </Dialog.Title>

          <div className="mt-3 min-h-0 flex-1 overflow-y-auto">
            {trash.length === 0 ? (
              <div className="py-10 text-center text-[13px] text-[var(--text-tertiary)]">
                {t.trash.empty}
              </div>
            ) : (
              trash.map((item) => {
                const key = `${item.kind}:${item.id}`;
                const isConfirming = confirming === key;
                return (
                  <div
                    key={key}
                    className="flex items-center gap-3 border-b border-[var(--divider)] py-2 last:border-0"
                  >
                    <span className="text-[var(--text-tertiary)]">
                      {item.kind === "projects" ? (
                        <FolderOpen size={15} />
                      ) : (
                        <Database size={15} />
                      )}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13px] text-[var(--text-primary)]">
                        {item.kind === "projects" || item.kind === "samples"
                          ? item.name
                          : (pathOf(item).split(" / ").pop() ?? item.name)}
                      </div>
                      <div className="truncate text-[11px] text-[var(--text-tertiary)]">
                        {KIND_LABEL[item.kind]}
                        {pathOf(item) && ` · ${pathOf(item)}`}
                        {item.childCount > 0 && ` · ${t.trash.childHint(item.childCount)}`}
                      </div>
                    </div>
                    <Button
                      kind="ghost"
                      disabled={busy}
                      onClick={() => void run(() => restoreItem(item.kind, item.id))}
                    >
                      <RotateCcw size={12} /> {t.trash.restore}
                    </Button>
                    <Button
                      kind={isConfirming ? "danger" : "ghost"}
                      disabled={busy}
                      onClick={() => {
                        if (isConfirming) void run(() => purgeItem(item.kind, item.id));
                        else setConfirming(key);
                      }}
                    >
                      {isConfirming ? t.trash.confirmPurge : t.trash.purge}
                    </Button>
                  </div>
                );
              })
            )}
          </div>

          <div className="mt-4 flex items-center gap-2 border-t border-[var(--divider)] pt-3">
            <span className="flex-1 text-[11px] text-[var(--text-tertiary)]">
              {trash.length > 0 && t.trash.purgeWarning}
            </span>
            {trash.length > 0 && (
              <Button
                kind={confirmAll ? "danger" : "ghost"}
                disabled={busy}
                onClick={() => {
                  if (confirmAll) void run(() => purgeAll());
                  else setConfirmAll(true);
                }}
              >
                {confirmAll ? t.trash.confirmPurgeAll : t.trash.purgeAll}
              </Button>
            )}
            <Button kind="secondary" onClick={() => setOpen(false)}>
              <X size={13} /> {t.trash.close}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** 侧栏条目上的「⋯」菜单项集合,复用同一套文案与危险色。 */
/** 把一条数据集加入对比。曲线走 datasets.curve 单独取 —— 不走 analysisStore,
 *  因为对比曲线不是"当前分析对象",不该影响峰表与指标。 */
async function addCompare(
  datasetId: number,
  name: string,
  showToast: (kind: "success" | "error", msg: string) => void,
): Promise<void> {
  const store = usePlotStore.getState();
  if (store.compareCurves.length >= 6) {
    showToast("error", t.library.compareFull);
    return;
  }
  const r = await engineCommand<CurvePayload>("datasets.curve", {
    id: datasetId,
    max_points: DISPLAY_MAX_POINTS,
  });
  if (!r.ok) {
    showToast("error", r.error);
    return;
  }
  store.addCompareCurve({
    datasetId,
    name,
    color: COMPARE_COLORS[store.compareCurves.length % COMPARE_COLORS.length],
    curve: r.data,
  });
}

export function RowMenuItems({
  table,
  id,
  name,
  onClose,
}: {
  table: TrashKind;
  id: number;
  name: string;
  onClose: () => void;
}) {
  const setRenaming = useUiStore((s) => s.setRenaming);
  const setEditingSample = useUiStore((s) => s.setEditingSample);
  const deleteItem = useProjectStore((s) => s.deleteItem);
  const showToast = useUiStore((s) => s.showToast);
  const inCompare = usePlotStore((s) =>
    s.compareCurves.some((c) => c.datasetId === id),
  );
  return (
    <div className="flex flex-col">
      <button
        className="tr flex items-center gap-2 rounded-[var(--radius-control)] px-2.5 py-1.5 text-left text-[13px] text-[var(--text-primary)] hover:bg-[var(--hover)]"
        onClick={() => {
          onClose();
          setRenaming({ table, id, name });
        }}
      >
        {t.library.rename}
      </button>
      {/* 实验元数据(温度/时长/波长…)是趋势图自变量,必须有编辑入口。
          此前 SampleInfoDialog 写好了却从未挂载/接线,用户填不了温度。 */}
      {table === "samples" && (
        <button
          className="tr flex items-center gap-2 rounded-[var(--radius-control)] px-2.5 py-1.5 text-left text-[13px] text-[var(--text-primary)] hover:bg-[var(--hover)]"
          onClick={() => {
            onClose();
            setEditingSample({ id, name });
          }}
        >
          {t.sample.edit}
        </button>
      )}
      {/* 只有数据集能加入对比 —— 项目/实验/批次是容器,没有曲线可画 */}
      {table === "datasets" && (
        <button
          className="tr flex items-center gap-2 rounded-[var(--radius-control)] px-2.5 py-1.5 text-left text-[13px] text-[var(--text-primary)] hover:bg-[var(--hover)]"
          onClick={() => {
            onClose();
            if (inCompare) {
              usePlotStore.getState().removeCompareCurve(id);
              return;
            }
            void addCompare(id, name, showToast);
          }}
        >
          {inCompare ? t.library.removeFromCompare : t.library.addToCompare}
        </button>
      )}
      {/* 文件型对象才有磁盘位置。右键行与「⋯」菜单共用本组件 → 两处都有。 */}
      {(table === "datasets" || table === "ec_cells") && (
        <button
          className="tr flex items-center gap-2 rounded-[var(--radius-control)] px-2.5 py-1.5 text-left text-[13px] text-[var(--text-primary)] hover:bg-[var(--hover)]"
          onClick={() => {
            onClose();
            void (async () => {
              const res = await revealInExplorer(table, id);
              if (!res.ok) {
                useUiStore.getState().showToast("error", res.error);
              } else if (res.mode === "degraded") {
                // 浏览器 dev 打不开资源管理器 → 至少把路径给出来
                useUiStore.getState().showToast("success", t.library.revealPath(res.path));
              }
            })();
          }}
        >
          {t.library.revealInDir}
        </button>
      )}
      <button
        className={cn(
          "tr flex items-center gap-2 rounded-[var(--radius-control)] px-2.5 py-1.5 text-left text-[13px]",
          "text-[var(--danger)] hover:bg-[var(--hover)]",
        )}
        onClick={() => {
          onClose();
          void deleteItem(table, id);
        }}
      >
        {t.library.delete}
      </button>
    </div>
  );
}
