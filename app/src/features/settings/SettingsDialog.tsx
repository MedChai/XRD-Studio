/** Settings Dialog(§69):General / Appearance / Data / Plot / About。
 * Milestone 1 先做 Appearance 与 About 骨架。 */

import * as Dialog from "@radix-ui/react-dialog";
import { useState } from "react";
import { Segmented } from "../../components/controls";
import { useAppStore, useUiStore } from "../../stores";
import type { ThemeMode } from "../../types/engine";
import { cn } from "../../components/cn";
import { t } from "../../i18n";

/** tab 的标识用英文常量(与主题值等内部值一致),显示名走 t.settings.tab*。
 * 注意:改这里的标识必须同步改下面的比较与初始值。 */
const TABS = [
  { id: "General", label: t.settings.tabGeneral },
  { id: "Appearance", label: t.settings.tabAppearance },
  { id: "Data", label: t.settings.tabData },
  { id: "Plot", label: t.settings.tabPlot },
  { id: "About", label: t.settings.tabAbout },
] as const;

type TabId = (typeof TABS)[number]["id"];

export function SettingsDialog() {
  const open = useUiStore((s) => s.settingsOpen);
  const setOpen = useUiStore((s) => s.setSettingsOpen);
  const { theme, setTheme } = useAppStore();
  const [tab, setTab] = useState<TabId>("Appearance");

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/25 backdrop-blur-[2px]" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 flex h-[420px] w-[560px] -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-[var(--radius-dialog)] bg-[var(--bg-panel)] shadow-[var(--shadow-dialog)]">
          <Dialog.Title className="sr-only">{t.topbar.settings}</Dialog.Title>
          <nav className="flex w-40 shrink-0 flex-col gap-0.5 border-r border-[var(--divider)] bg-[var(--bg-sidebar)] p-2">
            {TABS.map((item) => (
              <button
                key={item.id}
                className={cn(
                  "tr rounded-[var(--radius-control)] px-3 py-1.5 text-left text-[13px]",
                  tab === item.id
                    ? "bg-[var(--selected)] font-medium text-[var(--accent)]"
                    : "text-[var(--text-secondary)] hover:bg-[var(--hover)]",
                )}
                onClick={() => setTab(item.id)}
              >
                {item.label}
              </button>
            ))}
          </nav>
          <div className="flex-1 p-5">
            {tab === "Appearance" && (
              <div className="flex flex-col gap-4">
                <h3 className="text-[17px] font-semibold">{t.settings.appearance}</h3>
                <label className="flex items-center justify-between text-[13px]">
                  {t.settings.theme}
                  <Segmented
                    options={[
                      { value: "system", label: t.settings.themeSystem },
                      { value: "light", label: t.settings.themeLight },
                      { value: "dark", label: t.settings.themeDark },
                    ]}
                    value={theme}
                    onChange={(v) => setTheme(v as ThemeMode)}
                  />
                </label>
                <div className="text-[12px] text-[var(--text-tertiary)]">
                  {t.settings.accent}
                </div>
              </div>
            )}
            {tab === "About" && (
              <div className="flex flex-col gap-2">
                <h3 className="text-[17px] font-semibold">{t.app.name}</h3>
                <div className="text-[13px] text-[var(--text-secondary)]">
                  {t.settings.version}
                </div>
                <div className="mt-4 text-[11px] leading-5 text-[var(--text-tertiary)]">
                  {t.settings.techStack}
                  <br />
                  {t.settings.nonDestructive}
                </div>
              </div>
            )}
            {tab !== "Appearance" && tab !== "About" && (
              <div className="flex h-full items-center justify-center text-[12px] text-[var(--text-tertiary)]">
                {t.settings.pending(
                  TABS.find((x) => x.id === tab)?.label ?? tab,
                )}
              </div>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
