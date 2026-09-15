"""引擎 stdio 主入口(§11.2)。

协议:长度前缀帧。

    请求:  [4 字节大端长度][UTF-8 JSON]
            {"id": 1, "cmd": "datasets.import", "payload": {...}}
    响应:  [4 字节大端长度][UTF-8 JSON]
            {"id": 1, "ok": true, "data": ...}

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

# 冻结 PyInstaller/嵌入式 Python 的常见坑:先确认 stdout 是二进制管道
from .api.commands import Engine

_HEADER = struct.Struct(">I")


def read_frame(stream) -> dict | None:
    raw = stream.read(_HEADER.size)
    if not raw or len(raw) < _HEADER.size:
        return None  # EOF
    (length,) = _HEADER.unpack(raw)
    data = b""
    while len(data) < length:
        chunk = stream.read(length - len(data))
        if not chunk:
            return None
        data += chunk
    return json.loads(data.decode("utf-8"))


def write_frame(stream, obj: dict) -> None:
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    stream.write(_HEADER.pack(len(data)) + data)
    stream.flush()


def main() -> int:
    data_root = os.environ.get("XRD_DATA_ROOT")
    if not data_root:
        sys.stderr.write("XRD_DATA_ROOT 未设置\n")
        return 2

    # Windows 下 sys.stdin/stdout 默认是文本模式,必须换成二进制
    stdin = sys.stdin.buffer if hasattr(sys.stdin, "buffer") else sys.stdin
    stdout = sys.stdout.buffer if hasattr(sys.stdout, "buffer") else sys.stdout

    engine = Engine(data_root)
    sys.stderr.write(f"xrd-engine ready (data_root={data_root})\n")
    sys.stderr.flush()

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

        # 心跳:Rust 侧健康检查用
        if cmd == "ping":
            write_frame(stdout, {"id": req_id, "ok": True, "data": "pong"})
            continue

        try:
            result = engine.handle(cmd, payload)
        except Exception:  # noqa: BLE001 兜底,任何漏网异常都不该杀死进程
            sys.stderr.write(traceback.format_exc())
            sys.stderr.flush()
            result = {"ok": False, "error": "引擎内部错误", "detail": ""}

        write_frame(stdout, {"id": req_id, **result})

    engine.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
