"""Read-only adapter for the verified LANDCex V7.4 Windows x64 ABI."""

from __future__ import annotations

import ctypes as C
import math
import os
import struct
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..services.data_store import sha256_of_file

DLL_SHA256 = "7f2c81bd86d7cd09a85963e4c312824fc8eeb122a947d95861b936273816f7cb"
REST_MODE = 0x70


class CexError(ValueError):
    """A CEX file or request that the viewer cannot open or process."""


@dataclass(frozen=True)
class Column:
    code: int
    label: str
    unit: str = ""

    @property
    def heading(self) -> str:
        return f"{self.label} ({self.unit})" if self.unit else self.label


CYCLE_COLUMNS = {
    "qCharge": Column(0x1001, "充电容量", "mAh"),
    "qDischarge": Column(0x1002, "放电容量", "mAh"),
    "sqCharge": Column(0x1003, "充电比容量", "mAh/g"),
    "sqDischarge": Column(0x1004, "放电比容量", "mAh/g"),
    "efficiency": Column(0x1005, "库仑效率", "%"),
    "retention": Column(0x1016, "容量保持率", "%"),
    "eCharge": Column(0x1006, "充电能量", "mWh"),
    "eDischarge": Column(0x1007, "放电能量", "mWh"),
    "chargeTime": Column(0x101A, "充电时间", "s"),
    "dischargeTime": Column(0x101B, "放电时间", "s"),
    "chargeEndV": Column(0x1019, "充电终压", "V"),
    "dischargeEndV": Column(0x1015, "放电终压", "V"),
}

STEP_COLUMNS = {
    "seconds": Column(0x2003, "工步时间", "s"),
    "capacity": Column(0x2004, "容量", "mAh"),
    "specificCapacity": Column(0x2005, "比容量", "mAh/g"),
    "energy": Column(0x2006, "能量", "mWh"),
    "startV": Column(0x200A, "起始电压", "V"),
    "endV": Column(0x200B, "结束电压", "V"),
    "startI": Column(0x200F, "起始电流", "mA"),
    "endI": Column(0x2010, "结束电流", "mA"),
}

RECORD_COLUMNS = {
    "time": Column(0x4002, "测试时间", "s"),
    "stepTime": Column(0x4003, "工步时间", "s"),
    "current": Column(0x4004, "电流", "mA"),
    "capacity": Column(0x4005, "容量", "mAh"),
    "specificCapacity": Column(0x4006, "比容量", "mAh/g"),
    "voltage": Column(0x4008, "电压", "V"),
    "energy": Column(0x4009, "能量", "mWh"),
}

MODE_NAMES = {
    0x02: "恒流放电",
    0x03: "恒流充电",
    0x05: "恒压充电",
    REST_MODE: "静置",
    0x75: "倍率放电",
    0x76: "倍率充电",
}

# LAND uses indices from zero and inclusive last-row indices at all three levels.
CYCLE_SPANS = {
    "records": ("GetFirstRecOfCycle", "GetLastRecOfCycle"),
    "steps": ("GetFirstStepOfCycle", "GetLastStepOfCycle"),
    "charge": ("GetFirstChargeStepOfCycle", "GetLastChargeStepOfCycle"),
    "discharge": ("GetFirstDischStepOfCycle", "GetLastDischStepOfCycle"),
}
STEP_SPAN = ("GetFirstRecOfStep", "GetLastRecOfStep")
UNIT_MULTIPLIERS = {
    "mA": {"ma": 1, "a": 1000, "ua": 0.001},
    "mAh": {"mah": 1, "ah": 1000, "uah": 0.001},
    "mWh": {"mwh": 1, "wh": 1000, "uwh": 0.001},
    "mAh/g": {"mah/g": 1, "ah/kg": 1, "ah/g": 1000},
    "V": {"v": 1, "mv": 0.001},
    "%": {"%": 1},
}


def _finite(value: float) -> float | None:
    return value if math.isfinite(value) else None


def _timestamp(seconds: int) -> str:
    return datetime.fromtimestamp(seconds).isoformat(sep=" ", timespec="seconds") if seconds else ""


