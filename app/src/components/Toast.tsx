/** Toast(§68):成功 ✓ / 失败人话,永不显示 traceback。 */

import { AlertCircle, CheckCircle2, X } from "lucide-react";
import { useUiStore } from "../stores";

export function Toast() {
  const toast = useUiStore((s) => s.toast);
  const dismiss = useUiStore((s) => s.dismissToast);
  if (!toast) return null;

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-12 z-50 flex justify-center">
      <div
        className={
          "pointer-events-auto tr flex max-w-[480px] items-center gap-2 rounded-full border px-4 py-2 text-[13px] shadow-[var(--shadow-dialog)] backdrop-blur-2xl " +
          (toast.kind === "success"
            ? "border-[var(--divider)] bg-[var(--bg-elevated)] text-[var(--text-primary)]"
            : "border-[color-mix(in_srgb,var(--danger)_30%,transparent)] bg-[color-mix(in_srgb,var(--danger)_10%,var(--bg-elevated))] text-[var(--text-primary)]")
        }
        style={{ animation: "toast-in 180ms cubic-bezier(.2,.8,.2,1)" }}
      >
        {toast.kind === "success" ? (
          <CheckCircle2 size={15} className="shrink-0 text-[var(--success)]" />
        ) : (
          <AlertCircle size={15} className="shrink-0 text-[var(--danger)]" />
        )}
        <span>{toast.text}</span>
        <button
          className="tr ml-1 text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"
          onClick={dismiss}
        >
          <X size={13} />
        </button>
      </div>
      <style>{`@keyframes toast-in{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}`}</style>
    </div>
  );
}
