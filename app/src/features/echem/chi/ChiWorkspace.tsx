import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Activity, FileDown, FolderOpen, Plus } from "lucide-react";
import { Button } from "../../../components/controls";
import { useProjectStore, useUiStore } from "../../../stores";
import { ChiChart, eisTraces, type Trace, type View } from "./ChiChart";
import {
  chiCommand, isDesktop, numberLabel, type ChiFile, type ChiFit, type ChiList, type ChiSummary,
  type CvFile, type EisFile, type FitModel, type ImportResult, type Technique, type Weighting,
} from "./model";
import "./chi.css";

const models: Record<FitModel, string> = { cpe: "", cpe2: "", warburg: "" };
const modelNames: Record<FitModel, string> = { cpe: "单支路", cpe2: "双支路", warburg: "单支路 + 扩散" };
const fileFilters: ("all" | Technique)[] = ["all", "CV", "EIS"];
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);

export function ChiWorkspace() {
  const projectId = useProjectStore((s) => s.currentProjectId);
  return <Workspace key={projectId ?? "unassigned"} projectId={projectId} />;
}

function Workspace({ projectId }: { projectId: number | null }) {
  const samples = useProjectStore((s) => s.samples);
  const toast = useUiStore((s) => s.showToast);
  const [archiveTo, setArchiveTo] = useState<number | null>(null);
  const [list, setList] = useState<ChiList>({ files: [], models });
  const [filter, setFilter] = useState<(typeof fileFilters)[number]>("all");
  const [selected, setSelected] = useState<number | null>(null);
  const [file, setFile] = useState<ChiFile | null>(null);
  const [loading, setLoading] = useState(isDesktop);
  const [busy, setBusy] = useState(false);
  const [dropping, setDropping] = useState(false);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState("");
  const importing = useRef(false);
  const disposed = useRef(false);

  useEffect(() => {
    disposed.current = false;
    return () => { disposed.current = true; };
  }, []);

  const reload = useCallback(async (preferred?: number) => {
    const next = await chiCommand<ChiList>("list", { project_id: projectId });
    if (disposed.current) return;
    setList(next);
    setSelected((current) => {
      const id = preferred ?? current;
      return next.files.some((item) => item.id === id) ? id : next.files[0]?.id ?? null;
    });
  }, [projectId]);

  useEffect(() => {
    if (!isDesktop) return;
    void reload().catch((cause) => setError(errorText(cause))).finally(() => setLoading(false));
  }, [reload]);

  useEffect(() => {
    if (selected == null) return;
    let cancelled = false;
    void chiCommand<ChiFile>("open", { id: selected }).then(
      (next) => { if (!cancelled) setFile(next); },
      (cause) => { if (!cancelled) setError(errorText(cause)); },
    );
    return () => { cancelled = true; };
  }, [selected]);

  const importFiles = useCallback(async (paths: string[]) => {
    if (!paths.length || importing.current || disposed.current) return;
    importing.current = true;
    setBusy(true);
    setError("");
    setImportResult(null);
    try {
      const next = await chiCommand<ImportResult>("import", { paths, sample_id: archiveTo });
      if (disposed.current) return;
      setImportResult(next);
      setFilter("all");
      await reload(next.imported[0]?.id ?? next.duplicates[0]?.id);
      if (!next.imported.length && !next.duplicates.length && !next.failures.length) {
        setError("文件夹中没有 TXT 或 CSV 文件");
      }
    } catch (cause) {
      if (!disposed.current) setError(errorText(cause));
    } finally {
      importing.current = false;
      if (!disposed.current) setBusy(false);
    }
  }, [archiveTo, reload]);

  const choose = useCallback(async (directory = false) => {
    if (!isDesktop || importing.current) return;
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const paths = await open(directory ? { multiple: false, directory: true, title: "选择 CHI 数据文件夹" } : {
        multiple: true, directory: false, title: "选择 CHI 的 CV 或 EIS 文本数据",
        filters: [{ name: "CHI 文本导出", extensions: ["txt", "csv"] }],
      });
      if (paths != null) await importFiles(typeof paths === "string" ? [paths] : paths);
    } catch (cause) {
      setError(errorText(cause));
    }
  }, [importFiles]);

  useEffect(() => {
    if (!isDesktop) return;
    let cancelled = false;
    let detach: (() => void) | undefined;
    void import("@tauri-apps/api/webview").then(async ({ getCurrentWebview }) => {
      if (cancelled) return;
      const unlisten = await getCurrentWebview().onDragDropEvent((event) => {
        const { payload } = event;
        if (payload.type === "enter" || payload.type === "over") setDropping(true);
        else {
          setDropping(false);
          if (payload.type === "drop") void importFiles(payload.paths);
        }
      });
      if (cancelled) unlisten();
      else detach = unlisten;
    }).catch((cause) => { if (!cancelled) setError(errorText(cause)); });
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && ["i", "o"].includes(event.key.toLowerCase())) {
        event.preventDefault();
        void choose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      cancelled = true;
      detach?.();
      window.removeEventListener("keydown", onKey);
    };
  }, [choose, importFiles]);

  async function exportCsv() {
    if (file?.id !== selected || !file) return;
    try {
      const { save } = await import("@tauri-apps/plugin-dialog");
      const path = await save({
        defaultPath: file.name.replace(/\.[^.]+$/, "") + "_data.csv",
        filters: [{ name: "CSV", extensions: ["csv"] }],
      });
      if (!path) return;
      const result = await chiCommand<{ path: string }>("export", { id: file.id, path });
      toast("success", "已导出 CSV · " + result.path.split(/[\\/]/).pop());
    } catch (cause) {
      toast("error", errorText(cause));
    }
  }

  const filtered = list.files.filter((item) => filter === "all" || item.kind === filter);
  const current = file?.id === selected ? file : null;
  const select = (id: number) => {
    setError("");
    setSelected(id);
  };

  return (
    <main className="chi-workspace">
      <header className="chi-toolbar">
        <div>
          <h1>电化学工作站</h1>
          <span>辰华 CHI760E</span>
        </div>
        <div className="chi-actions">
          <label>导入位置
            <select value={archiveTo ?? ""} onChange={(event) => setArchiveTo(Number(event.target.value) || null)}>
              <option value="">工作站数据</option>
              {samples.filter((sample) => sample.project_id === projectId).map((sample) =>
                <option key={sample.id} value={sample.id}>{sample.name}</option>)}
            </select>
          </label>
          <Button disabled={busy || !isDesktop} onClick={() => void choose(true)}><FolderOpen size={14} /> 导入文件夹</Button>
          <Button kind="primary" disabled={busy || !isDesktop} onClick={() => void choose()}><Plus size={15} /> 导入文件</Button>
        </div>
      </header>
      <div className="chi-body">
        <aside className="chi-files" aria-label="工作站文件">
          <div className="chi-files-heading"><h2>测量数据</h2><span>{list.files.length}</span></div>
          <div className="chi-tabs chi-filters" aria-label="实验类型">
            {fileFilters.map((value) => <button key={value} type="button" aria-pressed={filter === value} onClick={() => {
              setFilter(value);
              const group = list.files.filter((item) => value === "all" || item.kind === value);
              if (!group.length) setSelected(null);
              else if (!group.some((item) => item.id === selected)) select(group[0].id);
            }}>{value === "all" ? "全部" : value}</button>)}
          </div>
          <div className="chi-file-list">
            {filtered.map((item) =>
              <button key={item.id} type="button" className="chi-file" aria-pressed={selected === item.id}
                onClick={() => select(item.id)} title={`${item.folder} / ${item.name}`}>
                <span className={`chi-file-kind ${item.kind.toLowerCase()}`}>{item.kind}</span>
                <span className="chi-file-name"><strong>{item.name}</strong><small>{item.sampleName ?? item.folder}</small></span>
              </button>,
            )}
            {!filtered.length && <p>{loading ? "正在载入…" : "这个分类暂无数据"}</p>}
          </div>
          {importResult && (
            <div className="chi-import-result" role="status">
              导入 {importResult.imported.length} 项，重复 {importResult.duplicates.length} 项
              {!!importResult.failures.length && <details>
                <summary>{importResult.failures.length} 项无法导入</summary>
                {importResult.failures.map((failure, index) => <p key={index}>{failure.name} · {failure.reason}</p>)}
              </details>}
            </div>
          )}
        </aside>
        <div className="chi-main">
          {error && <p className="chi-error" role="alert">{error}</p>}
          {current ? <>
            <header className="chi-document">
              <div>
                <h2 title={current.name}>{current.name}</h2>
                <p>{current.sampleName ?? current.folder} <span>·</span> {current.pointCount.toLocaleString()} 个实测点</p>
              </div>
              <Button onClick={() => void exportCsv()}><FileDown size={14} /> 导出 CSV</Button>
            </header>
            {current.kind === "CV"
              ? <CvView key={current.id} file={current} files={list.files} />
              : <EisView key={current.id} file={current} modelLabels={list.models}
                  onFit={(fit) => setFile((previous) => previous?.id === current.id && previous.kind === "EIS"
                    ? { ...previous, fit } : previous)} />}
          </> : (
            <div className="chi-empty">
              <Activity size={44} strokeWidth={1.3} />
              <h2>{busy ? "正在导入测量数据…" : selected != null && !error ? "正在读取测量数据…" : "打开 CV 或 EIS 数据"}</h2>
              <p>{isDesktop ? "选择辰华导出的 TXT、CSV，或把文件拖到这里。" : "请在 Windows 桌面版打开工作站数据。"}</p>
              <Button kind="primary" disabled={busy || !isDesktop} onClick={() => void choose()}><Plus size={15} /> 导入文件</Button>
            </div>
          )}
        </div>
      </div>
      {dropping && <div className="chi-drop">松开以导入工作站数据</div>}
    </main>
  );
}

