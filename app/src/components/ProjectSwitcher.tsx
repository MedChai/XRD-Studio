/** 项目切换(§16):顶栏下拉;新建项目弹窗由 uiStore 全局控制
 * (App 挂载一次,命令面板/侧栏/顶栏共用)。 */

import { useEffect, useRef, useState } from "react";
import * as Popover from "@radix-ui/react-popover";
import * as Dialog from "@radix-ui/react-dialog";
import { Check, ChevronDown, FolderPlus } from "lucide-react";
import { useProjectStore, useUiStore } from "../stores";
import { Button } from "./controls";
import { cn } from "./cn";

export function ProjectSwitcher() {
  const { projects, currentProjectId, setCurrentProject } = useProjectStore();
  const setNewProjectOpen = useUiStore((s) => s.setNewProjectOpen);
  const [open, setOpen] = useState(false);
  const current = projects.find((p) => p.id === currentProjectId);

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button className="tr flex items-center gap-1 rounded-[var(--radius-control)] px-2 py-1 text-[13px] text-[var(--text-secondary)] hover:bg-[var(--hover)] hover:text-[var(--text-primary)]">
          {current?.name ?? "No Project"}
          <ChevronDown size={13} />
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="start"
          sideOffset={6}
          className="z-50 w-60 rounded-[var(--radius-panel)] border border-[var(--divider)] bg-[var(--bg-elevated)] p-1 shadow-[var(--shadow-dialog)] backdrop-blur-2xl"
        >
          <div className="max-h-64 overflow-y-auto">
            {projects.map((p) => (
              <button
                key={p.id}
                className="tr flex w-full items-center gap-2 rounded-[var(--radius-control)] px-2.5 py-1.5 text-left text-[13px] text-[var(--text-primary)] hover:bg-[var(--hover)]"
                onClick={() => {
                  setCurrentProject(p.id);
                  setOpen(false);
                }}
              >
                <Check
                  size={13}
                  className={cn(
                    "shrink-0 text-[var(--accent)]",
                    p.id === currentProjectId ? "opacity-100" : "opacity-0",
                  )}
                />
                <span className="truncate">{p.name}</span>
              </button>
            ))}
            {projects.length === 0 && (
              <div className="px-2.5 py-2 text-[12px] text-[var(--text-tertiary)]">
                还没有项目
              </div>
            )}
          </div>
          <div className="my-1 border-t border-[var(--divider)]" />
          <button
            className="tr flex w-full items-center gap-2 rounded-[var(--radius-control)] px-2.5 py-1.5 text-left text-[13px] text-[var(--accent)] hover:bg-[var(--hover)]"
            onClick={() => {
              setOpen(false);
              setNewProjectOpen(true);
            }}
          >
            <FolderPlus size={13} />
            New Project…
          </button>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

/** 新建项目弹窗:全局挂载一次(App.tsx)。 */
export function NewProjectDialog() {
  const open = useUiStore((s) => s.newProjectOpen);
  const setOpen = useUiStore((s) => s.setNewProjectOpen);
  const showToast = useUiStore((s) => s.showToast);
  const createProject = useProjectStore((s) => s.createProject);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setName("");
      setDescription("");
      setTimeout(() => inputRef.current?.focus(), 30);
    }
  }, [open]);

  async function submit() {
    if (!name.trim()) return;
    setBusy(true);
    const id = await createProject(name.trim(), description.trim());
    setBusy(false);
    if (id != null) {
      setOpen(false);
      showToast("success", `已创建项目 ${name.trim()}`);
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/25 backdrop-blur-[2px]" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[380px] -translate-x-1/2 -translate-y-1/2 rounded-[var(--radius-dialog)] bg-[var(--bg-panel)] p-5 shadow-[var(--shadow-dialog)]">
          <Dialog.Title className="text-[17px] font-semibold">New Project</Dialog.Title>
          <div className="mt-4 flex flex-col gap-3">
            <label className="flex flex-col gap-1 text-[13px] text-[var(--text-secondary)]">
              Name
              <input
                ref={inputRef}
                value={name}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && void submit()}
                placeholder="LFP Study"
                className="rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-2.5 py-1.5 text-[13px] text-[var(--text-primary)] focus:border-[var(--accent)] focus:outline-none"
              />
            </label>
            <label className="flex flex-col gap-1 text-[13px] text-[var(--text-secondary)]">
              Description
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={2}
                placeholder="可选"
                className="resize-none rounded-[var(--radius-input)] border border-[var(--divider)] bg-[var(--bg-panel)] px-2.5 py-1.5 text-[13px] text-[var(--text-primary)] focus:border-[var(--accent)] focus:outline-none"
              />
            </label>
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <Button kind="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button kind="primary" disabled={!name.trim() || busy} onClick={() => void submit()}>
              {busy ? "创建中…" : "Create"}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
