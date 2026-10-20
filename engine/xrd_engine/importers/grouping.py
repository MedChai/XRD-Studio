"""按文件名把一批谱文件分组成"实验"。

用户的真实数据是按文件夹组织的:一个文件夹 = 一次实验,里面的文件是
同一个样品的多次扫描或不同条件。文件名通常形如:

    GENBB-1-14-20260915-125048.txt
    GENBB-1-15-20260915-130209.txt
    GENBB-1-16-20260915-131330.txt
    GENBB-2-06-20260915-160058.txt

前三个是同一个实验(GENBB-1),第四个是另一个(GENBB-2)。

**算法:先求全组公共前缀,再在第一个"有意义的差异位"上分。**

这个文件夹内的文件共享前缀 `GENBB`,下一个 token 位置上出现 `1/1/1/2`
—— 这个位置就是实验编号,按它分组即可。

**为什么不做"逐个剥掉尾部数字段"。** 那个思路在 `GENBB-1-14-...` 上必然
失败:`1` 是实验编号必须留,`14` 是扫描序号该剥,而两者都是纯数字段,单看
一个文件名无法区分。早先的实现就剥掉了 `1`,把两个实验并成了一个。

**为什么差异位要判"是不是尾巴"。** 若差异位上的取值全是 `scan01/scan02`
这种明确的序号,说明它们不是不同的实验,只是同一实验的多次扫描 ——
此时应当合成一组而不是拆开。判据是 `_is_tail`。

**分组只是建议。** 结果必须先在 UI 里展示、允许改名与拖动,确认后才落库:
命名千奇百怪,自动分组猜对的收益只是省一次点击,猜错的代价是数据已经
落库、用户很难发现哪几条归错了。所以不确定时**宁可分得更细**。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# 文件名里的分隔符:短横线、下划线、空格、点
_SEP = r"[-_. ]"

# 日期 / 时间戳 / 长编号:20260915、130209
_DATEISH = re.compile(r"^\d{6,}$")

# scan01 / run2 / s3 这类带前缀的序号
_INDEXED = re.compile(r"^(scan|run|s|r)\d+$", re.IGNORECASE)

# 纯数字
_NUMERIC = re.compile(r"^\d+$")


@dataclass
class ScanGroup:
    """一组建议归到一起的文件。"""

    name: str
    """建议的实验名(用户可改)。"""
    paths: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"name": self.name, "paths": list(self.paths)}


def stem_of(path: str | Path) -> str:
    return Path(path).stem


def _tokens(stem: str) -> list[str]:
    return [t for t in re.split(_SEP, stem) if t]


def _is_tail(token: str) -> bool:
    """这个 token 是不是"序号/日期/时间"这类可变尾巴。"""
    return bool(_DATEISH.match(token) or _INDEXED.match(token))


def _trim_tail(stem: str) -> str:
    """去掉末尾明确的序号/日期段,给单文件组一个体面的默认名。

    只剥**有明确标记**的尾巴(`scan01`、`20260915`、`125048`),不碰孤立的
    短数字段 —— 那个可能是实验编号。
    """
    tokens = _tokens(stem)
    while len(tokens) > 1 and _is_tail(tokens[-1]):
        tokens.pop()
    return "-".join(tokens) if tokens else stem


def _common_token_count(token_lists: list[list[str]]) -> int:
    """逐 token 的公共前缀长度。"""
    shortest = min(len(t) for t in token_lists)
    for i in range(shortest):
        token = token_lists[0][i]
        if any(t[i] != token for t in token_lists):
            return i
    return shortest


def _char_prefix(stems: list[str]) -> str:
    """字符级公共前缀,用于 token 级完全没有公共部分的场合。"""
    first = min(stems, key=len)
    for i, ch in enumerate(first):
        if any(len(s) <= i or s[i] != ch for s in stems):
            return first[:i].rstrip("-_ .")
    return first


def _leading_numeric_run(stem: str) -> str:
    """文件名开头的纯数字段(如 `1-14-x` 的 `1`)。

    字符级前缀可能停在数字中间(`GENBB-`),补回这段以免丢掉实验编号。
    """
    run: list[str] = []
    for token in _tokens(stem):
        if _NUMERIC.match(token):
            run.append(token)
        else:
            break
    return "-".join(run)


def common_prefix_name(stems: list[str]) -> str:
    """一组文件名的实验名:组内公共前缀。"""
    if not stems:
        return "未命名实验"
    if len(stems) == 1:
        return _trim_tail(stems[0])

    token_lists = [_tokens(s) for s in stems]
    n = _common_token_count(token_lists)
    if n:
        return "-".join(token_lists[0][:n])

    name = _char_prefix(stems)
    run = _leading_numeric_run(stems[0])
    if run and not name.endswith(run):
        name = f"{name.rstrip('-_ .')}-{run}"
    return name or _trim_tail(stems[0])


def group_paths(paths: list[str]) -> list[ScanGroup]:
    """把文件路径按"实验"分组。

    求全组公共前缀 → 看第一个差异位 → 若该位置全是序号尾巴则不分,
    否则按该位置的取值分组。
    """
    if not paths:
        return []

    ordered = sorted(paths, key=lambda p: stem_of(p))
    stems = [stem_of(p) for p in ordered]
    token_lists = [_tokens(s) for s in stems]
    n = _common_token_count(token_lists)

    # 差异位:所有文件名都还有第 n 个 token 时才有得比
    if n < min(len(t) for t in token_lists):
        discriminators = [t[n] for t in token_lists]
        if not all(_is_tail(d) for d in discriminators):
            buckets: dict[str, list[str]] = {}
            for path, token_list in zip(ordered, token_lists, strict=True):
                key = "-".join(token_list[: n + 1])
                buckets.setdefault(key, []).append(path)
            if len(buckets) > 1:
                return [
                    ScanGroup(name=key, paths=sorted(bucket))
                    for key, bucket in sorted(buckets.items())
                ]

    # 差异位是序号尾巴,或压根没有差异位 —— 整批算一个实验
    return [ScanGroup(name=common_prefix_name(stems), paths=ordered)]


def scan_folder(folder: str | Path, extensions: set[str]) -> list[str]:
    """列出文件夹下(不递归)所有支持扩展名的文件,按名排序。

    不递归是刻意的:递归会把子文件夹里的另一批数据混进来,而用户对
    "这个文件夹"的心智模型就是不包含子目录。
    """
    root = Path(folder)
    if not root.is_dir():
        return []
    out = [
        str(p)
        for p in root.iterdir()
        if p.is_file() and p.suffix.lower() in extensions
    ]
    return sorted(out)