class LandCex:
    def __init__(self, path: Path):
        if os.name != "nt" or C.sizeof(C.c_void_p) != 8:
            raise CexError("读取蓝电 CEX 需要 Windows x64")
        if not path.is_file():
            raise CexError("未找到蓝电 LANDCex_x64.dll，请检查蓝电安装或设置 XRD_LAND_DLL")
        if sha256_of_file(path) != DLL_SHA256:
            raise CexError("当前蓝电 DLL 版本未验证，请使用已验证的 LANDdt V7.4.7.6 版本")
        try:
            self.dll = C.WinDLL(str(path))
            self._bind()
        except (AttributeError, OSError) as exc:
            raise CexError(f"无法加载蓝电读取组件：{exc}") from exc
        self.modes = dict(MODE_NAMES)

    def _bind(self) -> None:
        handle, word, integer, byte = C.c_void_p, C.c_ushort, C.c_int, C.c_ubyte
        pointer = C.POINTER
        signatures = {
            "EnableSilentForLoadData": ([byte], byte),
            "LoadData": ([C.c_char_p, integer], handle),
            "FreeData": ([handle], None),
            "GetRows": ([handle, word], integer),
            "GetDataAsDouble": ([handle, word, integer], C.c_double),
            "GetUnitNameOfColumn": ([handle, word, handle], None),
            "GetDescriptionOfMode": ([byte, handle], None),
            "GetCycleFromStep": ([handle, integer], integer),
            "GetCycleFromRec": ([handle, integer, pointer(integer)], integer),
            "GetActMaterialMass": ([handle, pointer(C.c_float)], C.c_float),
            "GetChannel": ([handle, pointer(byte), pointer(byte), pointer(integer), pointer(word)], byte),
            "GetStartTime": ([handle], C.c_uint32),
            "GetEndTime": ([handle], C.c_uint32),
            "GetBattNo": ([handle, handle], byte),
            "GetBattBatchNo": ([handle, handle], byte),
            "GetChlNote": ([handle, handle], byte),
            "GetNumOfProcedure": ([handle], integer),
            "GetProcedureName": ([handle, handle, integer, handle], byte),
        }
        for pair in (*CYCLE_SPANS.values(), STEP_SPAN):
            for name in pair:
                signatures[name] = ([handle, integer], integer)
        for name, (args, result) in signatures.items():
            fn = getattr(self.dll, name)
            fn.argtypes = args
            fn.restype = result
        self.clear_variant = C.OleDLL("oleaut32").VariantClear
        self.clear_variant.argtypes = [handle]
        self.clear_variant.restype = C.c_long
        self.dll.EnableSilentForLoadData(1)

    def text(self, name: str, *args, after: tuple = ()) -> str:
        variant = C.create_string_buffer(24)
        try:
            getattr(self.dll, name)(*args, variant, *after)
            kind = struct.unpack_from("<H", variant, 0)[0]
            pointer = struct.unpack_from("<Q", variant, 8)[0]
            return C.wstring_at(pointer).strip() if kind == 8 and pointer else ""
        finally:
            self.clear_variant(variant)

    def mode_name(self, mode: int) -> str:
        if mode not in self.modes:
            description = self.text("GetDescriptionOfMode", mode)
            self.modes[mode] = (
                description if description and "?" not in description else f"工步模式 0x{mode:02X}"
            )
        return self.modes[mode]

    def open(self, working_copy: Path) -> CexFile:
        try:
            encoded = str(working_copy).encode("gbk")
        except UnicodeEncodeError as exc:
            raise CexError("蓝电组件无法读取项目库路径，请使用可用 GBK 表示的目录") from exc
        handle = self.dll.LoadData(encoded, 0)
        if not handle:
            raise CexError("蓝电组件无法打开此 CEX，文件可能损坏或格式不受当前版本支持")
        try:
            return CexFile(self, handle)
        except Exception:
            self.dll.FreeData(handle)
            raise


