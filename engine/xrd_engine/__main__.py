"""引擎 stdio 主入口(§11.2)。

协议:长度前缀帧。

    请求:  [4 字节大端长度][UTF-8 JSON]
            {"id": 1, "cmd": "datasets.import", "payload": {...}}
    响应:  [4 字节大端长度][UTF-8 JSON][可选二进制体]
            {"id": 1, "ok": true, "data": {...}}
            二进制体由 data.__bin__ 描述(§9.1 曲线信封),没有大数组时为空。

    **长度前缀只覆盖 JSON 头**,二进制体的长度写在 `data.__bin__.bytes`
    里。读取方先读头、再按需多读那一段 —— 这样旧读取方(只读头)依然
    能正确解析所有无二进制的帧。

Rust 侧(Tauri sidecar 监管)负责进程启动、请求路由与崩溃重启;
本文件只做「读帧 → 分发 → 写帧」,stderr 留给日志。

环境变量:
    XRD_DATA_ROOT   Data Store 根目录(必填)

开发期可另外用 api/dev_server.py 起 FastAPI 调试,生产不打包它。
"""

from __future__ import annotations

import json
import os
import struct
import sys
import traceback
from pathlib import Path

# 冻结 PyInstaller/嵌入式 Python 的常见坑:先确认 stdout 是二进制管道
from .api.commands import Engine
from .services.library_upgrade import prepare_library

_HEADER = struct.Struct(">I")


def _read_exact(stream, n: int) -> bytes | None:
    """读满 n 字节,读不满(EOF)返回 None。

    **不能只调一次 stream.read(n)**:管道不保证一次给满,短读会让长度
    前缀与帧体错位,而错位后读到的长度通常是个天文数字 —— 进程会一直
    等永远不来的数据,表现为界面永远转圈且没有任何错误信息。
    帧体的读取原本就循环了,头部却没有,这里统一。
    """
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def read_frame(stream) -> dict | None:
    raw = _read_exact(stream, _HEADER.size)
    if raw is None:
        return None  # EOF
    (length,) = _HEADER.unpack(raw)
    data = _read_exact(stream, length)
    if data is None:
        return None
    return json.loads(data.decode("utf-8"))



def _strip_binary(obj):
    """递归摘掉 `_binary`,并返回 (可序列化的对象, 二进制体)。

    **必须递归。** 二进制体不总在 `data` 顶层:`analysis.preview` 的曲线
    嵌在 `data.curve` 里,`datasets.curve` 才在 `data` 顶层。早先只检查
    顶层,结果 preview 一调用就让引擎在 `json.dumps` 上抛
    `TypeError: Object of type bytes is not JSON serializable` —— 而且是
    在主循环里抛,整个进程直接退出,前端看到的是"分析引擎意外退出"。

    进程内测试发现不了这个:它们直接调 `Engine.handle`,不经过
    `write_frame`。所以另有一组测试真的起子进程走完整协议
    (tests/test_protocol_e2e.py)。

    浅拷贝而非就地改:命令返回值会被复用(测试、dev_server 都直接读它)。
    """
    binary = b""
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if key == "_binary":
                if isinstance(value, (bytes, bytearray)):
                    binary = bytes(value)
                continue
            if isinstance(value, (dict, list)):
                cleaned, found = _strip_binary(value)
                if found:
                    binary = found
                out[key] = cleaned
            else:
                out[key] = value
        return out, binary
    if isinstance(obj, list):
        out_list = []
        for item in obj:
            cleaned, found = _strip_binary(item)
            if found:
                binary = found
            out_list.append(cleaned)
        return out_list, binary
    return obj, binary

def write_frame(stream, obj: dict) -> None:
    """写一帧:[u32 BE jsonLen][json][可选二进制体]。

    **二进制体是可选的,且为空时与旧格式逐字节相同。** 这一点是刻意的:
    绝大多数命令(建项目、改参数、查库)没有大数组,它们的帧不该因为
    这个改动而变长一个字节 —— 传输层一旦错位,表现是"整个软件打不开
    任何数据"而不是"性能没变好"。

    二进制体藏在 data 的 `_binary` 键里(由 curve_payload 放进去),
    序列化前必须摘掉 —— 否则 json.dumps 会因为 bytes 不可序列化而报错。
    """
    data_obj, binary = _strip_binary(obj)

    data = json.dumps(data_obj, ensure_ascii=False).encode("utf-8")
    stream.write(_HEADER.pack(len(data)) + data + binary)
    stream.flush()


def main() -> int:
    data_root = os.environ.get("XRD_DATA_ROOT")
    if not data_root:
        sys.stderr.write("XRD_DATA_ROOT 未设置\n")
        return 2

    # Windows 下 sys.stdin/stdout 默认是文本模式,必须换成二进制
    stdin = sys.stdin.buffer if hasattr(sys.stdin, "buffer") else sys.stdin
    stdout = sys.stdout.buffer if hasattr(sys.stdout, "buffer") else sys.stdout

    engine = None
    try:
        legacy = os.environ.get("XRD_LEGACY_DATA_ROOT")
        if legacy:
            prepare_library(Path(data_root), Path(legacy))
        engine = Engine(data_root, log_dir=os.environ.get("XRD_LOG_DIR"))
    except Exception as exc:
        startup_error = f"无法打开项目库 {data_root}：{exc}"
        print(startup_error, file=sys.stderr, flush=True)
    else:
        print(f"xrd-engine ready (data_root={data_root})", file=sys.stderr, flush=True)

    while True:
        try:
            request = read_frame(stdin)
        except (json.JSONDecodeError, struct.error):
            write_frame(stdout, {
                "id": -1, "ok": False,
                "error": "请求帧格式错误", "detail": "",
            })
            continue
        if request is None:
            break  # 正常退出

        req_id = request.get("id", -1)
        cmd = request.get("cmd", "")
        payload = request.get("payload") or {}

        if engine is None:
            write_frame(stdout, {"id": req_id, "ok": False, "error": startup_error})
            continue
        # 心跳:Rust 侧健康检查用
        if cmd == "ping":
            write_frame(stdout, {"id": req_id, "ok": True, "data": "pong"})
            continue

        try:
            result = engine.handle(cmd, payload)
        except Exception:
            sys.stderr.write(traceback.format_exc())
            sys.stderr.flush()
            result = {"ok": False, "error": "引擎内部错误", "detail": ""}

        write_frame(stdout, {"id": req_id, **result})

    if engine is not None:
        engine.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
