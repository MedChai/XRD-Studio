/** 导入 Sheet(§40/§42):小弹窗,不做 Wizard;失败文件走列映射。
 *
 * 文件来源(§10.6):Tauri 环境下用原生文件对话框拿真实路径;
 * 拖放事件由 AppShell 统一接收(webview 拖放事件提供路径)。
 * 纯浏览器开发模式下用文件名占位 + mock 引擎。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Button, Segmented } from "../../components/controls";
import { engineCommand } from "../../services/engine";
import type { ImportResult, ScanResult } from "../../types/engine";
import { useProjectStore, useUiStore, useSelectionStore, useAnalysisStore } from "../../stores";
import { cn } from "../../components/cn";
import { t } from "../../i18n";

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
  /** 归属方式。**这是用户反馈的核心**:此前只有"新建样品"一条路,每导
   *  一次就多出一个文件夹,用户没有任何办法把一批文件归到一起。 */
  const [destMode, setDestMode] = useState<"new" | "existing">("new");
  const [destSampleId, setDestSampleId] = useState<number | null>(null);
  const [batchName, setBatchName] = useState("");
  /** 文件夹扫描的建议分组(确认前不落库) */
  const [scan, setScan] = useState<ScanResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [mapping, setMapping] = useState<ImportResult["needColumns"][0] | null>(null);
  const [mapX, setMapX] = useState(1);
  const [mapY, setMapY] = useState(2);
  /** 列映射往返时归属的 sample。
   * **必须存在组件局部 state**:引擎在返回 needColumns 的同时已经把 sample
   * 建好了,第二次调用要复用它。只依赖 uiStore.lastSampleId 的话,中间用户
   * 切了样品就会串到别的样品上。 */
  const [mappingSampleId, setMappingSampleId] = useState<number | null>(null);
  /** 重复文件的待决态。非 null 时显示确认区块,让用户选「打开已有」或
   * 「仍然导入」—— 而不是像以前那样直接中止整个导入。 */
  const [duplicates, setDuplicates] = useState<ImportResult["duplicates"] | null>(null);

  const project = projectId ?? currentProjectId ?? projects[0]?.id ?? null;
  /** 当前项目下的实验,供"已有实验"下拉。
   *
   *  **必须先取原始数组再 filter,不能把 filter 写进选择器。**
   *  Zustand v5 去掉了默认的浅比较,选择器返回新数组引用时
   *  `useSyncExternalStore` 每次渲染都判定"快照变了"→ 无限重渲染 →
   *  React 抛 Maximum update depth 并卸载整棵树。ImportSheet 是常驻
   *  挂载的(空状态也要能导入),所以这会让**整个界面白屏**。
   */
  const allSamples = useProjectStore((s) => s.samples);
  const projectSamples = useMemo(
    () => allSamples.filter((x) => x.project_id === project),
    [allSamples, project],
  );

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

  /** 选文件夹 → 扫描 → 给出建议分组(不落库)。 */
  async function pickFolder() {
    if (project == null) {
      showToast("error", t.import.needProject);
      setOpen(false);
      useUiStore.getState().setNewProjectOpen(true);
      return;
    }
    if (!isTauri) {
      showToast("error", t.import.folderNeedsDesktop);
      return;
    }
    const { open } = await import("@tauri-apps/plugin-dialog");
    const folder = await open({ directory: true, multiple: false });
    if (!folder || typeof folder !== "string") return;
    const r = await engineCommand<ScanResult>("datasets.scan_folder", { folder });
    if (!r.ok) {
      showToast("error", r.error);
      return;
    }
    if (r.data.groups.length === 0) {
      showToast("error", t.import.scanNoGroups);
      return;
    }
    setScan(r.data);
  }

  /** 确认扫描结果:逐组导入。分组名就是实验名。
   *
   *  needColumns / duplicates 必须与单文件路径同样处理 —— 此前文件夹导入
   *  把这两类结果一律记成 failed 或静默丢掉,用户看到"导入 0 个"却不知道
   *  为什么。列映射与重复确认进入与单文件相同的 UI 状态。
   *
   *  needColumns 按 sample 分组:不同实验各自建了 sample,列映射往返必须
   *  带对应的 sample_id,混用会把文件导进错误的实验。先处理第一组,
   *  其余记在 pendingNeedQueues 里,映射完成后再依次进入。 */
  const pendingNeedQueues = useRef<
    { needs: ImportResult["needColumns"]; sampleId: number }[]
  >([]);

  async function confirmScan() {
    if (!scan || project == null) return;
    setBusy(true);
    let imported = 0;
    let failed = 0;
    const needBySample = new Map<number, ImportResult["needColumns"]>();
    const allDups: ImportResult["duplicates"] = [];
    for (const group of scan.groups) {
      const r = await engineCommand<ImportResult>("datasets.import", {
        paths: group.paths,
        project_id: project,
        new_sample: { name: group.name },
        batch_source_path: scan.folder,
      });
      if (!r.ok) {
        failed += group.paths.length;
        continue;
      }
      imported += r.data.imported.length;
      failed += r.data.failures.length;
      allDups.push(...r.data.duplicates);
      if (r.data.needColumns.length > 0) {
        const list = needBySample.get(r.data.sampleId) ?? [];
        list.push(...r.data.needColumns);
        needBySample.set(r.data.sampleId, list);
      }
    }
    setBusy(false);

    // 列映射优先:与单文件一致,进入 mapping UI 待用户选列后重导。
    const needEntries = [...needBySample.entries()];
    if (needEntries.length > 0) {
      const [firstSampleId, firstNeeds] = needEntries[0];
      pendingNeedQueues.current = needEntries
        .slice(1)
        .map(([sampleId, needs]) => ({ needs, sampleId }));
      enterMapping(firstNeeds, firstSampleId);
      setScan(null);
      if (failed > 0) showToast("error", t.import.scanPartial(imported, failed));
      return;
    }

    if (allDups.length > 0) {
      setDuplicates(allDups);
      setScan(null);
      if (failed > 0) showToast("error", t.import.scanPartial(imported, failed));
      else if (imported > 0) showToast("success", t.import.imported(imported));
      return;
    }

    if (failed > 0) {
      showToast("error", t.import.scanPartial(imported, failed));
    } else {
      showToast("success", t.import.imported(imported));
    }
    setScan(null);
    setPaths([]);
    setOpen(false);
    void load();
  }

  function enterMapping(
    needs: ImportResult["needColumns"],
    sampleId: number,
  ) {
    setMapping(needs[0]);
    setMappingSampleId(sampleId);
    setMapX(needs[0].suggestedX + 1);
    setMapY(needs[0].suggestedY + 1);
    setPaths(needs.map((n) => n.path));
    useUiStore.getState().setLastSampleId(sampleId);
  }

  /** 列映射成功后:若文件夹导入还有待映射的实验,继续下一批。 */
  function drainNeedQueue(): boolean {
    const next = pendingNeedQueues.current.shift();
    if (!next) return false;
    enterMapping(next.needs, next.sampleId);
    return true;
  }

  async function runImport(
    xColumn?: number,
    yColumn?: number,
    importAnyway = false,
  ) {
    if (!paths.length) return;
    if (project == null) {
      // 空 Library 的第一步:先建项目,避免静默失败
      showToast("error", t.import.needProject);
      setOpen(false);
      useUiStore.getState().setNewProjectOpen(true);
      return;
    }
    setBusy(true);
    // 归属三选一。**已有实验这条路此前完全缺失** —— 引擎本来就支持传
    // sample_id 复用样品,只是前端从来不传,于是每次导入都新建一个。
    const destination = mapping
      ? { sample_id: mappingSampleId ?? useUiStore.getState().lastSampleId }
      : destMode === "existing" && destSampleId != null
        ? { sample_id: destSampleId }
        : { new_sample: { name: newSampleName || t.import.defaultSampleName } };
    const batch = batchName.trim() ? { batch_name: batchName.trim() } : {};

    const r = await engineCommand<ImportResult>("datasets.import", {
      paths,
      project_id: project,
      ...destination,
      ...batch,
      ...(mapping ? { x_column: (xColumn ?? mapX) - 1, y_column: (yColumn ?? mapY) - 1 } : {}),
      ...(importAnyway ? { import_anyway: true } : {}),
    });
    setBusy(false);
    if (!r.ok) {
      showToast("error", r.error);
      return;
    }
    const d = r.data;
    // **先回填 sampleId,再做任何早返回**。此前这一步在 needColumns 的
    // return 之后,导致第二次(带列映射的)调用发 sample_id: null 且没有
    // new_sample,引擎必然报「新样品必须填写 Sample Name」—— 凡是需要手选
    // 列的 CSV 都导不进来。
    useUiStore.getState().setLastSampleId(d.sampleId);
    setMappingSampleId(d.sampleId);

    if (d.needColumns.length > 0 && !mapping) {
      setMapping(d.needColumns[0]);
      setMapX(d.needColumns[0].suggestedX + 1);
      setMapY(d.needColumns[0].suggestedY + 1);
      return;
    }
    // 重复文件进入待决态:不中止,交给用户选「打开已有」或「仍然导入」
    if (d.duplicates.length > 0 && !importAnyway) {
      setDuplicates(d.duplicates);
      return;
    }
    const parts: string[] = [];
    if (d.imported.length) parts.push(t.import.imported(d.imported.length));
    if (d.failures.length) parts.push(t.import.failed(d.failures.length));
    showToast("success", parts.join(" · ") || t.import.done);
    if (d.imported.length) {
      setNewSampleName("");
      setBatchName("");
      setDestSampleId(null);
      setDuplicates(null);
      // 文件夹导入可能还有待列映射的实验:继续下一批,不关弹窗。
      // enterMapping 会自己 setPaths / setMappingSampleId ——
      // 这里不能再覆盖,否则刚排上的下一批会被清掉。
      if (drainNeedQueue()) return;
      setPaths([]);
      setMapping(null);
      setMappingSampleId(null);
      setScan(null);
      setOpen(false);
      void load();
    }
  }

  /** 「打开已有」:把重复项对应的那个 dataset 选出来直接看。 */
  function openExisting(datasetId: number) {
    useSelectionStore.getState().selectDataset(null, datasetId);
    void useAnalysisStore.getState().loadDataset(datasetId);
    setDuplicates(null);
    setOpen(false);
  }

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/25 backdrop-blur-[2px]" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[460px] -translate-x-1/2 -translate-y-1/2 rounded-[var(--radius-dialog)] bg-[var(--bg-panel)] p-5 shadow-[var(--shadow-dialog)]">
          <Dialog.Title className="text-[17px] font-semibold">
            {scan
              ? t.import.scanTitle
              : duplicates
                ? t.import.duplicateTitle
                : mapping
                  ? t.import.mappingTitle
                  : t.import.title}
          </Dialog.Title>

          {scan ? (
            <div className="mt-4">
              <div className="text-[12px] text-[var(--text-secondary)]">
                {t.import.scanHint(scan.files.length)}
              </div>
              <div className="mt-2 max-h-64 overflow-auto rounded-[var(--radius-panel)] border border-[var(--divider)]">
                {scan.groups.map((g, gi) => (
                  <div key={gi} className="border-b border-[var(--divider)] p-2.5 last:border-0">
                    <div className="flex items-center gap-2">
                      <input
                        value={g.name}
                        onChange={(e) => {
                          const next = [...scan.groups];
                          next[gi] = { ...g, name: e.target.value };
                          setScan({ ...scan, groups: next });
                        }}
                        className="min-w-0 flex-1 rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-2 py-0.5 text-[12px] focus:border-[var(--accent)] focus:outline-none"
                      />
                      <span className="tabular shrink-0 text-[11px] text-[var(--text-tertiary)]">
                        {t.import.scanGroupFiles(g.paths.length)}
                      </span>
                    </div>
                    <div className="mt-1 truncate text-[10px] text-[var(--text-tertiary)]">
                      {g.paths.map((x) => x.split(/[\/]/).pop()).join(" · ")}
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-4 flex justify-end gap-2">
                <Button onClick={() => setScan(null)}>{t.import.cancel}</Button>
                <Button kind="primary" onClick={() => void confirmScan()} disabled={busy}>
                  {t.import.scanConfirm}
                </Button>
              </div>
            </div>
          ) : duplicates ? (
            <div className="mt-4">
              <div className="text-[12px] text-[var(--text-secondary)]">
                {t.import.duplicateHint(duplicates.length)}
              </div>
              <div className="mt-2 max-h-40 overflow-auto rounded-[var(--radius-panel)] border border-[var(--divider)]">
                {duplicates.map((d) => (
                  <div
                    key={d.path}
                    className="flex items-center gap-2 border-b border-[var(--divider)] px-2.5 py-2 last:border-0"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[12px] text-[var(--text-primary)]">
                        {d.existing.datasetName}
                      </div>
                      <div className="truncate text-[11px] text-[var(--text-tertiary)]">
                        {d.existing.projectName} / {d.existing.sampleName}
                      </div>
                    </div>
                    <Button
                      kind="ghost"
                      onClick={() => openExisting(d.existing.datasetId)}
                    >
                      {t.import.openExisting}
                    </Button>
                  </div>
                ))}
              </div>
            </div>
          ) : !mapping ? (
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
                        : t.import.fileCount(paths.length)}
                    </span>
                    {paths.length > 1 && (
                      <span className="truncate text-[11px]" style={{ maxWidth: 380 }}>
                        {paths.map((p) => p.split(/[\\/]/).pop()).join(" · ")}
                      </span>
                    )}
                  </>
                ) : (
                  <>
                    <span>{t.import.pickFile}</span>
                    <span>{t.import.formats}</span>
                  </>
                )}
              </button>

              {/* 文件夹导入:用户的数据本来就是按文件夹组织的一次实验,
                  逐个选文件再手工归组既慢又容易漏。 */}
              {isTauri && (
                <button
                  type="button"
                  onClick={() => void pickFolder()}
                  className="tr -mt-1 self-start text-[12px] text-[var(--accent)] hover:underline"
                >
                  {t.import.scanFolder}
                </button>
              )}

              <label className="flex items-center justify-between text-[13px] text-[var(--text-secondary)]">
                {t.import.projectLabel}
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

              {/* 归属:新建 / 已有实验二选一。
                  「不分组」选项已移除 —— 引擎要求 sample_id 与 new_sample
                  至少给一个,datasets.sample_id 是 NOT NULL,选了它导入
                  **必然报错**,等于给了用户一条必坏的路。批次层是可选的,
                  不需要"完全不分组"来跳过实验层。 */}
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

              {/* 批次是可选第三层 —— 不给就不建,不强迫用户维护层级 */}
              {(
                <label className="flex items-center justify-between text-[13px] text-[var(--text-secondary)]">
                  <span>
                    {t.import.batchLabel}
                    <span className="ml-1 text-[11px] text-[var(--text-tertiary)]">
                      {t.import.batchHint}
                    </span>
                  </span>
                  <input
                    value={batchName}
                    onChange={(e) => setBatchName(e.target.value)}
                    placeholder={t.import.batchPlaceholder}
                    className="w-56 rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-2 py-1 text-[12px] focus:border-[var(--accent)] focus:outline-none"
                  />
                </label>
              )}
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
                          {t.import.colHeader(c + 1)}
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
                  {t.import.xAxis}
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
                  {t.import.yAxis}
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
                    void runImport(undefined, undefined, true);
                  }}
                >
                  {busy ? t.import.importing : t.import.importAnyway}
                </Button>
              </>
            ) : (
              <>
                <Button kind="ghost" onClick={() => setOpen(false)}>
                  {t.import.cancel}
                </Button>
                <Button
                  kind="primary"
                  disabled={busy || paths.length === 0}
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
