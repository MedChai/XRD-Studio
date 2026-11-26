import type { AnalysisStateResult, CurvePayload, PeakResult } from "../types/engine";
import { engineCommand } from "./engine";

const isTauri = "__TAURI_INTERNALS__" in window;

function downloadText(filename: string, content: string): void {
  const blob = new Blob(["\uFEFF", content], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

async function choosePath(
  filename: string,
  extension: string,
): Promise<string | null> {
  if (!isTauri) return null;
  const { save } = await import("@tauri-apps/plugin-dialog");
  return save({
    defaultPath: filename,
    filters: [{ name: extension.toUpperCase(), extensions: [extension] }],
  });
}

function csvCell(value: unknown): string {
  const text = value == null ? "" : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function peaksCsv(datasetId: number, peaks: PeakResult[]): string {
  const lines = [
    "# XRD Studio browser export",
    `# dataset_id: ${datasetId}`,
    `# exported_at: ${new Date().toISOString()}`,
    "dataset_id,sample_name,peak_index,position,intensity,fwhm,area,left_bound,right_bound,prominence,manual_override",
  ];
  for (const [index, peak] of peaks.entries()) {
    lines.push([
      datasetId, "", index, peak.position, peak.intensity, peak.fwhm,
      peak.area, peak.left_bound, peak.right_bound, peak.prominence,
      peak.manual_override,
    ].map(csvCell).join(","));
  }
  return lines.join("\n") + "\n";
}

function dataCsv(curve: CurvePayload): string {
  const lines = [
    "# XRD Studio browser export",
    `# exported_at: ${new Date().toISOString()}`,
    "x,raw_y,background,processed_y",
  ];
  for (let i = 0; i < curve.x.length; i += 1) {
    lines.push([
      curve.x[i], curve.y[i], curve.background?.[i] ?? "", curve.y[i],
    ].map(csvCell).join(","));
  }
  return lines.join("\n") + "\n";
}

export async function exportPeaks(
  datasetId: number,
  datasetName: string,
  peaks: PeakResult[],
): Promise<{ ok: boolean; cancelled?: boolean; error?: string }> {
  const filename = `${datasetName.replace(/\.[^.]+$/, "")}_peaks.csv`;
  const path = await choosePath(filename, "csv");
  if (isTauri && path == null) return { ok: false, cancelled: true };
  if (isTauri) {
    const result = await engineCommand("export.peaks", {
      dataset_id: datasetId,
      path,
      overwrite: false,
    });
    return result.ok ? { ok: true } : { ok: false, error: result.error };
  }
  downloadText(filename, peaksCsv(datasetId, peaks));
  return { ok: true };
}

export async function exportData(
  datasetId: number,
  datasetName: string,
  curve: CurvePayload,
): Promise<{ ok: boolean; cancelled?: boolean; error?: string }> {
  const filename = `${datasetName.replace(/\.[^.]+$/, "")}_processed.csv`;
  const path = await choosePath(filename, "csv");
  if (isTauri && path == null) return { ok: false, cancelled: true };
  if (isTauri) {
    const result = await engineCommand("export.data", {
      dataset_id: datasetId,
      path,
      overwrite: false,
    });
    return result.ok ? { ok: true } : { ok: false, error: result.error };
  }
  downloadText(filename, dataCsv(curve));
  return { ok: true };
}

export async function exportFigure(
  datasetId: number,
  datasetName: string,
  dataUrl: string,
): Promise<{ ok: boolean; cancelled?: boolean; error?: string }> {
  const filename = `${datasetName.replace(/\.[^.]+$/, "")}_figure.png`;
  const path = await choosePath(filename, "png");
  if (isTauri && path == null) return { ok: false, cancelled: true };
  if (isTauri) {
    const encoded = dataUrl.replace(/^data:image\/png;base64,/, "");
    const result = await engineCommand("export.figure", {
      dataset_id: datasetId,
      path,
      filename,
      png_base64: encoded,
      overwrite: false,
    });
    return result.ok ? { ok: true } : { ok: false, error: result.error };
  }
  const anchor = document.createElement("a");
  anchor.href = dataUrl;
  anchor.download = filename;
  anchor.click();
  return { ok: true };
}

/** 批量导出(S5 场景收尾:4 个温度点交差要的是整项目全套表,不该点 N 次)。
 *
 * Tauri:只选一次目录,逐条落盘 <名字>_peaks.csv / <名字>_processed.csv;
 * 浏览器 dev:退化为逐条下载。图(PNG)不进批量 —— 图是前端 ECharts 渲的,
 * 批量渲图需要逐个切数据集,会打断用户操作,按需单条导出。 */
export async function exportBatch(
  datasets: { id: number; name: string; sampleName: string }[],
  opts: { peaks: boolean; data: boolean },
): Promise<{
  ok: boolean;
  cancelled?: boolean;
  exported?: number;
  dir?: string;
  error?: string;
}> {
  if (datasets.length === 0) return { ok: false, error: "没有可导出的数据集" };
  let dir: string | null = null;
  if (isTauri) {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const selected = await open({ directory: true, multiple: false, title: "选择导出目录" });
    if (typeof selected !== "string") return { ok: false, cancelled: true };
    dir = selected;
  }
  let exported = 0;
  const errors: string[] = [];
  for (const ds of datasets) {
    const label = (ds.sampleName + "_" + ds.name.replace(/\.[^.]+$/, ""))
      .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").slice(0, 120);
    const stem = "ds" + ds.id + "_" + label;
    for (const kind of ["peaks", "data"] as const) {
      if (!opts[kind]) continue;
      const filename = stem + (kind === "peaks" ? "_peaks.csv" : "_processed.csv");
      if (dir !== null) {
        const r = await engineCommand("export." + kind, {
          dataset_id: ds.id, path: dir + "\\" + filename, overwrite: false,
        });
        if (r.ok) exported++;
        else errors.push(filename + " — " + r.error);
      } else {
        const r = await engineCommand<AnalysisStateResult>("analysis.state", { dataset_id: ds.id });
        if (r.ok) {
          downloadText(filename, kind === "peaks" ? peaksCsv(ds.id, r.data.peaks) : dataCsv(r.data.curve));
          exported++;
        } else errors.push(filename + " — " + r.error);
      }
    }
  }
  if (errors.length) {
    return { ok: false, exported, error: `已导出 ${exported} 个文件，${errors.length} 个失败。\n${errors.join("\n")}` };
  }
  return { ok: true, exported, dir: dir ?? undefined };
}

/** 电化学图 PNG 导出:与 exportFigure 同链路,但不带 dataset_id ——
 *  引擎 export.figure 的旁车溯源是可选的(commands.py `_cmd_export_figure`,
 *  p.get("dataset_id") 为 None 时跳过),电化学 cell 没有谱图 dataset。 */
export async function exportEcFigure(
  figureName: string,
  dataUrl: string,
  metadata?: Record<string, unknown>,
): Promise<{ ok: boolean; cancelled?: boolean; error?: string }> {
  const filename = `${figureName.replace(/\.[^.]+$/, "")}_figure.png`;
  const path = await choosePath(filename, "png");
  if (isTauri && path == null) return { ok: false, cancelled: true };
  if (isTauri) {
    const encoded = dataUrl.replace(/^data:image\/png;base64,/, "");
    const result = await engineCommand("export.figure", {
      path,
      filename,
      png_base64: encoded,
      cex_dqdv: metadata,
      overwrite: false,
    });
    return result.ok ? { ok: true } : { ok: false, error: result.error };
  }
  const anchor = document.createElement("a");
  anchor.href = dataUrl;
  anchor.download = filename;
  anchor.click();
  return { ok: true };
}

export async function exportDqdvTable(
  name: string,
  columns: string[],
  rows: (string | number | null)[][],
): Promise<{ ok: boolean; cancelled?: boolean; error?: string }> {
  const filename = `${name}.csv`;
  if (!isTauri) {
    downloadText(filename, [columns, ...rows].map((row) => row.map(csvCell).join(",")).join("\r\n"));
    return { ok: true };
  }
  const path = await choosePath(filename, "csv");
  if (path == null) return { ok: false, cancelled: true };
  const result = await engineCommand("cex.dqdv_export", { path, columns, rows });
  return result.ok ? { ok: true } : { ok: false, error: result.error };
}

export async function exportCexDqdvCurves(payload: Record<string, unknown>) {
  const path = await choosePath("CEX-dQdV-曲线.csv", "csv");
  if (path == null) return { ok: false, cancelled: true };
  const result = await engineCommand("cex.dqdv_export_curves", { ...payload, path });
  return result.ok ? { ok: true } : { ok: false, error: result.error };
}

/** SVG 导出(F6)。走 blob 下载,不经引擎 —— SVG 是纯文本,浏览器/
 *  WebView 的 <a download> 足够;引擎 export.figure 目前只认 PNG 魔数。 */
export async function exportFigureSvg(
  datasetName: string,
  svg: string,
): Promise<{ ok: boolean; cancelled?: boolean; error?: string }> {
  const filename = `${datasetName.replace(/\.[^.]+$/, "")}_figure.svg`;
  try {
    const blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}
