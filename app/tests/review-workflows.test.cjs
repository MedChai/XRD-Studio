const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

// Exercise the actual store and export handlers with deterministic timers and IPC.
function loadSource(filename, imports, window) {
  const source = readFileSync(path.join(__dirname, "../src", filename), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  const run = new Function("require", "exports", "window", "localStorage", compiled);
  run((name) => imports[name] ?? require(name), exports, window, { getItem: () => null });
  return exports;
}

test("CEX dQ/dV auto-selects a two-direction cycle and cancellation preserves a completed batch", async () => {
  const document = { id: "file", name: "cell", filename: "cell.cex", counts: { cycles: 5 } };
  const settings = { gridMv: 2, smoothingMv: 0 };
  const cycle = (cycleNumber, paired) => ({ cycle: cycleNumber, paired,
    curves: paired ? [{ direction: "charge" }, { direction: "discharge" }] : [{ direction: "charge" }] });
  const requests = [];
  let cancel;
  const { calculateDqdv } = loadSource("features/echem/cex/dqdv.ts", { "./model": {
    cexCommand: async (_command, payload) => {
      requests.push(payload.offset);
      cancel?.();
      return { algorithm: "native", sha256: "sha", total: 5,
        results: payload.offset === 0 ? [cycle(1, false), cycle(2, false)] : [cycle(3, true), cycle(4, true)] };
    },
    errorText: (e) => e.message,
  } }, {});
  const auto = await calculateDqdv([{ doc: document, expression: "" }], settings, new AbortController().signal, () => {});
  assert.deepEqual(auto.sources[0].cycles.map((c) => c.cycle), [3]);
  assert.deepEqual(requests, [0, 2]);
  requests.length = 0;
  const controller = new AbortController();
  cancel = () => controller.abort();
  const batch = await calculateDqdv([{ doc: document, expression: "全部" }], settings, controller.signal, () => {});
  assert.deepEqual(requests, [0]);
  assert.deepEqual(batch.sources[0].cycles.map((c) => c.cycle), [1, 2]);
  assert.equal(batch.cancelled, true);
});

test("CEX peak metrics use absolute discharge capacity and keep clipped windows visible", () => {
  const { measurePeak } = loadSource("features/echem/cex/dqdv.ts", { "./model": {} }, {});
  const trace = { startBin: 3000, values: [-2, -3, -5, -9, -5, -3, -2] };
  const peak = measurePeak(trace, 1, 3, 3.007);
  assert.ok(Math.abs(peak.voltage - 3.0035) < 1e-10);
  assert.equal(peak.height, 7);
  assert.ok(Math.abs(peak.area - 0.029) < 1e-10);
  assert.ok(peak.width > 0);
  const clipped = measurePeak(trace, 1, 2.999, 3.007);
  assert.equal(clipped.voltage, null);
  assert.ok(clipped.coverage < 1);
  assert.ok(clipped.area > 0);
});

test("dataset menu locates the project copy and the original independently", () => {
  const calls = [];
  const store = (pick) => pick({ compareCurves: [] });
  const { RowMenuItems } = loadSource("features/library/TrashDialog.tsx", {
    "../../components/controls": {}, "../../components/cn": { cn: () => "" },
    "../../i18n": { t: { trash: {}, library: { revealStored: "定位项目副本", revealSource: "定位原始文件" } } },
    "../../stores": { useUiStore: store, usePlotStore: store, useProjectStore: store },
    "../../services/engine": { revealInExplorer: async (...args) => {
      calls.push(args);
      return { ok: true, mode: "reveal" };
    } },
  }, {});
  const menu = RowMenuItems({ table: "datasets", id: 19, name: "scan.xy", onClose() {} });
  const children = menu.props.children.flat();
  for (const label of ["定位项目副本", "定位原始文件"]) {
    children.find((child) => child?.props?.children === label).props.onClick();
  }
  assert.deepEqual(calls, [["datasets", 19, "stored"], ["datasets", 19, "source"]]);
});

function makeStore() {
  const timers = new Map();
  let id = 0;
  const window = {
    setTimeout(fn, ms) { timers.set(++id, { fn, ms }); return id; },
    clearTimeout(key) { timers.delete(key); },
  };
  const saved = new Map();
  const curve = { x: new Float32Array([10, 20]), y: new Float32Array([100, 200]) };
  let resumePreview;
  const engineCommand = async (cmd, payload) => {
    switch (cmd) {
      case "datasets.curve": return { ok: true, data: curve };
      case "analysis.state": return { ok: true, data: {
        pipeline: saved.get(payload.dataset_id) ?? {}, curve,
        peaks: [], annotation: null, peaksStored: false,
      }};
      case "analysis.preview":
        await new Promise((resolve) => { resumePreview = resolve; });
        return { ok: true, data: { curve, peaks: [] }};
      case "analysis.commit":
        saved.set(payload.dataset_id, structuredClone(payload.pipeline));
        return { ok: true, data: {} };
      default: throw Error(cmd);
    }
  };
  const mod = loadSource("stores/index.ts", {
    "../services/engine": { engineCommand, DISPLAY_MAX_POINTS: 100 },
    "../i18n": { t: {} },
  }, window);
  return {
    state: () => mod.useAnalysisStore.getState(), saved,
    finishPreview: () => resumePreview(),
    startCommitTimer: () => [...timers.values()].find((timer) => timer.ms === 300).fn(),
  };
}

const cropA = { enabled: true, x_min: 30, x_max: 40 };
const cropB = { enabled: true, x_min: 42, x_max: 55 };

test("switching before debounce saves the original dataset, including undo and redo", async () => {
  const { state, saved } = makeStore();
  await state().loadDataset(1);
  state().patchPipeline({ crop: cropA });
  state().undo();
  await state().loadDataset(2);
  assert.equal(saved.get(1).crop.enabled, false);
  state().patchPipeline({ crop: cropB });
  state().undo();
  state().redo();
  await state().loadDataset(1);
  assert.deepEqual(saved.get(2).crop, cropB);
  assert.equal(state().pipeline.crop.enabled, false);
});

test("a late preview cannot write another sample's settings", async () => {
  const { state, saved, finishPreview, startCommitTimer } = makeStore();
  await state().loadDataset(1);
  state().patchPipeline({ crop: cropA });
  const oldRequest = startCommitTimer();
  await state().loadDataset(2);
  state().patchPipeline({ crop: cropB });
  finishPreview();
  await oldRequest;
  await state().loadDataset(1);
  assert.deepEqual(state().pipeline.crop, cropA);
  assert.deepEqual(saved.get(2).crop, cropB);
});

test("bulk export gives same-name scans unique paths and reports partial failures", async () => {
  const files = new Set();
  let fail = false;
  const exports = loadSource("services/exports.ts", {
    "@tauri-apps/plugin-dialog": { open: async () => "exports" },
    "./engine": { engineCommand: async (cmd, payload) => {
      assert.equal(payload.overwrite, false);
      if (fail && cmd === "export.data") return { ok: false, error: "写入失败" };
      files.add(payload.path);
      return { ok: true, data: {} };
    }},
  }, { __TAURI_INTERNALS__: {} });
  const scans = [1, 2].map((id) => ({ id, sampleName: "sample/A", name: "scan.xy" }));
  const opts = { peaks: true, data: true };
  const result = await exports.exportBatch(scans, opts);
  assert.equal(result.exported, 4);
  assert.equal(files.size, 4);
  assert.ok([...files].every((name) => !name.includes("sample/A")));
  fail = true;
  const partial = await exports.exportBatch(scans, opts);
  assert.equal(partial.ok, false);
  assert.equal(partial.exported, 2);
  assert.match(partial.error, /2 个失败/);
});

test("native d-axis zoom bounds round-trip in each display view", () => {
  const { orderedRange, toDisplayX, fromDisplayX } = loadSource("services/coords.ts", {}, {});
  for (const view of ["two_theta", "d", "q"]) {
    const projected = [1.2, 4.8].map((v) => toDisplayX(v, view, "d", 1.789));
    const range = orderedRange(...projected);
    assert.ok(range[0] < range[1]);
    const native = orderedRange(...range.map((v) => fromDisplayX(v, view, "d", 1.789)));
    assert.ok(Math.abs(native[0] - 1.2) < 1e-9 && Math.abs(native[1] - 4.8) < 1e-9);
  }
});
