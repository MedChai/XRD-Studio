import type { CurvePayload, PeakResult } from "../types/engine";
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
  datasets: { id: number; name: string }[],
  opts: { peaks: boolean; data: boolean },
): Promise<{
  ok: boolean;
  cancelled?: boolean;
  exported?: number;
  dir?: string;
  error?: string;
}> {
  if (datasets.length === 0) return { ok: false, error: "没有可导出的数据集" };
  if (isTauri) {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const dir = await open({ directory: true, multiple: false, title: "选择导出目录" });
    if (dir == null || Array.isArray(dir)) return { ok: false, cancelled: true };
    let exported = 0;
    let lastError: string | undefined;
    for (const ds of datasets) {
      const stem = ds.name.replace(/\.[^.]+$/, "");
      if (opts.peaks) {
        const r = await engineCommand("export.peaks", {
          dataset_id: ds.id,
          path: `${dir}\\${stem}_peaks.csv`,
          overwrite: true,
        });
        if (r.ok) exported += 1;
        else lastError = r.error;
      }
      if (opts.data) {
        const r = await engineCommand("export.data", {
          dataset_id: ds.id,
          path: `${dir}\\${stem}_processed.csv`,
          overwrite: true,
        });
        if (r.ok) exported += 1;
        else lastError = r.error;
      }
    }
    if (exported === 0 && lastError) return { ok: false, error: lastError };
    return { ok: true, exported, dir: String(dir) };
  }
  // 浏览器:逐条下载(每个数据集一次保存弹窗/一次下载,比没有强)
  let exported = 0;
  for (const ds of datasets) {
    if (opts.peaks) {
      const peaks = await engineCommand<{ peaks: PeakResult[] }>("peaks.list", {
        dataset_id: ds.id,
      });
      const list = peaks.ok ? peaks.data.peaks : [];
      const r = await exportPeaks(ds.id, ds.name, list);
      if (r.ok) exported += 1;
    }
    if (opts.data) {
      const cur = await engineCommand<CurvePayload>("datasets.curve", {
        dataset_id: ds.id,
        max_points: 20000,
      });
      if (cur.ok) {
        const r = await exportData(ds.id, ds.name, cur.data);
        if (r.ok) exported += 1;
      }
    }
  }
  return { ok: true, exported };
}

/** 电化学图 PNG 导出:与 exportFigure 同链路,但不带 dataset_id ——
 *  引擎 export.figure 的旁车溯源是可选的(commands.py `_cmd_export_figure`,
 *  p.get("dataset_id") 为 None 时跳过),电化学 cell 没有谱图 dataset。 */
export async function exportEcFigure(
  figureName: string,
  dataUrl: string,
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
