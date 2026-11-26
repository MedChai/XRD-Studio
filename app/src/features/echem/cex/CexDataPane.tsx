import { Fragment, memo, useEffect, useRef, useState, type ReactNode } from "react";
import * as Popover from "@radix-ui/react-popover";
import { ChevronRight, Columns3, Copy } from "lucide-react";
import { Button, Segmented } from "../../../components/controls";
import { useUiStore } from "../../../stores";
import {
  CEX_COLUMNS, CEX_LEVELS, cexPage, errorText, formatValue, unitLabel,
  type CexColumn, type CexCycle, type CexDocument, type CexLevel,
  type CexLocation, type CexRows, type CexStep, type Unit,
} from "./model";

const PAGE_SIZE = 60;
const HEADER = "whitespace-nowrap px-2.5 py-2 text-left font-medium text-[var(--text-secondary)]";
const CELL = "whitespace-nowrap px-2.5 py-1.5 tabular-nums";
const COLUMN_PREFERENCES = "xrd-cex-hidden-columns";
type HiddenColumns = Record<CexLevel, string[]>;
type TableColumns = typeof CEX_COLUMNS;

function readHiddenColumns(): HiddenColumns {
  try {
    const saved = JSON.parse(localStorage.getItem(COLUMN_PREFERENCES) ?? "{}") as Partial<HiddenColumns> | null;
    return {
      cycles: Array.isArray(saved?.cycles) ? saved.cycles : [],
      steps: Array.isArray(saved?.steps) ? saved.steps : [],
      records: Array.isArray(saved?.records) ? saved.records : [],
    };
  } catch {
    return { cycles: [], steps: [], records: [] };
  }
}

function usePage<K extends CexLevel>(id: string, level: K, start: number, count: number) {
  const key = [id, level, start, count].join(":");
  const [state, setState] = useState<{ key: string; rows: CexRows[K][]; error?: string }>();
  useEffect(() => {
    if (!count) return;
    let valid = true;
    void cexPage(id, level, start, count).then(
      ({ rows }) => { if (valid) setState({ key, rows }); },
      (error: unknown) => { if (valid) setState({ key, rows: [], error: errorText(error) }); },
    );
    return () => { valid = false; };
  }, [id, level, start, count, key]);
  return state?.key === key ? state : { rows: [], loading: count > 0 };
}

function heading(column: { label: string; unit?: Unit }, base: boolean) {
  return column.unit && column.unit !== "s" ? column.label + " (" + unitLabel(column.unit, base) + ")" : column.label;
}

