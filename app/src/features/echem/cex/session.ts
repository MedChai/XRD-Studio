import { create } from "zustand";
import { useCexLibraryStore, useProjectStore, useSelectionStore, useUiStore } from "../../../stores";
import { cexCommand, errorText, type CexDocument, type CexLink } from "./model";

type ArchiveRequest = { ids: string[]; sampleId: number | null };

interface CexSession {
  documents: CexDocument[];
  active: string | null;
  comparing: boolean;
  pending: number;
  archive: ArchiveRequest | null;
  requestArchive: (archive: ArchiveRequest | null) => void;
  select: (id: string) => void;
  compare: () => void;
  close: (id: string) => void;
}

export const useCexSession = create<CexSession>((set, get) => ({
  documents: [],
  active: null,
  comparing: false,
  pending: 0,
  archive: null,
  requestArchive: (archive) => set({ archive }),
  select: (id) => {
    const doc = get().documents.find((item) => item.id === id);
    if (doc) {
      set({ active: id, comparing: false });
      useSelectionStore.getState().selectCex(doc.sampleId, doc.cellId);
    }
  },
  compare: () => {
    set({ comparing: true });
    useSelectionStore.getState().selectCex();
  },
  close: (id) => {
    const state = get();
    const documents = state.documents.filter((doc) => doc.id !== id);
    set({ documents });
    if (state.active === id) {
      const next = documents[documents.length - 1];
      if (next && !state.comparing) {
        get().select(next.id);
      } else {
        set({ active: next?.id ?? null, comparing: state.comparing && documents.length > 0 });
        useSelectionStore.getState().selectCex();
      }
    }
    void cexCommand("close", { id }).catch(() => {});
  },
}));

function toast(message: string) {
  useUiStore.getState().showToast("error", message);
}

function remember(doc: CexDocument) {
  useCexSession.setState((state) => ({
    documents: [...state.documents.filter((old) => old.id !== doc.id), doc],
  }));
}

let latestOpen = 0;

export async function openCexFiles(paths: string[], archiveSampleId?: number | null) {
  if (!paths.length) return;
  const request = ++latestOpen;
  useSelectionStore.getState().selectCex();
  useCexSession.setState((state) => ({ pending: state.pending + 1, comparing: false }));
  const opened: string[] = [];
  const failures: string[] = [];
  try {
    for (const path of new Set(paths)) {
      try {
        const doc = await cexCommand<CexDocument>("open", { path });
        remember(doc);
        opened.push(doc.id);
        if (opened.length === 1 && request === latestOpen && useSelectionStore.getState().cexActive) {
          useCexSession.getState().select(doc.id);
        }
      } catch (error) {
        failures.push((path.split(/[\\/]/).pop() ?? path) + " · " + errorText(error));
      }
    }
    if (archiveSampleId !== undefined && opened.length && request === latestOpen && useSelectionStore.getState().cexActive) {
      useCexSession.getState().requestArchive({ ids: opened, sampleId: archiveSampleId });
    }
    if (failures.length) toast(failures.join("\n"));
  } finally {
    useCexSession.setState((state) => ({ pending: state.pending - 1 }));
  }
}

export async function chooseCexFiles(sampleId?: number) {
  if (!("__TAURI_INTERNALS__" in window)) {
    toast("请在 Windows 桌面版打开蓝电 CEX 文件");
    return;
  }
  try {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const paths = await open({
      multiple: true,
      directory: false,
      title: sampleId != null ? "选择要添加到样品的蓝电文件" : "打开蓝电原始数据",
      filters: [{ name: "蓝电原始数据", extensions: ["cex"] }],
    });
    if (paths) await openCexFiles(typeof paths === "string" ? [paths] : paths, sampleId);
  } catch (error) {
    toast(errorText(error));
  }
}

export async function openArchivedCex(sampleId: number, cellId: number) {
  const request = ++latestOpen;
  const sample = useProjectStore.getState().samples.find((row) => row.id === sampleId);
  if (sample) useProjectStore.getState().setCurrentProject(sample.project_id);
  useSelectionStore.getState().selectCex(sampleId, cellId);
  const known = useCexSession.getState().documents.find((doc) => doc.cellId === cellId);
  if (known) {
    useCexSession.getState().select(known.id);
    return;
  }
  useCexSession.setState((state) => ({ pending: state.pending + 1, comparing: false }));
  try {
    const doc = await cexCommand<CexDocument>("open", { cell_id: cellId });
    remember(doc);
    const selection = useSelectionStore.getState();
    if (request === latestOpen && selection.cexActive && selection.cellId === cellId) {
      useCexSession.getState().select(doc.id);
    }
  } catch (error) {
    toast(errorText(error));
  } finally {
    useCexSession.setState((state) => ({ pending: state.pending - 1 }));
  }
}

export async function archiveCex(ids: string[], sampleId: number, names: Record<string, string>) {
  const result = await cexCommand<{
    imported: CexLink[];
    duplicates: CexLink[];
    failures: { id: string; reason: string }[];
  }>("archive", { ids, sample_id: sampleId, names });
  if (result.imported.length) {
    const links = new Map(result.imported.map((link) => [link.id, link]));
    useCexSession.setState((state) => ({
      documents: state.documents.map((doc) => {
        const link = links.get(doc.id);
        return link ? { ...doc, cellId: link.cellId, sampleId: link.sampleId, name: link.name } : doc;
      }),
    }));
    const project = useProjectStore.getState().samples.find((sample) => sample.id === sampleId);
    if (project) useProjectStore.getState().setCurrentProject(project.project_id);
    await Promise.all([
      useCexLibraryStore.getState().loadCells(sampleId, true),
      useProjectStore.getState().load(),
    ]);
    useCexSession.getState().select(result.imported[0].id);
  }
  return result;
}
