import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Group, Panel, Separator, useDefaultLayout } from "react-resizable-panels";
import { ArrowLeft, BatteryCharging, Download, FolderOpen, Info, Layers, X } from "lucide-react";
import { Button } from "../../../components/controls";
import { cn } from "../../../components/cn";
import { COMPARE_COLORS } from "../../../services/engine";
import { useAnalysisStore, useSelectionStore, useUiStore } from "../../../stores";
import { EcChartModeSelect, EcLineChart, type EcChartMode, type EcSeries } from "../EcLineChart";
import { CexDataPane, CexTable } from "./CexDataPane";
import { CexCycleOverview } from "./CexCycleOverview";
import { CexArchiveDialog, CexExportDialog, CexInfoDialog } from "./CexDialogs";
import { CexDqdvView } from "./CexDqdvView";
import {
  CEX_COLUMNS, CEX_PLOTS, cexCommand, elapsed, errorText, formatValue, scaled, unitLabel,
  type CexColumn, type CexComparison, type CexDocument, type CexLocation,
  type CexPlotKind, type CexSeries, type Unit,
} from "./model";
import { chooseCexFiles, useCexSession } from "./session";
import { useCexPlot } from "./useCexPlot";

const PLOT_AXES: Record<CexPlotKind, { x: string; y: string; u?: Unit; second?: string; secondUnit?: Unit }> = {
  time: { x: "测试时间", y: "电压", u: "V", second: "电流", secondUnit: "mA" },
  voltageCapacity: { x: "容量", y: "电压", u: "V" },
  cycles: { x: "循环序号", y: "容量", u: "mAh", second: "库仑效率", secondUnit: "%" },
  specificCycles: { x: "循环序号", y: "比容量", u: "mAh/g", second: "库仑效率", secondUnit: "%" },
  steps: { x: "工步序号", y: "容量", u: "mAh", second: "能量", secondUnit: "mWh" },
};
const CYCLE_SYMBOLS: Record<string, EcSeries["symbol"]> = { 充电: "circle", 放电: "diamond", 库仑效率: "triangle" };
const sideBySideQuery = "(min-width: 1160px)";

function described(name: string, unit: Unit | undefined, base: boolean) {
  return unit ? name + " (" + unitLabel(unit, base) + ")" : name;
}

function CexPlot({
  result, kind, base, mode, focus, locate, exportName,
}: {
  result: CexSeries[];
  kind: CexPlotKind;
  base: boolean;
  mode: EcChartMode;
  focus?: CexLocation | null;
  locate?: (number: number) => void;
  exportName?: string;
}) {
  const axes = PLOT_AXES[kind];
  const series = useMemo<EcSeries[]>(() => {
    const labels = new Map<string, number>();
    const colors = new Map<string, number>();
    return result.map((source) => {
      const group = source.group ?? source.id;
      if (!colors.has(group)) colors.set(group, colors.size);
      const labelCount = (labels.get(source.name) ?? 0) + 1;
      labels.set(source.name, labelCount);
      const name = source.name + (labelCount > 1 ? " (" + labelCount + ")" : "");
      const axisUnit = source.axis === 1 ? axes.secondUnit : axes.u;
      return {
        name,
        yAxisIndex: source.axis,
        data: source.points.map(([x, y]) => [
          kind === "voltageCapacity" ? scaled(x, "mAh", base) ?? x : x,
          scaled(y, axisUnit, base),
        ]),
        recordNumbers: source.points.map((point) => point[2]),
        color: COMPARE_COLORS[(colors.get(group) ?? 0) % COMPARE_COLORS.length],
        dashed: source.dashed,
        symbol: kind === "cycles" || kind === "specificCycles" ? CYCLE_SYMBOLS[source.name] : undefined,
      };
    });
  }, [result, axes, base, kind]);
  const record = focus?.record;
  const recordX = record && (kind === "time" ? record.time
    : kind === "voltageCapacity" ? scaled(record.capacity, "mAh", base) : null);
  const focusedPoint = useMemo(() => record && recordX != null ? {
    x: recordX, y: [record.voltage, kind === "time" ? scaled(record.current, "mA", base) : null] as [number | null, number | null],
  } : null, [recordX, record, kind, base]);
  return (
    <EcLineChart
      series={series}
      mode={mode}
      height="100%"
      xName={described(axes.x, kind === "voltageCapacity" ? "mAh" : undefined, base)}
      y1Name={described(axes.y, axes.u, base)}
      y2Name={axes.second && described(axes.second, axes.secondUnit, base)}
      formatX={kind === "time" ? elapsed : undefined}
      xMinInterval={kind === "time" || kind === "voltageCapacity" ? undefined : 1}
      focusedPoint={focusedPoint}
      onRecord={locate}
      exportName={exportName}
    />
  );
}

