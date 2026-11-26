/** 行右键菜单:光标锚定的轻量 ContextMenu(文件行"在资源管理器中显示")。
 *
 * 不引 @radix-ui/react-context-menu:项目没装这个包,而这里只是把已有的
 * RowMenuItems 摆到光标处,自己实现(固定定位 + 点外关闭)不到百行,
 * 不值得为它加依赖。
 *
 * 不用 Context.Provider 包外层:<aside> 上有 backdrop-filter,会给
 * position:fixed 建包含块 —— 菜单得用 portal 挂到 body 才能锚在光标。
 * 而侧栏本就要把 open 传给 ProjectGroup/EcCellsRow(既有 props 范式),
 * 状态放 LibrarySidebar + portal 渲染,不动侧栏的 JSX 嵌套层级。
 *
 * 用法:const { state, open, close } = useRowContextMenu();
 *       行上 onContextMenu={open};渲染 <RowContextMenu state={state} …/>。
 */

import { useCallback, useEffect, useState, type MouseEvent as ReactMouseEvent } from "react";
import { createPortal } from "react-dom";
import type { TrashKind } from "../types/engine";
import { RowMenuItems } from "../features/library/TrashDialog";

export type RowContextTarget = { table: TrashKind; id: number; name: string };
type Anchored = RowContextTarget & { x: number; y: number };

export type RowContextOpen = (e: ReactMouseEvent, target: RowContextTarget) => void;

/** 预估菜单尺寸,用于贴边时不溢出视口。 */
const MENU_W = 176;
const MENU_H = 194;

/** 右键菜单的开合状态。放在持有行列表的组件(LibrarySidebar)里。 */
export function useRowContextMenu(): {
  state: Anchored | null;
  open: RowContextOpen;
  close: () => void;
} {
  const [state, setState] = useState<Anchored | null>(null);

  const open = useCallback<RowContextOpen>((e, t) => {
    e.preventDefault(); // 拦掉 WebView 默认右键菜单
    e.stopPropagation();
    const x = Math.max(4, Math.min(e.clientX, window.innerWidth - MENU_W - 4));
    const y = Math.max(4, Math.min(e.clientY, window.innerHeight - MENU_H - 4));
    setState({ ...t, x, y });
  }, []);

  const close = useCallback(() => setState(null), []);

  useEffect(() => {
    if (!state) return;
    const outside = (ev: Event) => {
      // 菜单内的 pointerdown/click 不关 —— 否则先于按钮 onClick 卸载,
      // 点击落空(全局监听在 capture 阶段,比目标元素更早执行)。
      // 菜单在 portal 里、状态在本 hook 里,用 data 属性判定归属而不是 ref。
      const el = ev.target;
      if (el instanceof Element && el.closest("[data-row-menu]")) return;
      setState(null);
    };
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") setState(null);
    };
    window.addEventListener("pointerdown", outside, true);
    window.addEventListener("click", outside, true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("blur", close);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    return () => {
      window.removeEventListener("pointerdown", outside, true);
      window.removeEventListener("click", outside, true);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("blur", close);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [state, close]);

  return { state, open, close };
}

/** 把菜单渲染到 body(portal):绕开祖先 backdrop-filter 的包含块限制。 */
export function RowContextMenu({
  state,
  onClose,
  onAddCex,
}: {
  state: Anchored | null;
  onClose: () => void;
  onAddCex: (sampleId: number) => void;
}) {
  if (!state) return null;
  return createPortal(
    <div
      data-row-menu
      className="fixed z-50 w-44 rounded-[var(--radius-panel)] border border-[var(--divider)] bg-[var(--bg-elevated)] p-1 shadow-[var(--shadow-dialog)] backdrop-blur-2xl"
      style={{ left: state.x, top: state.y }}
    >
      <RowMenuItems
        table={state.table}
        id={state.id}
        name={state.name}
        onClose={onClose}
        onAddCex={state.table === "samples" ? () => onAddCex(state.id) : undefined}
      />
    </div>,
    document.body,
  );
}