class CexFile:
    def __init__(self, api: LandCex, handle: int):
        self.api = api
        self.dll = api.dll
        self.handle = handle
        self.counts = {
            "cycles": self.dll.GetRows(handle, 0x1001),
            "steps": self.dll.GetRows(handle, 0x2001),
            "records": self.dll.GetRows(handle, 0x4001),
        }
        if min(self.counts.values()) < 0:
            raise CexError("无法读取此 CEX 的数据表")
        self._multipliers: dict[int, float] = {}
        mass = _finite(self.dll.GetActMaterialMass(handle, None))
        self.mass_mg = mass * 1000 if mass is not None and mass > 0 else None

    def close(self) -> None:
        if self.handle:
            self.dll.FreeData(self.handle)
            self.handle = 0

    def session_info(self) -> dict:
        rack, channel, device, flags = C.c_ubyte(), C.c_ubyte(), C.c_int(), C.c_ushort()
        has_channel = self.dll.GetChannel(
            self.handle, C.byref(rack), C.byref(channel), C.byref(device), C.byref(flags),
        )
        n = self.dll.GetNumOfProcedure(self.handle)
        if not 0 <= n <= 10000:
            raise CexError("蓝电测试工艺版本数异常")
        programs = []
        for index in range(n):
            started = C.c_uint32()
            title = self.api.text("GetProcedureName", self.handle, after=(index, C.byref(started)))
            programs.append({"name": title, "startTime": _timestamp(started.value)})
        return {
            "channel": f"{rack.value + 1:03d}_{channel.value + 1}" if has_channel else "",
            "barcode": self.api.text("GetBattNo", self.handle),
            "batch": self.api.text("GetBattBatchNo", self.handle),
            "note": self.api.text("GetChlNote", self.handle),
            "massMg": self.mass_mg,
            "startTime": _timestamp(self.dll.GetStartTime(self.handle)),
            "endTime": _timestamp(self.dll.GetEndTime(self.handle)),
            "programs": programs,
        }

    def multiplier(self, column: Column) -> float:
        factors = UNIT_MULTIPLIERS.get(column.unit)
        if not factors:
            return 1
        if column.code not in self._multipliers:
            unit = self.api.text("GetUnitNameOfColumn", self.handle, column.code)
            key = unit.lower().replace("μ", "u").replace("µ", "u").replace(" ", "")
            if key not in factors:
                raise CexError(f"无法确认蓝电数据列的单位（{column.label}，{unit or '缺失'}）")
            self._multipliers[column.code] = factors[key]
        return self._multipliers[column.code]

    def value(self, column: Column, index: int) -> float | None:
        value = _finite(self.dll.GetDataAsDouble(self.handle, column.code, index))
        if value is None or (column.unit == "mAh/g" and value == 0 and self.mass_mg is None):
            return None
        return value * self.multiplier(column)

    def values(self, columns: dict[str, Column], index: int) -> dict:
        return {key: self.value(column, index) for key, column in columns.items()}

    def _span(self, pairs: tuple[str, str], index: int, size: int) -> tuple[int, int]:
        start, last = (getattr(self.dll, name)(self.handle, index) for name in pairs)
        if start == -1 and last == -1:
            return (0, 0)
        if not 0 <= start <= last < size:
            raise CexError("CEX 中的工步或采样记录范围无效")
        return (start, last + 1)

    def cycle_span(self, index: int, level: str) -> tuple[int, int]:
        size = self.counts["records" if level == "records" else "steps"]
        return self._span(CYCLE_SPANS[level], index, size)

    def step_span(self, index: int) -> tuple[int, int]:
        return self._span(STEP_SPAN, index, self.counts["records"])

    def step_mode(self, index: int) -> int:
        return int(self.dll.GetDataAsDouble(self.handle, 0x2002, index))

    def cycle_row(self, index: int) -> dict:
        first_step, end_step = self.cycle_span(index, "steps")
        first_record, end_record = self.cycle_span(index, "records")
        return {
            "number": index + 1,
            "firstStep": first_step,
            "stepCount": end_step - first_step,
            "firstRecord": first_record,
            "recordCount": end_record - first_record,
            **self.values(CYCLE_COLUMNS, index),
        }

    def step_row(self, index: int) -> dict:
        start, end = self.step_span(index)
        mode = self.step_mode(index)
        cycle = self.dll.GetCycleFromStep(self.handle, index)
        return {
            "number": index + 1,
            "cycle": cycle + 1 if cycle >= 0 else None,
            "mode": self.api.mode_name(mode),
            "firstRecord": start,
            "recordCount": end - start,
            **self.values(STEP_COLUMNS, index),
        }

    def record_row(self, index: int) -> dict:
        return {"number": index + 1, **self.values(RECORD_COLUMNS, index)}

    def locate(self, record: int) -> dict:
        step = C.c_int(-1)
        cycle = self.dll.GetCycleFromRec(self.handle, record, C.byref(step))
        return {
            "cycle": cycle + 1 if 0 <= cycle < self.counts["cycles"] else None,
            "step": self.step_row(step.value) if 0 <= step.value < self.counts["steps"] else None,
        }
