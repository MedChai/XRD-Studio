import { useEffect, useRef, useState } from "react";
import { cexCommand, errorText, type CexPlotKind, type CexSeries } from "./model";

interface PlotRequest {
  key: string;
  id: string;
  kind: CexPlotKind;
  first: number;
  last: number;
  cycle?: number;
  recordStart?: number;
  recordEnd?: number;
  preview: boolean;
}

interface PlotResult {
  key: string;
  kind: CexPlotKind;
  series: CexSeries[];
  error?: string;
}

export function useCexPlot(request: PlotRequest) {
  const [plot, setPlot] = useState<PlotResult>();
  const wanted = useRef<PlotRequest | null>(null);
  const running = useRef(false);

  useEffect(() => () => { wanted.current = null; }, []);

  useEffect(() => {
    wanted.current = request;
    if (running.current) return;
    running.current = true;

    async function draw() {
      try {
        let current = wanted.current;
        while (current) {
          const { key, ...payload } = current;
          const { kind, preview } = payload;
          try {
            const { series } = await cexCommand<{ series: CexSeries[] }>("plot", payload);
            const next = wanted.current;
            if (next?.key === key || (preview && next?.preview && next.kind === kind)) {
              setPlot({ key, kind, series });
            }
          } catch (error) {
            if (!preview && wanted.current?.key === key) setPlot({ key, kind, series: [], error: errorText(error) });
          }
          // The native engine is serial. Skip drag positions that arrived while it was busy.
          current = wanted.current?.key === key ? null : wanted.current;
        }
      } finally {
        running.current = false;
      }
    }

    void draw();
  }, [request]);

  return plot;
}
