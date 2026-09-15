"""XRD 文本文件解析器。

支持 CSV / TXT / XY / DAT 四种扩展名。真实仪器导出的文件格式非常杂,
本模块的策略是「尽量读懂,读不懂就交给列映射对话框」:

  1. 编码探测:UTF-8 → GB18030 → Latin-1 逐级回退
     (中文实验室的仪器软件常导出 GB18030)
  2. 跳过注释与垃圾头:以 # ; % ! 开头的行,以及前部无法解析为数字的行
  3. 分隔符自动识别:comma / tab / space / semicolon / 多空格
  4. 识别科学计数法(1.2E+03、1.2e3)与常见缺失值占位(-、NA、null)

解析失败不抛异常给用户看 traceback,而是返回 ParseFailure 让上层
弹出列映射对话框(§7.3)。
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# 注释行前缀。仪器软件各家用不同符号,这里穷举常见几种。
COMMENT_PREFIXES = ("#", ";", "%", "!", "//", "*")

# 分隔符候选,按「越专一越先试」排序
DELIMITER_CANDIDATES = [",", "\t", ";", r"\s+"]

# 缺失值占位符(小写比较)
MISSING_TOKENS = {"-", "na", "n/a", "null", "none", "", "nan"}

# 单个数值的合法形式:可选符号 + 数字 + 可选小数 + 可选指数
_NUMBER_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")

ENCODINGS = ("utf-8-sig", "gb18030", "latin-1")


@dataclass
class ParseFailure:
    """解析失败的描述,供 UI 构造列映射对话框。"""

    reason: str
    """人话原因,可直接显示。"""
    detail: str = ""
    """技术细节,折叠在「View details」里。"""
    preview_rows: list[list[str]] | None = None
    """前若干行的原始文本,列映射对话框用来做 Data Preview。"""
    detected_columns: int = 0


@dataclass
class ParsedTable:
    """成功解析出的数值表。"""

    data: np.ndarray
    """形状 (n_rows, n_cols) 的 Float64 数组。"""
    header_row_index: int
    """数据起始行在文件中的行号(0 起),用于审计。"""
    dropped_rows: int
    delimiter: str
    encoding: str

    @property
    def column_count(self) -> int:
        return int(self.data.shape[1])


def decode_text(raw: bytes) -> tuple[str, str]:
    """按 ENCODINGS 顺序尝试解码,返回 (文本, 实际编码)。"""
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    # latin-1 永不失败,理论上到不了这里
    return raw.decode("latin-1", errors="replace"), "latin-1"


def is_numeric_token(token: str) -> bool:
    """判断一个 token 是否是合法数值(含科学计数法)。"""
    stripped = token.strip()
    if not stripped or stripped.lower() in MISSING_TOKENS:
        return False
    return bool(_NUMBER_RE.match(stripped))


def split_line(line: str, delimiter: str) -> list[str]:
    """按给定分隔符切分一行。delimiter 为 r"\\s+" 时按连续空白切分。"""
    if delimiter == r"\s+":
        return re.split(r"\s+", line.strip())
    if delimiter == ",":
        return next(csv.reader([line]))
    return line.split(delimiter)


def score_delimiter(lines: list[str], delimiter: str) -> float:
    """给分隔符打分:切出的列数一致性越高、数值列越多,分数越高。

    返回 0 表示该分隔符不可用。

    只有「大半 token 是数值」的行才参与统计 —— 仪器导出的文本头
    (Instrument: Bruker D8 之类)会被空白分隔符切成多列,若把它们
    计入一致性,任何带垃圾头的文件都会检测失败。

    对「比众数列数多、且前众数个 token 全是数值」的行按超集处理:
    真实数据常见末尾多出几列(时间戳、误差棒),这不该否定分隔符,
    多余的列由 parse_numeric_table 丢弃。
    """
    counts: list[int] = []
    numeric_ratio_sum = 0.0
    usable = 0

    for line in lines:
        tokens = [t for t in split_line(line, delimiter) if t.strip() != ""]
        if len(tokens) < 2:
            continue
        numeric = sum(1 for t in tokens if is_numeric_token(t))
        # 文本行不参与:数值 token 不足一半的行是表头或散文
        if numeric < len(tokens) / 2:
            continue
        counts.append(len(tokens))
        numeric_ratio_sum += numeric / len(tokens)
        usable += 1

    if usable == 0:
        return 0.0

    # 众数并列时取更大的列数:取大列数能更严格地吸收超集,
    # 避免小众数把长行全当成超集放行(如 [5, 2, 7] 误判)
    mode_count = max(set(counts), key=lambda c: (counts.count(c), c))
    consistency = counts.count(mode_count) / len(counts)

    if consistency < 0.8:
        # 允许超集行:比众数多列、且前众数个 token 全数值的行不算不一致
        ok = counts.count(mode_count)
        for c in counts:
            if c != mode_count and c > mode_count:
                ok += 1  # 超集行,后续会被截断/丢弃
        consistency = ok / len(counts)
        if consistency < 0.8:
            return 0.0

    avg_numeric_ratio = numeric_ratio_sum / usable
    if avg_numeric_ratio < 0.5:
        return 0.0

    # 列数多且一致、且大部分是数字,更可能是正确分隔符。
    # 加 0.1 的基数避免全单列时除零。
    return consistency * avg_numeric_ratio * (1.0 + 0.1 * min(mode_count, 10))


def detect_delimiter(lines: list[str]) -> str | None:
    """在所有候选分隔符里选分数最高的。全部不可用返回 None。"""
    best: str | None = None
    best_score = 0.0
    for delim in DELIMITER_CANDIDATES:
        score = score_delimiter(lines, delim)
        if score > best_score:
            best_score = score
            best = delim
    return best


def find_data_start(lines: list[str], delimiter: str) -> int:
    """找到第一行真正的数据行,跳过注释与文本头。"""
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(COMMENT_PREFIXES):
            continue
        tokens = [t for t in split_line(stripped, delimiter) if t.strip() != ""]
        if len(tokens) >= 2 and sum(1 for t in tokens if is_numeric_token(t)) >= 2:
            return i
    return -1


def parse_numeric_table(text: str) -> ParsedTable | ParseFailure:
    """把文本解析成数值表。

    只读取列数一致的数据行;列数不符的行计入 dropped_rows。
    完全不产出任何数据行时返回 ParseFailure。
    """
    lines = text.splitlines()
    if not lines:
        return ParseFailure(reason="文件是空的", detail="splitlines() 返回 0 行")

    delimiter = detect_delimiter(lines)
    if delimiter is None:
        preview = [split_line(line, r"\s+") for line in lines[:10] if line.strip()]
        return ParseFailure(
            reason="没能识别出数据列",
            detail="所有候选分隔符(逗号、制表符、分号、空白)都未能切出稳定的两列以上数值",
            preview_rows=preview,
            detected_columns=max((len(r) for r in preview), default=0),
        )

    start = find_data_start(lines, delimiter)
    if start < 0:
        preview = [split_line(line, delimiter) for line in lines[:10] if line.strip()]
        return ParseFailure(
            reason="文件里没有找到数据行",
            detail=f"以分隔符 {delimiter!r} 切分后,没有任何一行包含两个以上数值",
            preview_rows=preview,
            detected_columns=max((len(r) for r in preview), default=0),
        )

    rows: list[list[float]] = []
    expected_cols = 0
    dropped = 0

    for line in lines[start:]:
        stripped = line.strip()
        if not stripped or stripped.startswith(COMMENT_PREFIXES):
            continue
        tokens = [t for t in split_line(stripped, delimiter) if t.strip() != ""]
        if not tokens:
            continue

        if expected_cols == 0:
            # 第一行数据确定列数标准
            expected_cols = len(tokens)

        if len(tokens) != expected_cols:
            dropped += 1
            continue

        try:
            values = [
                float(t) if is_numeric_token(t) else np.nan
                for t in tokens
            ]
        except ValueError:
            dropped += 1
            continue
        rows.append(values)

    if not rows:
        return ParseFailure(
            reason="数据行全部无法解析为数值",
            detail=f"数据从第 {start + 1} 行开始,但没有任何一行能读成数值",
            preview_rows=[split_line(line, delimiter) for line in lines[start : start + 10] if line.strip()],
            detected_columns=expected_cols,
        )

    # 列数不一致的尾部行会短一截,统一 pad 到最大列数
    width = max(len(r) for r in rows)
    padded = np.full((len(rows), width), np.nan, dtype=np.float64)
    for i, r in enumerate(rows):
        padded[i, : len(r)] = r

    return ParsedTable(
        data=padded,
        header_row_index=start,
        dropped_rows=dropped,
        delimiter=delimiter,
        encoding="",  # 由调用方填入实际编码
    )


def read_table(path: str | Path) -> tuple[ParsedTable, str] | ParseFailure:
    """读取文件并解析成数值表。返回 (表, 编码) 或 ParseFailure。"""
    p = Path(path)
    if not p.exists():
        return ParseFailure(reason="文件不存在", detail=str(p))
    if p.stat().st_size == 0:
        return ParseFailure(reason="文件是空的", detail=f"{p.name} 大小为 0 字节")

    try:
        raw = p.read_bytes()
    except OSError as exc:
        return ParseFailure(reason="无法读取文件", detail=f"{type(exc).__name__}: {exc}")

    text, encoding = decode_text(raw)
    result = parse_numeric_table(text)
    if isinstance(result, ParseFailure):
        return result
    result.encoding = encoding
    return result, encoding


def preview_columns(path: str | Path, max_rows: int = 10) -> list[list[str]]:
    """给列映射对话框用的原始预览(§42 的 Data Preview)。"""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return []
    text, _ = decode_text(raw)
    out: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(COMMENT_PREFIXES):
            continue
        out.append([t for t in re.split(r"[,\t;]|\s{1,}", stripped) if t != ""])
        if len(out) >= max_rows:
            break
    return out
