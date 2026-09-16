/** 右侧 Inspector(§27):PROCESSING / PEAKS / DISPLAY / SAMPLE 四段。 */

import { useState } from "react";
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
import { useAnalysisStore, usePlotStore, useUiStore } from "../../stores";
import { engineCommand } from "../../services/engine";
import { cn } from "../../components/cn";
import { t } from "../../i18n";

export function Inspector() {
  const pipeline = useAnalysisStore((s) => s.pipeline);
  const patch = useAnalysisStore((s) => s.patchPipeline);
  const smoothingAffects = useAnalysisStore((s) => s.smoothingAffectsMetrics);
  const status = useAnalysisStore((s) => s.status);
  const loadedDatasetId = useAnalysisStore((s) => s.loadedDatasetId);
  const { offset, setOffset, showLabels, setShowLabels } = usePlotStore();
  const [advanced, setAdvanced] = useState(false);
  // 选择器订阅:不要用 useUiStore() 全量解构 —— uiStore 里的 toast 计时器
  // 每次变化都会重渲染整个 Inspector
  const showToast = useUiStore((s) => s.showToast);

  const bg = pipeline.background;
  const sm = pipeline.smoothing;
  const pk = pipeline.peaks;

  return (
    <aside
      className="flex shrink-0 flex-col gap-3 overflow-y-auto border-l border-[var(--divider)] bg-[var(--bg-panel)] py-2"
      style={{ width: "var(--inspector-w)" }}
    >
      {/* ---------------- PROCESSING ---------------- */}
      <section>
        <SectionHeader>{t.inspector.processing}</SectionHeader>

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
            disabled={loadedDatasetId == null}
            onClick={async () => {
              // Detect Peaks:把当前参数 commit 后正式写 peaks 表
              const analyses = useAnalysisStore.getState();
              await engineCommand("analysis.commit", {
                dataset_id: loadedDatasetId,
                pipeline: analyses.pipeline,
              });
              showToast("success", t.inspector.detectUpdated);
            }}
          >
            {t.inspector.detectPeaks}
          </Button>
        </div>
      </section>

      {/* ---------------- DISPLAY ---------------- */}
      <section className="border-t border-[var(--divider)] pt-1">
        <SectionHeader>{t.inspector.display}</SectionHeader>
        <Row label={t.inspector.normalization}>
          <span className="text-[12px] text-[var(--text-tertiary)]">
            {t.inspector.normalizationValue}
          </span>
        </Row>
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
