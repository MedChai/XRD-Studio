import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Crosshair, X } from "lucide-react";
import { Button, Segmented } from "../../../components/controls";
import { COMPARE_COLORS } from "../../../services/engine";
import { exportCexDqdvCurves, exportDqdvTable } from "../../../services/exports";
import { useUiStore } from "../../../stores";
import { EcLineChart, type EcSeries } from "../EcLineChart";
import { DqdvHeatmap } from "./DqdvHeatmap";
import { displayNumber, DqdvPeaks, peakRows } from "./DqdvPeaks";
import {
  calculateDqdv, traceName, traceRefs, voltageAt,
  type Direction, type DqdvRun, type DqdvSettings, type TraceRef,
} from "./dqdv";
import { cexCommand, errorText, type CexDocument, type CexLocation } from "./model";

type View = "curves" | "evolution" | "peaks";
type SourceDetail = CexLocation & { points: [number, number | null, number][] };
const defaults = { gridMv: 2, smoothingMv: 0 };
const seriesPerPage = 24;
const traceColumns = [
  "sample", "filename", "source_sha256", "channel", "cycle", "direction", "step", "segment",
  "record_first", "record_last", "current_mA", "sample_interval_mV", "charge_CV_mAh",
  "capacity_source", "mass_mg", "mass_source", "has_both_directions", "algorithm", "voltage_grid_mV", "smoothing_FWHM_mV", "unit",
];

function capacityBasis(doc: CexDocument, draft: string | undefined) {
  if (!draft?.trim()) return { massMg: doc.massMg, massSource: doc.massMg ? "cex" : "missing" };
  const value = Number(draft);
  return Number.isFinite(value) && value > 0
    ? { massMg: value, massSource: "manual" }
    : { massMg: null, massSource: "missing" };
}

function traceMetadata({ source, trace, cycle }: TraceRef, settings: DqdvSettings, unit: string,
  basis: ReturnType<typeof capacityBasis>) {
  return [source.doc.name, source.doc.filename, source.sha256, source.doc.channel, cycle.cycle,
    trace.direction, trace.step, trace.part, trace.firstRecord, trace.lastRecord, trace.currentMa,
    trace.sampleMv, cycle.cvCapacityMah, trace.capacitySource, basis.massMg, basis.massSource,
    Number(cycle.paired), source.algorithm, settings.gridMv, settings.smoothingMv, unit];
}

