import { engineCommand } from "../../../services/engine";

export type Technique = "CV" | "EIS";
export type FitModel = "cpe" | "cpe2" | "warburg";
export type Weighting = "modulus" | "uniform";
type ImpedancePoint = [frequency: number, real: number, imaginary: number];

export interface ChiSummary {
  id: number;
  kind: Technique;
  name: string;
  folder: string;
  sampleId: number | null;
  sampleName: string | null;
  pointCount: number;
}

export interface ChiFit {
  model: FitModel;
  circuit: string;
  weighting: Weighting;
  requestedRange: [number, number];
  range: [number, number];
  count: number;
  relativeRmse: number;
  rmse: number;
  parameters: { name: string; value: number; unit: string }[];
  warnings: string[];
  curve: ImpedancePoint[];
  matched: [index: number, real: number, imaginary: number][];
}

interface ChiMetadata {
  date: string;
  instrument?: string;
  initialPotential?: number;
  highPotential?: number;
  lowPotential?: number;
  scanRate?: number;
  sampleInterval?: number;
  plannedSegments?: number;
  amplitude?: number;
}

interface ChiFileBase extends ChiSummary {
  metadata: ChiMetadata;
  sha256: string;
  warnings: string[];
}

export interface CvFile extends ChiFileBase {
  kind: "CV";
  points: [potential: number, current: number][];
  segments: { start: number; end: number; direction: -1 | 0 | 1 }[];
}

export interface EisFile extends ChiFileBase {
  kind: "EIS";
  points: [frequency: number, real: number, imaginary: number, magnitude: number, phase: number][];
  fit: ChiFit | null;
}

export type ChiFile = CvFile | EisFile;

export interface ChiList {
  files: ChiSummary[];
  models: Record<FitModel, string>;
}

export interface ImportResult {
  imported: { id: number; kind: Technique }[];
  duplicates: { id: number; name: string }[];
  failures: { name: string; reason: string }[];
}

export const isDesktop = "__TAURI_INTERNALS__" in window;

export async function chiCommand<T>(name: string, payload: Record<string, unknown> = {}): Promise<T> {
  const response = await engineCommand<T>(`chi.${name}`, payload);
  if (!response.ok) throw new Error(response.error ?? "操作失败");
  return response.data;
}

export function numberLabel(value: number, digits = 4): string {
  return Number(value.toPrecision(digits)).toString();
}
