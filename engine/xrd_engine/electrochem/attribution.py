"""容量衰减三分量归因(M3,半电池适用)。

分量定义(见 docs/PLAN_ECHEM.md §2.3,求和=总衰减,按构造闭合):

* 极化型(polarization) —— 材料还在,电压够不着了。
  方法 A:主倍率容量 − 低倍率回访圈插值容量(准)。
  方法 B:放电主峰位移 × 峰高(近似,置信度低,报告建议补回访圈)。
* 活性物质型(material) —— 参与反应的物质没了。
  H1H2 放电主轨迹峰面积萎缩率 × 基线容量(等比例假设,见 notes)。
* 窗口截断型(cutoff) —— 容量被极化推出截止电压窗口。
  充电侧 H2→H3 截断峰证据 + 总量减去前两者后的残差(≥0 截断)。

半电池方法学局限(写入结论,不藏着):锂片负极的界面阻抗混在"极化"
里无法剥离;方法 B 的极化估算是近似值。

仅依赖 numpy。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

# ---------------------------------------------------------------- 参数/结果


@dataclass
class AttributionParams:
    method: str = "auto"        # auto / A / B
    baseline_cycles: int = 3    # 主倍率前 N 圈均值作基线
    checkup_group: str = "slow" # 回访圈的倍率组标签

    def validate(self) -> None:
        if self.method not in ("auto", "A", "B"):
            raise ValueError(f"method 非法: {self.method}")
        if self.baseline_cycles < 1 or self.baseline_cycles > 10:
            raise ValueError(f"baseline_cycles 越界: {self.baseline_cycles}")


@dataclass
class AttributionCycle:
    cycle: int
    total: float = 0.0          # mAh/g
    polarization: float = 0.0
    material: float = 0.0
    cutoff: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle": self.cycle,
            "total": round(self.total, 3),
            "polarization": round(self.polarization, 3),
            "material": round(self.material, 3),
            "cutoff": round(self.cutoff, 3),
        }


@dataclass
class AttributionResult:
    method: str                    # 实际采用的 A / B
    confidence: str                # high / low
    baseline: float                # 基线容量 mAh/g
    cycles: list[AttributionCycle] = field(default_factory=list)
    ce_series: list[tuple[int, float]] = field(default_factory=list)
    cumulative_irreversible: list[tuple[int, float]] = field(default_factory=list)
    truncated_evidence: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    conclusion: str = ""
    capacity_unit: str = "mAh/g"
    # 方法 B 峰序列不足时置位:此时三分量只是残差拆分,结论不能断言机理
    split_uninformative: bool = False

    def to_dict(self) -> dict[str, Any]:
        last = self.cycles[-1] if self.cycles else None
        return {
            "method": self.method,
            "confidence": self.confidence,
            "baseline": round(self.baseline, 3),
            "nCycles": len(self.cycles),
            "lastCycle": last.cycle if last else None,
            "lastTotal": last.total if last else None,
            "cycles": [c.to_dict() for c in self.cycles],
            "ceSeries": [(n, round(v, 4)) for n, v in self.ce_series],
            "cumulativeIrreversible": [(n, round(v, 3)) for n, v in self.cumulative_irreversible],
            "truncatedEvidence": self.truncated_evidence,
            "notes": self.notes,
            "conclusion": self.conclusion,
            "capacityUnit": self.capacity_unit,
        }


# ---------------------------------------------------------------- 主入口


def run_attribution(
    cycles: list[dict[str, Any]],
    peaks: list[Any],
    params: AttributionParams | None = None,
    *, capacity_unit: str = "mAh/g",
) -> AttributionResult:
    """计算归因。

    cycles: 每圈统计 dict(来自 land_parser/ec_cycles):
        {index, q_discharge(mAh/g), ce, i_med_charge, rate_group, incomplete}
    peaks: 全部检峰结果(M2 的 CyclePeak 列表,含截断峰)。
        方法 B 用"放电 H1H2 每圈最强非截断峰"序列,不依赖轨迹连续性
        —— 单条轨迹会在峰形剧变处断开(真实数据教训),归因不能跟着断。

    容量一律 mAh/g(调用方完成归一);无质量时 mAh,数值口径一致即可。
    """
    if params is None:
        params = AttributionParams()
    params.validate()
    valid = [c for c in cycles if not c.get("incomplete") and c.get("q_discharge")]
    checkup = [c for c in valid if c.get("rate_group") == params.checkup_group]
    # 主倍率圈只取 "main" 簇。**fast 必须排除**:倍率阶梯里高速率圈容量
    # 天然低,混进基线会被算成"衰减"。
    main = [
        c for c in valid
        if c.get("rate_group", "main") == "main" and c.get("rate_group") != params.checkup_group
    ]
    if len(main) < 3:
        # 主簇缺席(异常分组)时退回"非回访且非快充",仍不把 fast 圈当衰减
        main = [
            c for c in valid
            if c.get("rate_group", "main") not in ("fast", params.checkup_group)
        ]
    if len(main) < 3:
        raise ValueError(f"有效主倍率圈不足({len(main)}),无法归因")

    q = {c["index"]: float(c["q_discharge"]) for c in valid}
    main_idx = sorted(c["index"] for c in main)
    n0 = main_idx[: params.baseline_cycles]
    baseline = float(np.mean([q[i] for i in n0]))

    # 方法选择:A 需要回访圈且至少出现在 2 个不同时间点。
    # 注意守卫顺序:method="A" 时必须先验回访圈是否够,否则 use_a 被
    # 第一项短路成 True,空 checkup 会直接冲进方法 A 的插值里炸掉。
    checkup_ok = (
        len(checkup) >= 2
        and max(c["index"] for c in checkup) > min(main_idx) + 5
    )
    if params.method == "A" and not checkup_ok:
        raise ValueError("指定方法 A 但回访圈不足(需 ≥2 个不同时间点)")
    use_a = (params.method == "A") or (params.method == "auto" and checkup_ok)

    result = AttributionResult(
        method="A" if use_a else "B",
        confidence="high" if use_a else "low",
        baseline=baseline,
        capacity_unit=capacity_unit,
    )

    # ---- CE 与累积不可逆(独立于方法,直接从圈统计来)
    cum = 0.0
    for c in sorted(valid, key=lambda x: x["index"]):
        ce = float(c.get("ce") or 0.0)
        if c.get("q_charge"):
            cum += float(c["q_charge"]) * (1.0 - ce)
        result.ce_series.append((c["index"], ce))
        result.cumulative_irreversible.append((c["index"], cum))

    if use_a:
        _attribution_method_a(result, q, main, checkup, baseline)
    else:
        _attribution_method_b(result, q, main, baseline, peaks, window_keys(peaks))

    _truncate_evidence(result, peaks, window_keys(peaks)[1])
    _conclusion(result)
    return result


# ---------------------------------------------------------------- 方法 A


def _attribution_method_a(
    result: AttributionResult,
    q: dict[int, float],
    main: list[dict],
    checkup: list[dict],
    baseline: float,
) -> None:
    """回访圈插值法:极化 = 主倍率 − 回访;活性 = 回访自身衰减。"""
    ck_x = np.array([c["index"] for c in checkup], dtype=float)
    ck_y = np.array([q[c["index"]] for c in checkup], dtype=float)
    order = np.argsort(ck_x)
    ck_x, ck_y = ck_x[order], ck_y[order]
    ck_base = float(ck_y[0])
    n_extrap = 0

    for c in sorted(main, key=lambda x: x["index"]):
        n = c["index"]
        if n > ck_x[-1]:
            n_extrap += 1
        total = baseline - q[n]
        if total <= 0:
            result.cycles.append(AttributionCycle(n, max(total, 0.0)))
            continue
        ck_interp = float(np.interp(n, ck_x, ck_y)) if ck_x.size >= 2 else ck_base
        # 低倍率回访容量**高于**主倍率(小电流极化小):差值才是极化损失。
        # 方向写反会把极化算成 0 并全部塞进活性 —— 符号这里是易错点。
        pol = max(ck_interp - q[n], 0.0)            # 主倍率特有损失 ≈ 极化
        mat = max(ck_base - ck_interp, 0.0)          # 低倍率也保不住的 ≈ 活性
        cut = max(total - pol - mat, 0.0)            # 残差 → 截断
        # 归一:三者之和可能超过 total(低倍率也有少量极化),按比例压回
        s = pol + mat + cut
        if s > total and s > 0:
            pol, mat, cut = pol * total / s, mat * total / s, cut * total / s
        result.cycles.append(AttributionCycle(n, total, pol, mat, cut))
    if n_extrap:
        # np.interp 在末回访点外钳制:此后的衰减全部落进截断残差,
        # 结论若说"截断主导"其实是外推假设,必须写明
        result.notes.append(
            f"有 {n_extrap} 个圈在最后一次回访圈之后,"
            "该段极化/活性按末次回访值外推,衰减计入截断残差 —— "
            "回访圈覆盖不足时不要把结论读成'截断主导'的直接证据。"
        )


# ---------------------------------------------------------------- 方法 B


def window_keys(peaks: list[Any]) -> tuple[str | None, str | None]:
    """从实际检峰结果推断 (极化主窗口, 截断证据窗口) 的 key。

    岛外命名("H1H2"/"H2H3")只是默认窗口的名字,自定义窗口下硬编码它
    会让方法 B 全部算成截断、结论还断言"截断主导"。没有约定名时:
    极化主窗口取检出峰最多的放电窗,截断证据取电压最高的充电窗
    (截断发生在高压侧)。
    """
    if not peaks:
        return None, None
    discharge_counts: dict[str, int] = {}
    charge_keys: dict[str, float] = {}
    for p in peaks:
        if p.direction == "discharge":
            discharge_counts[p.window_key] = discharge_counts.get(p.window_key, 0) + 1
        elif p.direction == "charge":
            charge_keys[p.window_key] = max(charge_keys.get(p.window_key, -1e9), p.position)
    pol_key = "H1H2" if "H1H2" in discharge_counts else (
        max(discharge_counts, key=discharge_counts.get) if discharge_counts else None
    )
    cut_key = "H2H3" if "H2H3" in charge_keys else (
        max(charge_keys, key=charge_keys.get) if charge_keys else None
    )
    return pol_key, cut_key


def _dominant_series(
    peaks: list[Any], direction: str, window_key: str, cycles_set: set[int]
) -> dict[int, Any]:
    """每圈取指定窗口内高度最大的非截断峰(不依赖轨迹连续性)。"""
    best: dict[int, Any] = {}
    for p in peaks:
        if (
            p.direction == direction
            and p.window_key == window_key
            and not p.truncated
            and p.cycle in cycles_set
            and (p.cycle not in best or p.height > best[p.cycle].height)
        ):
            best[p.cycle] = p
    return best


def _attribution_method_b(
    result: AttributionResult,
    q: dict[int, float],
    main: list[dict],
    baseline: float,
    peaks: list[Any],
    keys: tuple[str | None, str | None],
) -> None:
    """峰位移/峰面积近似法(置信度低,notes 建议补回访圈)。

    序列取"放电主窗口每圈最强峰",缺圈对位置/面积线性插值 ——
    保证每个主倍率圈都有分量,不随检峰空洞断档。
    """
    result.notes.append(
        "未检出低倍率回访圈,极化与活性拆分为近似值(方法 B)。"
        "后续实验建议每 ~50 圈插入一个 C/10 回访圈,归因将升级为方法 A。"
    )
    main_set = {c["index"] for c in main}
    pol_key = keys[0]
    series = _dominant_series(peaks, "discharge", pol_key, main_set) if pol_key else {}
    if len(series) < 3:
        result.notes.append(
            f"放电主窗口({pol_key or '无'})峰序列不足,活性/极化无法估算"
        )
        series = {}
        result.split_uninformative = True

    pol_s: dict[int, float] = {}
    mat_s: dict[int, float] = {}
    if series:
        base_cycles = sorted(series)[:3]
        a0 = float(np.mean([series[n].area for n in base_cycles]))
        p0 = float(np.mean([series[n].position for n in base_cycles]))
        for n, pk in series.items():
            ratio = pk.area / a0 if a0 > 0 else 1.0
            mat_s[n] = max(baseline * (1.0 - min(ratio, 1.0)), 0.0)
            pol_s[n] = max((p0 - pk.position) * pk.height, 0.0)

    for c in sorted(main, key=lambda x: x["index"]):
        n = c["index"]
        total = baseline - q[n]
        if total <= 0:
            result.cycles.append(AttributionCycle(n, max(total, 0.0)))
            continue
        if series:
            ns = sorted(series)
            pol = float(np.interp(n, ns, [pol_s[x] for x in ns]))
            mat = float(np.interp(n, ns, [mat_s[x] for x in ns]))
        else:
            pol = mat = 0.0
        cut = max(total - pol - mat, 0.0)
        if pol + mat + cut > total:
            s = pol + mat + cut
            pol, mat, cut = pol * total / s, mat * total / s, cut * total / s
        result.cycles.append(AttributionCycle(n, total, pol, mat, cut))


# ---------------------------------------------------------------- 证据与结论


def _truncate_evidence(result: AttributionResult, peaks: list[Any], cut_key: str | None) -> None:
    """充电高压侧窗口的截断峰统计:相变在窗口内做不完的直接证据。

    窗口 key 由 window_keys 推断(默认 "H2H3",自定义窗口取最高压充电窗),
    不能写死 —— 自定义窗口下写死会永远 available=False,截断结论没有证据。
    """
    h3 = [p for p in peaks if p.direction == "charge" and cut_key and p.window_key == cut_key]
    trunc = [p for p in h3 if p.truncated]
    full = [p for p in h3 if not p.truncated]
    if not h3:
        result.truncated_evidence = {"available": False}
        return
    positions = [p.position for p in h3]
    result.truncated_evidence = {
        "available": True,
        "window": cut_key,
        "nTruncatedCycles": len(trunc),
        "nFullCycles": len(full),
        "firstPosition": round(min(positions), 3),
        "lastPosition": round(max(positions), 3),
        "driftV": round(max(positions) - min(positions), 3),
    }


def _conclusion(result: AttributionResult) -> None:
    """规则化结论文本。宁说方向,不断言机理。"""
    if not result.cycles:
        result.conclusion = "有效圈不足,无法给出结论。"
        return
    last = result.cycles[-1]
    unit = result.capacity_unit
    fmt = ".1f" if unit == "mAh/g" else ".3g"
    if last.total <= (0.5 if unit == "mAh/g" else 0):
        result.conclusion = "容量无明显衰减。"
        return
    if result.split_uninformative:
        # 峰序列不足时三分量只是残差堆在截断上,不能当成"截断主导"的结论
        result.conclusion = (
            f"第 {last.cycle} 圈累计衰减 {last.total:{fmt}} {unit}"
            f"(基线 {result.baseline:{fmt}}),"
            "但放电主峰序列不足,极化/活性/截断三分量无法拆分"
            "(该拆分把全部残差记入截断项,不代表机理判断)。"
            "建议补做低倍率回访圈,或降低寻峰高度阈值后重算。"
        )
        return
    parts = {
        "polarization": (last.polarization, "极化型(界面/动力学方向:优先排查界面副反应、电解液氧化与阻抗增长)"),
        "material": (last.material, "活性物质型(颗粒开裂/失联/惰性化方向:建议对照循环后 XRD 的 (108)/(110) 分裂度与 R-3m 保持情况)"),
        "cutoff": (last.cutoff, "窗口截断型(相变在截止电压内做不完:高压侧极化增长把容量推出窗口)"),
    }
    _, (dom_val, dom_text) = max(parts.items(), key=lambda kv: kv[1][0])
    share = dom_val / last.total * 100 if last.total > 0 else 0.0
    lines = [
        f"第 {last.cycle} 圈累计衰减 {last.total:{fmt}} {unit}"
        f"(基线 {result.baseline:{fmt}}),主导分量为{dom_text.split('(')[0]}"
        f"占 {share:.0f}%。",
        f"分量:极化 {last.polarization:{fmt}} / 活性 {last.material:{fmt}} / 截断 {last.cutoff:{fmt}} {unit}。",
    ]
    ev = result.truncated_evidence
    if ev.get("available") and ev.get("driftV", 0) > 0.05:
        lines.append(
            f"充电 H2→H3 峰位漂移 {ev['driftV'] * 1000:.0f} mV 并贴近截止电压,"
            "相变在窗口内趋于不完整 —— 与截断型分量互为印证。"
        )
    if result.confidence == "low":
        lines.append("注:方法 B 为近似拆分,分量比例仅供参考;方向判断可靠。")
    lines.append("方法学局限:Li 半电池的极化含锂片界面贡献,无法与正极完全剥离。")
    result.conclusion = " ".join(lines)
