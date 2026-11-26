import { engineCommand } from "../../../services/engine";

export type CexLevel = "cycles" | "steps" | "records";
export type CexPlotKind = "time" | "voltageCapacity" | "cycles" | "specificCycles" | "steps";
export type CexFormat = "xlsx" | "csv" | "txt";

export interface CexDocument {
  id: string;
  filename: string;
  path: string;
  name: string;
  cellId: number | null;
  sampleId: number | null;
  counts: Record<CexLevel, number>;
  channel: string;
  barcode: string;
  batch: string;
  note: string;
  massMg: number | null;
  startTime: string;
  endTime: string;
  programs: { name: string; startTime: string }[];
}

export interface CexCycle {
  number: number;
  firstStep: number;
  stepCount: number;
  firstRecord: number;
  recordCount: number;
  qCharge: number | null;
  qDischarge: number | null;
  sqCharge: number | null;
  sqDischarge: number | null;
  efficiency: number | null;
  retention: number | null;
  eCharge: number | null;
  eDischarge: number | null;
  chargeTime: number | null;
  dischargeTime: number | null;
  chargeEndV: number | null;
  dischargeEndV: number | null;
}

export interface CexStep {
  number: number;
  cycle: number | null;
  mode: string;
  firstRecord: number;
  recordCount: number;
  seconds: number | null;
  capacity: number | null;
  specificCapacity: number | null;
  energy: number | null;
  startV: number | null;
  endV: number | null;
  startI: number | null;
  endI: number | null;
}

export interface CexRecord {
  number: number;
  time: number | null;
  stepTime: number | null;
  current: number | null;
  capacity: number | null;
  specificCapacity: number | null;
  voltage: number | null;
  energy: number | null;
}

export type CexRows = { cycles: CexCycle; steps: CexStep; records: CexRecord };

export interface CexPage<T> {
  start: number;
  total: number;
  rows: T[];
}

export interface CexSeries {
  id: string;
  name: string;
  points: [number, number | null, number | null][];
  axis: 0 | 1;
  dashed: boolean;
  group?: string;
}

export interface CexComparison extends CexCycle {
  id: string;
  filename: string;
  channel: string;
  barcode: string;
  startTime: string;
}

export interface CexLocation {
  cycle: number | null;
  step: CexStep | null;
  record: CexRecord;
}

export interface CexLink {
  id: string;
  cellId: number;
  sampleId: number;
  name: string;
  sample: string;
  project: string;
}

export const CEX_PLOTS: { value: CexPlotKind; label: string }[] = [
  { value: "time", label: "时间–电压 / 电流" },
  { value: "voltageCapacity", label: "容量–电压" },
  { value: "cycles", label: "循环–容量 / 效率" },
  { value: "specificCycles", label: "循环–比容量 / 效率" },
  { value: "steps", label: "工步–容量 / 能量" },
];

export const CEX_LEVELS = { cycles: "循环", steps: "工步", records: "记录" } as const;

export type Unit = "mA" | "mAh" | "mWh" | "mAh/g" | "V" | "%" | "s";
export interface CexColumn<T> {
  key: keyof T & string;
  label: string;
  unit?: Unit;
}

export const CEX_COLUMNS: { [K in CexLevel]: CexColumn<CexRows[K]>[] } = {
  cycles: [
    { key: "number", label: "循环序号" },
    { key: "qCharge", label: "充电容量", unit: "mAh" },
    { key: "qDischarge", label: "放电容量", unit: "mAh" },
    { key: "efficiency", label: "库仑效率", unit: "%" },
    { key: "retention", label: "容量保持率", unit: "%" },
    { key: "sqCharge", label: "充电比容量", unit: "mAh/g" },
    { key: "sqDischarge", label: "放电比容量", unit: "mAh/g" },
    { key: "eCharge", label: "充电能量", unit: "mWh" },
    { key: "eDischarge", label: "放电能量", unit: "mWh" },
    { key: "chargeTime", label: "充电时间", unit: "s" },
    { key: "dischargeTime", label: "放电时间", unit: "s" },
    { key: "chargeEndV", label: "充电终压", unit: "V" },
    { key: "dischargeEndV", label: "放电终压", unit: "V" },
  ],
  steps: [
    { key: "number", label: "工步序号" },
    { key: "cycle", label: "循环序号" },
    { key: "mode", label: "工步模式" },
    { key: "seconds", label: "工步时间", unit: "s" },
    { key: "capacity", label: "容量", unit: "mAh" },
    { key: "specificCapacity", label: "比容量", unit: "mAh/g" },
    { key: "energy", label: "能量", unit: "mWh" },
    { key: "startV", label: "起始电压", unit: "V" },
    { key: "endV", label: "结束电压", unit: "V" },
    { key: "startI", label: "起始电流", unit: "mA" },
    { key: "endI", label: "结束电流", unit: "mA" },
  ],
  records: [
    { key: "number", label: "记录序号" },
    { key: "time", label: "测试时间", unit: "s" },
    { key: "stepTime", label: "工步时间", unit: "s" },
    { key: "voltage", label: "电压", unit: "V" },
    { key: "current", label: "电流", unit: "mA" },
    { key: "capacity", label: "容量", unit: "mAh" },
    { key: "specificCapacity", label: "比容量", unit: "mAh/g" },
    { key: "energy", label: "能量", unit: "mWh" },
  ],
};

const BASE_UNITS = { mA: "A", mAh: "Ah", mWh: "Wh" } as const;

export function unitLabel(unit: Unit, base: boolean): string {
  return base && unit in BASE_UNITS ? BASE_UNITS[unit as keyof typeof BASE_UNITS] : unit;
}

export function scaled(value: number | null, unit: Unit | undefined, base: boolean): number | null {
  return value != null && unit && base && unit in BASE_UNITS ? value / 1000 : value;
}

export function elapsed(seconds: number): string {
  const base = Math.floor(Math.abs(seconds));
  const hours = Math.floor(base / 3600);
  const parts = [hours, Math.floor((base % 3600) / 60), base % 60]
    .map((part) => String(part).padStart(2, "0"))
    .join(":");
  const fraction = Math.floor((Math.abs(seconds) - base) * 1000 + 1e-7);
  const millis = fraction ? "." + String(fraction).padStart(3, "0").replace(/0+$/, "") : "";
  return (seconds < 0 ? "−" : "") + parts + millis;
}

export function formatValue(value: unknown, unit?: Unit, base = false): string {
  if (value == null || value === "") return "—";
  if (typeof value !== "number") return String(value);
  if (unit === "s") return elapsed(value);
  if (unit === "%") return value.toFixed(2);
  const result = scaled(value, unit, base) ?? value;
  if (!unit) return String(result);
  if (result !== 0 && Math.abs(result) < 0.001) return result.toExponential(3);
  return String(Number(result.toFixed(4)));
}

export async function cexCommand<T>(name: string, payload: Record<string, unknown>): Promise<T> {
  const result = await engineCommand<T>("cex." + name, payload);
  if (!result.ok) throw new Error(result.error);
  return result.data;
}

export function cexPage<K extends CexLevel>(id: string, level: K, start: number, count: number) {
  return cexCommand<CexPage<CexRows[K]>>("page", { id, level, start, count });
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
