"""LAND(蓝电)导出文件解析器。

支持 .xlsx(记录表/循环表/测试流程表 三工作表布局),后续扩展 csv/txt。
设计要点(来自真实样例 170214_005_7 的确认):

* 时间列混用 datetime.time / "D-HH:MM:SS" 字符串,统一折算为累计秒;
* 工步状态实测取值 R / RateC / RateD,归一化为 rest/charge/discharge,
  并兼容 C / D / CV / 中文"恒流充电"等其它蓝电版本写法;
* 电流充正放负,逐圈随容量自动重标定("倍率充电 0.2*C")——
  因此倍率分组必须按圈中位电流聚类,不能用固定阈值;
* 容量以 ∫I·dt 梯形积分自算,与循环表容量交叉校验。

仅依赖 numpy + 标准库。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------- 常量表

# 记录表必需列 → 同义词(去掉单位后按关键词匹配)
_RECORD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "time": ("测试时间", "记录时间", "绝对时间", "相对时间", "testtime", "time"),
    "current": ("电流", "current"),
    "voltage": ("电压", "voltage"),
    "cycle": ("循环序号", "循环次数", "圈数", "cyclenumber", "cycle"),
    "step": ("工步序号", "步骤序号", "工步号", "step"),
    "state": ("工步状态", "状态", "state"),
    "capacity": ("容量", "capacity"),  # 可选,校验用
}

# 循环表列 → 同义词。**必须按表头名匹配,不能按固定下标** ——
# 导出对话框勾选不同列时位置会变(真实样例的"充电中压/放电中压"排在
# 容量列之后,勾掉它们不移位,但勾掉更靠前的列会整体左移,按位置硬读
# 会把能量/时间读成容量)。比容量写在容量前面,先精确后宽泛。
_CYCLE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "cycle": ("循环序号", "循环次数", "圈数", "cyclenumber", "cycle"),
    "q_charge_g": ("充电比容量", "chargecapacitydensity"),
    "q_discharge_g": ("放电比容量", "dischargecapacitydensity"),
    "q_charge": ("充电容量", "chargecapacity"),
    "q_discharge": ("放电容量", "dischargecapacity"),
    "ce": ("效率", "库仑效率", "库伦效率", "efficiency", "ce"),
}

# 表头认不出时的回退布局(旧蓝电固定 0..5 列),保持旧行为不炸。
_CYCLE_LEGACY_COLS: dict[str, int] = {
    "cycle": 0, "q_charge": 1, "q_discharge": 2,
    "q_charge_g": 3, "q_discharge_g": 4, "ce": 5,
}

# 状态原文 → 归一化方向。顺序即匹配优先级,前缀匹配。
#
# "cv"/"cc" 必须排在单字母 "c"/"d" 之前:英文 "CV" 会被 "c" 前缀抢走
# 变成恒流充电,CV 段容量的归属会随语言写法漂移。"充电"/"放电"(无
# "恒流"前缀)按恒流处理 —— 蓝电部分版本只写方向,而静默丢行比把
# 它当恒流更糟(丢行会把整圈容量抹掉且不报错)。
_STATE_MAP: list[tuple[str, str]] = [
    ("ratec", "charge"),
    ("恒流充电", "charge"), ("恒压", "charge_cv"),
    ("cc", "charge"), ("cv", "charge_cv"), ("c", "charge"),
    ("充电", "charge"),
    ("rated", "discharge"),
    ("恒流放电", "discharge"), ("dc", "discharge"), ("d", "discharge"),
    ("放电", "discharge"),
    ("r", "rest"), ("静置", "rest"), ("rest", "rest"), ("搁置", "rest"),
    ("idle", "rest"), ("stand", "rest"),
]


class LandParseError(ValueError):
    """结构无法识别或必需列缺失。"""


# ---------------------------------------------------------------- 数据结构


@dataclass
class SessionInfo:
    """测试流程表 / 元信息。"""

    test_name: str = ""
    channel: str = ""
    start_time: str = ""
    end_time: str = ""
    program: list[str] = field(default_factory=list)  # 工步描述原文


@dataclass
class LandCycle:
    """单圈统计(来自记录表积分 + 循环表参照)。"""

    index: int
    q_charge: float = 0.0        # mAh, ∫I·dt
    q_discharge: float = 0.0     # mAh
    ce: float = 0.0
    i_med_charge: float = 0.0    # mA,本圈充电中位电流
    i_med_discharge: float = 0.0
    v_min: float = float("nan")
    v_max: float = float("nan")
    n_rest: int = 0
    has_cv: bool = False
    # 循环表参照值(缺列时为 nan)
    ref_q_charge: float = float("nan")
    ref_q_discharge: float = float("nan")
    ref_q_charge_g: float = float("nan")     # mAh/g,循环表比容量列
    ref_q_discharge_g: float = float("nan")
    ref_ce: float = float("nan")
    # 每圈倍率组 / 完整性(归因输入)
    rate_group: str = "main"     # main / slow / fast
    incomplete: bool = False     # 末圈容量骤降(测试中途停止)


@dataclass
class LandFile:
    """一个 LAND 导出文件的完整解析结果。"""

    source: str
    session: SessionInfo
    # 记录表原始数据(按行)
    t: np.ndarray            # 累计秒
    i: np.ndarray            # mA
    v: np.ndarray            # V
    cycle: np.ndarray        # 循环序号(int32,-1 = 缺失)
    step: np.ndarray         # 工步序号(int32)
    state: np.ndarray        # 归一化状态码 0=rest 1=charge 2=discharge 3=cv
    cycles: list[LandCycle]
    qa: dict[str, Any] = field(default_factory=dict)


STATE_CODES = {"rest": 0, "charge": 1, "discharge": 2, "charge_cv": 3}
STATE_NAMES = {v: k for k, v in STATE_CODES.items()}


# ---------------------------------------------------------------- 工具函数


def _norm_header(raw: Any) -> str:
    """表头归一化:去单位后缀、去空白、转小写,便于同义词匹配。"""
    s = str(raw).strip() if raw is not None else ""
    s = s.split("/")[0] if "/" in s else s
    return s.strip().lower()


def _match_columns(header: list[Any], required: dict[str, tuple[str, ...]]) -> dict[str, int]:
    """把表头映射到逻辑列。必需列缺失时抛 LandParseError。

    匹配分三级偏好:精确 → 前缀 → 子串。不能一上来就子串 ——
    `time` 会命中 `steptime`/`cycle-time`,把步时间当累计时间,
    后续容量积分与回退过滤全链条错位且不报错。
    """
    cols: dict[str, int] = {}
    normed = [_norm_header(h) for h in header]
    for mode in ("exact", "prefix", "contains"):
        for key, synonyms in required.items():
            if key in cols:
                continue
            for syn in synonyms:
                syn_n = syn.lower()
                for idx, h in enumerate(normed):
                    if not h:
                        continue
                    hit = (
                        h == syn_n
                        if mode == "exact"
                        else h.startswith(syn_n)
                        if mode == "prefix"
                        else syn_n in h
                    )
                    if hit and idx not in cols.values():
                        cols[key] = idx
                        break
                if key in cols:
                    break
    return cols


def parse_time_value(raw: Any) -> float | None:
    """时间列取值 → 累计秒。

    兼容: datetime.time / datetime.datetime / timedelta /
    "HH:MM:SS" / "D-HH:MM:SS" / "D:HH:MM:SS" / "2026/10/06 12:34:56" /
    纯数字(秒)。

    datetime.datetime 与带日期的文本返回**相对同一纪元的秒数**;调用方
    (记录表循环)再统一减去首行纪元。此前这两类取值直接返回 None 被
    整行丢弃 —— 一张 7184 行的表只剩 29 行还能"导入成功",不报错。
    """
    if raw is None:
        return None
    if isinstance(raw, timedelta):
        return raw.total_seconds()
    if isinstance(raw, datetime):
        return raw.timestamp()
    if isinstance(raw, time):
        return raw.hour * 3600 + raw.minute * 60 + raw.second + raw.microsecond / 1e6
    s = str(raw).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    # 带日期的文本("2026/10/06 12:34:56"、"2026-10-06 12:34:56")→ 纪元秒
    dt = _try_parse_datetime_text(s)
    if dt is not None:
        return dt.timestamp()
    days = 0
    # "44-13:35:26"(D-HH:MM:SS):只有「纯数字-时间」形状才算,
    # 避免把其它带连字符的写法误切成天。
    m = re.match(r"^(\d+)-(\d{1,2}:\d{1,2}:\d{1,2}(?:\.\d+)?)$", s)
    if m:
        days = int(m.group(1))
        s = m.group(2)
    s = s.replace("+", "")
    parts = s.split(":")
    try:
        if len(parts) == 3:
            h, mnt, sec = parts
            return days * 86400 + int(h) * 3600 + int(mnt) * 60 + float(sec)
        if len(parts) == 4:  # D:HH:MM:SS
            d, h, mnt, sec = parts
            return int(d) * 86400 + int(h) * 3600 + int(mnt) * 60 + float(sec)
    except ValueError:
        return None
    return None


def _try_parse_datetime_text(s: str) -> datetime | None:
    """识别 "YYYY/MM/DD HH:MM:SS" / "YYYY-MM-DD HH:MM:SS" 等日期时间文本。"""
    norm = s.replace("/", "-")
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(norm, fmt)
        except ValueError:
            continue
    return None


def _norm_state(raw: Any) -> int:
    """状态原文 → 状态码;无法识别返回 -1。"""
    if raw is None:
        return -1
    s = str(raw).strip()
    if not s:
        return -1
    low = s.lower()
    for prefix, name in _STATE_MAP:
        if low.startswith(prefix.lower()) or s.startswith(prefix):
            return STATE_CODES[name]
    return -1


def _to_float(raw: Any) -> float:
    if raw is None:
        return float("nan")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float("nan")


# ---------------------------------------------------------------- 主解析


def parse_land_path(path: str | Path) -> LandFile:
    """解析 LAND 导出文件(xlsx)。失败抛 LandParseError。"""
    path = Path(path)
    if path.suffix.lower() == ".xlsx":
        return _parse_xlsx(path)
    if path.suffix.lower() in (".xls",):
        # xlrd 属 engine 打包依赖,原型阶段暂未安装,提示后走 xlsx
        raise LandParseError(".xls 旧格式待并入 engine 时启用 xlrd,请先用 xlsx 导出")
    if path.suffix.lower() in (".csv", ".txt"):
        raise LandParseError("csv/txt 解析在 M0 第二批实现,当前样例为 xlsx")
    raise LandParseError(f"不支持的扩展名: {path.suffix}")


def _parse_xlsx(path: Path) -> LandFile:
    import openpyxl  # 延迟导入,engine 打包时随依赖提供

    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise LandParseError(f"无法打开 xlsx: {exc}") from exc
    try:
        names = {n.strip(): n for n in wb.sheetnames}
        record_sheet = _find_sheet(names, ("记录表", "record"))
        cycle_sheet = _find_sheet(names, ("循环表", "cycle"))
        flow_sheet = _find_sheet(names, ("测试流程表", "流程", "flow"))
        if record_sheet is None:
            raise LandParseError("找不到记录表工作表")
        lf = _parse_record_sheet(wb[record_sheet], str(path))
        if cycle_sheet is not None:
            _attach_cycle_table(lf, wb[cycle_sheet])
        else:
            # 无循环表(蓝电导出可只勾记录表)也必须从记录表积分出圈 ——
            # 此前圈构建埋在 _attach_cycle_table 里,缺循环表就 0 圈,
            # 导入"成功"但 dQ/dV 永远算不了。
            _build_cycles(lf, {})
        if flow_sheet is not None:
            _attach_flow_sheet(lf, wb[flow_sheet])
        _finalize_qa(lf)
        return lf
    finally:
        wb.close()


def _find_sheet(names: dict[str, str], keywords: tuple[str, ...]) -> str | None:
    for kw in keywords:
        for norm, orig in names.items():
            if kw in norm:
                return orig
    return None


def _parse_record_sheet(ws, source: str) -> LandFile:
    """读记录表:定位表头行 → 逐行数值化 → 返回原始数组。"""
    header: list[Any] = []
    header_row = 0
    for ridx, row in enumerate(ws.iter_rows(min_row=1, max_row=5, values_only=True), start=1):
        cells = list(row)
        has_content = any(c is not None and str(c).strip() for c in cells)
        if has_content and (_match_columns(cells, _RECORD_SYNONYMS) or ridx >= 2):
            header = cells
            header_row = ridx
            break
    if not header:
        raise LandParseError("记录表前 5 行找不到表头")
    cols = _match_columns(header, _RECORD_SYNONYMS)
    missing = [k for k in ("time", "current", "voltage", "state") if k not in cols]
    if missing:
        raise LandParseError(f"记录表缺少必需列: {missing} (识别到: {sorted(cols)})")

    t_list: list[float] = []
    i_list: list[float] = []
    v_list: list[float] = []
    cyc_list: list[int] = []
    step_list: list[int] = []
    state_list: list[int] = []
    skipped = 0
    skip_reasons = {"time": 0, "state": 0, "value": 0}
    # datetime/日期文本时间列返回纪元秒,最后统一减去首行纪元折算成相对秒
    epoch: float | None = None

    cyc_col = cols.get("cycle")
    step_col = cols.get("step")
    n_rows = 0
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        if row is None or all(c is None for c in row):
            continue
        n_rows += 1
        t = parse_time_value(row[cols["time"]])
        if t is None:
            skipped += 1
            skip_reasons["time"] += 1
            continue
        st = _norm_state(row[cols["state"]]) if cols["state"] < len(row) else -1
        cur = _to_float(row[cols["current"]])
        vol = _to_float(row[cols["voltage"]])
        if st < 0:
            skipped += 1
            skip_reasons["state"] += 1
            continue
        if np.isnan(cur) or np.isnan(vol):
            skipped += 1
            skip_reasons["value"] += 1
            continue
        if epoch is None:
            epoch = t
        # 循环/工步序号解析失败按缺失(-1)处理,不能让 int() 的 ValueError
        # 落到 handle() 兜底变成"引擎内部错误"
        try:
            cyc = int(row[cyc_col]) if cyc_col is not None and row[cyc_col] is not None else -1
        except (TypeError, ValueError):
            cyc = -1
        try:
            step = int(row[step_col]) if step_col is not None and row[step_col] is not None else -1
        except (TypeError, ValueError):
            step = -1
        t_list.append(t)
        i_list.append(cur)
        v_list.append(vol)
        cyc_list.append(cyc)
        step_list.append(step)
        state_list.append(st)

    # 静默丢行防线:解析失败行数占多数时必须报错,不能"导入成功"一张空壳
    # (真实样例:datetime 时间列全列解析失败,7184 行只剩 29 行还在跑通)。
    if n_rows > 0 and not t_list:
        raise LandParseError(
            f"记录表 {n_rows} 行全部解析失败"
            f"(时间列 {skip_reasons['time']} 行无法识别、状态列 {skip_reasons['state']} 行无法识别),"
            "请确认导出的是蓝电记录表"
        )
    if n_rows >= 100 and skipped > 0.5 * n_rows:
        raise LandParseError(
            f"记录表过半行无法解析({skipped}/{n_rows}:"
            f"时间 {skip_reasons['time']}、状态 {skip_reasons['state']}、数值 {skip_reasons['value']}),"
            "多半是时间列或工步状态的写法未识别,请反馈样例"
        )

    t_arr = np.asarray(t_list, dtype=np.float64)
    # 只在确实是纪元秒(1970 年以来)时做相对化:纯秒数时间列不受影响
    if epoch and epoch > 1e9 and t_arr.size:
        t_arr = t_arr - epoch
    # Excel 时长数值的单位是「天」:若被当秒读进来的中位间隔小到不可能
    # (亚毫秒采样),按天解释重标为秒。必须在积分之前做 —— 容量 ∫I·dt
    # 差一个 86400 倍照样"看起来合理"。
    time_unit_rescaled = False
    if t_arr.size > 10:
        pos_dt = np.diff(t_arr)
        pos_dt = pos_dt[pos_dt > 0]
        if pos_dt.size and float(np.median(pos_dt)) < 1e-3 and float(t_arr.max()) < 600:
            t_arr = t_arr * 86400.0
            time_unit_rescaled = True

    lf = LandFile(
        source=source,
        session=SessionInfo(),
        t=t_arr,
        i=np.asarray(i_list, dtype=np.float64),
        v=np.asarray(v_list, dtype=np.float64),
        cycle=np.asarray(cyc_list, dtype=np.int32),
        step=np.asarray(step_list, dtype=np.int32),
        state=np.asarray(state_list, dtype=np.int8),
        cycles=[],
    )
    lf.qa["skipped_rows"] = skipped
    lf.qa["skip_reasons"] = skip_reasons
    lf.qa["time_epoch_based"] = bool(epoch and epoch > 1e9)
    if time_unit_rescaled:
        lf.qa["time_unit_rescaled"] = "days->seconds"
    return lf


def _attach_cycle_table(lf: LandFile, ws) -> None:
    """循环表 → 每圈参照容量/CE,并做积分交叉校验。

    列位置**按表头同义词匹配**(前 6 行找表头行),不按下标 —— 导出勾选
    不同列时位置会变。缺的列给 nan(比容量/效率可缺);前 6 行认不出
    表头才回退旧蓝电固定 0..5 布局,比瞎猜更接近原行为。
    """
    cols: dict[str, int] | None = None
    start_row = 2  # 旧行为:表头占第 1 行
    for ridx, row in enumerate(ws.iter_rows(min_row=1, max_row=6, values_only=True), start=1):
        if row is None:
            continue
        m = _match_columns(list(row), _CYCLE_SYNONYMS)
        if "cycle" in m:  # 认出表头的标志
            cols, start_row = m, ridx + 1
            break
    use = cols if cols is not None else _CYCLE_LEGACY_COLS

    def pick(row: tuple[Any, ...], key: str) -> Any:
        idx = use.get(key)
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    by_cycle: dict[int, dict[str, float]] = {}
    for row in ws.iter_rows(min_row=start_row, values_only=True):
        if row is None:
            continue
        raw_cycle = pick(row, "cycle")
        if raw_cycle is None:
            continue
        try:
            n = int(raw_cycle)
        except (TypeError, ValueError):
            continue
        ce = pick(row, "ce")
        by_cycle[n] = {
            "ref_q_charge": _to_float(pick(row, "q_charge")),
            "ref_q_discharge": _to_float(pick(row, "q_discharge")),
            "ref_q_charge_g": _to_float(pick(row, "q_charge_g")),
            "ref_q_discharge_g": _to_float(pick(row, "q_discharge_g")),
            "ref_ce": _to_float(ce) / 100.0 if ce is not None else float("nan"),
        }

    lf.cycles = []
    _build_cycles(lf, by_cycle)
    lf.qa["cycle_table_matched"] = cols is not None


def cycle_dicts(lf: LandFile, mass_mg: float | None = None) -> list[dict[str, Any]]:
    """LandFile.cycles → 归因输入用的 dict 列表(容量归一到 mAh/g)。

    质量优先级:显式 mass_mg > 循环表比容量反推(充/放取中位) > 不归一(mAh)。
    """
    mass = mass_mg
    if mass is None:
        ratios = [
            c.ref_q_charge / c.ref_q_charge_g
            for c in lf.cycles
            if c.ref_q_charge_g and c.ref_q_charge_g > 1 and c.ref_q_charge > 1e-9
        ]
        if not ratios:
            ratios = [
                c.ref_q_discharge / c.ref_q_discharge_g
                for c in lf.cycles
                if c.ref_q_discharge_g and c.ref_q_discharge_g > 1 and c.ref_q_discharge > 1e-9
            ]
        if ratios:
            mass = float(np.median(ratios)) * 1000.0   # g → mg
    scale = 1000.0 / mass if mass else 1.0
    out = []
    for c in lf.cycles:
        out.append({
            "index": c.index,
            "q_charge": c.q_charge * scale,
            "q_discharge": c.q_discharge * scale,
            "ce": c.ce,
            "i_med_charge": c.i_med_charge,
            "rate_group": c.rate_group,
            "incomplete": c.incomplete,
        })
    return out


def _fill_cycle_stats(lf: LandFile, c: LandCycle) -> None:
    mask = lf.cycle == c.index
    i, v, st = lf.i[mask], lf.v[mask], lf.state[mask]
    chg, dis = st == 1, st == 2
    if chg.any():
        c.i_med_charge = float(np.median(i[chg]))
    if dis.any():
        c.i_med_discharge = float(np.median(i[dis]))
    c.v_min = float(np.nanmin(v)) if v.size else float("nan")
    c.v_max = float(np.nanmax(v)) if v.size else float("nan")
    c.n_rest = int((st == 0).sum())
    c.has_cv = bool((st == 3).any())


def _build_cycles(lf: LandFile, by_cycle: dict[int, dict[str, float]]) -> None:
    """从记录表积分出每圈容量,合并循环表参照值,写入 lf.cycles。

    无循环表时 by_cycle 传 {} —— 圈仍然必须建出来(ref_* 为 nan),
    否则"只勾记录表"的导出会得到一个永远算不了的死数据集。
    """
    seg_c = _integrate_by_cycle(lf, "charge")
    seg_d = _integrate_by_cycle(lf, "discharge")
    unique_cycles = sorted({int(n) for n in lf.cycle.tolist() if n >= 0})
    lf.cycles = []
    for n in unique_cycles:
        c = LandCycle(
            index=n,
            q_charge=seg_c.get(n, 0.0),
            q_discharge=seg_d.get(n, 0.0),
        )
        c.ce = c.q_discharge / c.q_charge if c.q_charge > 0 else 0.0
        ref = by_cycle.get(n, {})
        c.ref_q_charge = ref.get("ref_q_charge", float("nan"))
        c.ref_q_discharge = ref.get("ref_q_discharge", float("nan"))
        c.ref_q_charge_g = ref.get("ref_q_charge_g", float("nan"))
        c.ref_q_discharge_g = ref.get("ref_q_discharge_g", float("nan"))
        c.ref_ce = ref.get("ref_ce", float("nan"))
        _fill_cycle_stats(lf, c)
        lf.cycles.append(c)


def _integrate_by_cycle(lf: LandFile, direction: str) -> dict[int, float]:
    """按圈积分指定方向的容量(mAh)。时间回退(重启)处 Δt 记 0。

    充电方向**必须含恒压(state=3)段**:CCCV 协议下 CV 段可以占大半容量,
    只积 state=1 会让充电容量系统性偏低、CE 虚高甚至超过 100%。
    放电方向目前只有恒流(2)一种状态码。
    """
    if direction == "charge":
        codes = (STATE_CODES["charge"], STATE_CODES["charge_cv"])
    else:
        codes = (STATE_CODES["discharge"],)
    out: dict[int, float] = {}
    t, i, cyc, st = lf.t, lf.i, lf.cycle, lf.state
    for n in {int(x) for x in cyc.tolist() if x >= 0}:
        mask = (cyc == n) & np.isin(st, codes)
        idx = np.flatnonzero(mask)
        if idx.size < 2:
            out[n] = 0.0
            continue
        # 只在相邻行的连续段内积分:同圈充电被静置打断时,静置时长不能按
        # 边界电流桥接进去(10 min rest × 边界 70 mA ≈ 12 mAh,比整圈容量
        # 还大一个量级)。CC→CV 状态切换行是相邻的,不受影响。
        runs = np.split(idx, np.flatnonzero(np.diff(idx) != 1) + 1)
        total = 0.0
        for run in runs:
            if run.size < 2:
                continue
            dt = np.diff(t[run])
            dt = np.where(dt > 0, dt, 0.0)  # 重启导致的回退不计
            mid_i = 0.5 * (i[run][1:] + i[run][:-1])  # 梯形
            total += float(np.abs(np.sum(mid_i * dt)) / 3600.0)
        out[n] = total
    return out


def _attach_flow_sheet(lf: LandFile, ws) -> None:
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    s = lf.session
    for r in rows:
        key = str(r[0]).strip() if r and r[0] is not None else ""
        val = str(r[1]).strip() if len(r) > 1 and r[1] is not None else ""
        if key == "测试名":
            s.test_name = val
        elif key == "通道":
            s.channel = val
        elif key == "开始时间":
            s.start_time = val
        elif key == "结束时间":
            s.end_time = val
    # 工步区:首列为数字序号的连续行
    in_steps = False
    for r in rows:
        c0 = r[0] if r else None
        if c0 is None:
            continue
        s0 = str(c0).strip()
        if s0 in ("工步序号", "步骤序号"):
            in_steps = True
            continue
        if not in_steps:
            continue
        is_num = s0.isdigit()
        has_desc = len(r) > 1 and r[1] is not None and str(r[1]).strip()
        if is_num and has_desc:
            desc = " | ".join(str(x) for x in r[1:3] if x is not None)
            if "结束" in desc.replace(" ", ""):   # 蓝电写法"测 试 结 束"带空格
                s.program.append("测试结束")
                break
            s.program.append(f"工步{s0}: {desc}")
        elif not is_num:
            if "结束" in (s0 + (str(r[1]) if len(r) > 1 and r[1] else "")).replace(" ", ""):
                s.program.append("测试结束")
            break


def _finalize_qa(lf: LandFile) -> None:
    """整体 QA:时间回退次数、记录间隔、状态分布、容量校验偏差。"""
    qa = lf.qa
    dt = np.diff(lf.t) if lf.t.size > 1 else np.array([])
    qa["n_records"] = int(lf.t.size)
    qa["backwards"] = int((dt < 0).sum())          # 时间回退(疑似重启/导出拼接)
    qa["dup_time"] = int((dt == 0).sum())
    if dt.size:
        pos = dt[dt > 0]
        qa["interval_med"] = float(np.median(pos)) if pos.size else 0.0
        qa["interval_max"] = float(np.max(dt))
    qa["state_counts"] = {
        STATE_NAMES[int(k)]: int(c)
        for k, c in zip(*np.unique(lf.state, return_counts=True), strict=True)
    }
    # 与循环表容量交叉校验(逐圈相对偏差)
    devs = []
    for c in lf.cycles:
        if not np.isnan(c.ref_q_discharge) and c.ref_q_discharge > 1e-6:
            devs.append(abs(c.q_discharge - c.ref_q_discharge) / c.ref_q_discharge)
    if devs:
        qa["capacity_dev_max"] = max(devs)
        qa["capacity_dev_med"] = float(np.median(devs))
    # 倍率分组:按圈中位充电电流,对主倍率取比值聚类
    rates = _rate_groups(lf)
    qa["rate_groups"] = rates
    for c in lf.cycles:
        if c.index in rates.get("slow", []):
            c.rate_group = "slow"
        elif c.index in rates.get("fast", []):
            c.rate_group = "fast"
        else:
            c.rate_group = "main"
    # 不完整末圈:最后一圈放电容量 < 前一圈的 60%(测试中途停止)。
    # 只标记末圈 —— 中途容量骤降可能是真实失效,交给归因与结论去解释。
    if len(lf.cycles) >= 2:
        last, prev = lf.cycles[-1], lf.cycles[-2]
        if prev.q_discharge > 1e-9 and last.q_discharge < 0.6 * prev.q_discharge:
            last.incomplete = True
            qa["incomplete_last_cycle"] = last.index
    # 活性物质质量估计(循环表比容量反推),供 UI 预填
    masses = [
        c.ref_q_charge / c.ref_q_charge_g
        for c in lf.cycles
        if c.ref_q_charge_g and c.ref_q_charge_g > 1 and c.ref_q_charge > 1e-9
    ]
    if masses:
        qa["mass_mg_estimate"] = float(np.median(masses)) * 1000.0


def _rate_groups(lf: LandFile) -> dict[str, list[int]]:
    """按充电中位电流聚类分倍率组(电流会随容量自动重标定,故用相对比值)。

    返回 {组标签: 圈号列表}。标签: main(主导倍率) / slow(≤0.6×主) / fast(≥1.5×主)。
    """
    if not lf.cycles:
        return {}
    currents = np.array([c.i_med_charge for c in lf.cycles if c.i_med_charge > 0])
    if currents.size < 3:
        return {"main": [c.index for c in lf.cycles]}
    # 主倍率 = 圈数最多的量级簇(对数空间 1D 聚类,阈值 0.2 dex ≈ ×1.5)
    order = np.argsort(currents)
    logcur = np.log(currents[order])
    groups: list[list[float]] = []
    for x in logcur:
        if groups and x - groups[-1][-1] < 0.2:
            groups[-1].append(x)
        else:
            groups.append([x])
    main_group = max(groups, key=len)
    main_cur = float(np.exp(np.mean(main_group)))
    out: dict[str, list[int]] = {"main": [], "slow": [], "fast": []}
    for c in lf.cycles:
        if c.i_med_charge <= 0:
            continue
        ratio = c.i_med_charge / main_cur
        if ratio <= 0.6:
            out["slow"].append(c.index)
        elif ratio >= 1.5:
            out["fast"].append(c.index)
        else:
            out["main"].append(c.index)
    return {k: v for k, v in out.items() if v}