export function CexDqdvView({ primary, docs }: { primary: CexDocument; docs: CexDocument[] }) {
  const [checked, setChecked] = useState<string[]>([primary.id]);
  const [expressions, setExpressions] = useState<Record<string, string>>({});
  const [grid, setGrid] = useState(String(defaults.gridMv));
  const [smooth, setSmooth] = useState(String(defaults.smoothingMv));
  const [view, setView] = useState<View>("curves");
  const [direction, setDirection] = useState<Direction | "both">("both");
  const [normalized, setNormalized] = useState(false);
  const [masses, setMasses] = useState<Record<string, string>>({});
  const [absolute, setAbsolute] = useState(false);
  const [rawOverlay, setRawOverlay] = useState(false);
  const [run, setRun] = useState<DqdvRun>();
  const [progress, setProgress] = useState("");
  const task = useRef<AbortController | null>(null);
  const [sourceId, setSourceId] = useState(primary.id);
  const [heatDirection, setHeatDirection] = useState<Direction>("charge");
  const [page, setPage] = useState(0);
  const [focus, setFocus] = useState<{ id: string; cycle: number } | null>(null);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [peakLabel, setPeakLabel] = useState("");
  const [baseline, setBaseline] = useState("");
  const [picking, setPicking] = useState<false | "first" | number>(false);
  const [source, setSource] = useState<{ ref: TraceRef; data: SourceDetail }>();
  const sourceRequest = useRef(0);
  const [exporting, setExporting] = useState(false);
  const notify = useUiStore((s) => s.showToast);

  const selected = docs.filter((doc) => checked.includes(doc.id) && doc.counts.cycles > 0);
  const proposed = { gridMv: Number(grid), smoothingMv: Number(smooth) };
  const canRun = selected.length > 0 && proposed.gridMv >= 0.5 && proposed.gridMv <= 50
    && proposed.smoothingMv >= 0 && proposed.smoothingMv <= 100;
  const settings = run?.settings ?? defaults;
  const unit = normalized ? "mAh/(g·V)" : "mAh/V";
  const areaUnit = normalized ? "mAh/g" : "mAh";
  const stale = settings.gridMv !== proposed.gridMv || settings.smoothingMv !== proposed.smoothingMv;
  const massOf = useCallback((doc: CexDocument) => capacityBasis(doc, masses[doc.id]), [masses]);
  const allRefs = useMemo(() => traceRefs(run?.sources ?? []), [run]);
  const eligible = useMemo(() => allRefs.filter((ref) => (direction === "both" || ref.trace.direction === direction)
    && (!normalized || massOf(ref.source.doc).massMg)), [allRefs, direction, normalized, massOf]);
  const scale = useCallback((ref: TraceRef) => {
    const mass = massOf(ref.source.doc).massMg;
    return normalized && mass ? 1000 / mass : 1;
  }, [normalized, massOf]);
  const displayed = useMemo(() => focus
    ? eligible.filter((ref) => ref.source.doc.id === focus.id && ref.cycle.cycle === focus.cycle)
    : eligible, [eligible, focus]);
  const lastPage = Math.max(0, Math.ceil(displayed.length / seriesPerPage) - 1);
  const activePage = Math.min(page, lastPage);
  const visible = useMemo(() => displayed.slice(activePage * seriesPerPage, (activePage + 1) * seriesPerPage), [displayed, activePage]);
  const window = useMemo<[number, number] | undefined>(() => {
    const lo = Number(from), hi = Number(to);
    return from.trim() && to.trim() && Number.isFinite(lo) && Number.isFinite(hi) && hi > lo ? [lo, hi] : undefined;
  }, [from, to]);
  const peaks = useMemo(() => window ? peakRows(eligible, settings.gridMv, window, baseline, scale) : [],
    [eligible, settings.gridMv, window, baseline, scale]);
  const metadata = useMemo(() => ({
    settings, unit, dischargeSign: absolute ? "absolute" : "signed", windowV: window, peakLabel, baselineCycle: baseline || null,
    sources: run?.sources.map((item) => ({
      name: item.doc.name, filename: item.doc.filename, sha256: item.sha256, algorithm: item.algorithm,
      ...massOf(item.doc), cycles: item.cycles.map((cycle) => cycle.cycle),
    })),
  }), [run, settings, unit, absolute, window, peakLabel, baseline, massOf]);
  const heatSources = run?.sources.filter(({ doc }) => !normalized || massOf(doc).massMg) ?? [];
  const heatSource = heatSources.find(({ doc }) => doc.id === sourceId) ?? heatSources[0];
  const heatMass = heatSource ? massOf(heatSource.doc).massMg : null;
  const { series, sourceBySeries } = useMemo(() => {
    const byName = new Map<string, TraceRef>();
    const names = new Set<string>();
    const traces: EcSeries[] = [];
    visible.forEach((ref, index) => {
      const { trace } = ref;
      const title = traceName(ref);
      const name = names.has(title) ? `${title} (${index + 1})` : title;
      names.add(name);
      byName.set(name, ref);
      const factor = scale(ref) * (absolute && trace.direction === "discharge" ? -1 : 1);
      const color = COMPARE_COLORS[index % COMPARE_COLORS.length];
      const points = (values: number[]): EcSeries["data"] => values.map((y, i) => [voltageAt(trace, i, settings.gridMv), y * factor]);
      traces.push({ name, color, data: points(trace.values), recordNumbers: trace.records });
      if (rawOverlay && trace.rawValues) traces.push({ name: `${name} · 未平滑`, color, dashed: true, data: points(trace.rawValues) });
    });
    return { series: traces, sourceBySeries: byName };
  }, [visible, rawOverlay, absolute, scale, settings.gridMv]);
  const diagnostics = useMemo(() => run?.sources.flatMap((item) => item.cycles.flatMap((cycle) => {
    const examples = [...cycle.issues];
    if (!cycle.paired) examples.push("该圈只有单向恒流工步或记录未齐");
    if (!item.doc.endTime && cycle.cycle === item.doc.counts.cycles) examples.push("末圈结束状态待确认");
    if (cycle.cvCapacityMah == null) examples.push("恒压段容量缺失");
    return examples.map((issue) => `${item.doc.name} · 圈 ${cycle.cycle} · ${issue}`);
  })) ?? [], [run]);
  const comparisonWarnings = useMemo(() => {
    const warnings: string[] = [];
    for (const dir of ["charge", "discharge"] as const) {
      const sameDirection = eligible.filter((ref) => ref.trace.direction === dir);
      if (sameDirection.length < 2) continue;
      let lowCurrent = Infinity, highCurrent = 0, lowerVoltage = -Infinity, upperVoltage = Infinity;
      for (const ref of sameDirection) {
        const magnitude = Math.abs(ref.trace.currentMa) * scale(ref);
        lowCurrent = Math.min(lowCurrent, magnitude);
        highCurrent = Math.max(highCurrent, magnitude);
        lowerVoltage = Math.max(lowerVoltage, ref.trace.startBin * settings.gridMv / 1000);
        upperVoltage = Math.min(upperVoltage, (ref.trace.startBin + ref.trace.values.length) * settings.gridMv / 1000);
      }
      const label = dir === "charge" ? "充电" : "放电";
      if (highCurrent > 1.12 * lowCurrent) warnings.push(`${label}曲线的实测${normalized ? "比电流" : "电流"}不同`);
      if (upperVoltage <= lowerVoltage) warnings.push(`${label}曲线无共同覆盖电压区间`);
    }
    return warnings;
  }, [eligible, normalized, scale, settings.gridMv]);

  const start = useCallback(async (selections: { doc: CexDocument; expression: string }[], applied: DqdvSettings) => {
    task.current?.abort();
    const controller = new AbortController();
    task.current = controller;
    setFocus(null);
    setPage(0);
    setSource(undefined);
    sourceRequest.current++;
    setProgress("正在读取原始工步…");
    const result = await calculateDqdv(selections, applied, controller.signal, (text) => {
      if (task.current === controller && !controller.signal.aborted) setProgress(text);
    });
    if (task.current === controller) {
      task.current = null;
      setRun(result);
      setProgress("");
    }
  }, []);

  useEffect(() => {
    if (primary.counts.cycles) void start([{ doc: primary, expression: "" }], defaults);
    return () => { task.current?.abort(); task.current = null; sourceRequest.current++; };
  }, [primary.id, primary.counts.cycles, start]);

  async function locate(ref: TraceRef, record: number) {
    const request = ++sourceRequest.current;
    try {
      const data = await cexCommand<SourceDetail>("dqdv_source", { id: ref.source.doc.id, record });
      if (request === sourceRequest.current) setSource({ ref, data });
    } catch (error) {
      if (request === sourceRequest.current) notify("error", errorText(error));
    }
  }

  function pickVoltage(value: number) {
    if (picking === "first") setPicking(value);
    else if (typeof picking === "number" && Math.abs(value - picking) >= 0.0001) {
      setFrom(Math.min(picking, value).toFixed(4));
      setTo(Math.max(picking, value).toFixed(4));
      setPicking(false);
      setView("peaks");
    }
  }

  async function saveTable(kind: "peaks" | "curves") {
    setExporting(true);
    try {
      const columns = [...traceColumns, "peak_name", "window_min_V", "window_max_V", "baseline_cycle",
        "peak_V", "height_above_baseline", "FWHM_mV", "window_integral", "integral_unit", "coverage", "shift_mV"];
      const result = kind === "peaks" ? await exportDqdvTable("CEX-dQdV-峰表", columns,
        peaks.map((row) => [...traceMetadata(row.ref, settings, unit, massOf(row.ref.source.doc)),
        peakLabel, window?.[0] ?? null, window?.[1] ?? null, baseline || null, row.voltage, row.height,
        row.width == null ? null : 1000 * row.width, row.area, areaUnit, row.coverage, row.shift]))
        : await exportCexDqdvCurves({ settings, direction, normalized, absolute,
          sources: run?.sources.filter(({ doc }) => !normalized || massOf(doc).massMg).map((item) => {
            const basis = massOf(item.doc);
            return { id: item.doc.id, sha256: item.sha256, displayName: item.doc.name,
              cycles: item.cycles.map((cycle) => cycle.cycle),
              massOverrideMg: basis.massSource === "manual" ? basis.massMg : null };
          }),
        });
      if (!result.cancelled) notify(result.ok ? "success" : "error", result.ok ? "已导出 CSV" : (result.error ?? "导出失败"));
    } catch (error) {
      notify("error", errorText(error));
    } finally {
      setExporting(false);
    }
  }

  const showCycle = useCallback((cycle: number) => {
    if (!heatSource) return;
    setFocus({ id: heatSource.doc.id, cycle });
    setDirection(heatDirection);
    setView("curves");
  }, [heatSource, heatDirection]);

  return (
    <div className="grid min-h-0 flex-1 grid-rows-[auto_minmax(0,1fr)] min-[1010px]:grid-cols-[244px_minmax(0,1fr)] min-[1010px]:grid-rows-1">
      <aside aria-label="dQ/dV 数据选择" className="max-h-44 overflow-auto border-b border-[var(--divider)] p-3 min-[1010px]:max-h-none min-[1010px]:border-r min-[1010px]:border-b-0">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">选择文件和圈号</h2>
          {progress ? <Button kind="secondary" onClick={() => {
            task.current?.abort(); setProgress("正在结束当前批次…");
          }}>取消</Button> : <Button kind="primary" disabled={!canRun} onClick={() => void start(selected.map((doc) => ({
            doc, expression: expressions[doc.id] ?? "",
          })), proposed)}>计算</Button>}
        </div>
        <div className="mt-2 space-y-2">
          {docs.map((doc) => <div key={doc.id} className="rounded-[var(--radius-input)] border border-[var(--divider)] p-2">
            <label className="flex min-w-0 items-center gap-2 text-[12px]" title={doc.path}>
              <input type="checkbox" disabled={!doc.counts.cycles} checked={checked.includes(doc.id) && !!doc.counts.cycles}
                onChange={(event) => setChecked(event.target.checked ? [...checked, doc.id] : checked.filter((id) => id !== doc.id))} />
              <span className="min-w-0 flex-1 truncate">{doc.name}</span>
              <span className="shrink-0 text-[var(--text-secondary)]">{doc.counts.cycles} 圈</span>
            </label>
            <input className="cex-input mt-1.5 w-full" aria-label={`${doc.name} 的循环范围`} value={expressions[doc.id] ?? ""}
              placeholder="自动首圈 · 如 1,10,20-50s5,r1" disabled={!checked.includes(doc.id) || !doc.counts.cycles}
              onChange={(event) => setExpressions({ ...expressions, [doc.id]: event.target.value })} />
            <div className="mt-1 text-[11px] text-[var(--text-secondary)]">
              {doc.channel && `通道 ${doc.channel} · `}{doc.massMg ? `${displayNumber(doc.massMg)} mg` : "质量未记录"}
            </div>
            {normalized && checked.includes(doc.id) && <label className="mt-1.5 flex items-center justify-between gap-1 text-[11px]">
              归一化质量 (mg)
              <input type="number" step="any" min="0" className="cex-input w-20" value={masses[doc.id] ?? ""}
                placeholder={doc.massMg ? displayNumber(doc.massMg) : "输入质量"}
                aria-label={`${doc.name} 的归一化质量 (mg)`}
                onChange={(event) => setMasses({ ...masses, [doc.id]: event.target.value })} />
            </label>}
          </div>)}
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2 text-[12px]">
          <label className="grid gap-1">步长 (mV)
            <input className="cex-input min-w-0" type="number" min="0.5" max="50" step="0.5" value={grid} onChange={(event) => setGrid(event.target.value)} />
          </label>
          <label className="grid gap-1" title="差分前对电压网格上的累计容量进行高斯平滑，0 表示关闭">平滑宽度 (mV)
            <input className="cex-input min-w-0" type="number" min="0" max="100" step="1" value={smooth} onChange={(event) => setSmooth(event.target.value)} />
          </label>
        </div>
        {!progress && run && <div className="mt-1 text-[11px] text-[var(--text-secondary)]">
          当前结果 {settings.gridMv} mV 步长 · {settings.smoothingMv} mV 平滑{stale ? " · 参数有待应用" : ""}
        </div>}
        <label className="mt-3 flex items-center justify-between gap-2 text-[12px]">容量单位
          <select className="cex-input" value={normalized ? "specific" : "absolute"}
            onChange={(event) => setNormalized(event.target.value === "specific")}>
            <option value="absolute">mAh/V</option><option value="specific">mAh/(g·V)</option>
          </select>
        </label>
        <label className="mt-2 flex items-center gap-2 text-[12px]"><input type="checkbox" checked={absolute}
          onChange={(event) => setAbsolute(event.target.checked)} />放电显示绝对值</label>
        {normalized && (run?.sources ?? []).some((item) => !massOf(item.doc).massMg) && (
          <div className="mt-1 text-[11px] text-[var(--text-secondary)]">此单位下省略质量缺失的文件。</div>
        )}
      </aside>
      <section aria-label="dQ/dV 结果" className="flex min-h-0 min-w-0 flex-col">
        <div className="flex flex-wrap items-center gap-2 border-b border-[var(--divider)] px-3 py-2 text-[12px]">
          <Segmented value={view} onChange={(value) => { setView(value as View); setPicking(false); }} options={[
            { value: "curves", label: "曲线" }, { value: "evolution", label: "演变" }, { value: "peaks", label: "峰分析" },
          ]} />
          {view !== "evolution" && <select className="cex-input" aria-label="充放电方向" value={direction}
            onChange={(event) => { setDirection(event.target.value as typeof direction); setFocus(null); setPage(0); }}>
            <option value="both">充放电</option><option value="charge">充电</option><option value="discharge">放电</option>
          </select>}
          <span className="flex-1" />
          {view === "curves" && !!settings.smoothingMv && (
            <label className="flex items-center gap-1"><input type="checkbox" checked={rawOverlay}
              onChange={(event) => setRawOverlay(event.target.checked)} />叠加未平滑</label>
          )}
          <Button kind="secondary" onClick={() => void saveTable("curves")} disabled={!eligible.length || exporting}>曲线 CSV</Button>
        </div>
        {progress && <div role="status" className="border-b border-[var(--divider)] px-3 py-2 text-[12px]">{progress}</div>}
        {run && (run.cancelled || run.errors.length > 0) && (
          <div role="status" className="max-h-24 overflow-auto whitespace-pre-wrap border-b border-[var(--divider)] px-3 py-2 text-[12px]">
            {[run.cancelled ? "已停止。可以查看已完成的循环。" : "", ...run.errors].filter(Boolean).join("\n")}
          </div>
        )}
        {!progress && comparisonWarnings.length > 0 && (
          <div className="border-b border-[var(--divider)] px-3 py-1.5 text-[11px] text-[var(--text-secondary)]">
            {comparisonWarnings.join(" · ")}
          </div>
        )}
        {view === "curves" && <>
          {(focus || lastPage > 0 || picking !== false) && <div className="flex min-h-8 flex-wrap items-center gap-2 px-3 text-[12px]">
            {focus && <Button kind="ghost" onClick={() => setFocus(null)}>显示已选各圈</Button>}
            {lastPage > 0 && <>
              <Button kind="ghost" disabled={activePage === 0} onClick={() => setPage(activePage - 1)}>上一组</Button>
              <span>曲线 {activePage * seriesPerPage + 1}–{Math.min(displayed.length, (activePage + 1) * seriesPerPage)} / {displayed.length}</span>
              <Button kind="ghost" disabled={activePage === lastPage} onClick={() => setPage(activePage + 1)}>下一组</Button>
            </>}
            {picking !== false && <><Crosshair size={14} /><span>{picking === "first" ? "选择电压窗口的第一条边界" : "选择另一条边界"}</span>
              <Button kind="ghost" onClick={() => setPicking(false)}>取消选窗</Button></>}
          </div>}
          <div className="min-h-32 flex-1 px-2 py-1">
            {series.length ? <EcLineChart series={series} xName="电压 (V)" y1Name={`dQ/dV (${unit})`}
              height="100%" onRecord={(record, name) => {
                const ref = sourceBySeries.get(name);
                if (ref) void locate(ref, record);
              }}
              onX={picking !== false ? pickVoltage : undefined} selectedRange={window}
              exportName="CEX-dQdV-曲线" figureMetadata={{ ...metadata, visibleTraces: visible.map((ref) => traceMetadata(ref, settings, unit, massOf(ref.source.doc))) }} />
              : <div className="grid h-full place-items-center text-sm text-[var(--text-secondary)]" role="status">
                {progress ? "正在计算 dQ/dV…" : run ? "所选方向和圈段没有可绘制的曲线" : "选择 CEX 文件和圈号开始计算"}
              </div>}
          </div>
          {source && <div className="flex h-42 shrink-0 flex-col border-t border-[var(--divider)]">
            <div className="flex min-w-0 items-center gap-2 px-3 py-1 text-[11px] tabular-nums">
              <span className="min-w-0 flex-1 truncate">{traceName(source.ref)} · {source.data.step?.mode}
                {` · 记录 ${source.data.record.number} · ${displayNumber(source.data.record.current, 5)} mA`}</span>
              <button className="p-1 hover:bg-[var(--hover)]" aria-label="关闭原始工步" onClick={() => setSource(undefined)}><X size={14} /></button>
            </div>
            <div className="min-h-0 flex-1 px-2">
              <EcLineChart series={[{ name: "原始记录", data: source.data.points.map(([q, v]) => [q, v]) }]}
                xName="记录容量 (mAh)" y1Name="电压 (V)" height="100%"
                focusedPoint={source.data.record.capacity == null ? null : { x: source.data.record.capacity, y: [source.data.record.voltage] }} />
            </div>
          </div>}
        </>}
        {view === "evolution" && (heatSource ? <>
          <div className="flex flex-wrap gap-2 px-3 py-2 text-[12px]">
            <select className="cex-input" aria-label="热图文件" value={heatSource.doc.id} onChange={(event) => setSourceId(event.target.value)}>
              {heatSources.map(({ doc }) => <option key={doc.id} value={doc.id}>{doc.name}</option>)}
            </select>
            <select className="cex-input" aria-label="热图方向" value={heatDirection}
              onChange={(event) => setHeatDirection(event.target.value as Direction)}>
              <option value="charge">充电</option><option value="discharge">放电</option>
            </select>
            <span className="self-center text-[var(--text-secondary)]">{heatSource.cycles.length} 圈 · {unit}</span>
          </div>
          <DqdvHeatmap source={heatSource} direction={heatDirection} unit={unit} gridMv={settings.gridMv}
            scale={normalized && heatMass ? 1000 / heatMass : 1} absolute={absolute}
            metadata={{ ...metadata, selectedFile: heatSource.doc.filename, direction: heatDirection }} onCycle={showCycle} />
        </> : <div role="status" className="m-auto p-4 text-sm text-[var(--text-secondary)]">计算所需循环后查看热图</div>)}
        {view === "peaks" && <DqdvPeaks rows={peaks} sources={run?.sources ?? []} unit={unit} areaUnit={areaUnit} metadata={metadata} label={peakLabel}
          from={from} to={to} baseline={baseline} onFrom={setFrom} onTo={setTo} onLabel={setPeakLabel} onBaseline={setBaseline}
          onPick={() => { setFocus(null); setView("curves"); setPicking("first"); }} onExport={() => void saveTable("peaks")} exporting={exporting} />}
        {run && <details className="max-h-32 shrink-0 overflow-auto border-t border-[var(--divider)] px-3 py-2 text-[11px] text-[var(--text-secondary)]">
          <summary className="cursor-pointer select-none">工步与采样 · {allRefs.length} 条曲线
            {diagnostics.length > 0 && ` · ${diagnostics.length} 条数据提示`}</summary>
          {visible.map((ref) => <div className="mt-1.5" key={ref.key}>{traceName(ref)} · {displayNumber(ref.trace.currentMa)} mA
            {` · ${ref.trace.count} 点 · 采样电压间隔中位数 ${displayNumber(ref.trace.sampleMv)} mV`}
            {` · 恒压容量 ${displayNumber(ref.cycle.cvCapacityMah)} mAh`}
            {ref.trace.capacitySource === "integrated" && " · 电流积分"}
            {!!ref.trace.warnings.length && ` · ${ref.trace.warnings.join("，")}`}
            {view === "curves" && <button className="ml-2 text-[var(--accent)] hover:underline" type="button"
              onClick={() => void locate(ref, ref.trace.records[Math.floor(ref.trace.records.length / 2)])}>查看工步</button>}</div>)}
          {diagnostics.map((issue, i) => <div className="mt-1" key={i}>{issue}</div>)}
        </details>}
      </section>
    </div>
  );
}
