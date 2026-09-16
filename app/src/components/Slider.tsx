/** 滑块封装(§6)。
 *
 * 存在的理由:Radix 的 `Slider.Root` **不会**注入 `position: relative`,
 * 而它的 Thumb 是 `position: absolute`。少了这个定位上下文,Thumb 的包含块
 * 会退化成视口 —— 手柄跑到窗口水平正中(在绘图区里看就是一个白色小圆点),
 * 轨道上反而空着。
 *
 * 把 `slider-root` 固定在组件内部,「谁忘了写这个 class」这件事就不存在了。
 */

import * as Slider from "@radix-ui/react-slider";
import { cn } from "./cn";

export function RangeSlider({
  value,
  min,
  max,
  step = 1,
  onValueChange,
  className,
  disabled,
}: {
  /** 单值传 [v],双柄范围传 [lo, hi] */
  value: number[];
  min: number;
  max: number;
  step?: number;
  onValueChange: (v: number[]) => void;
  className?: string;
  disabled?: boolean;
}) {
  return (
    <Slider.Root
      className={cn("slider-root", className)}
      value={value}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      onValueChange={onValueChange}
    >
      <Slider.Track className="slider-track">
        <Slider.Range className="slider-range" />
      </Slider.Track>
      {value.map((_, i) => (
        <Slider.Thumb key={i} className="slider-thumb" />
      ))}
    </Slider.Root>
  );
}
