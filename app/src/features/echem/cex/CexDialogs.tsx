import * as Dialog from "@radix-ui/react-dialog";
import { useState, type ReactNode } from "react";
import { X } from "lucide-react";
import { Button } from "../../../components/controls";
import { useProjectStore, useUiStore } from "../../../stores";
import {
  CEX_COLUMNS, CEX_LEVELS, cexCommand, errorText,
  type CexDocument, type CexFormat, type CexLevel,
} from "./model";
import { archiveCex, openArchivedCex, useCexSession } from "./session";

function CexDialog({
  title, description, onClose, children, busy,
}: {
  title: string; description: string; onClose: () => void; children: ReactNode; busy?: boolean;
}) {
  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/25 backdrop-blur-[2px]" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-[min(94vw,34rem)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-[var(--radius-dialog)] border border-[var(--divider)] bg-[var(--bg-elevated)] p-5 shadow-[var(--shadow-dialog)]"
        >
          <div className="flex items-start justify-between gap-4">
            <div>
              <Dialog.Title className="text-[16px] font-semibold text-[var(--text-primary)]">{title}</Dialog.Title>
              <Dialog.Description className="mt-1 break-all text-[12px] text-[var(--text-secondary)]">{description}</Dialog.Description>
            </div>
            <Dialog.Close
              disabled={busy} aria-label="关闭"
              className="rounded-[var(--radius-control)] p-1 text-[var(--text-secondary)] hover:bg-[var(--hover)]"
            >
              <X size={16} />
            </Dialog.Close>
          </div>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function CexArchiveDialog({ ids, initialSampleId, onClose }: {
  ids: string[]; initialSampleId: number | null; onClose: () => void;
}) {
  const projects = useProjectStore((s) => s.projects);
  const samples = useProjectStore((s) => s.samples);
  const currentProject = useProjectStore((s) => s.currentProjectId);
  const documents = useCexSession((s) => s.documents);
  const notify = useUiStore((s) => s.showToast);
  const [project, setProject] = useState<number | null>(
    samples.find((sample) => sample.id === initialSampleId)?.project_id ?? currentProject,
  );
  const [sample, setSample] = useState<number | null>(initialSampleId);
  const available = samples.filter((row) => row.project_id === project);
  const files = documents.filter((doc) => ids.includes(doc.id));
  const [names, setNames] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [outcome, setOutcome] = useState<Awaited<ReturnType<typeof archiveCex>> | null>(null);
  const pendingFiles = outcome
    ? files.filter((doc) => outcome.failures.some((failure) => failure.id === doc.id)) : files;
  const canSubmit = available.some((row) => row.id === sample) && pendingFiles.length > 0
    && pendingFiles.every((doc) => !!nameFor(doc).trim());

  function nameFor(doc: CexDocument) {
    return names[doc.id] ?? doc.filename.replace(/\.cex$/i, "");
  }

  async function submit() {
    if (busy || sample == null || !canSubmit) return;
    setBusy(true);
    setError("");
    try {
      const result = await archiveCex(
        pendingFiles.map((doc) => doc.id), sample,
        Object.fromEntries(pendingFiles.map((doc) => [doc.id, nameFor(doc)])),
      );
      const [first] = result.imported;
      if (first) {
        const detail = result.imported.length === 1 ? first.name : `${result.imported.length} 份 CEX`;
        notify("success", `已将 ${detail} 添加到 ${first.sample}`);
      }
      const duplicates = [...(outcome?.duplicates ?? []), ...result.duplicates];
      if (duplicates.length || result.failures.length) setOutcome({ ...result, duplicates });
      else onClose();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <CexDialog title="添加到样品" description={files.length === 1 ? files[0].filename : files.length + " 份原始 CEX"} {...{ onClose, busy }}>
      <div className="my-4 grid grid-cols-2 gap-3">
        <label className="flex min-w-0 flex-col gap-1 text-[12px]">
          项目
          <select
            className="cex-input" value={project ?? ""} disabled={busy || !pendingFiles.length}
            onChange={(event) => {
              setProject(event.target.value ? Number(event.target.value) : null);
              setSample(null);
            }}
          >
            <option value="">选择项目</option>
            {projects.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}
          </select>
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-[12px]">
          样品
          <select
            className="cex-input" value={sample ?? ""} disabled={busy || !pendingFiles.length || !available.length}
            onChange={(event) => setSample(event.target.value ? Number(event.target.value) : null)}
          >
            <option value="">{available.length ? "选择样品" : "该项目暂无样品"}</option>
            {available.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}
          </select>
        </label>
      </div>
      {pendingFiles.length > 0 && (
        <div className="mb-4 space-y-3">
          {pendingFiles.map((doc) => {
            const failure = outcome?.failures.find((item) => item.id === doc.id);
            return (
              <label key={doc.id} className="flex min-w-0 flex-col gap-1 text-[12px]">
                <span className="truncate" title={doc.filename}>
                  {files.length === 1 ? "数据名称" : `数据名称 · ${doc.filename}`}
                </span>
                <span className="flex items-center gap-2">
                  <input className="cex-input min-w-0 flex-1" value={nameFor(doc)} disabled={busy}
                    aria-invalid={!!failure}
                    onKeyDown={(event) => { if (event.key === "Enter" && !event.nativeEvent.isComposing) void submit(); }}
                    onChange={(event) => setNames({ ...names, [doc.id]: event.target.value })} />
                  <span aria-hidden="true" className="text-[var(--text-secondary)]">.cex</span>
                </span>
                {failure && <span role="alert" className="break-all text-[var(--danger)]">{failure.reason}</span>}
              </label>
            );
          })}
        </div>
      )}
      {error && <p role="alert" className="text-[12px] text-[var(--danger)]">{error}</p>}
      {!!outcome?.duplicates.length && (
        <ul className="space-y-2 rounded-[var(--radius-input)] border border-[var(--divider)] p-3 text-[12px]">
          {outcome.duplicates.map((link) => (
            <li key={link.id} className="flex flex-wrap items-center justify-between gap-2">
              <span>
                {files.find((doc) => doc.id === link.id)?.filename} 已归档到 {link.project} / {link.sample}
              </span>
              <Button kind="ghost" onClick={() => {
                onClose();
                void openArchivedCex(link.sampleId, link.cellId);
              }}>打开已有条目</Button>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-4 flex justify-end gap-2">
        <Button kind="ghost" onClick={onClose} disabled={busy}>{pendingFiles.length ? "取消" : "关闭"}</Button>
        {pendingFiles.length > 0 && (
          <Button kind="primary" disabled={busy || !canSubmit} onClick={() => void submit()}>
            {busy ? "正在添加…" : "添加到样品"}
          </Button>
        )}
      </div>
    </CexDialog>
  );
}

export function CexExportDialog({ doc, cycles: initialCycles, onClose }: {
  doc: CexDocument; cycles: string; onClose: () => void;
}) {
  const [level, setLevel] = useState<CexLevel>(doc.counts.cycles ? "cycles" : "records");
  const [format, setFormat] = useState<CexFormat>("xlsx");
  const [cycles, setCycles] = useState(initialCycles);
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fields = CEX_COLUMNS[level];
  const columns = fields.map((field) => field.key).filter((key) => !excluded.has(key));

  async function submit() {
    setBusy(true);
    setError("");
    try {
      const { save } = await import("@tauri-apps/plugin-dialog");
      const path = await save({
        title: "导出蓝电" + CEX_LEVELS[level] + "数据",
        defaultPath: doc.filename.replace(/\.cex$/i, "") + "-" + CEX_LEVELS[level] + "." + format,
        filters: [{ name: format.toUpperCase(), extensions: [format] }],
      });
      if (!path) return;
      const result = await cexCommand<{ rows: number; path: string }>("export", {
        id: doc.id, level, cycles, columns, format, path,
      });
      useUiStore.getState().showToast("success", "已导出 " + result.rows.toLocaleString() + " 行：" + result.path);
      onClose();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <CexDialog title="导出蓝电数据" description={doc.filename} {...{ onClose, busy }}>
      <div className="my-4 grid grid-cols-2 gap-3">
        <label className="flex flex-col gap-1 text-[12px]">
          数据层级
          <select
            className="cex-input" value={level} disabled={busy}
            onChange={(event) => {
              setLevel(event.target.value as CexLevel);
              setExcluded(new Set());
            }}
          >
            {Object.entries(CEX_LEVELS).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[12px]">
          文件格式
          <select
            className="cex-input" value={format} disabled={busy}
            onChange={(event) => setFormat(event.target.value as CexFormat)}
          >
            <option value="xlsx">Excel (.xlsx)</option>
            <option value="csv">CSV</option>
            <option value="txt">TXT（制表符分隔）</option>
          </select>
        </label>
        {doc.counts.cycles > 0 && (
          <label className="col-span-2 flex flex-col gap-1 text-[12px]">
            循环范围
            <input
              className="cex-input" placeholder="留空为全部，可输入 1-20s5 或 r2"
              value={cycles} disabled={busy} onChange={(event) => setCycles(event.target.value)}
            />
          </label>
        )}
      </div>
      <fieldset className="grid grid-cols-2 gap-2 rounded-[var(--radius-panel)] border border-[var(--divider)] p-3 text-[12px]" disabled={busy}>
        <legend className="px-1">导出列</legend>
        {fields.map((field) => (
          <label key={field.key} className="flex items-center gap-2">
            <input type="checkbox" checked={!excluded.has(field.key)} onChange={(event) => {
              const next = new Set(excluded);
              if (event.target.checked) next.delete(field.key);
              else next.add(field.key);
              setExcluded(next);
            }} />
            {field.label}
          </label>
        ))}
      </fieldset>
      {error && <p role="alert" className="mt-3 text-[12px] text-[var(--danger)]">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <Button kind="ghost" disabled={busy} onClick={onClose}>取消</Button>
        <Button kind="primary" disabled={busy || !columns.length || !doc.counts[level]}
          onClick={() => void submit()}>{busy ? "正在导出…" : "保存文件"}</Button>
      </div>
    </CexDialog>
  );
}

export function CexInfoDialog({ doc, onClose }: { doc: CexDocument; onClose: () => void }) {
  const info = [
    ["通道", doc.channel], ["电池条码", doc.barcode], ["电池批号", doc.batch],
    ["活性物质", doc.massMg == null ? "—" : Number(doc.massMg.toFixed(4)) + " mg"],
    ["启动时间", doc.startTime], ["结束时间", doc.endTime], ["通道备注", doc.note],
  ];
  return (
    <CexDialog title="工作信息" description={doc.filename} onClose={onClose}>
      <dl className="my-4 grid grid-cols-[auto_1fr] gap-x-5 gap-y-2 text-[12px]">
        {info.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-[var(--text-secondary)]">{label}</dt>
            <dd className="whitespace-pre-wrap break-all">{value || "—"}</dd>
          </div>
        ))}
      </dl>
      <h3 className="text-[12px] font-semibold">测试工艺</h3>
      {doc.programs.length ? (
        <ol className="mt-2 space-y-1 text-[12px]">
          {doc.programs.map((program, index) => (
            <li key={index} className="flex flex-wrap justify-between gap-1 rounded-[var(--radius-input)] bg-[var(--bg-panel)] px-3 py-2">
              <span>{program.name || "工艺 " + (index + 1)}</span>
              <span className="text-[var(--text-secondary)]">{program.startTime}</span>
            </li>
          ))}
        </ol>
      ) : <div className="mt-2 text-[12px] text-[var(--text-secondary)]">—</div>}
    </CexDialog>
  );
}
