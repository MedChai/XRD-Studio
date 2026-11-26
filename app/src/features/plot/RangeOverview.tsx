import { memo, useMemo } from "react";
import { useAnalysisStore, usePlotStore } from "../../stores";
import { RangeSlider, type SliderPoint } from "./RangeSlider";
import { XRD_PLOT_INSETS } from "./XRDChart";

function RangeOverviewInner() {
  const curve = useAnalysisStore((s) => s.curve);
  const zoom = usePlotStore((s) => s.zoom);
  const xDomain = usePlotStore((s) => s.xDomain);
  const setZoom = usePlotStore((s) => s.setZoom);

  // 缩略图显示整条谱，不随主图的纵向偏移移动。
  const points = useMemo<SliderPoint[]>(() => {
    if (!curve) return [];
    const { x, y } = curve;
    const maxY = y.reduce((m, v) => Math.max(m, v), 0) || 1;
    return Array.from(x, (value, index) => [value, (y[index] / maxY) * 100]);
  }, [curve]);
  if (!points.length) return null;
  const [min, max] = xDomain ?? [points[0][0], points[points.length - 1][0]];

  return (
    <div role="group" aria-label="谱图范围轴" className="h-7 shrink-0 pb-1">
      <RangeSlider min={min} max={max} range={zoom ?? { from: min, to: max }} points={points} onChange={setZoom}
        insets={XRD_PLOT_INSETS} compact />
    </div>
  );
}

export const RangeOverview = memo(RangeOverviewInner);
