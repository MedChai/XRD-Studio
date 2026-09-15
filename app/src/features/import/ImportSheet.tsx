/** 导入 Sheet(§40/§42):小弹窗,不做 Wizard;失败文件走列映射。
 *
 * 文件来源(§10.6):Tauri 环境下用原生文件对话框拿真实路径;
 * 拖放事件由 AppShell 统一接收(webview 拖放事件提供路径)。
 * 纯浏览器开发模式下用文件名占位 + mock 引擎。
 */

import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Button } from "../../components/controls";
import { engineCommand } from "../../services/engine";
import type { ImportResult } from "../../types/engine";
import { useProjectStore, useUiStore } from "../../stores";
import { cn } from "../../components/cn";

const isTauri = "__TAURI_INTERNALS__" in window;

export function ImportSheet() {
  const open = useUiStore((s) => s.importOpen);
  const setOpen = useUiStore((s) => s.setImportOpen);
  const showToast = useUiStore((s) => s.showToast);
  const pendingDrop = useUiStore((s) => s.pendingDropPaths);
  const setPendingDrop = useUiStore((s) => s.setPendingDropPaths);
  const { projects, currentProjectId, load } = useProjectStore();

  const [paths, setPaths] = useState<string[]>([]);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [newSampleName, setNewSampleName] = useState("");
  const [busy, setBusy] = useState(false);
  const [mapping, setMapping] = useState<ImportResult["needColumns"][0] | null>(null);
  const [mapX, setMapX] = useState(1);
  const [mapY, setMapY] = useState(2);

  const project = projectId ?? currentProjectId ?? projects[0]?.id ?? null;

  // 拖放进来的文件路径(AppShell 写入)自动填入
  useEffect(() => {
    if (pendingDrop && pendingDrop.length > 0) {
      setPaths(pendingDrop);
      setPendingDrop(null);
      setOpen(true);
    }
  }, [pendingDrop, setOpen, setPendingDrop]);

  async function pickFiles() {
    if (isTauri) {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const selected = await open({
        multiple: true,
        filters: [{ name: "XRD 数据", extensions: ["csv", "txt", "xy", "dat"] }],
      });
      if (selected) setPaths(selected as string[]);
    } else {
      const input = document.createElement("input");
      input.type = "file";
      input.multiple = true;
      input.accept = ".csv,.txt,.xy,.dat";
      input.onchange = () =>
        setPaths(Array.from(input.files ?? []).map((f) => f.name));
      input.click();
    }
  }

  async function runImport(xColumn?: number, yColumn?: number) {
    if (!paths.length || project == null) return;
    setBusy(true);
    const r = await engineCommand<ImportResult>("datasets.import", {
      paths,
      project_id: project,
      ...(mapping
        ? { x_column: (xColumn ?? mapX) - 1, y_column: (yColumn ?? mapY) - 1, sample_id: useUiStore.getState().lastSampleId }
        : { new_sample: { name: newSampleName || "New Sample" } }),
    });
    setBusy(false);
    if (!r.ok) {
      showToast("error", r.error);
      return;
    }
    const d = r.data;
    if (d.needColumns.length > 0 && !mapping) {
      setMapping(d.needColumns[0]);
      setMapX(d.needColumns[0].suggestedX + 1);
      setMapY(d.needColumns[0].suggestedY + 1);
      return;
    }
    if (d.duplicates.length > 0) {
      const dup = d.duplicates[0];
      showToast(
        "error",
        `重复数据：${dup.existing.projectName} / ${dup.existing.sampleName} / ${dup.existing.datasetName}`,
      );
      return;
    }
    useUiStore.getState().setLastSampleId(d.sampleId);
    const parts: string[] = [];
    if (d.imported.length) parts.push(`导入 ${d.imported.length} 个数据集`);
    if (d.failures.length) parts.push(`${d.failures.length} 个文件无法读取`);
    showToast("success", parts.join(" · ") || "完成");
    if (d.imported.length) {
      setPaths([]);
      setNewSampleName("");
      setMapping(null);
      setOpen(false);
      void load();
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/25 backdrop-blur-[2px]" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[460px] -translate-x-1/2 -translate-y-1/2 rounded-[var(--radius-dialog)] bg-[var(--bg-panel)] p-5 shadow-[var(--shadow-dialog)]">
          <Dialog.Title className="text-[17px] font-semibold">
            {mapping ? "选择数据列" : "Import XRD Data"}
          </Dialog.Title>

          {!mapping ? (
            <div className="mt-4 flex flex-col gap-3">
              <button
                type="button"
                onClick={() => void pickFiles()}
                className={cn(
                  "tr flex h-20 flex-col items-center justify-center gap-1 rounded-[var(--radius-panel)] border border-dashed border-[var(--divider)] text-[12px] text-[var(--text-tertiary)] hover:border-[var(--accent)] hover:text-[var(--text-secondary)]",
                  paths.length > 0 && "border-[var(--accent)] text-[var(--text-primary)]",
                )}
              >
                {paths.length > 0 ? (
                  <>
                    <span className="font-medium">
                      {paths.length === 1
                        ? paths[0].split(/[\\/]/).pop()
                        : `${paths.length} files`}
                    </span>
                    {paths.length > 1 && (
                      <span className="truncate text-[11px]" style={{ maxWidth: 380 }}>
                        {paths.map((p) => p.split(/[\\/]/).pop()).join(" · ")}
                      </span>
                    )}
                  </>
                ) : (
                  <>
                    <span>点击选择文件</span>
                    <span>CSV · TXT · XY · DAT</span>
                  </>
                )}
              </button>

              <label className="flex items-center justify-between text-[13px] text-[var(--text-secondary)]">
                Project
                <select
                  value={project ?? ""}
                  onChange={(e) => setProjectId(Number(e.target.value))}
                  className="w-56 rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-2 py-1 text-[12px]"
                >
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>

              <label className="flex items-center justify-between text-[13px] text-[var(--text-secondary)]">
                New Sample
                <input
                  value={newSampleName}
                  onChange={(e) => setNewSampleName(e.target.value)}
                  placeholder="LFP-500C"
                  className="w-56 rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-2 py-1 text-[12px] focus:border-[var(--accent)] focus:outline-none"
                />
              </label>
            </div>
          ) : (
            <div className="mt-4">
              <div className="text-[12px] text-[var(--text-secondary)]">{mapping.reason}</div>
              <div className="mt-2 max-h-40 overflow-auto rounded-[var(--radius-panel)] border border-[var(--divider)] p-2 text-[11px]">
                <table>
                  <thead>
                    <tr className="text-left text-[var(--text-tertiary)]">
                      {mapping.previewRows[0]?.map((_, c) => (
                        <th key={c} className="px-2 font-medium">
                          Col {c + 1}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {mapping.previewRows.slice(1, 9).map((row, r) => (
                      <tr key={r}>
                        {row.map((cell, c) => (
                          <td key={c} className="tabular px-2 py-0.5">
                            {cell}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="mt-3 flex gap-6 text-[13px] text-[var(--text-secondary)]">
                <label className="flex items-center gap-2">
                  X Axis
                  <input
                    type="number"
                    min={1}
                    max={mapping.columnCount}
                    value={mapX}
                    onChange={(e) => setMapX(Number(e.target.value))}
                    className="tabular w-14 rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-1.5 py-0.5 text-[12px]"
                  />
                </label>
                <label className="flex items-center gap-2">
                  Y Axis
                  <input
                    type="number"
                    min={1}
                    max={mapping.columnCount}
                    value={mapY}
                    onChange={(e) => setMapY(Number(e.target.value))}
                    className="tabular w-14 rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-1.5 py-0.5 text-[12px]"
                  />
                </label>
              </div>
            </div>
          )}

          <div className="mt-5 flex justify-end gap-2">
            <Button kind="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              kind="primary"
              disabled={busy || paths.length === 0}
              onClick={() => void runImport()}
            >
              {busy ? "导入中…" : "Import"}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