function ColumnPicker({ doc, view, base, hidden, update }: {
  doc: CexDocument;
  view: CexLevel;
  base: boolean;
  hidden: HiddenColumns;
  update: (level: CexLevel, keys: string[]) => void;
}) {
  const [level, setLevel] = useState(view);
  const available = (Object.keys(CEX_LEVELS) as CexLevel[]).filter((key) => key === view || doc.counts[key] > 0);
  return (
    <Popover.Root onOpenChange={(open) => { if (open) setLevel(view); }}>
      <Popover.Trigger asChild>
        <button type="button" title="选择数据表显示的列"
          className="tr inline-flex h-7 items-center gap-1.5 rounded-[var(--radius-button)] px-2 text-[12px] text-[var(--text-secondary)] hover:bg-[var(--hover)] hover:text-[var(--text-primary)]">
          <Columns3 size={14} /> 列设置
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content aria-label="选择显示列" align="end" sideOffset={5} collisionPadding={8}
          className="z-50 w-54 rounded-[var(--radius-panel)] border border-[var(--divider)] bg-[var(--bg-panel)] p-2 shadow-[var(--shadow-dialog)]">
          {available.length > 1 && (
            <Segmented<CexLevel> value={level} onChange={setLevel}
              options={available.map((key) => ({ value: key, label: CEX_LEVELS[key] }))} />
          )}
          <div role="group" aria-label={CEX_LEVELS[level] + "列"}
            className="my-1.5 max-h-[min(48vh,17rem)] overflow-y-auto">
            {CEX_COLUMNS[level].map((column) => {
              const fixed = column.key === "number";
              return (
                <label key={column.key} className="flex items-center gap-2 rounded px-2 py-1 text-[12px] hover:bg-[var(--hover)]">
                  <input type="checkbox" checked={fixed || !hidden[level].includes(column.key)} disabled={fixed}
                    className="accent-[var(--accent)]"
                    onChange={(event) => update(level, event.target.checked
                      ? hidden[level].filter((key) => key !== column.key) : [...hidden[level], column.key])} />
                  {heading(column, base)}
                </label>
              );
            })}
          </div>
          <Button kind="ghost" disabled={!hidden[level].length} onClick={() => update(level, [])}>显示全部</Button>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

async function copyRows<T>(
  rows: T[],
  columns: CexColumn<T>[],
  base: boolean,
) {
  const notify = useUiStore.getState().showToast;
  try {
    const text = [columns.map((column) => heading(column, base)), ...rows.map((row) =>
      columns.map((column) => formatValue(row[column.key], column.unit, base))),
    ].map((row) => row.join("\t")).join("\n");
    await navigator.clipboard.writeText(text);
    notify("success", "已复制 " + rows.length + " 行");
  } catch (error) {
    notify("error", "无法复制：" + errorText(error));
  }
}

export function CexTable<T extends { number: number }>({
  columns, rows, base, rowNumber, rowKey, highlighted, extra, onRowDoubleClick,
}: {
  columns: CexColumn<T>[];
  rows: T[];
  base: boolean;
  rowNumber?: (row: T) => ReactNode;
  rowKey?: (row: T) => string;
  highlighted?: number | null;
  extra?: (row: T) => ReactNode;
  onRowDoubleClick?: (row: T) => void;
}) {
  return (
    <table className="w-max border-separate border-spacing-0 text-[12px]">
      <thead className="bg-[var(--bg-elevated)]">
        <tr>
          {columns.map((column) => (
            <th key={column.key} scope="col" className={HEADER}>{heading(column, base)}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const expanded = extra?.(row);
          return (
            <Fragment key={rowKey?.(row) ?? row.number}>
              <tr
                className={highlighted === row.number ? "bg-[var(--selected)]" : "hover:bg-[var(--hover)]"}
                onDoubleClick={onRowDoubleClick ? (event) => {
                  if (!(event.target instanceof Element) || !event.target.closest("button")) onRowDoubleClick(row);
                } : undefined}
                data-current-record={highlighted === row.number ? "" : undefined}
              >
                {columns.map((column) => (
                  <td key={column.key} className={CELL + " border-b border-[var(--divider)]"}>
                    {column.key === "number" && rowNumber
                      ? rowNumber(row)
                      : formatValue(row[column.key], column.unit, base)}
                  </td>
                ))}
              </tr>
              {expanded && (
                <tr>
                  <td colSpan={columns.length} className="border-b border-[var(--divider)] p-0">
                    {expanded}
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

function PageNav({
  start, count, offset, setOffset, onCopy,
}: {
  start: number;
  count: number;
  offset: number;
  setOffset: (offset: number) => void;
  onCopy?: () => void;
}) {
  return (
    <div className="flex w-full items-center gap-1 px-2 py-1 text-[11px] text-[var(--text-secondary)]"
      style={{ maxWidth: "calc(100cqw - 18px)" }}>
      <span className="mr-auto tabular-nums">
        {count ? (start + offset + 1) + "–" + (start + Math.min(count, offset + PAGE_SIZE)) : "0"}
        {" / "}{count.toLocaleString()} 条
      </span>
      {onCopy && (
        <Button kind="ghost" onClick={onCopy} title="复制本页">
          <Copy size={12} /> 复制
        </Button>
      )}
      {count > PAGE_SIZE && (
        <>
          <Button kind="ghost" disabled={!offset} onClick={() => setOffset(offset - PAGE_SIZE)}>上一页</Button>
          <Button kind="ghost" disabled={offset + PAGE_SIZE >= count} onClick={() => setOffset(offset + PAGE_SIZE)}>下一页</Button>
        </>
      )}
    </div>
  );
}

function TableMessage({ loading, error }: { loading?: boolean; error?: string }) {
  return (
    <div role={error ? "alert" : "status"} className="px-4 py-6 text-[12px] text-[var(--text-secondary)]">
      {error || (loading ? "正在读取数据…" : "该范围内没有数据")}
    </div>
  );
}

function TreeToggle({ expanded, label, onClick }: { expanded: boolean; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-expanded={expanded}
      className="flex items-center gap-1 font-medium text-[var(--text-primary)] hover:text-[var(--accent)]"
      onClick={onClick}
    >
      <ChevronRight size={13} className={expanded ? "rotate-90" : ""} />{label}
    </button>
  );
}

function pageOf(number: number, start: number) {
  return Math.max(0, Math.floor((number - 1 - start) / PAGE_SIZE) * PAGE_SIZE);
}

function RecordTable({
  id, start, count, base, focus, locate, columns,
}: {
  id: string; start: number; count: number; base: boolean;
  focus: CexLocation | null; locate: (number: number) => void;
  columns: TableColumns["records"];
}) {
  const number = focus?.record.number;
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    if (number != null && number > start && number <= start + count) setOffset(pageOf(number, start));
  }, [number, start, count]);
  const page = usePage(id, "records", start + offset, Math.min(PAGE_SIZE, Math.max(0, count - offset)));
  return (
    <div className="bg-[var(--bg-panel)]" aria-label="原始记录表">
      <PageNav {...{ start, count, offset, setOffset }} onCopy={page.rows.length
        ? () => void copyRows(page.rows, columns, base) : undefined} />
      {page.rows.length ? (
        <CexTable
          columns={columns} rows={page.rows} base={base}
          highlighted={number} onRowDoubleClick={(row) => locate(row.number)}
          rowNumber={(row) => (
            <button
              className="text-[var(--accent)] hover:underline" title="定位到曲线"
              aria-label={"定位记录 " + row.number} onClick={() => locate(row.number)}
            >
              {row.number}
            </button>
          )}
        />
      ) : <TableMessage {...page} />}
    </div>
  );
}

function StepTable({
  id, start, count, base, focus, locate, columns,
}: {
  id: string; start: number; count: number; base: boolean;
  focus: CexLocation | null; locate: (number: number) => void;
  columns: TableColumns;
}) {
  const [offset, setOffset] = useState(0);
  const [expanded, setExpanded] = useState<number | null>(null);
  const focusStep = focus?.step?.number;
  useEffect(() => {
    if (focusStep != null && focusStep > start && focusStep <= start + count) {
      setOffset(pageOf(focusStep, start));
      setExpanded(focusStep);
    }
  }, [focusStep, focus?.record.number, start, count]);
  const page = usePage(id, "steps", start + offset, Math.min(PAGE_SIZE, Math.max(0, count - offset)));
  function toggle(step: CexStep) {
    setExpanded((old) => old === step.number ? null : step.number);
  }
  return (
    <div className="border-l-2 border-[var(--accent)] bg-[var(--bg-panel)] pl-2" aria-label="工步表">
      <PageNav {...{ start, count, offset, setOffset }} onCopy={page.rows.length
        ? () => void copyRows(page.rows, columns.steps, base) : undefined} />
      {page.rows.length ? (
        <CexTable
          columns={columns.steps} rows={page.rows} base={base}
          rowNumber={(row) => (
            <TreeToggle expanded={expanded === row.number} label={"工步 " + row.number} onClick={() => toggle(row)} />
          )}
          extra={(row) => expanded === row.number && (
            <div className="border-l border-[var(--divider)] pl-2">
              <RecordTable
                key={row.number} id={id} start={row.firstRecord} count={row.recordCount}
                base={base} focus={focus} locate={locate} columns={columns.records}
              />
            </div>
          )}
        />
      ) : <TableMessage {...page} />}
    </div>
  );
}

function CycleTable({
  id, start, count, base, focus, locate, selectCycle, columns,
}: {
  id: string; start: number; count: number; base: boolean;
  focus: CexLocation | null; locate: (number: number) => void; selectCycle: (number: number) => void;
  columns: TableColumns;
}) {
  const [offset, setOffset] = useState(0);
  const [expanded, setExpanded] = useState<number | null>(null);
  const cycle = focus?.cycle;
  useEffect(() => {
    if (cycle != null && cycle > start && cycle <= start + count) {
      setOffset(pageOf(cycle, start));
      setExpanded(cycle);
    }
  }, [cycle, focus?.record.number, start, count]);
  const page = usePage(id, "cycles", start + offset, Math.min(PAGE_SIZE, Math.max(0, count - offset)));
  function toggle(row: CexCycle) {
    setExpanded((old) => old === row.number ? null : row.number);
    selectCycle(row.number);
  }
  return (
    <>
      <PageNav {...{ start, count, offset, setOffset }} onCopy={page.rows.length
        ? () => void copyRows(page.rows, columns.cycles, base) : undefined} />
      {page.rows.length ? (
        <CexTable
          columns={columns.cycles} rows={page.rows} base={base}
          rowNumber={(row) => (
            <TreeToggle expanded={expanded === row.number} label={"循环 " + row.number} onClick={() => toggle(row)} />
          )}
          extra={(row) => expanded === row.number && (
            <StepTable
              key={row.number} id={id} start={row.firstStep} count={row.stepCount}
              base={base} focus={focus} locate={locate} columns={columns}
            />
          )}
        />
      ) : <TableMessage {...page} />}
    </>
  );
}

export const CexDataPane = memo(function CexDataPane({
  doc, first, last, base, focus, locate, selectCycle,
}: {
  doc: CexDocument; first: number; last: number; base: boolean;
  focus: CexLocation | null; locate: (number: number) => void; selectCycle: (number: number) => void;
}) {
  const hierarchy = doc.counts.cycles ? "cycles" : "steps";
  const [view, setView] = useState<CexLevel>(doc.counts.cycles || doc.counts.steps ? hierarchy : "records");
  const [recordNumber, setRecordNumber] = useState("");
  const [hidden, setHidden] = useState(readHiddenColumns);
  const columns: TableColumns = {
    cycles: CEX_COLUMNS.cycles.filter(({ key }) => key === "number" || !hidden.cycles.includes(key)),
    steps: CEX_COLUMNS.steps.filter(({ key }) => key === "number" || !hidden.steps.includes(key)),
    records: CEX_COLUMNS.records.filter(({ key }) => key === "number" || !hidden.records.includes(key)),
  };
  function updateColumns(level: CexLevel, keys: string[]) {
    const next = { ...hidden, [level]: keys };
    setHidden(next);
    try {
      localStorage.setItem(COLUMN_PREFERENCES, JSON.stringify(next));
    } catch {
      // Column selection still works when browser storage is unavailable.
    }
  }
  useEffect(() => {
    if (!focus) return;
    if (focus.cycle != null && hierarchy === "cycles") {
      setView((current) => current === "records" ? current : "cycles");
    } else if (!focus.step || hierarchy === "cycles") {
      setView("records");
    }
  }, [focus, hierarchy]);
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!focus) return;
    const node = body.current;
    if (!node) return;
    const reveal = () => {
      const row = node.querySelector("[data-current-record]");
      if (!row) return;
      const target = row.getBoundingClientRect();
      const viewport = node.getBoundingClientRect();
      if (target.top < viewport.top) node.scrollTop += target.top - viewport.top;
      else if (target.bottom > viewport.bottom) node.scrollTop += target.bottom - viewport.bottom;
    };
    reveal();
    const observer = new MutationObserver(reveal);
    observer.observe(node, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [focus, view]);
  function jump() {
    const number = Number(recordNumber);
    if (Number.isSafeInteger(number) && number >= 1 && number <= doc.counts.records) locate(number);
  }
  return (
    <section aria-label="蓝电数据表" className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-[var(--divider)] px-3 py-2">
        <Segmented<CexLevel> value={view} onChange={setView} options={[
          { value: hierarchy, label: hierarchy === "cycles" ? "循环 / 工步" : "工步" },
          { value: "records", label: "全部记录" },
        ]} />
        <ColumnPicker {...{ doc, view, base, hidden }} update={updateColumns} />
        <div className="ml-auto flex items-center gap-1">
          <input
            aria-label="跳转到记录序号" type="number" min={1} max={doc.counts.records}
            value={recordNumber} onChange={(e) => setRecordNumber(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") jump(); }}
            placeholder="记录序号" className="cex-input w-25"
          />
          <Button kind="ghost" disabled={!recordNumber} onClick={jump}>定位</Button>
        </div>
      </div>
      <div ref={body} className="min-h-0 flex-1 overflow-auto" style={{ containerType: "inline-size" }}>
        {view === "cycles" && (
          <CycleTable
            key={first + ":" + last} id={doc.id} start={first - 1} count={last - first + 1}
            base={base} {...{ focus, locate, selectCycle, columns }}
          />
        )}
        {view === "steps" && <StepTable id={doc.id} start={0} count={doc.counts.steps} {...{ base, focus, locate, columns }} />}
        {view === "records" && (
          <RecordTable id={doc.id} start={0} count={doc.counts.records} {...{ base, focus, locate }} columns={columns.records} />
        )}
      </div>
    </section>
  );
});
