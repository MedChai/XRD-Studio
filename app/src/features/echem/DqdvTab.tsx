/** dQ/dV 页签:参数面板 + 多圈/多方向叠加曲线(按需拉取) + 逐圈瀑布热图。
 *
 *  性能约定(不许卡顿):
 *  - 曲线按 (圈号 × 方向) 并行拉取(ec.dqdv_curve,几百点/条),只在
 *    圈号/方向组合变化时重拉;
 *  - 瀑布矩阵一次拉取缓存在 store,方向/步长变了才重新拉(方向由
 *    瀑布区自己控制,与曲线区解耦);
 *  - 计算由显式按钮触发(约 1-2 秒,带等待态);参数改动只亮
 *    「参数已修改」徽标,不自动重算 —— 与 XRD 标注的 stale 语义一致。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { EcLineChart, type EcSeries } from "./EcLineChart";
import { Button, NumberField, Segmented } from "../../components/controls";
import { COMPARE_COLORS, ecDqdvCurve } from "../../services/engine";
import type { EcCellOverview, EcCurvePayload } from "../../types/engine";
import { useEcStore, useUiStore } from "../../stores";
import { t } from "../../i18n";

type Dir = "charge" | "discharge";
/** 曲线区方向:充电 / 放电 / 充放同图叠加。 */
type CurveDir = Dir | "both";
/** 同屏最多叠加的圈数 —— 与 plotStore.compareCurves 上限一致,配色刚好 6 色。 */
const MAX_PICKED = 6;