function FileInformation({ file }: { file: ChiFile }) {
  const { metadata } = file;
  const facts = [
    ["仪器", metadata.instrument],
    ["测量时间", metadata.date],
    ["初始电位", metadata.initialPotential == null ? "" : `${metadata.initialPotential} V`],
    ...(file.kind === "CV"
      ? [["扫描速率", metadata.scanRate == null ? "" : `${numberLabel(metadata.scanRate * 1_000)} mV/s`],
         ["实测段数", String(file.segments.length)]]
      : [["交流振幅", metadata.amplitude == null ? "" : `${numberLabel(metadata.amplitude * 1_000)} mV`]]),
  ];
  return (
    <section className="chi-section">
      <h3>测量信息</h3>
      <dl className="chi-definition">
        {facts.filter(([, value]) => Boolean(value)).map(([label, value]) =>
          <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
      </dl>
      {file.warnings.map((warning) => <p key={warning} className="chi-warning">{warning}</p>)}
    </section>
  );
}

function CvView({ file, files }: { file: CvFile; files: ChiSummary[] }) {
  const [segments, setSegments] = useState<number[]>([]);
  const [comparison, setComparison] = useState<number | null>(null);
  const [other, setOther] = useState<CvFile | null>(null);
  const [compareError, setCompareError] = useState("");
  const candidates = files.filter((item) => item.id !== file.id && item.kind === "CV");

  useEffect(() => {
    if (comparison == null) return;
    let cancelled = false;
    void chiCommand<CvFile>("open", { id: comparison }).then(
      (next) => { if (!cancelled) setOther(next); },
      (cause) => { if (!cancelled) setCompareError(errorText(cause)); },
    );
    return () => { cancelled = true; };
  }, [comparison]);

  const traces = useMemo<Trace[]>(() => {
    const scale = ([potential, current]: [number, number]): [number, number] => [potential, current * 1_000];
    const colors = ["--chi-raw", "--chi-green", "--chi-violet", "--chi-fit"];
    const primary = segments.length
      ? segments.map((index, color) => ({
          name: `第 ${index + 1} 段`, color: colors[color % colors.length],
          data: file.points.slice(file.segments[index].start, file.segments[index].end).map(scale),
        }))
      : [{ name: file.name, color: "--chi-raw", data: file.points.map(scale) }];
    if (other && other.id === comparison) primary.push({ name: other.name, color: "--chi-compare", data: other.points.map(scale) });
    return primary;
  }, [segments, file, comparison, other]);

  return (
    <div className="chi-analysis">
      <div className="chi-figure">
        <div className="chi-figure-heading"><h3>循环伏安</h3>
          <div className="chi-legend">{traces.map((trace) =>
            <span key={trace.name} style={{ color: `var(${trace.color})` }}><i className="chi-line" />{trace.name}</span>)}</div>
        </div>
        <ChiChart view="cv" traces={traces} filename={file.name.replace(/\.[^.]+$/, "") + "_CV"} />
      </div>
      <aside className="chi-settings" aria-label="CV 选段">
        <section className="chi-section">
          <h3>扫描段</h3>
          <div className="chi-segments chi-tabs">
            <button type="button" aria-pressed={!segments.length} onClick={() => setSegments([])}>整条曲线</button>
            {file.segments.map(({ direction }, i) => <button key={i} type="button" aria-pressed={segments.includes(i)}
              onClick={() => setSegments((current) => current.includes(i)
                ? current.filter((index) => index !== i) : [...current, i])}>
              {i + 1} {direction > 0 ? "正扫" : direction < 0 ? "反扫" : "恒电位"}
            </button>)}
          </div>
          {!!candidates.length && <label className="chi-field">叠加对比
            <select value={comparison ?? ""} onChange={(event) => {
              setCompareError("");
              setComparison(Number(event.target.value) || null);
            }}>
              <option value="">选择另一条 CV</option>
              {candidates.map((item) => <option value={item.id} key={item.id}>{item.folder} / {item.name}</option>)}
            </select>
          </label>}
          {compareError && <p className="chi-warning">{compareError}</p>}
        </section>
        <FileInformation file={file} />
      </aside>
    </div>
  );
}

function EisView({ file, modelLabels, onFit }: {
  file: EisFile; modelLabels: Record<FitModel, string>; onFit: (fit: ChiFit) => void;
}) {
  const frequencyLimits = file.points.reduce<[number, number]>(([min, max], [f]) => [Math.min(min, f), Math.max(max, f)], [Infinity, 0]);
  const [view, setView] = useState<View>("nyquist");
  const [rawVisible, setRawVisible] = useState(true);
  const [fitVisible, setFitVisible] = useState(true);
  const [model, setModel] = useState<FitModel>(file.fit?.model ?? "cpe2");
  const [weighting, setWeighting] = useState<Weighting>(file.fit?.weighting ?? "modulus");
  const [lower, setLower] = useState(String(file.fit?.requestedRange[0] ?? frequencyLimits[0]));
  const [upper, setUpper] = useState(String(file.fit?.requestedRange[1] ?? frequencyLimits[1]));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const rangeInvalid = !(Number(lower) > 0 && Number(upper) > Number(lower) && Number.isFinite(Number(upper)));
  const traces = useMemo(() => eisTraces(file, view, rawVisible, fitVisible), [file, view, rawVisible, fitVisible]);
  const { fit } = file;
  const draft = fit && (fit.model !== model || fit.weighting !== weighting
    || fit.requestedRange[0] !== Number(lower) || fit.requestedRange[1] !== Number(upper));
  const tabs = [["nyquist", "Nyquist"], ["bode", "Bode"], ["residual", "残差"]] as const;

  async function runFit() {
    setBusy(true);
    setError("");
    try {
      onFit(await chiCommand<ChiFit>("fit", {
        id: file.id, model, weighting, min_hz: Number(lower), max_hz: Number(upper),
      }));
      setFitVisible(true);
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="chi-analysis">
      <div className="chi-figure">
        <div className="chi-figure-heading">
          <div className="chi-tabs" aria-label="阻抗图类型">
            {tabs.map(([value, label]) => <button key={value} type="button" aria-pressed={view === value}
              disabled={value === "residual" && !fit} onClick={() => setView(value)}>{label}</button>)}
          </div>
          {view !== "residual" ? <div className="chi-legend">
            <button type="button" aria-pressed={rawVisible} onClick={() => setRawVisible(!rawVisible)}>
              <i className="chi-dot" />原始点
            </button>
            <button type="button" aria-pressed={Boolean(fit && fitVisible)} disabled={!fit}
              onClick={() => setFitVisible(!fitVisible)}><i className="chi-line" />拟合曲线</button>
          </div> : <div className="chi-legend">
            <span><i className="chi-dot" />实部残差</span>
            <span><i className="chi-dot chi-fit-dot" />虚部残差</span>
          </div>}
        </div>
        <ChiChart view={view} traces={traces} filename={file.name.replace(/\.[^.]+$/, "") + "_" + view} />
        <div className="chi-figure-note">
          {view === "nyquist" ? "横纵轴等比例" : view === "residual" ? "残差 = 拟合值 − 实测值" : "频率使用对数坐标"}
          {fit && fitVisible && view !== "residual" && fit.count < file.pointCount && <span>淡蓝色为拟合频段外的原始点</span>}
        </div>
      </div>
      <aside className="chi-settings" aria-label="EIS 拟合">
        <section className="chi-section">
          <h3>等效电路拟合</h3>
          <label className="chi-field">电路预设
            <select value={model} onChange={(event) => setModel(event.target.value as FitModel)} disabled={busy}>
              {(Object.keys(modelNames) as FitModel[]).map((key) => <option value={key} key={key}>{modelNames[key]}</option>)}
            </select>
          </label>
          <div className="chi-circuit">{modelLabels[model]}</div>
          <div className="chi-range">
            <label className="chi-field">最低频率 (Hz)
              <input type="number" step="any" value={lower} disabled={busy} onChange={(event) => setLower(event.target.value)} />
            </label>
            <label className="chi-field">最高频率 (Hz)
              <input type="number" step="any" value={upper} disabled={busy} onChange={(event) => setUpper(event.target.value)} />
            </label>
          </div>
          {rangeInvalid && <p className="chi-warning">最低频率应大于零，且低于最高频率。</p>}
          <div className="chi-range-line">
            <span>{numberLabel(frequencyLimits[0])}–{numberLabel(frequencyLimits[1])} Hz 实测</span>
            <button type="button" disabled={busy} onClick={() => {
              setLower(String(frequencyLimits[0]));
              setUpper(String(frequencyLimits[1]));
            }}>全频段</button>
          </div>
          <label className="chi-field">权重
            <select value={weighting} onChange={(event) => setWeighting(event.target.value as Weighting)} disabled={busy}>
              <option value="modulus">按阻抗模值 |Z|</option>
              <option value="uniform">等权</option>
            </select>
          </label>
          <Button kind="primary" className="chi-fit-button" disabled={busy || rangeInvalid}
            onClick={() => void runFit()}>{busy ? "正在拟合…" : "运行拟合"}</Button>
          {error && <p className="chi-warning" role="alert">{error}</p>}
        </section>
        {fit && <section className="chi-section chi-results" aria-label="拟合结果">
          <h3>拟合结果</h3>
          {draft && <p className="chi-warning">设置已更改，当前显示上一次拟合结果。</p>}
          <div className="chi-metric">
            <strong>{numberLabel(fit.relativeRmse, 3)}%</strong><span>相对均方根残差</span>
          </div>
          <p>{fit.count} 个频点 · {numberLabel(fit.range[0])}–{numberLabel(fit.range[1])} Hz</p>
          <dl className="chi-definition">
            {fit.parameters.map(({ name, value, unit }) => <div key={name}><dt>{name}</dt><dd>{numberLabel(value)} {unit}</dd></div>)}
          </dl>
          {fit.warnings.map((warning) => <p className="chi-warning" key={warning}>{warning}</p>)}
        </section>}
        <FileInformation file={file} />
      </aside>
    </div>
  );
}
