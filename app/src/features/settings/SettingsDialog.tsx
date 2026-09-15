/** Settings Dialog(§69):General / Appearance / Data / Plot / About。
 * Milestone 1 先做 Appearance 与 About 骨架。 */

import * as Dialog from "@radix-ui/react-dialog";
import { useState } from "react";
import { Segmented } from "../../components/controls";
import { useAppStore, useUiStore } from "../../stores";
import type { ThemeMode } from "../../types/engine";
import { cn } from "../../components/cn";

const TABS = ["General", "Appearance", "Data", "Plot", "About"] as const;

export function SettingsDialog() {
  const open = useUiStore((s) => s.settingsOpen);
  const setOpen = useUiStore((s) => s.setSettingsOpen);
  const { theme, setTheme } = useAppStore();
  const [tab, setTab] = useState<(typeof TABS)[number]>("Appearance");

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/25 backdrop-blur-[2px]" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 flex h-[420px] w-[560px] -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-[var(--radius-dialog)] bg-[var(--bg-panel)] shadow-[var(--shadow-dialog)]">
          <Dialog.Title className="sr-only">设置</Dialog.Title>
          <nav className="flex w-40 shrink-0 flex-col gap-0.5 border-r border-[var(--divider)] bg-[var(--bg-sidebar)] p-2">
            {TABS.map((t) => (
              <button
                key={t}
                className={cn(
                  "tr rounded-[var(--radius-control)] px-3 py-1.5 text-left text-[13px]",
                  tab === t
                    ? "bg-[var(--selected)] font-medium text-[var(--accent)]"
                    : "text-[var(--text-secondary)] hover:bg-[var(--hover)]",
                )}
                onClick={() => setTab(t)}
              >
                {t}
              </button>
            ))}
          </nav>
          <div className="flex-1 p-5">
            {tab === "Appearance" && (
              <div className="flex flex-col gap-4">
                <h3 className="text-[17px] font-semibold">Appearance</h3>
                <label className="flex items-center justify-between text-[13px]">
                  Theme
                  <Segmented
                    options={[
                      { value: "system", label: "System" },
                      { value: "light", label: "Light" },
                      { value: "dark", label: "Dark" },
                    ]}
                    value={theme}
                    onChange={(v) => setTheme(v as ThemeMode)}
                  />
                </label>
                <div className="text-[12px] text-[var(--text-tertiary)]">
                  Accent · Blue(Graphite 暂未开放)
                </div>
              </div>
            )}
            {tab === "About" && (
              <div className="flex flex-col gap-2">
                <h3 className="text-[17px] font-semibold">XRD Studio</h3>
                <div className="text-[13px] text-[var(--text-secondary)]">V0.3 · 开发中</div>
                <div className="mt-4 text-[11px] leading-5 text-[var(--text-tertiary)]">
                  Tauri 2 · React · ECharts · Python Engine
                  <br />
                  数据非破坏性管理,原始谱永不被修改。
                </div>
              </div>
            )}
            {(tab === "General" || tab === "Data" || tab === "Plot") && (
              <div className="flex h-full items-center justify-center text-[12px] text-[var(--text-tertiary)]">
                {tab} 设置将在 Milestone 5–6 落地
              </div>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