export function DqdvTab({ overview }: { overview: EcCellOverview }) {
  const cellId = useEcStore((s) => s.loadedCellId);
  const computed = useEcStore((s) => s.dqdvComputed);
  const params = useEcStore((s) => s.params);
  const paramsDirty = useEcStore((s) => s.paramsDirty);
  const computing = useEcStore((s) => s.computing);
  const patchParams = useEcStore((s) => s.patchParams);
  const computeDqdv = useEcStore((s) => s.computeDqdv);
  const showToast = useUiStore((s) => s.showToast);

  const validCycles = useMemo(
    () => overview.cycles.filter((c) => !c.incomplete).map((c) => c.cycle),
    [overview],
  );
  // 叠加比较:默认首圈(可增删,上限 6)。cell 切换时 CellPage 内容区
  // 带 key 重挂载,旧圈号不会带到新 cell —— 这里再按 validCycles 过滤兜底。
  const [pickedRaw, setPickedRaw] = useState<number[]>(() =>
    validCycles.length ? [validCycles[0]] : [],
  );
  const picked = useMemo(
    () => pickedRaw.filter((n) => validCycles.includes(n)),
    [pickedRaw, validCycles],
  );
  const [direction, setDirection] = useState<CurveDir>("charge");

  const [curves, setCurves] = useState<
    { cycle: number; dir: Dir; payload: EcCurvePayload }[]
  >([]);
  const [curveLoading, setCurveLoading] = useState(false);

  // 曲线:(圈号 × 方向) 并行拉取;alive 丢弃过期请求(快速改选时
  // 旧响应不覆盖新状态)。全部失败且非"没有曲线"才报错。
  useEffect(() => {
    if (cellId == null || !computed || picked.length === 0) {
      setCurves([]);
      setCurveLoading(false);
      return;
    }
    const dirs: Dir[] = direction === "both" ? ["charge", "discharge"] : [direction];
    const jobs = picked.flatMap((cycle) => dirs.map((dir) => ({ cycle, dir })));
    let alive = true;
    setCurveLoading(true);
    void Promise.all(
      jobs.map(async (j) => ({ ...j, r: await ecDqdvCurve(cellId, j.cycle, j.dir) })),
    ).then((results) => {
      if (!alive) return;
      setCurveLoading(false);
      const ok: { cycle: number; dir: Dir; payload: EcCurvePayload }[] = [];
      let firstErr: string | null = null;
      for (const { cycle, dir, r } of results) {
        if (r.ok) ok.push({ cycle, dir, payload: r.data });
        else if (!r.error.includes("没有曲线") && firstErr == null) firstErr = r.error;
      }
      if (firstErr != null && ok.length === 0) showToast("error", firstErr);
      setCurves(ok);
    });
    return () => {
      alive = false;
    };
  }, [cellId, picked, direction, computed, showToast]);

  const curveSeries = useMemo<EcSeries[]>(() => {
    const list: EcSeries[] = [];
    for (const { cycle, dir, payload } of curves) {
      const data: [number, number][] = [];
      for (let i = 0; i < payload.v.length; i++) data.push([payload.v[i], payload.dqdv[i]]);
      // 同圈同色(按选中次序取 6 色循环);充/放同图时用后缀 + 虚线区分
      const color = COMPARE_COLORS[picked.indexOf(cycle) % COMPARE_COLORS.length];
      const name =
        direction === "both"
          ? `${t.echem.cycleChip(cycle)} · ${
              dir === "charge" ? t.echem.directionCharge : t.echem.directionDischarge
            }`
          : t.echem.cycleChip(cycle);
      list.push({ name, data, color, dashed: dir === "discharge" && direction === "both" });
    }
    return list;
  }, [curves, picked, direction]);

  return (
    <div className="flex h-full flex-col overflow-y-auto px-4 pb-4">
      {/* 参数行:数值都走 patchParams(标脏),重算由按钮显式触发 */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[12px] text-[var(--text-secondary)]">
        <label className="flex items-center gap-1.5">
          {t.echem.paramDv}
          <NumberField
            value={params.dv_mv}
            onChange={(v) => patchParams({ dv_mv: v })}
            min={0.5}
            max={20}
            width={52}
          />
        </label>
        <label className="flex items-center gap-1.5">
          {t.echem.paramSg}
          <NumberField
            value={params.sg_window}
            onChange={(v) => patchParams({ sg_window: Math.max(5, v | 1) })}
            min={5}
            max={51}
            width={52}
          />
        </label>
        <label className="flex items-center gap-1.5">
          {t.echem.paramVMin}
          <NumberField
            value={params.v_min}
            onChange={(v) => patchParams({ v_min: v })}
            width={56}
          />
        </label>
        <label className="flex items-center gap-1.5">
          {t.echem.paramVMax}
          <NumberField
            value={params.v_max}
            onChange={(v) => patchParams({ v_max: v })}
            width={56}
          />
        </label>
        <label className="flex items-center gap-1.5">
          {t.echem.paramMass}
          <NumberField
            value={params.mass_mg ?? overview.massMg ?? 0}
            onChange={(v) => patchParams({ mass_mg: v > 0 ? v : null })}
            min={0}
            width={56}
          />
        </label>
        <span className="flex-1" />
        {paramsDirty && (
          <span className="rounded-full border border-[var(--warning)] px-2 py-0.5 text-[11px] text-[var(--warning)]">
            {t.echem.paramsModified}
          </span>
        )}
        <Button kind="primary" disabled={computing} onClick={() => void computeDqdv()}>
          {computing ? t.echem.dqdvComputing : t.echem.dqdvCompute}
        </Button>
      </div>

      {!computed ? (
        <div className="mt-6 flex flex-1 items-center justify-center text-[13px] text-[var(--text-tertiary)]">
          {t.echem.dqdvNotComputed}
        </div>
      ) : (
        <>
          {/* 曲线控件:方向(充/放/同图叠加)+ 圈号多选 */}
          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 text-[12px] text-[var(--text-secondary)]">
            <span className="font-medium">{t.echem.curveTitle}</span>
            <Segmented<CurveDir>
              options={[
                { value: "charge", label: t.echem.directionCharge },
                { value: "discharge", label: t.echem.directionDischarge },
                { value: "both", label: t.echem.directionBoth },
              ]}
              value={direction}
              onChange={setDirection}
            />
            <span className="text-[var(--text-tertiary)]">{t.echem.selectCycle}</span>
            {picked.map((n, i) => {
              const color = COMPARE_COLORS[i % COMPARE_COLORS.length];
              return (
                <button
                  key={n}
                  className="flex items-center gap-1.5 rounded-full border border-[var(--divider)] bg-[var(--bg-panel)] py-0.5 pl-1.5 pr-2 text-[11px] hover:bg-[var(--hover)]"
                  title={t.echem.removeCycleHint}
                  onClick={() => setPickedRaw((prev) => prev.filter((x) => x !== n))}
                >
                  <span
                    className="h-2.5 w-2.5 rounded-full"
                    style={{ background: color }}
                  />
                  <span className="tabular">{t.echem.cycleChip(n)}</span>
                  <span className="text-[var(--text-tertiary)]">×</span>
                </button>
              );
            })}
            <select
              value=""
              onChange={(e) => {
                const n = Number(e.target.value);
                if (!n) return;
                if (picked.length >= MAX_PICKED) {
                  showToast("error", t.echem.maxPicked(MAX_PICKED));
                  e.target.value = "";
                  return;
                }
                setPickedRaw((prev) => [...prev, n]);
              }}
              className="tabular rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-2 py-0.5 text-[12px]"
            >
              <option value="">{t.echem.addCycle}</option>
              {validCycles
                .filter((n) => !picked.includes(n))
                .map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
            </select>
            {curveLoading && (
              <span className="text-[var(--text-tertiary)]">{t.echem.loading}</span>
            )}
          </div>
          {curves.length > 0 ? (
            <EcLineChart
              series={curveSeries}
              xName="V"
              y1Name={t.echem.dqdvYAxis}
              height={280}
              exportName={`${overview.name}_dQdV`}
            />
          ) : curveLoading ? (
            <div className="h-[280px]" />
          ) : picked.length === 0 ? (
            <div className="flex h-[280px] items-center justify-center text-[12px] text-[var(--text-tertiary)]">
              {t.echem.pickCycleHint}
            </div>
          ) : (
            <div className="flex h-[280px] items-center justify-center text-[12px] text-[var(--text-tertiary)]">
              {t.echem.curveMissing}
            </div>
          )}

          {/* 瀑布热图(自带充/放切换,与曲线方向解耦) */}
          <WaterfallBlock />
        </>
      )}
    </div>
  );
}

/** 逐圈瀑布:canvas 直绘(rows×cols 矩阵,NaN 留空)。
 *  ECharts heatmap 要把 356 个电压 bin 变成 category 轴,开销大且无必要
 *  —— 矩阵图像本身用 2D canvas 十几行就够了。
 *  方向切换由本块自己控制:曲线区可以"充放同图",瀑布是单方向热图,
 *  两者语义不同,不该再联动。 */
function WaterfallBlock() {
  const cellId = useEcStore((s) => s.loadedCellId);
  const waterfall = useEcStore((s) => s.waterfall);
  const wfDirection = useEcStore((s) => s.waterfallDirection);
  const wfStep = useEcStore((s) => s.waterfallStep);
  const setWaterfallOptions = useEcStore((s) => s.setWaterfallOptions);
  const loadWaterfall = useEcStore((s) => s.loadWaterfall);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // 进入该方向即取矩阵;缓存命中(方向/步长一致)则不重发
  useEffect(() => {
    if (cellId == null) return;
    void loadWaterfall(wfDirection, wfStep);
  }, [cellId, wfDirection, wfStep, loadWaterfall]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wf = waterfall;
    if (!canvas || !wf) return;
    const [rows, cols] = wf.shape;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = 240;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    // 值域:排除 NaN,压到 p98 防单个尖峰把整图压黑
    let lo = Infinity;
    let hi = -Infinity;
    const finite: number[] = [];
    for (let i = 0; i < wf.matrix.length; i++) {
      const v = wf.matrix[i];
      if (Number.isFinite(v)) {
        finite.push(v);
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
    }
    finite.sort((a, b) => a - b);
    const hi98 = finite.length ? finite[Math.floor(finite.length * 0.98)] : hi;
    const range = hi98 - lo || 1;

    const GUTTER = 36; // 左侧圈号刻度留白
    const cw = (w - GUTTER) / cols;
    const ch = h / rows;
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const v = wf.matrix[r * cols + c];
        if (!Number.isFinite(v)) continue;
        const k = Math.min(1, Math.max(0, (v - lo) / range));
        ctx.fillStyle = heatColor(k);
        ctx.fillRect(GUTTER + c * cw, r * ch, Math.ceil(cw), Math.ceil(ch));
      }
    }

    // 圈号刻度(顶/中/底,行数少时去重)—— 此前 y 方向没有任何标注,
    // 第几行是第几圈只能对着取样间隔心算。
    const labelRows = [...new Set([0, Math.floor((rows - 1) / 2), rows - 1])];
    ctx.font = "10px system-ui, sans-serif";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.fillStyle =
      getComputedStyle(document.documentElement)
        .getPropertyValue("--text-tertiary")
        .trim() || "#8a8f96";
    for (const r of labelRows) {
      if (r < 0 || r >= wf.cycles.length) continue;
      ctx.fillText(String(wf.cycles[r]), GUTTER - 6, r * ch + ch / 2);
    }
  }, [waterfall]);

  return (
    <div className="mt-3">
      <div className="flex items-center gap-3 text-[12px] text-[var(--text-secondary)]">
        <span className="font-medium">{t.echem.waterfallTitle}</span>
        <Segmented<Dir>
          options={[
            { value: "charge", label: t.echem.directionCharge },
            { value: "discharge", label: t.echem.directionDischarge },
          ]}
          value={wfDirection}
          onChange={(v) => setWaterfallOptions(v, wfStep)}
        />
        <label className="flex items-center gap-1.5">
          {t.echem.waterfallStep}
          <NumberField
            value={wfStep}
            onChange={(v) => setWaterfallOptions(wfDirection, Math.max(1, Math.round(v)))}
            min={1}
            width={48}
          />
        </label>
        {waterfall && (
          <span className="text-[11px] text-[var(--text-tertiary)]">
            {waterfall.cycles[0]} → {waterfall.cycles[waterfall.cycles.length - 1]}
          </span>
        )}
      </div>
      {waterfall ? (
        <div className="mt-1">
          <canvas ref={canvasRef} className="w-full rounded-[var(--radius-input)]" style={{ height: 240 }} />
          {/* 左侧 36px 是画布内的圈号刻度留白,x 标签同步缩进对齐电压区 */}
          <div className="flex justify-between pl-[36px] text-[10px] text-[var(--text-tertiary)]">
            <span>{waterfall.v[0].toFixed(2)} V</span>
            <span>V</span>
            <span>{waterfall.v[waterfall.v.length - 1].toFixed(2)} V</span>
          </div>
        </div>
      ) : (
        <div className="flex h-[240px] items-center justify-center text-[12px] text-[var(--text-tertiary)]">
          {t.echem.loading}
        </div>
      )}
    </div>
  );
}

/** 热图色带:蓝 → 橙(两端在浅/深主题下都可辨)。 */
function heatColor(k: number): string {
  // hue 210(蓝)→ 25(橙),饱和度 70%,亮度 45–62%
  const hue = 210 - 185 * k;
  const light = 45 + 17 * k;
  return `hsl(${hue.toFixed(0)}, 70%, ${light.toFixed(0)}%)`;
}
