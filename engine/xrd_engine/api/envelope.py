"""曲线数据的二进制信封(§9.1)。

## 为什么要做

曲线是唯一的大数组。当前它走 JSON `number[]`:3501 个点要序列化两个
数组,5000 点降采样后仍有约 100 KB 文本,而且这条路径要**穿过四次
JSON 序列化**(前端 stringify → Rust from_str → Rust to_vec → 引擎
json.loads),回程再来四次。滑块拖动时每秒数十次往返,直接击穿 §9.4
的"滑块→图 < 100ms"。

Float32 交错数组把这条路径压成一次内存拷贝:

    {"kind": "curve", "n": 3501} + f32[n][2]

## 协议

帧格式仍是长度前缀,只是 JSON 头之后可以跟一段二进制体::

    [u32 BE jsonLen][json header][binary body]

**这个格式是旧格式的严格超集**:binary body 为空时,字节序列与
`[u32 jsonLen][json]` 完全一致。所以没有二进制体的命令(绝大多数)
不需要任何改动,旧读取方也不会因此错位 —— 这是刻意设计的,因为
传输层一旦错位,表现是"整个软件打不开任何数据"而不是"性能没变好"。

JSON 头里的 `__bin__` 描述二进制体怎么切分::

    "__bin__": {"bytes": 28008, "n": 3501, "layout": ["x", "y"]}

`layout` 是有序的:第 k 个字段对应二进制体里第 k 段连续 f32[n]。
读取方据此切片,不需要猜。
"""

from __future__ import annotations

from typing import Any

import numpy as np

# 二进制体在 JSON 头里的描述键。用双下划线前缀,与业务字段不可能撞名。
BIN_KEY = "__bin__"


def pack_arrays(arrays: list[tuple[str, np.ndarray]]) -> tuple[dict[str, Any], bytes]:
    """把若干等长数组打包成 (描述头, 二进制体)。

    统一转 float32:曲线数据是仪器读数的显示副本,float32 的 7 位有效
    数字远超 2θ 与强度所需的显示精度(§4.4:2θ 显示 3 位小数)。用
    float64 会让传输量翻倍而用户看不出差别。

    调用方必须保证所有数组等长 —— 不等长的话 `layout` 无法用单一的
    `n` 描述,切片会错位。这里显式检查而不是静默截断。
    """
    if not arrays:
        return {}, b""

    lengths = {name: int(np.asarray(arr).size) for name, arr in arrays}
    if len(set(lengths.values())) != 1:
        raise ValueError(f"二进制信封要求所有数组等长,实际 {lengths}")

    n = next(iter(lengths.values()))
    layout = [name for name, _ in arrays]
    chunks = [
        np.ascontiguousarray(np.asarray(arr, dtype=np.float32)).tobytes()
        for _, arr in arrays
    ]
    body = b"".join(chunks)
    header = {
        "bytes": len(body),
        "n": n,
        "layout": layout,
    }
    return header, body


def unpack_arrays(header: dict[str, Any], body: bytes) -> dict[str, np.ndarray]:
    """按描述头把二进制体切回各字段。读取方(Rust/前端)的同构实现。

    长度对不上时抛错而不是尽力而为:截断的曲线画出来是"谱图少了后半段",
    用户完全看不出是传输出了问题。
    """
    n = int(header.get("n") or 0)
    layout = list(header.get("layout") or [])
    expected = n * len(layout) * 4
    if len(body) != expected:
        raise ValueError(
            f"二进制体长度不符:期望 {expected} 字节,实际 {len(body)}"
        )
    out: dict[str, np.ndarray] = {}
    stride = n * 4
    for i, name in enumerate(layout):
        chunk = body[i * stride : (i + 1) * stride]
        out[name] = np.frombuffer(chunk, dtype="<f4")
    return out
