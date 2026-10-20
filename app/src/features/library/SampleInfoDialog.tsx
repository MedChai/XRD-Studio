/** 实验信息编辑弹窗(docs/DEVELOPMENT_HIGH_NI.md §11.4)。
 *
 * 温度/时长/材料/批次是**结构趋势图的自变量**。这些列在库里早就有,
 * 但此前只有导入时能写、之后完全无法修改,而导入弹窗又只让填名字 ——
 * 用户即使想填也无处可填,趋势图因而永远只能按导入顺序排,温度轴永远是
 * "未填温度"。
 *
 * 与 RenameDialog 分开而不是合并:重命名是高频轻操作(改个错别字),
 * 这个弹窗有六个字段、属于"整理实验档案"这类低频操作,混在一起会让
 * 重命名变重。
 */

import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Button } from "../../components/controls";
import { engineCommand } from "../../services/engine";
import { useProjectStore, useUiStore } from "../../stores";
import { t } from "../../i18n";

/** 字段定义:key 与引擎 `samples.update` 的参数名一一对应。
 *  wavelength 单独处理(数字校验),不在此列表。 */
const FIELDS = [
  { key: "temperature", label: "temperature", placeholder: "temperature" },
  { key: "duration", label: "duration", placeholder: "duration" },
  { key: "material", label: "material", placeholder: "material" },
  { key: "batch", label: "batch", placeholder: "batch" },
  { key: "notes", label: "notes", placeholder: "notes" },
  { key: "wavelength", label: "wavelength", placeholder: "wavelength" },
] as const;

export function SampleInfoDialog() {
  const target = useUiStore((s) => s.editingSample);
  const setTarget = useUiStore((s) => s.setEditingSample);
  const showToast = useUiStore((s) => s.showToast);
  const load = useProjectStore((s) => s.load);
  const [form, setForm] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!target) return;
    // 每次打开都从库里的当前值回填 —— 用闭包里的旧值会让用户看到过期数据
    const all = useProjectStore.getState().samples;
    const cur = all.find((x) => x.id === target.id);
    setForm({
      name: cur?.name ?? "",
      temperature: cur?.temperature ?? "",
      duration: cur?.duration ?? "",
      material: cur?.material ?? "",
      batch: cur?.batch ?? "",
      notes: cur?.notes ?? "",
      wavelength: cur?.wavelength != null ? String(cur.wavelength) : "",
    });
  }, [target]);

  async function submit() {
    if (!target || !form.name?.trim()) return;
    // 波长可留空(沿用默认 Cu Kα);填了就必须是 0.3–3.0 的数字 ——
    // 与引擎 samples.update 的校验同口径,提前拦截避免整次保存失败。
    const lamRaw = (form.wavelength ?? "").trim();
    if (lamRaw) {
      const lam = Number(lamRaw);
      if (!Number.isFinite(lam) || lam < 0.3 || lam > 3.0) {
        showToast("error", t.sample.wavelengthInvalid);
        return;
      }
    }
    setBusy(true);
    const payload: Record<string, unknown> = {
      id: target.id,
      name: form.name.trim(),
      temperature: form.temperature ?? "",
      duration: form.duration ?? "",
      material: form.material ?? "",
      batch: form.batch ?? "",
      notes: form.notes ?? "",
    };
    if (lamRaw) payload.wavelength = Number(lamRaw);
    const r = await engineCommand("samples.update", payload);
    setBusy(false);
    if (!r.ok) {
      showToast("error", r.error);
      return;
    }
    showToast("success", t.sample.saved);
    setTarget(null);
    await load();
  }

  const labels: Record<string, string> = {
    temperature: t.sample.temperature,
    duration: t.sample.duration,
    material: t.sample.material,
    batch: t.sample.batch,
    notes: t.sample.notes,
    wavelength: t.sample.wavelength,
  };
  const hints: Record<string, string> = {
    temperature: t.sample.temperaturePlaceholder,
    duration: t.sample.durationPlaceholder,
    material: t.sample.materialPlaceholder,
    batch: t.sample.batchPlaceholder,
    notes: "",
    wavelength: t.sample.wavelengthPlaceholder,
  };

  return (
    <Dialog.Root open={target != null} onOpenChange={(o) => !o && setTarget(null)}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/25 backdrop-blur-[2px]" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[420px] -translate-x-1/2 -translate-y-1/2 rounded-[var(--radius-dialog)] bg-[var(--bg-panel)] p-5 shadow-[var(--shadow-dialog)]">
          <Dialog.Title className="text-[17px] font-semibold">
            {t.sample.title}
          </Dialog.Title>

          <div className="mt-4 flex flex-col gap-2">
            <label className="flex items-center justify-between gap-3 text-[12px] text-[var(--text-secondary)]">
              <span className="shrink-0">{t.sample.name}</span>
              <input
                autoFocus
                value={form.name ?? ""}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                className="w-56 rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-2 py-1 text-[12px] focus:border-[var(--accent)] focus:outline-none"
              />
            </label>
            {FIELDS.map((f) => (
              <label
                key={f.key}
                className="flex items-center justify-between gap-3 text-[12px] text-[var(--text-secondary)]"
              >
                <span className="shrink-0">{labels[f.key]}</span>
                <input
                  value={form[f.key] ?? ""}
                  placeholder={hints[f.key]}
                  onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                  className="w-56 rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-2 py-1 text-[12px] focus:border-[var(--accent)] focus:outline-none"
                />
              </label>
            ))}
          </div>

          {/* 说明这些字段是干什么的 —— 不写的话用户不知道填了有什么用 */}
          <div className="mt-2 text-[11px] text-[var(--text-tertiary)]">
            {t.sample.hint}
          </div>

          <div className="mt-5 flex justify-end gap-2">
            <Button kind="ghost" onClick={() => setTarget(null)}>
              {t.rename.cancel}
            </Button>
            <Button
              kind="primary"
              disabled={!form.name?.trim() || busy}
              onClick={() => void submit()}
            >
              {busy ? t.rename.saving : t.rename.confirm}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
