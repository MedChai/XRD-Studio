/** 控件库:全部走设计 token,禁止硬编码色值(§6)。 */

import * as ToggleGroup from "@radix-ui/react-toggle-group";
import type { ReactNode } from "react";
import { cn } from "./cn";

export function Button({
  children,
  onClick,
  kind = "secondary",
  className,
  disabled,
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  kind?: "primary" | "secondary" | "ghost" | "danger";
  className?: string;
  disabled?: boolean;
  title?: string;
}) {
  const base =
    "tr inline-flex h-7 items-center gap-1.5 rounded-[var(--radius-button)] px-3 text-[13px] font-medium disabled:opacity-40";
  const kinds = {
    primary: "bg-[var(--accent)] text-white hover:opacity-90",
    secondary:
      "bg-[var(--bg-elevated)] text-[var(--text-primary)] border border-[var(--divider)] hover:bg-[var(--hover)]",
    ghost: "text-[var(--text-secondary)] hover:bg-[var(--hover)] hover:text-[var(--text-primary)]",
    danger: "bg-[var(--danger)] text-white hover:opacity-90",
  };
  return (
    <button className={cn(base, kinds[kind], className)} onClick={onClick} disabled={disabled} title={title}>
      {children}
    </button>
  );
}

export function IconButton({
  children,
  onClick,
  active,
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  active?: boolean;
  title?: string;
}) {
  return (
    <button
      title={title}
      onClick={onClick}
      className={cn(
        "tr flex h-7 w-7 items-center justify-center rounded-[var(--radius-control)] text-[var(--text-secondary)] hover:bg-[var(--hover)] hover:text-[var(--text-primary)]",
        active && "bg-[var(--selected)] text-[var(--accent)]",
      )}
    >
      {children}
    </button>
  );
}

/** Section 标题:14/600(§6.2) */
export function SectionHeader({
  children,
  right,
}: {
  children: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="flex h-8 items-center justify-between px-3">
      <span className="text-[11px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)]">
        {children}
      </span>
      {right}
    </div>
  );
}

export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex h-8 items-center justify-between px-3">
      <span className="text-[13px] text-[var(--text-secondary)]">{label}</span>
      {children}
    </div>
  );
}

/** 开关行:标题右侧带 ● 指示(§29 UI 草图) */
export function SwitchRow({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      className="tr flex h-9 w-full items-center justify-between px-3 text-[13px] font-medium text-[var(--text-primary)] hover:bg-[var(--hover)]"
      onClick={() => onChange(!checked)}
    >
      {label}
      <span
        className={cn(
          "relative h-4.5 w-8 rounded-full transition-colors duration-150",
          checked ? "bg-[var(--accent)]" : "bg-[var(--divider)]",
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 h-3.5 w-3.5 rounded-full bg-white shadow transition-transform duration-150",
            checked ? "translate-x-4" : "translate-x-0.5",
          )}
        />
      </span>
    </button>
  );
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <ToggleGroup.Root
      type="single"
      value={value}
      onValueChange={(v) => v && onChange(v as T)}
      className="inline-flex rounded-[var(--radius-control)] border border-[var(--divider)] bg-[var(--bg-elevated)] p-0.5"
    >
      {options.map((o) => (
        <ToggleGroup.Item
          key={o.value}
          value={o.value}
          className={cn(
            "tr rounded-[4px] px-2.5 py-0.5 text-[12px] text-[var(--text-secondary)] data-[state=on]:bg-[var(--bg-panel)] data-[state=on]:text-[var(--text-primary)] data-[state=on]:shadow-sm",
          )}
        >
          {o.label}
        </ToggleGroup.Item>
      ))}
    </ToggleGroup.Root>
  );
}

export function NumberField({
  value,
  onChange,
  suffix,
  width = 64,
  min,
  max,
}: {
  value: number;
  onChange: (v: number) => void;
  suffix?: string;
  width?: number;
  min?: number;
  max?: number;
}) {
  return (
    <span className="inline-flex items-center gap-1">
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        onChange={(e) => {
          const v = Number(e.target.value);
          if (Number.isFinite(v)) onChange(v);
        }}
        className="tabular rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-1.5 py-0.5 text-right text-[12px] focus:border-[var(--accent)] focus:outline-none"
        style={{ width }}
      />
      {suffix && <span className="text-[11px] text-[var(--text-tertiary)]">{suffix}</span>}
    </span>
  );
}