function SegmentControls({
  total, first, last, apply,
}: { total: number; first: number; last: number; apply: (first: number, last: number) => void }) {
  const [from, setFrom] = useState(String(first));
  const [to, setTo] = useState(String(last));
  const a = Number(from);
  const b = Number(to);
  const valid = Number.isInteger(a) && Number.isInteger(b) && a >= 1 && b >= a && b <= total;
  function commit() {
    if (valid) apply(a, b);
  }
  return (
    <div className="flex items-center gap-1.5 text-[12px] text-[var(--text-secondary)]"
      onKeyDown={(event) => { if (event.key === "Enter") commit(); }}>
      <span>圈段</span>
      <input aria-label="起始圈" type="number" min={1} max={total} className="cex-input w-16"
        value={from} onChange={(event) => setFrom(event.target.value)} />
      <span>–</span>
      <input aria-label="结束圈" type="number" min={1} max={total} className="cex-input w-16"
        value={to} onChange={(event) => setTo(event.target.value)} />
      <Button kind="ghost" disabled={!valid || (a === first && b === last)} onClick={commit}>查看</Button>
    </div>
  );
}

function CexDocumentView({ doc }: { doc: CexDocument }) {
  const size = doc.counts.cycles;
  const [range, setRange] = useState({ first: 1, last: size || 1 });
  const [dragRange, setDragRange] = useState<typeof range | null>(null);
  const [kind, setKind] = useState<CexPlotKind>("time");
  const [modes, setModes] = useState<Partial<Record<CexPlotKind, EcChartMode>>>({});
  const mode = modes[kind] ?? (kind === "cycles" || kind === "specificCycles" ? "scatter" : "line");
  const [cycle, setCycle] = useState(1);
  const [base, setBase] = useState(false);
  const [focus, setFocus] = useState<CexLocation | null>(null);
  const [infoOpen, setInfoOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [recordWindow, setRecordWindow] = useState<{ first: number; end: number } | null>(null);
  const [sideBySide, setSideBySide] = useState(() => window.matchMedia(sideBySideQuery).matches);
  const orientation = sideBySide ? "horizontal" : "vertical";
  const layout = useDefaultLayout({ id: `xrd-cex-${orientation}`, onlySaveAfterUserInteractions: true });
  const lastSeek = useRef(0);
  const requestArchive = useCexSession((s) => s.requestArchive);
  const current = useSelectionStore((s) => s.sampleId);
  const activeRange = dragRange ?? range;
  const selectedCycle = kind === "voltageCapacity"
    ? cycle >= activeRange.first && cycle <= activeRange.last ? cycle : activeRange.first : undefined;
  const recordStart = kind === "time" && !dragRange ? recordWindow?.first : undefined;
  const recordEnd = kind === "time" && !dragRange ? recordWindow?.end : undefined;
  const first = selectedCycle ?? activeRange.first;
  const last = selectedCycle ?? activeRange.last;
  const preview = dragRange != null && (selectedCycle == null || selectedCycle !== cycle);
  const request = useMemo(() => ({
    key: [doc.id, kind, first, last, recordStart, recordEnd, preview].join(":"),
    id: doc.id, kind, first, last, cycle: selectedCycle, recordStart, recordEnd, preview,
  }), [doc.id, kind, first, last, selectedCycle, recordStart, recordEnd, preview]);
  const plot = useCexPlot(request);
  const currentPlot = plot?.key === request.key;

  useEffect(() => {
    const media = window.matchMedia(sideBySideQuery);
    const update = () => setSideBySide(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  useEffect(() => () => { lastSeek.current++; }, []);
  const locate = useCallback((number: number) => {
    const sequence = ++lastSeek.current;
    void cexCommand<CexLocation>("locate", { id: doc.id, number }).then((location) => {
      if (sequence !== lastSeek.current) return;
      setFocus(location);
      setKind((currentKind) => currentKind === "voltageCapacity" && location.cycle != null
        ? "voltageCapacity" : "time");
      if (location.cycle != null) {
        const target = location.cycle;
        setCycle(target);
        setRecordWindow(null);
        setRange((old) => target >= old.first && target <= old.last ? old : { first: target, last: target });
      } else {
        const offset = Math.max(0, number - 25000);
        setRecordWindow({ first: offset, end: Math.min(doc.counts.records, offset + 50000) });
      }
    }, (error: unknown) => {
      if (sequence === lastSeek.current) useUiStore.getState().showToast("error", errorText(error));
    });
  }, [doc.id, doc.counts.records]);

  const setSection = useCallback((first: number, last: number) => {
    lastSeek.current++;
    setDragRange(null);
    setRange((old) => old.first === first && old.last === last ? old : { first, last });
    setRecordWindow(null);
    setCycle((old) => old < first || old > last ? first : old);
    setFocus((old) => old && old.cycle != null && old.cycle >= first && old.cycle <= last ? old : null);
  }, []);

  const previewSection = useCallback((first: number, last: number) => {
    lastSeek.current++;
    setDragRange({ first, last });
  }, []);

  return (
    <>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-[var(--divider)] px-4 py-3">
        <div className="min-w-36 flex-1">
          <div className="truncate text-[15px] font-semibold" title={doc.filename}>{doc.name}</div>
          <div className="flex flex-wrap gap-x-2 text-[11px] text-[var(--text-secondary)]">
            {doc.channel && <span>通道 {doc.channel}</span>}
            <span>{size.toLocaleString()} 圈</span>
            <span>{doc.counts.steps.toLocaleString()} 工步</span>
            <span>{doc.counts.records.toLocaleString()} 条记录</span>
          </div>
        </div>
        <Button kind="ghost" onClick={() => setInfoOpen(true)}><Info size={14} />工作信息</Button>
        <Button kind="ghost" onClick={() => setExportOpen(true)}><Download size={14} />导出数据</Button>
        {doc.cellId == null && (
          <Button kind="primary" onClick={() => requestArchive({ ids: [doc.id], sampleId: current })}>
            添加到样品
          </Button>
        )}
      </div>
      <Group key={orientation} orientation={orientation} className="min-h-0 flex-1" defaultLayout={layout.defaultLayout}
        onLayoutChanged={layout.onLayoutChanged}>
        <Panel id="cex-chart" defaultSize={sideBySide ? "51%" : "45%"} minSize={sideBySide ? 290 : 190}>
          <section aria-label="蓝电曲线" className="flex h-full min-h-0 min-w-0 flex-col">
            <div className="flex flex-wrap items-center gap-2 border-b border-[var(--divider)] px-3 py-2">
              <select aria-label="曲线类型" className="cex-input" value={kind}
                onChange={(event) => setKind(event.target.value as CexPlotKind)}>
                {CEX_PLOTS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
              <EcChartModeSelect value={mode} onChange={(value) => setModes((current) => ({ ...current, [kind]: value }))} />
              {size > 0 && (
                <SegmentControls key={range.first + ":" + range.last} total={size} {...range} apply={setSection} />
              )}
              {kind === "voltageCapacity" && size > 0 && (
                <label className="flex items-center gap-1 text-[12px]">
                  比较圈
                  <input className="cex-input w-17" type="number" min={range.first} max={range.last} value={cycle}
                    onChange={(event) => {
                      const number = Number(event.target.value);
                      if (Number.isInteger(number) && number >= range.first && number <= range.last) setCycle(number);
                    }} />
                </label>
              )}
              <select aria-label="电化学单位" className="cex-input ml-auto" value={base ? "base" : "milli"}
                onChange={(event) => setBase(event.target.value === "base")}>
                <option value="milli">mA / mAh</option>
                <option value="base">A / Ah</option>
              </select>
            </div>
            {recordWindow && kind === "time" && (
              <div className="flex items-center gap-2 px-3 py-1 text-[11px] text-[var(--text-secondary)]">
                记录 {(recordWindow.first + 1).toLocaleString()}–{recordWindow.end.toLocaleString()}
                {recordWindow.first > 0 && <Button kind="ghost" onClick={() => {
                  const first = Math.max(0, recordWindow.first - 50000);
                  setRecordWindow({ first, end: Math.min(first + 50000, doc.counts.records) });
                }}>上一段</Button>}
                {recordWindow.end < doc.counts.records && <Button kind="ghost" onClick={() =>
                  setRecordWindow({ first: recordWindow.end, end: Math.min(recordWindow.end + 50000, doc.counts.records) })}>
                  下一段
                </Button>}
              </div>
            )}
            <div className="min-h-0 flex-1 p-2">
              {plot?.kind === kind && plot.series.some((item) => item.points.some((point) => point[1] != null)) ? (
                <CexPlot result={plot.series} {...{ kind, base, mode, locate }} focus={dragRange ? null : focus}
                  exportName={currentPlot && !preview ? doc.name + "-" + kind : undefined} />
              ) : <EmptyPlot label={currentPlot ? plot?.error ?? "该范围内没有可绘制的曲线" : "正在绘制曲线…"} />}
            </div>
            {size > 0 && <CexCycleOverview id={doc.id} total={size} {...activeRange} preview={previewSection} apply={setSection} />}
            {focus && (
              <div className="flex flex-wrap gap-2 border-t border-[var(--divider)] px-3 py-2 text-[11px] text-[var(--text-secondary)]">
                <span>记录 {focus.record.number}</span>
                <span>{formatValue(focus.record.time, "s")}</span>
                <span>{formatValue(focus.record.voltage, "V")} V</span>
                <span>{formatValue(focus.record.current, "mA", base)} {unitLabel("mA", base)}</span>
              </div>
            )}
          </section>
        </Panel>
        <Separator className="pane-divider" aria-label="调整蓝电曲线和数据表大小" title="拖动调整蓝电曲线和数据表大小" />
        <Panel id="cex-table" minSize={sideBySide ? 250 : 125} className="flex flex-col">
          <CexDataPane doc={doc} {...range} base={base} focus={focus} locate={locate} selectCycle={setCycle} />
        </Panel>
      </Group>
      {infoOpen && <CexInfoDialog doc={doc} onClose={() => setInfoOpen(false)} />}
      {exportOpen && <CexExportDialog doc={doc} cycles={size ? range.first + "-" + range.last : ""} onClose={() => setExportOpen(false)} />}
    </>
  );
}

function EmptyPlot({ label }: { label: string }) {
  return (
    <div role="status" className="flex h-full items-center justify-center p-6 text-center text-[13px] text-[var(--text-secondary)]">
      {label}
    </div>
  );
}

const COMPARISON_COLUMNS: CexColumn<CexComparison>[] = [
  { key: "filename", label: "文件" }, { key: "channel", label: "通道" },
  { key: "number", label: "循环" }, { key: "startTime", label: "启动时间" },
  ...CEX_COLUMNS.cycles.filter((column) =>
    ["qCharge", "qDischarge", "sqCharge", "sqDischarge", "efficiency"].includes(column.key)),
];

function ComparisonView({ docs }: { docs: CexDocument[] }) {
  const [unselected, setUnselected] = useState<Set<string>>(new Set());
  const [perFile, setPerFile] = useState<Record<string, string>>({});
  const [common, setCommon] = useState("r1");
  const [ignoreRest, setIgnoreRest] = useState(true);
  const [base, setBase] = useState(false);
  const [mode, setMode] = useState<EcChartMode>("line");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<{ series: CexSeries[]; rows: CexComparison[] }>();
  const notify = useUiStore((s) => s.showToast);
  const sources = docs.filter((doc) => doc.counts.cycles > 0 && !unselected.has(doc.id));

  async function draw() {
    setBusy(true);
    setError("");
    try {
      const rendered = await cexCommand<{ series: CexSeries[]; rows: CexComparison[] }>("compare", {
        sources: sources.map((doc) => ({ id: doc.id, cycles: perFile[doc.id]?.trim() || common.trim() })),
        ignoreRest,
      });
      setResult(rendered);
    } catch (failure) {
      setResult(undefined);
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  }

  async function copySummary() {
    if (!result) return;
    const text = [
      COMPARISON_COLUMNS.map((column) => column.unit
        ? column.label + " (" + unitLabel(column.unit, base) + ")" : column.label),
      ...result.rows.map((row) => COMPARISON_COLUMNS.map(({ key, unit }) => formatValue(row[key], unit, base))),
    ].map((row) => row.join("\t")).join("\n");
    try {
      await navigator.clipboard.writeText(text);
      notify("success", "已复制对比表");
    } catch (failure) {
      notify("error", errorText(failure));
    }
  }

  return (
    <div className="grid min-h-0 flex-1 grid-rows-[auto_minmax(180px,1fr)] min-[1060px]:grid-cols-[minmax(230px,22%)_minmax(0,1fr)] min-[1060px]:grid-rows-1">
      <section aria-label="选择对比循环" className="max-h-50 min-w-0 overflow-y-auto border-b border-[var(--divider)] p-3 min-[1060px]:max-h-none min-[1060px]:border-b-0 min-[1060px]:border-r">
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="text-[14px] font-semibold">循环对比</h2>
          <Button kind="primary" onClick={() => void draw()} disabled={!sources.length || busy}>
            {busy ? "正在绘制…" : "叠加曲线"}
          </Button>
        </div>
        <label className="flex flex-col gap-1 text-[12px]">
          默认选圈
          <input className="cex-input" value={common} placeholder="如 1-10s2 或 r2"
            onChange={(event) => setCommon(event.target.value)} />
        </label>
        <label className="my-3 flex items-center gap-2 text-[12px]">
          <input type="checkbox" checked={ignoreRest} onChange={(event) => setIgnoreRest(event.target.checked)} />
          忽略圈末静置
        </label>
        <div className="space-y-2">
          {docs.map((doc) => (
            <div key={doc.id} className="rounded-[var(--radius-input)] border border-[var(--divider)] px-2.5 py-2">
              <label className="flex min-w-0 items-center gap-2 text-[12px]" title={doc.path}>
                <input type="checkbox"
                  checked={doc.counts.cycles > 0 && !unselected.has(doc.id)} disabled={!doc.counts.cycles}
                  onChange={(event) => {
                    const next = new Set(unselected);
                    if (event.target.checked) next.delete(doc.id);
                    else next.add(doc.id);
                    setUnselected(next);
                  }} />
                <span className="min-w-0 flex-1 truncate">{doc.filename}</span>
                <span className="shrink-0 text-[var(--text-secondary)]">{doc.counts.cycles} 圈</span>
              </label>
              <input className="cex-input mt-2 w-full" aria-label={doc.filename + " 的循环范围"}
                value={perFile[doc.id] ?? ""} placeholder={common || "默认选圈"}
                disabled={unselected.has(doc.id) || !doc.counts.cycles}
                onChange={(event) => setPerFile({ ...perFile, [doc.id]: event.target.value })} />
            </div>
          ))}
        </div>
      </section>
      <section aria-label="循环叠图结果" className="flex min-h-0 min-w-0 flex-col">
        <div className="flex justify-end gap-2 border-b border-[var(--divider)] px-3 py-2">
          <EcChartModeSelect value={mode} onChange={setMode} />
          <select aria-label="对比容量单位" className="cex-input" value={base ? "base" : "milli"}
            onChange={(event) => setBase(event.target.value === "base")}>
            <option value="milli">mAh</option><option value="base">Ah</option>
          </select>
        </div>
        <div className="min-h-32 flex-1 p-2">
          {result?.series.length ? (
            <CexPlot result={result.series} kind="voltageCapacity" base={base} mode={mode} exportName="蓝电循环对比" />
          ) : <EmptyPlot label={error || (busy ? "正在读取对比曲线…" : "选择文件和圈号后叠加曲线")} />}
        </div>
        {result && (
          <div className="max-h-48 overflow-auto border-t border-[var(--divider)]">
            <div className="flex items-center justify-between px-3 py-1 text-[12px]">
              <span>{result.rows.length} 个循环</span><Button kind="ghost" onClick={() => void copySummary()}>复制对比表</Button>
            </div>
            <CexTable columns={COMPARISON_COLUMNS} rows={result.rows} base={base}
              rowKey={(row) => row.id + ":" + row.number} />
          </div>
        )}
      </section>
    </div>
  );
}

export function CexWorkspace() {
  const [dqdvOpen, setDqdvOpen] = useState(false);
  const docs = useCexSession((s) => s.documents);
  const active = useCexSession((s) => s.active);
  const comparing = useCexSession((s) => s.comparing);
  const pending = useCexSession((s) => s.pending);
  const archive = useCexSession((s) => s.archive);
  const requestArchive = useCexSession((s) => s.requestArchive);
  const select = useCexSession((s) => s.select);
  const compare = useCexSession((s) => s.compare);
  const close = useCexSession((s) => s.close);
  const doc = docs.find((item) => item.id === active);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex min-w-0 items-center gap-2 border-b border-[var(--divider)] bg-[var(--bg-sidebar)] px-3 py-1.5">
        <Button kind="ghost" title="返回谱图" onClick={() =>
          useSelectionStore.getState().selectDataset(null, useAnalysisStore.getState().loadedDatasetId)}>
          <ArrowLeft size={14} />谱图
        </Button>
        <nav aria-label="已打开的蓝电文件" className="flex min-w-0 flex-1 gap-1 overflow-x-auto">
          {docs.map((item) => (
            <div key={item.id} className={cn("flex shrink-0 items-center rounded-[var(--radius-control)] border border-transparent",
              item.id === active && !comparing && "border-[var(--divider)] bg-[var(--selected)]")}>
              <button className="flex max-w-42 items-center gap-1.5 py-1 pl-2 pr-1 text-[12px]"
                aria-current={item.id === active && !comparing ? "page" : undefined}
                onClick={() => { setDqdvOpen(false); select(item.id); }} title={item.path}>
                <BatteryCharging size={13} className="shrink-0" />
                <span className="truncate">{item.filename}</span>
              </button>
              <button className="m-0.5 rounded p-1 text-[var(--text-secondary)] hover:bg-[var(--hover)]"
                aria-label={"关闭 " + item.filename} onClick={() => close(item.id)}><X size={12} /></button>
            </div>
          ))}
        </nav>
        {pending > 0 && <span role="status" className="shrink-0 text-[11px] text-[var(--text-secondary)]">正在打开…</span>}
        <Button kind={dqdvOpen && !comparing ? "primary" : "secondary"} disabled={!doc?.counts.cycles}
          aria-pressed={dqdvOpen && !comparing} onClick={() => {
            if (comparing && doc) select(doc.id);
            setDqdvOpen((open) => !open || comparing);
          }}>dQ/dV</Button>
        <Button kind={comparing ? "primary" : "secondary"} disabled={!docs.length}
          onClick={() => { setDqdvOpen(false); compare(); }}>
          <Layers size={14} />循环对比
        </Button>
      </div>
      {comparing ? <ComparisonView docs={docs} /> : doc ? (
        dqdvOpen && doc.counts.cycles
          ? <CexDqdvView key={doc.id} primary={doc} docs={docs} />
          : <CexDocumentView key={doc.id} doc={doc} />
      ) : (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 text-[var(--text-secondary)]">
          <BatteryCharging size={26} />
          <h2 className="text-[16px] font-semibold text-[var(--text-primary)]">蓝电数据</h2>
          <Button kind="primary" disabled={pending > 0} onClick={() => void chooseCexFiles()}>
            <FolderOpen size={14} />打开 CEX
          </Button>
        </div>
      )}
      {archive && (
        <CexArchiveDialog
          key={archive.ids.join(",") + ":" + archive.sampleId}
          ids={archive.ids} initialSampleId={archive.sampleId} onClose={() => requestArchive(null)}
        />
      )}
    </div>
  );
}
