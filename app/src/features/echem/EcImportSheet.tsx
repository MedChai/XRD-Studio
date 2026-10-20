/** 电化学导入弹窗(LAND xlsx)。
 *
 *  与 XRD 的 ImportSheet 平行、不复用其状态机:LAND 导入只有一步
 *  归属(无列映射/批次/文件夹扫描),但多一个「预览解析」阶段 ——
 *  20MB xlsx 解析约 20 秒,必须先给用户 QA/圈数/实际工步再确认导入。
 *  归属三选一(新建/已有实验)沿用 ImportSheet 的交互语义。
 */

import { useEffect, useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Button, Segmented } from "../../components/controls";
import { ecImport, ecPreview } from "../../services/engine";
import type { EcImportResult, EcPreviewResult } from "../../types/engine";
import { useEcStore, useProjectStore, useSelectionStore, useUiStore } from "../../stores";
import { cn } from "../../components/cn";
import { t } from "../../i18n";

const isTauri = "__TAURI_INTERNALS__" in window;

export function EcImportSheet() {
  const open = useUiStore((s) => s.ecImportOpen);
  const setOpen = useUiStore((s) => s.setEcImportOpen);
  const showToast = useUiStore((s) => s.showToast);
  const { projects, currentProjectId, load } = useProjectStore();

  const [paths, setPaths] = useState<string[]>([]);
  const [preview, setPreview] = useState<EcPreviewResult | null>(null);
  const [parsing, setParsing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [massInput, setMassInput] = useState("");
  const [destMode, setDestMode] = useState<"new" | "existing">("new");
  const [newSampleName, setNewSampleName] = useState("");
  const [destSampleId, setDestSampleId] = useState<number | null>(null);
  const [duplicates, setDuplicates] = useState<EcImportResult["duplicates"] | null>(null);

  const project = currentProjectId ?? projects[0]?.id ?? null;
  const allSamples = useProjectStore((s) => s.samples);
  const projectSamples = useMemo(
    () => allSamples.filter((x) => x.project_id === project),
    [allSamples, project],
  );

  // 打开时重置(上次预览残留会让用户误以为已经在解析新文件)
  useEffect(() => {
    if (open) {
      setPreview(null);
      setDuplicates(null);
      setPaths([]);
      setMassInput("");
    }
  }, [open]);

  async function pickFiles() {
    if (isTauri) {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const selected = await open({
        multiple: true,
        filters: [{ name: "LAND 导出", extensions: ["xlsx"] }],
      });
      if (selected) {
        const list = selected as string[];
        setPaths(list);
        await runPreview(list[0]);
      }
    } else {
      const input = document.createElement("input");
      input.type = "file";
      input.accept = ".xlsx";
      input.onchange = () => {
        const names = Array.from(input.files ?? []).map((f) => f.name);
        if (names.length) {
          setPaths(names);
          void runPreview(names[0]);
        }
      };
      input.click();
    }
  }

  async function runPreview(path: string) {
    setParsing(true);
    setPreview(null);
    const r = await ecPreview(path);
    setParsing(false);
    if (!r.ok) {
      showToast("error", t.echem.parseFailed(r.error));
      return;
    }
    setPreview(r.data);
  }

  async function runImport(importAnyway = false) {
    if (!paths.length || project == null) return;
    setBusy(true);
    const mass = massInput.trim() ? Number(massInput) : undefined;
    const r = await ecImport({
      paths,
      project_id: project,
      ...(destMode === "existing" && destSampleId != null
        ? { sample_id: destSampleId }
        : { new_sample: { name: newSampleName || t.import.defaultSampleName } }),
      ...(Number.isFinite(mass) && mass && mass > 0 ? { active_mass_mg: mass } : {}),
      ...(importAnyway ? { import_anyway: true } : {}),
    });
    setBusy(false);
    if (!r.ok) {
      showToast("error", r.error);
      return;
    }
    if (r.data.duplicates.length > 0 && !importAnyway) {
      setDuplicates(r.data.duplicates);
      return;
    }
    const n = r.data.imported.length;
    showToast("success", t.echem.imported(n));
    if (n > 0) {
      setOpen(false);
      await load();
      // 直接选中最先导入的那颗 cell,立即进入电化学页面
      const first = r.data.imported[0];
      const sampleId = r.data.sampleId;
      useEcStore.getState().invalidateCells();
      useSelectionStore.getState().selectCell(sampleId, first.id);
      void useEcStore.getState().loadCell(first.id);
      showToast("success", t.echem.cellImported(first.name, first.n_cycles));
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/25 backdrop-blur-[2px]" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[480px] -translate-x-1/2 -translate-y-1/2 rounded-[var(--radius-dialog)] bg-[var(--bg-panel)] p-5 shadow-[var(--shadow-dialog)]">
          <Dialog.Title className="text-[17px] font-semibold">
            {duplicates ? t.echem.duplicateTitle : t.echem.importTitle}
          </Dialog.Title>

          {duplicates ? (
            <div className="mt-4">
              <div className="text-[12px] text-[var(--text-secondary)]">
                {t.echem.duplicateHint(duplicates.length)}
              </div>
              <div className="mt-2 max-h-40 overflow-auto rounded-[var(--radius-panel)] border border-[var(--divider)]">
                {duplicates.map((d) => (
                  <div
                    key={d.path}
                    className="flex items-center gap-2 border-b border-[var(--divider)] px-2.5 py-2 last:border-0"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[12px] text-[var(--text-primary)]">{d.name}</div>
                      <div className="truncate text-[11px] text-[var(--text-tertiary)]">{d.path}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="mt-4 flex flex-col gap-3">
              <button
                type="button"
                onClick={() => void pickFiles()}
                className={cn(
                  "tr flex h-20 flex-col items-center justify-center gap-1 rounded-[var(--radius-panel)] border border-dashed border-[var(--divider)] text-[12px] text-[var(--text-tertiary)] hover:border-[var(--accent)] hover:text-[var(--text-secondary)]",
                  paths.length > 0 && "border-[var(--accent)] text-[var(--text-primary)]",
                )}
              >
                {parsing ? (
                  <span className="text-[var(--accent)]">{t.echem.parsing}</span>
                ) : paths.length > 0 ? (
                  <>
                    <span className="font-medium">
                      {paths.length === 1
                        ? paths[0].split(/[\\/]/).pop()
                        : t.import.fileCount(paths.length)}
                    </span>
                    <span>{t.echem.formats}</span>
                  </>
                ) : (
                  <>
                    <span>{t.echem.pickFile}</span>
                    <span>{t.echem.formats}</span>
                  </>
                )}
              </button>

              {/* 解析预览:圈数/程序/QA —— 大文件解析 20 秒后的第一反馈 */}
              {preview && (
                <div className="rounded-[var(--radius-panel)] border border-[var(--divider)] p-3 text-[12px]">
                  <div className="flex flex-wrap gap-x-4 gap-y-1">
                    <span className="font-medium text-[var(--accent)]">
                      {t.echem.previewCycles(preview.n_cycles)}
                    </span>
                    {preview.mass_mg_estimate != null && (
                      <span className="text-[var(--text-secondary)]">
                        {t.echem.massEstimate(preview.mass_mg_estimate.toFixed(2))}
                      </span>
                    )}
                  </div>
                  <div className="mt-2 text-[11px] text-[var(--text-tertiary)]">
                    {t.echem.programTitle}
                  </div>
                  <div className="mt-1 max-h-28 overflow-y-auto rounded-[var(--radius-input)] bg-[var(--bg-elevated)] p-2 font-mono text-[10px] text-[var(--text-secondary)]">
                    {preview.session.program.map((line, i) => (
                      <div key={i}>{line}</div>
                    ))}
                  </div>
                </div>
              )}

              <label className="flex items-center justify-between text-[13px] text-[var(--text-secondary)]">
                {t.import.projectLabel}
                <select
                  value={project ?? ""}
                  disabled
                  className="w-52 rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-2 py-1 text-[12px]"
                >
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>

              <div className="flex items-start justify-between gap-2 text-[13px] text-[var(--text-secondary)]">
                <span className="pt-1">{t.import.destLabel}</span>
                <div className="flex w-56 flex-col gap-1.5">
                  <Segmented
                    options={[
                      { value: "new", label: t.import.destNew },
                      { value: "existing", label: t.import.destExisting },
                    ]}
                    value={destMode}
                    onChange={setDestMode}
                  />
                  {destMode === "new" && (
                    <input
                      value={newSampleName}
                      onChange={(e) => setNewSampleName(e.target.value)}
                      placeholder={t.import.newSamplePlaceholder}
                      className="w-full rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-2 py-1 text-[12px] focus:border-[var(--accent)] focus:outline-none"
                    />
                  )}
                  {destMode === "existing" &&
                    (projectSamples.length === 0 ? (
                      <span className="text-[11px] text-[var(--text-tertiary)]">
                        {t.import.destExistingEmpty}
                      </span>
                    ) : (
                      <select
                        value={destSampleId ?? ""}
                        onChange={(e) => setDestSampleId(Number(e.target.value))}
                        className="w-full rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-2 py-1 text-[12px]"
                      >
                        <option value="">{t.import.destExisting}</option>
                        {projectSamples.map((x) => (
                          <option key={x.id} value={x.id}>
                            {x.name}
                          </option>
                        ))}
                      </select>
                    ))}
                </div>
              </div>

              <label className="flex items-center justify-between text-[13px] text-[var(--text-secondary)]">
                <span>
                  {t.echem.massLabel}
                  <span className="ml-1 text-[11px] text-[var(--text-tertiary)]">
                    {t.echem.massPlaceholder}
                  </span>
                </span>
                <input
                  value={massInput}
                  onChange={(e) => setMassInput(e.target.value)}
                  placeholder={t.echem.massPlaceholder}
                  className="tabular w-52 rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-2 py-1 text-right text-[12px] focus:border-[var(--accent)] focus:outline-none"
                />
              </label>
            </div>
          )}

          <div className="mt-5 flex justify-end gap-2">
            {duplicates ? (
              <>
                <Button kind="ghost" onClick={() => setDuplicates(null)}>
                  {t.import.cancel}
                </Button>
                <Button
                  kind="primary"
                  disabled={busy}
                  onClick={() => {
                    setDuplicates(null);
                    void runImport(true);
                  }}
                >
                  {busy ? t.import.importing : t.echem.importAnyway}
                </Button>
              </>
            ) : (
              <>
                <Button kind="ghost" onClick={() => setOpen(false)}>
                  {t.import.cancel}
                </Button>
                <Button
                  kind="primary"
                  disabled={busy || parsing || paths.length === 0}
                  onClick={() => void runImport()}
                >
                  {busy ? t.import.importing : t.import.import}
                </Button>
              </>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
