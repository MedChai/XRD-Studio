/** 右侧 Inspector(§27):PROCESSING / PEAKS / DISPLAY / SAMPLE 四段。 */

import { useEffect, useMemo, useState } from "react";
import { ChevronDown } from "lucide-react";
import {
  Button,
  NumberField,
  Row,
  SectionHeader,
  Segmented,
  SwitchRow,
} from "../../components/controls";
import { RangeSlider } from "../../components/Slider";
import {
  useAnalysisStore,
  usePlotStore,
  useSelectionStore,
  useUiStore,
} from "../../stores";
import { engineCommand } from "../../services/engine";
import type {
  AnalysisPreset,
  HighNiResult,
  MaterialProfile,
} from "../../types/engine";
import { cn } from "../../components/cn";
import { t } from "../../i18n";
import { useProjectStore } from "../../stores";

export function Inspector() {
  const pipeline = useAnalysisStore((s) => s.pipeline);
  const patch = useAnalysisStore((s) => s.patchPipeline);
  const smoothingAffects = useAnalysisStore((s) => s.smoothingAffectsMetrics);
  const status = useAnalysisStore((s) => s.status);
  const loadedDatasetId = useAnalysisStore((s) => s.loadedDatasetId);
  const curve = useAnalysisStore((s) => s.curve);
  // 标注状态在 store 里持久于数据集会话:打开图恢复、调参不丢、
  // 点「标注晶面」刷新 —— 不再用组件本地 useState(切数据集即丢)。
  const annotation = useAnalysisStore((s) => s.annotation);
  const annotationStale = useAnalysisStore((s) => s.annotationStale);
  const annotating = useAnalysisStore((s) => s.annotationLoading);
  const recomputeAnnotation = useAnalysisStore((s) => s.recomputeAnnotation);
  const detecting = useAnalysisStore((s) => s.detecting);
  const detectPeaks = useAnalysisStore((s) => s.detectPeaks);
  const peaks = useAnalysisStore((s) => s.peaks);
  // 未归属峰提示:杂相峰引擎刻意报 none(CLAUDE.md 高镍单物相假设)。
  // 不提示的话用户会把"没标"读成"没有杂相" —— 那是一个错误的科学结论。
  const unassignedCount = useMemo(
    () => peaks.filter((p) => !p.hkl && p.hkl_confidence !== "ambiguous").length,
    [peaks],
  );
  const annotatedCount = useMemo(() => peaks.filter((p) => p.hkl).length, [peaks]);
  // 逐字段选择器(§15):plotStore 全量解构会让 crosshair/cursor 的
  // mousemove 更新重渲染整个 Inspector。
  const offset = usePlotStore((s) => s.offset);
  const setOffset = usePlotStore((s) => s.setOffset);
  const showLabels = usePlotStore((s) => s.showLabels);
  const setShowLabels = usePlotStore((s) => s.setShowLabels);
  const showBackground = usePlotStore((s) => s.showBackground);
  const setShowBackground = usePlotStore((s) => s.setShowBackground);
  const xDomain = usePlotStore((s) => s.xDomain);
  const zoom = usePlotStore((s) => s.zoom);
  const [advanced, setAdvanced] = useState(false);
  const [cropMinDraft, setCropMinDraft] = useState("");
  const [cropMaxDraft, setCropMaxDraft] = useState("");
  // 选择器订阅:不要用 useUiStore() 全量解构 —— uiStore 里的 toast 计时器
  // 每次变化都会重渲染整个 Inspector
  const showToast = useUiStore((s) => s.showToast);
  const samples = useProjectStore((s) => s.samples);
  const selectedSampleId = useSelectionStore((s) => s.sampleId);
  const currentSample = samples.find((sample) => sample.id === selectedSampleId);
  const profileProjectId = currentSample?.project_id ?? null;
  const [profiles, setProfiles] = useState<MaterialProfile[]>([]);
  const [profileId, setProfileId] = useState<number | null>(null);
  // 参数预设(§1.2 S5):与材料档案同项目作用域,随 project 加载。
  const [presets, setPresets] = useState<AnalysisPreset[]>([]);
  const [presetName, setPresetName] = useState("");
  const [presetId, setPresetId] = useState<number | null>(null);
  // S5 套用范围:只对所选几条谱套参数是真实需求(如"400/450 °C 两条"),
  // 默认整项目会误改全部
  const [presetScope, setPresetScope] = useState<"current" | "selected" | "project">(
    "project",
  );
  const [presetDsSel, setPresetDsSel] = useState<Set<number>>(new Set());
  const projectDatasets = useMemo(() => {
    const out: { id: number; label: string }[] = [];
    for (const s of samples) {
      if (s.project_id !== profileProjectId) continue;
      for (const d of s.datasets) out.push({ id: d.id, label: `${s.name} / ${d.name}` });
      for (const b of s.batches) {
        for (const d of b.datasets) {
          out.push({ id: d.id, label: `${s.name} / ${b.name} / ${d.name}` });
        }
      }
    }
    return out;
  }, [samples, profileProjectId]);
  const [highNiResult, setHighNiResult] = useState<HighNiResult | null>(null);
  const [highNiBusy, setHighNiBusy] = useState(false);
  /** 库里存的结果是对上一版文件算的(source_hash 不匹配)。仍显示,但提示重算。 */
  const [highNiStale, setHighNiStale] = useState(false);

  // 引擎只在 background.enabled 时返回背景曲线
  const hasBackground = (curve?.background?.length ?? 0) > 0;

  const bg = pipeline.background;
  const sm = pipeline.smoothing;
  const pk = pipeline.peaks;
  const crop = pipeline.crop;

  useEffect(() => {
    let alive = true;
    setHighNiResult(null);
    setHighNiStale(false);
    setProfiles([]);
    setProfileId(null);
    setPresets([]);
    setPresetId(null);
    if (profileProjectId == null) return () => { alive = false; };
    void engineCommand<{ profiles: MaterialProfile[] }>("profiles.list", {
      project_id: profileProjectId,
    }).then((result) => {
      if (!alive || !result.ok) return;
      setProfiles(result.data.profiles);
      setProfileId(result.data.profiles[0]?.id ?? null);
    });
    void engineCommand<{ presets: AnalysisPreset[] }>("presets.list", {
      project_id: profileProjectId,
    }).then((result) => {
      if (!alive || !result.ok) return;
      setPresets(result.data.presets);
      setPresetId(result.data.presets[0]?.id ?? null);
    });
    return () => { alive = false; };
  }, [profileProjectId]);

  // 切数据集时**先读回已保存的分析结果**。
  //
  // domain_results 此前是只写表(全仓库只有 INSERT/DELETE,没有 SELECT),
  // 于是用户切走再切回来只能重算 —— 单次全谱精修实测 1.5–4.5 秒,而这期间
  // 面板是空的。库里那份结果一直都在,只是从来没人读。
  useEffect(() => {
    let alive = true;
    setHighNiResult(null);
    setHighNiStale(false);
    if (loadedDatasetId == null) return () => { alive = false; };
    void engineCommand<{
      found: boolean;
      result: HighNiResult | null;
      stale: boolean;
    }>("domain.high_ni.get", { dataset_id: loadedDatasetId }).then((r) => {
      if (!alive || !r.ok || !r.data.found || !r.data.result) return;
      setHighNiResult(r.data.result);
      setHighNiStale(r.data.stale);
    });
    return () => { alive = false; };
  }, [loadedDatasetId]);

  useEffect(() => {
    setCropMinDraft(crop.x_min == null ? "" : String(crop.x_min));
    setCropMaxDraft(crop.x_max == null ? "" : String(crop.x_max));
  }, [crop.x_min, crop.x_max]);

  const cropMin = cropMinDraft.trim() === "" ? null : Number(cropMinDraft);
  const cropMax = cropMaxDraft.trim() === "" ? null : Number(cropMaxDraft);
  const cropNumbersValid =
    (cropMin === null || Number.isFinite(cropMin)) &&
    (cropMax === null || Number.isFinite(cropMax));
  const cropInvalid =
    !cropNumbersValid ||
    (cropMin !== null && cropMax !== null && cropMin >= cropMax);

  function commitCrop() {
    if (cropInvalid) {
      showToast("error", t.inspector.cropInvalid);
      return;
    }
    patch({
      crop: {
        ...crop,
        x_min: cropMin,
        x_max: cropMax,
      },
    });
  }

  function useVisibleRange() {
    const range: [number, number] | null = zoom
      ? [zoom.from, zoom.to]
      : xDomain;
    if (!range) return;
    setCropMinDraft(String(range[0]));
    setCropMaxDraft(String(range[1]));
    patch({
      crop: { ...crop, enabled: true, x_min: range[0], x_max: range[1] },
    });
  }

  async function savePreset() {
    if (profileProjectId == null) return;
    const name = presetName.trim();
    if (!name) {
      showToast("error", t.inspector.presetNameRequired);
      return;
    }
    const r = await engineCommand<{ presetId: number }>("presets.save", {
      project_id: profileProjectId,
      name,
      pipeline,
    });
    if (!r.ok) {
      showToast("error", r.error);
      return;
    }
    setPresetName("");
    const listed = await engineCommand<{ presets: AnalysisPreset[] }>("presets.list", {
      project_id: profileProjectId,
    });
    if (listed.ok) {
      setPresets(listed.data.presets);
      setPresetId(r.data.presetId);
    }
    showToast("success", t.inspector.presetSaved(name));
  }

  async function applyPreset() {
    if (profileProjectId == null || presetId == null) return;
    // S5:把预设批量写到所选范围数据集的 analyses.pipeline_json。
    // 当前打开的那条再 loadDataset 读回 —— 不旁路 preview/commit 状态机。
    const ids =
      presetScope === "current"
        ? loadedDatasetId != null
          ? [loadedDatasetId]
          : []
        : presetScope === "selected"
          ? [...presetDsSel]
          : projectDatasets.map((d) => d.id);
    if (ids.length === 0) {
      showToast("error", t.inspector.presetNoTarget);
      return;
    }
    const r = await engineCommand<{ applied: number }>("presets.apply", {
      preset_id: presetId,
      dataset_ids: ids,
    });
    if (!r.ok) {
      showToast("error", r.error);
      return;
    }
    if (loadedDatasetId != null) {
      await useAnalysisStore.getState().loadDataset(loadedDatasetId);
    }
    showToast("success", t.inspector.presetApplied(r.data.applied));
  }

  async function createDefaultProfile() {
    if (profileProjectId == null) return;
    const result = await engineCommand<{ profileId: number }>("profiles.create", {
      project_id: profileProjectId,
      name: t.inspector.defaultProfileName,
      formula: "LiNi0.90Mn0.05Al0.05O2",
      ni_fraction: 0.90,
      li_tm_ratio: 1.0,
      wavelength: curve?.wavelength ?? 1.5418,
    });
    if (!result.ok) {
      showToast("error", result.error);
      return;
    }
    const listed = await engineCommand<{ profiles: MaterialProfile[] }>("profiles.list", {
      project_id: profileProjectId,
    });
    if (listed.ok) {
      setProfiles(listed.data.profiles);
      setProfileId(result.data.profileId);
    }
    showToast("success", t.inspector.profileCreated);
  }

  async function runHighNiAnalysis() {
    if (loadedDatasetId == null) return;
    setHighNiBusy(true);
    const result = await engineCommand<HighNiResult>("domain.high_ni.analyze", {
      dataset_id: loadedDatasetId,
      profile_id: profileId,
      modules: ["mixing", "layered", "lattice"],
      fit: true,
    });
    setHighNiBusy(false);
    if (!result.ok) {
      showToast("error", result.error);
      return;
    }
    setHighNiResult(result.data);
    setHighNiStale(false);
    showToast("success", t.inspector.highNiDone);
  }

  async function annotateHkl() {
    // 「标注晶面」= 权威重算:走 store.recomputeAnnotation(引擎侧会
    // 覆盖 annotation_json、清 stale;合并逻辑与后台补标共用一份)。
    await recomputeAnnotation();
  }

  /** 引擎的 splitStatus 是内部枚举,直接渲染会在中文界面里露出英文。 */
  const splitLabel = (v?: string) =>
    v === "clear"
      ? t.inspector.splitClear
      : v === "weak"
        ? t.inspector.splitWeak
        : v === "unresolved"
          ? t.inspector.splitUnresolved
          : v === "not_found"
            ? t.inspector.splitNotFound
            : "—";
  const confLabel = (v?: string) =>
    v === "high"
      ? t.inspector.confHigh
      : v === "medium"
        ? t.inspector.confMedium
        : v === "low"
          ? t.inspector.confLow
          : "—";

  const riskLabel = (risk?: string) =>
    risk === "low"
      ? t.inspector.riskLow
      : risk === "medium"
        ? t.inspector.riskMedium
        : risk === "high"
          ? t.inspector.riskHigh
          : t.inspector.riskUnknown;
  const orderLabel = (order?: string) =>
    order === "good"
      ? t.inspector.orderGood
      : order === "acceptable"
        ? t.inspector.orderAcceptable
        : order === "degraded"
          ? t.inspector.orderDegraded
          : t.inspector.orderUnknown;
  const numberText = (value: number | null | undefined, digits = 3) =>
    value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);

  return (
    <aside
      className="flex shrink-0 flex-col gap-3 overflow-y-auto border-l border-[var(--divider)] bg-[var(--bg-panel)] py-2"
      style={{ width: "var(--inspector-w)" }}
    >
      {/* ---------------- PROCESSING ---------------- */}
      <section>
        <SectionHeader>{t.inspector.processing}</SectionHeader>

        <SwitchRow
          label={t.inspector.crop}
          checked={crop.enabled}
          onChange={(v) => {
            if (v && cropInvalid) {
              showToast("error", t.inspector.cropInvalid);
              return;
            }
            patch({ crop: { ...crop, enabled: v } });
          }}
        />
        {crop.enabled && (
          <div className="space-y-1 px-3 pb-2">
            <div className="flex items-center gap-1.5">
              <input
                type="number"
                value={cropMinDraft}
                placeholder={t.inspector.cropMinPlaceholder}
                onChange={(e) => setCropMinDraft(e.target.value)}
                onBlur={commitCrop}
                onKeyDown={(e) => e.key === "Enter" && commitCrop()}
                className="tabular min-w-0 flex-1 rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-1.5 py-1 text-[12px] focus:border-[var(--accent)] focus:outline-none"
                aria-label={t.inspector.cropMin}
              />
              <span className="text-[11px] text-[var(--text-tertiary)]">–</span>
              <input
                type="number"
                value={cropMaxDraft}
                placeholder={t.inspector.cropMaxPlaceholder}
                onChange={(e) => setCropMaxDraft(e.target.value)}
                onBlur={commitCrop}
                onKeyDown={(e) => e.key === "Enter" && commitCrop()}
                className="tabular min-w-0 flex-1 rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-1.5 py-1 text-[12px] focus:border-[var(--accent)] focus:outline-none"
                aria-label={t.inspector.cropMax}
              />
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-[10px] text-[var(--text-tertiary)]">{t.inspector.cropUnit}</span>
              <Button
                kind="ghost"
                disabled={!xDomain}
                onClick={useVisibleRange}
              >
                {t.inspector.useVisibleRange}
              </Button>
            </div>
            {cropInvalid && (
              <div className="text-[11px] text-[var(--danger)]">{t.inspector.cropInvalid}</div>
            )}
          </div>
        )}

        <SwitchRow
          label={t.inspector.background}
          checked={bg.enabled}
          onChange={(v) => patch({ background: { ...bg, enabled: v } })}
        />
        {bg.enabled && (
          <>
            <Row label={t.inspector.method}>
              <Segmented
                options={[
                  { value: "asls", label: "AsLS" },
                  { value: "polynomial", label: "Poly" },
                ]}
                value={bg.method}
                onChange={(m) => patch({ background: { ...bg, method: m } })}
              />
            </Row>
            {bg.method === "asls" ? (
              <>
                {/* §7.3:λ 用 log2 刻度,禁止裸输入 */}
                <Row label={t.inspector.smoothness}>
                  <span className="tabular text-[12px] font-medium">
                    10^{Math.round(Math.log10(bg.smoothness))}
                  </span>
                </Row>
                <div className="flex items-center gap-2 px-3 py-1">
                  <RangeSlider
                    value={[Math.log10(bg.smoothness)]}
                    min={2}
                    max={10}
                    step={0.1}
                    onValueChange={([v]) =>
                      patch({
                        background: { ...bg, smoothness: Math.pow(10, v) },
                      })
                    }
                  />
                </div>
                <Row label={t.inspector.asymmetry}>
                  <NumberField
                    value={bg.asymmetry}
                    min={0.0001}
                    max={0.5}
                    onChange={(v) => patch({ background: { ...bg, asymmetry: v } })}
                    width={72}
                  />
                </Row>
              </>
            ) : (
              <Row label={t.inspector.order}>
                <NumberField
                  value={bg.order}
                  min={1}
                  max={8}
                  onChange={(v) => patch({ background: { ...bg, order: Math.round(v) } })}
                  width={56}
                />
              </Row>
            )}
          </>
        )}

        <SwitchRow
          label={t.inspector.smoothing}
          checked={sm.enabled}
          onChange={(v) =>
            patch({ smoothing: { ...sm, enabled: v } })
          }
        />
        {sm.enabled && (
          <>
            <Row label={t.inspector.window}>
              <span className="tabular text-[12px] font-medium">{sm.window_length}</span>
            </Row>
            <div className="flex items-center gap-2 px-3 py-1">
              <RangeSlider
                value={[sm.window_length]}
                min={3}
                max={101}
                step={2}
                onValueChange={([v]) =>
                  patch({ smoothing: { ...sm, window_length: Math.round(v) | 1 } })
                }
              />
            </div>
            {smoothingAffects && (
              <div className="mx-3 my-1 rounded-[var(--radius-control)] bg-[color-mix(in_srgb,var(--warning)_14%,transparent)] px-2 py-1.5 text-[11px] text-[var(--text-secondary)]">
                {t.inspector.smoothingWarning}
              </div>
            )}
          </>
        )}

        {/* 参数预设(§1.2 S5):当前 pipeline 存一份,同项目其它数据集一键套用 */}
        <div className="mt-1 border-t border-[var(--divider)] pt-1">
          <div className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
            {t.inspector.presetSection}
          </div>
          <div className="flex gap-1.5 px-3 pb-1">
            <input
              value={presetName}
              onChange={(e) => setPresetName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void savePreset()}
              placeholder={t.inspector.presetNamePlaceholder}
              disabled={profileProjectId == null}
              className="min-w-0 flex-1 rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-1.5 py-1 text-[12px] focus:border-[var(--accent)] focus:outline-none disabled:opacity-50"
            />
            <Button
              kind="ghost"
              disabled={profileProjectId == null}
              onClick={() => void savePreset()}
            >
              {t.inspector.presetSave}
            </Button>
          </div>
          <div className="flex gap-1.5 px-3 pb-2">
            <select
              value={presetId ?? ""}
              onChange={(e) =>
                setPresetId(e.target.value ? Number(e.target.value) : null)
              }
              className="min-w-0 flex-1 rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-1.5 py-1 text-[12px] text-[var(--text-primary)] focus:border-[var(--accent)] focus:outline-none"
              aria-label={t.inspector.presetPick}
            >
              {presets.length === 0 && (
                <option value="">{t.inspector.presetNone}</option>
              )}
              {presets.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <Button
              kind="ghost"
              disabled={presetId == null}
              onClick={() => void applyPreset()}
            >
              {t.inspector.presetApply}
            </Button>
          </div>
          <div className="px-3 pb-1">
            <div className="mb-1 text-[11px] text-[var(--text-tertiary)]">
              {t.inspector.presetScopeLabel}
            </div>
            <Segmented
              options={[
                { value: "current", label: t.inspector.presetScopeCurrent },
                { value: "selected", label: t.inspector.presetScopeSelected },
                { value: "project", label: t.inspector.presetScopeProject },
              ]}
              value={presetScope}
              onChange={setPresetScope}
            />
            {presetScope === "selected" && (
              <div className="mt-1 max-h-28 overflow-auto rounded-[var(--radius-control)] border border-[var(--divider)] p-1">
                {projectDatasets.length === 0 && (
                  <div className="px-1 py-1 text-[11px] text-[var(--text-tertiary)]">
                    {t.inspector.presetNoTarget}
                  </div>
                )}
                {projectDatasets.map((d) => (
                  <label
                    key={d.id}
                    className="flex cursor-pointer items-center gap-1.5 rounded px-1 py-0.5 text-[11px] hover:bg-[var(--hover)]"
                  >
                    <input
                      type="checkbox"
                      checked={presetDsSel.has(d.id)}
                      onChange={(e) => {
                        const next = new Set(presetDsSel);
                        if (e.target.checked) next.add(d.id);
                        else next.delete(d.id);
                        setPresetDsSel(next);
                      }}
                    />
                    <span className="truncate">{d.label}</span>
                  </label>
                ))}
              </div>
            )}
          </div>
        </div>
      </section>

      {/* ---------------- HIGH-NI ---------------- */}
      <section className="border-t border-[var(--divider)] pt-1">
        <SectionHeader>{t.inspector.highNi}</SectionHeader>
        <div className="space-y-2 px-3 pb-2">
          <div className="flex items-center gap-2">
            <select
              value={profileId ?? ""}
              onChange={(e) => setProfileId(e.target.value ? Number(e.target.value) : null)}
              className="min-w-0 flex-1 rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-1.5 py-1 text-[12px] text-[var(--text-primary)] focus:border-[var(--accent)] focus:outline-none"
              aria-label={t.inspector.profile}
              disabled={profiles.length === 0}
            >
              {profiles.length === 0 && <option value="">{t.inspector.noProfile}</option>}
              {profiles.map((profile) => (
                <option key={profile.id} value={profile.id}>{profile.name}</option>
              ))}
            </select>
            <Button kind="ghost" onClick={() => void createDefaultProfile()}>
              {t.inspector.createProfile}
            </Button>
          </div>
          <Button
            kind="secondary"
            disabled={loadedDatasetId == null || highNiBusy || profileId == null}
            onClick={() => void runHighNiAnalysis()}
          >
            {highNiBusy ? t.inspector.highNiRunning : t.inspector.runHighNi}
          </Button>
          {highNiResult && (
            <div className="space-y-1.5 border-t border-[var(--divider)] pt-2 text-[11px]">
              <div className="flex justify-between gap-2">
                <span className="text-[var(--text-secondary)]">{t.inspector.mixingRisk}</span>
                <span className="font-medium text-[var(--text-primary)]">{riskLabel(highNiResult.mixing?.risk)}</span>
              </div>
              <div className="flex justify-between gap-2">
                <span className="text-[var(--text-secondary)]">{t.inspector.areaRatio}</span>
                <span className="tabular text-[var(--text-primary)]">{numberText(highNiResult.mixing?.i003_i104_area_ratio, 3)}</span>
              </div>
              <div className="flex justify-between gap-2">
                <span className="text-[var(--text-secondary)]">{t.inspector.heightRatio}</span>
                <span className="tabular text-[var(--text-primary)]">{numberText(highNiResult.mixing?.i003_i104_height_ratio, 3)}</span>
              </div>
              <div className="flex justify-between gap-2">
                <span className="text-[var(--text-secondary)]">{t.inspector.rOrder}</span>
                <span className="tabular text-[var(--text-primary)]">{numberText(highNiResult.mixing?.r_order, 3)}</span>
              </div>
              {/* 混排指标的积分边界(F8):面积比是按哪一段算的,摊开给用户核对。
                  没有它,用户无法判断 0.2° 的边界偏移是不是把邻峰算进去了。 */}
              {(() => {
                const b = highNiResult.mixing?.peaks_area_bounds;
                if (!b) return null;
                const fmt = (v: [number, number] | null | undefined) =>
                  v ? `${v[0].toFixed(2)}–${v[1].toFixed(2)}` : "—";
                return (
                  <div className="tabular flex justify-between gap-2 text-[10px] text-[var(--text-tertiary)]">
                    <span>003 {fmt(b["003"])}</span>
                    <span>104 {fmt(b["104"])}</span>
                    <span>101 {fmt(b["101"])}</span>
                  </div>
                );
              })()}
              <div className="flex justify-between gap-2">
                <span className="text-[var(--text-secondary)]">{t.inspector.layeredOrder}</span>
                <span className="font-medium text-[var(--text-primary)]">{orderLabel(highNiResult.layered?.layeredOrder)}</span>
              </div>
              <div className="flex justify-between gap-2">
                <span className="text-[var(--text-secondary)]">{t.inspector.split006102}</span>
                <span className="tabular text-[var(--text-primary)]">{splitLabel(highNiResult.layered?.split006_102.splitStatus)}</span>
              </div>
              <div className="flex justify-between gap-2">
                <span className="text-[var(--text-secondary)]">{t.inspector.split018110}</span>
                <span className="tabular text-[var(--text-primary)]">{splitLabel(highNiResult.layered?.split018_110.splitStatus)}</span>
              </div>
              <div className="grid grid-cols-3 gap-1 border-t border-[var(--divider)] pt-1.5">
                <span className="tabular">a {numberText(highNiResult.lattice?.a, 4)}</span>
                <span className="tabular">c {numberText(highNiResult.lattice?.c, 4)}</span>
                <span className="tabular">c/a {numberText(highNiResult.lattice?.c_over_a, 3)}</span>
              </div>
              <div className="text-[10px] text-[var(--text-tertiary)]">
                {t.inspector.confidence}: {confLabel(highNiResult.confidence)}
              </div>
              {highNiStale && (
                <div className="rounded-[var(--radius-control)] bg-[color-mix(in_srgb,var(--warning)_18%,transparent)] px-2 py-1 text-[10px] text-[var(--text-secondary)]">
                  {t.inspector.staleResult}
                </div>
              )}
              {highNiResult.warnings.length > 0 && (
                <div className="rounded-[var(--radius-control)] bg-[color-mix(in_srgb,var(--warning)_12%,transparent)] px-2 py-1 text-[10px] text-[var(--text-secondary)]">
                  {highNiResult.warnings[0]}
                </div>
              )}
            </div>
          )}
        </div>
      </section>

      {/* ---------------- PEAKS ---------------- */}
      <section className="border-t border-[var(--divider)] pt-1">
        <SectionHeader>{t.inspector.peaks}</SectionHeader>
        <Row label={t.inspector.sensitivity}>
          <Segmented
            options={[
              { value: "low", label: t.inspector.sensitivityLow },
              { value: "medium", label: t.inspector.sensitivityMed },
              { value: "high", label: t.inspector.sensitivityHigh },
            ]}
            value={pk.sensitivity}
            onChange={(v) => patch({ peaks: { ...pk, sensitivity: v } })}
          />
        </Row>
        <Row label={t.inspector.minHeight}>
          <NumberField
            value={pk.min_height_pct}
            min={0}
            max={100}
            suffix="%"
            onChange={(v) => patch({ peaks: { ...pk, min_height_pct: v } })}
          />
        </Row>
        <Row label={t.inspector.minDistance}>
          <NumberField
            value={pk.min_distance_deg}
            min={0.01}
            max={5}
            suffix="°"
            onChange={(v) => patch({ peaks: { ...pk, min_distance_deg: v } })}
          />
        </Row>

        <button
          className="tr flex w-full items-center gap-1 px-3 py-1 text-[12px] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
          onClick={() => setAdvanced(!advanced)}
        >
          <ChevronDown
            size={12}
            className={cn("transition-transform", !advanced && "-rotate-90")}
          />
          {t.inspector.advanced}
        </button>
        {advanced && (
          <>
            <Row label={t.inspector.prominence}>
              <NumberField
                value={pk.prominence ?? 0}
                onChange={(v) =>
                  patch({ peaks: { ...pk, prominence: v > 0 ? v : null } })
                }
              />
            </Row>
            <Row label={t.inspector.width}>
              <NumberField
                value={pk.width ?? 0}
                onChange={(v) => patch({ peaks: { ...pk, width: v > 0 ? v : null } })}
              />
            </Row>
          </>
        )}

        <Row label={t.inspector.labels}>
          <input
            type="checkbox"
            checked={showLabels}
            onChange={(e) => setShowLabels(e.target.checked)}
            className="h-3.5 w-3.5 accent-[var(--accent)]"
          />
        </Row>

        <div className="flex gap-2 px-3 pt-1">
          <Button
            kind="secondary"
            disabled={loadedDatasetId == null || detecting}
            onClick={() => void detectPeaks()}
          >
            {detecting ? t.inspector.detecting : t.inspector.detectPeaks}
          </Button>
          <Button
            kind="ghost"
            disabled={loadedDatasetId == null || annotating}
            onClick={() => void annotateHkl()}
            // 全谱精修首次 1.5–4.5s;之后命中 _FIT_CACHE 亚秒。说清楚
            // 免得用户以为点了没反应(P1)。
            title={t.inspector.annotateExpect}
          >
            {annotating ? t.inspector.annotating : t.inspector.annotateHkl}
          </Button>
        </div>

        {/* 标注依据:晶胞是每张谱精修出来的,把它摊开给用户核对。
            归属不是"软件说是就是" —— 用户要能自己验证 a/c 是否合理。 */}
        {annotatedCount > 0 && unassignedCount > 0 && (
          <div className="mt-2 rounded-[var(--radius-control)] bg-[color-mix(in_srgb,var(--warning)_16%,transparent)] px-2 py-1 text-[10px] leading-relaxed text-[var(--text-secondary)]">
            {t.inspector.unassignedHint(unassignedCount)}
          </div>
        )}
        {annotation && (
          <div className="mt-2 rounded-[var(--radius-control)] bg-[var(--bg-elevated)] px-3 py-2 text-[11px] leading-relaxed text-[var(--text-secondary)]">
            <div className="mb-1 font-medium text-[var(--text-primary)]">
              {t.inspector.annotationTitle}
            </div>
            <div className="tabular grid grid-cols-2 gap-x-3">
              <span>
                a = {annotation.a.toFixed(4)} Å
              </span>
              <span>
                c = {annotation.c.toFixed(4)} Å
              </span>
              <span>
                {t.inspector.zeroShift} = {annotation.zeroShift.toFixed(3)}°
              </span>
              <span>Rwp = {(annotation.rwp * 100).toFixed(1)}%</span>
            </div>
            {annotationStale && (
              <div className="mt-1.5 rounded-[var(--radius-control)] bg-[color-mix(in_srgb,var(--warning)_16%,transparent)] px-2 py-1 text-[10px] text-[var(--text-secondary)]">
                {t.inspector.annotationStale}
              </div>
            )}
            <div className="mt-1 text-[var(--text-tertiary)]">
              {t.inspector.annotationHint}
            </div>
          </div>
        )}
      </section>

      {/* ---------------- DISPLAY ---------------- */}
      <section className="border-t border-[var(--divider)] pt-1">
        <SectionHeader>{t.inspector.display}</SectionHeader>
        <Row label={t.inspector.normalization}>
          <span className="text-[12px] text-[var(--text-tertiary)]">
            {t.inspector.normalizationValue}
          </span>
        </Row>
        <SwitchRow
          label={t.inspector.showBackground}
          checked={showBackground && hasBackground}
          onChange={(v) => {
            // 背景曲线只在「背景扣除」开启时才由引擎返回。关着的时候把开关
            // 点亮会毫无变化,用户会以为坏了 —— 明确告诉他先去开背景扣除。
            if (v && !hasBackground) {
              showToast("error", t.inspector.needBackgroundFirst);
              return;
            }
            setShowBackground(v);
          }}
        />
        <Row label={`${t.inspector.offset}${offset !== 0 ? ` ${offset.toFixed(0)}` : ""}`}>
          <div className="w-28">
            <RangeSlider
              className="w-full"
              value={[offset]}
              min={-100}
              max={100}
              step={1}
              onValueChange={([v]) => setOffset(v)}
            />
          </div>
        </Row>
      </section>

      <div className="mt-auto px-3 pt-2 text-[11px] text-[var(--text-tertiary)]">
        {status === "previewing"
          ? t.inspector.statusPreviewing
          : status === "committing"
            ? t.inspector.statusCommitting
            : loadedDatasetId
              ? t.inspector.statusSaved
              : t.inspector.statusNoDataset}
      </div>
    </aside>
  );
}
