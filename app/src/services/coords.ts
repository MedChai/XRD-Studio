/** 显示层坐标换算(§4.1)。
 *
 * 引擎侧的公式唯一实现点是 `engine/xrd_engine/coords.py`;本文件是它的
 * **前端镜像**,只服务视图层 2θ/d/Q 切换 —— 这是纯显示参数(§7:不走
 * 引擎),每次切换都往返引擎既慢,又会把显示态变成有状态的请求。两边
 * 公式必须一致,改一边要同步另一边。
 *
 * **峰表与 plotStore.zoom 永远按采集轴存储**(README §4.1),换算只作用
 * 于图上 x 映射、刻度与读数,避免结果歧义。
 *
 * d 视图的显示约定:d 随 2θ 增大而减小,文献惯用「大 d 在左」,峰序与 2θ
 * 视图一致(切换视图时峰不左右镜像)。ECharts 的 inverse 轴与 dataZoom
 * 组合有历史坑,所以内部映射取**负 d**(显示轴仍单调递增),刻度与读数
 * 显示时取绝对值 —— 用户看到的轴与文献图一致,而数据流是确定的。
 */

import type { AxisType } from "../types/engine";

/** 视图层可切换的三种坐标。generic 无换算定义,不进切换器。 */
export type XView = "two_theta" | "d" | "q";

/** toDisplayX/fromDisplayX 接受的目标视图(含无法换算的 generic)。 */
export type XDisplay = XView | "generic";

const DEG = Math.PI / 180;

/** 2θ(度)→ d(Å)。d = λ / (2 sinθ),θ = 2θ/2。2θ → 0 时 d → ∞,返回 Infinity。 */
export function twoThetaToD(twoThetaDeg: number, wavelength: number): number {
  return wavelength / (2 * Math.sin((twoThetaDeg / 2) * DEG));
}

/** d(Å)→ 2θ(度)。d < λ/2 时无解,返回 NaN。 */
export function dToTwoTheta(dSpacing: number, wavelength: number): number {
  const ratio = wavelength / (2 * dSpacing);
  if (!Number.isFinite(ratio) || Math.abs(ratio) > 1) return NaN;
  return 2 * Math.asin(ratio) / DEG;
}

/** 2θ(度)→ Q(Å⁻¹)。Q = 4π sinθ / λ。 */
export function twoThetaToQ(twoThetaDeg: number, wavelength: number): number {
  return (4 * Math.PI * Math.sin((twoThetaDeg / 2) * DEG)) / wavelength;
}

/** Q(Å⁻¹)→ 2θ(度)。|Qλ/4π| > 1 时无解,返回 NaN。 */
export function qToTwoTheta(q: number, wavelength: number): number {
  const ratio = (q * wavelength) / (4 * Math.PI);
  if (!Number.isFinite(ratio) || Math.abs(ratio) > 1) return NaN;
  return 2 * Math.asin(ratio) / DEG;
}

/** 任意两轴之间换算(经 2θ 中转)。generic 或同轴返回原值。 */
function convertOne(x: number, from: AxisType, to: AxisType, wavelength: number): number {
  if (from === to || from === "generic" || to === "generic") return x;
  const tt =
    from === "two_theta" ? x : from === "d" ? dToTwoTheta(x, wavelength) : qToTwoTheta(x, wavelength);
  return to === "two_theta" ? tt : to === "d" ? twoThetaToD(tt, wavelength) : twoThetaToQ(tt, wavelength);
}

/** 采集轴数值 → 显示坐标。d 视图取负(见模块头的显示约定)。 */
export function toDisplayX(
  x: number,
  view: XDisplay,
  nativeAxis: AxisType,
  wavelength: number,
): number {
  if (view === "generic") return x;
  const v = convertOne(x, nativeAxis, view, wavelength);
  return view === "d" ? -v : v;
}

/** 显示坐标 → 采集轴数值(toDisplayX 的逆)。 */
export function fromDisplayX(
  u: number,
  view: XDisplay,
  nativeAxis: AxisType,
  wavelength: number,
): number {
  if (view === "generic") return u;
  const v = view === "d" ? -u : u;
  return convertOne(v, view, nativeAxis, wavelength);
}

/** 轴的标准显示名(与引擎 coords.axis_label 一致;领域符号,不译)。 */
export function axisLabel(axis: XDisplay | AxisType): string {
  return {
    two_theta: "2θ (°)",
    d: "d (Å)",
    q: "Q (Å⁻¹)",
    generic: "x",
  }[axis];
}

/** 轴的短名(峰表列头用)。与 t.peaks.colPosition("2θ")在默认轴上一致。 */
export function axisShortLabel(axis: XDisplay | AxisType): string {
  return {
    two_theta: "2θ",
    d: "d (Å)",
    q: "Q (Å⁻¹)",
    generic: "x",
  }[axis];
}

/** 读数/标签格式(精度对齐 README §4.4:2θ 3 位、d/Q 4 位)。
 * d 取绝对值 —— 内部映射是负 d(见模块头),用户看到的恒为正。 */
export function formatAxisValue(u: number, view: XDisplay): string {
  if (!Number.isFinite(u)) return "—";
  if (view === "d") return `${Math.abs(u).toFixed(4)} Å`;
  if (view === "q") return `${u.toFixed(4)} Å⁻¹`;
  if (view === "generic") return u.toFixed(3);
  return `${u.toFixed(3)}°`;
}

/** 光标读数整行(轴名 + 值),tooltip 与状态栏共用,保证两处读数一致。 */
export function formatAxisReadout(u: number, view: XDisplay): string {
  if (!Number.isFinite(u)) return "—";
  if (view === "generic") return `x ${u.toFixed(3)}`;
  const name = view === "two_theta" ? "2θ" : view === "d" ? "d" : "Q";
  return `${name} ${formatAxisValue(u, view)}`;
}
