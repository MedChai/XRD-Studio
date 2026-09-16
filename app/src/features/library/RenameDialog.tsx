/** 重命名弹窗(全局挂载一次,App.tsx)。
 *
 * 复用 NewProjectDialog 的 Dialog 范式与 class 串,受控于 uiStore.renaming
 * —— 侧栏的三个层级(项目/样品/数据集)共用同一个弹窗,不必各写一份。
 */

import { useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Button } from "../../components/controls";
import { useProjectStore, useUiStore } from "../../stores";
import { t } from "../../i18n";

export function RenameDialog() {
  const target = useUiStore((s) => s.renaming);
  const setTarget = useUiStore((s) => s.setRenaming);
  const renameItem = useProjectStore((s) => s.renameItem);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (target) {
      setName(target.name);
      setTimeout(() => {
        inputRef.current?.focus();
        inputRef.current?.select();
      }, 30);
    }
  }, [target]);

  async function submit() {
    if (!target || !name.trim()) return;
    setBusy(true);
    const ok = await renameItem(target.table, target.id, name.trim());
    setBusy(false);
    if (ok) setTarget(null);
  }

  return (
    <Dialog.Root open={target != null} onOpenChange={(o) => !o && setTarget(null)}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/25 backdrop-blur-[2px]" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[380px] -translate-x-1/2 -translate-y-1/2 rounded-[var(--radius-dialog)] bg-[var(--bg-panel)] p-5 shadow-[var(--shadow-dialog)]">
          <Dialog.Title className="text-[17px] font-semibold">{t.rename.title}</Dialog.Title>
          <div className="mt-4 flex flex-col gap-1 text-[13px] text-[var(--text-secondary)]">
            {t.rename.label}
            <input
              ref={inputRef}
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void submit()}
              className="rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-2.5 py-1.5 text-[13px] text-[var(--text-primary)] focus:border-[var(--accent)] focus:outline-none"
            />
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <Button kind="ghost" onClick={() => setTarget(null)}>
              {t.rename.cancel}
            </Button>
            <Button
              kind="primary"
              disabled={!name.trim() || busy}
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
