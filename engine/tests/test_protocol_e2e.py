"""端到端协议测试:真的起一个引擎子进程,用真实帧跟它说话。

这比在进程内调 write_frame/read_frame 强 —— 它覆盖 `__main__.py` 的
主循环(读帧 → 分发 → 写帧),而那正是 Rust 壳实际对接的东西。
进程内测试全绿但主循环坏了,表现就是"软件完全打不开数据"。
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).resolve().parent.parent
PYTHON = ENGINE_DIR / ".venv" / "Scripts" / "python.exe"


def _read_exact(stream, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def _send(proc, obj: dict) -> None:
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    proc.stdin.write(struct.pack(">I", len(data)) + data)
    proc.stdin.flush()


def _find_bin_meta(node):
    """在响应里递归找 __bin__ 描述。

    **必须递归**:`datasets.curve` 把信封放在 `data` 顶层,而
    `analysis.preview` 放在 `data.curve` 里。只查顶层的话,preview 的
    二进制体会被当成"没有二进制",于是它留在管道里污染下一帧 ——
    后续所有帧全部错位。这正是 Rust 侧 read_frame 也要递归的原因。
    """
    if isinstance(node, dict):
        meta = node.get("__bin__")
        if isinstance(meta, dict) and meta.get("bytes"):
            return meta
        for value in node.values():
            found = _find_bin_meta(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_bin_meta(item)
            if found:
                return found
    return None


def _recv(proc):
    """读一帧,返回 (json 头, 二进制体)。二进制体长度由 __bin__.bytes 决定。"""
    raw = _read_exact(proc.stdout, 4)
    if raw is None:
        raise AssertionError("引擎没有响应就退出了")
    (json_len,) = struct.unpack(">I", raw)
    head_bytes = _read_exact(proc.stdout, json_len)
    head = json.loads(head_bytes.decode("utf-8"))
    meta = _find_bin_meta(head)
    bin_len = int(meta["bytes"]) if meta else 0
    body = _read_exact(proc.stdout, bin_len) if bin_len else b""
    return head, body or b""


@pytest.fixture
def engine_proc(tmp_path):
    """起一个真实引擎子进程,数据根指向临时目录。"""
    if not PYTHON.exists():
        pytest.skip("引擎 venv 未就绪")
    env = {**os.environ, "XRD_DATA_ROOT": str(tmp_path / "store")}
    proc = subprocess.Popen(
        [str(PYTHON), "-m", "xrd_engine"],
        cwd=str(ENGINE_DIR),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    yield proc
    proc.kill()
    proc.wait(timeout=10)


class TestRealEngineProcess:
    def test_ping_round_trip(self, engine_proc):
        """最基本的往返:证明主循环活着且帧对齐。"""
        _send(engine_proc, {"id": 1, "cmd": "ping", "payload": {}})
        head, body = _recv(engine_proc)
        assert head["ok"] is True and head["data"] == "pong"
        assert body == b"", "ping 不该有二进制体"

    def test_consecutive_frames_stay_aligned(self, engine_proc):
        """连续多帧必须逐一对齐。

        长度前缀算错一字节,后续所有帧都会错位 —— 而错位后读到的长度
        通常是个天文数字,进程会一直等永远不来的数据。
        """
        for i in range(5):
            _send(engine_proc, {"id": i, "cmd": "ping", "payload": {}})
        for i in range(5):
            head, _ = _recv(engine_proc)
            assert head["id"] == i

    def test_error_frame_has_no_binary(self, engine_proc):
        _send(engine_proc, {"id": 9, "cmd": "no.such.command", "payload": {}})
        head, body = _recv(engine_proc)
        assert head["ok"] is False
        assert body == b""

    def test_curve_envelope_over_the_wire(self, engine_proc, fixtures_dir):
        """曲线经真实帧传输:JSON 头不含数组,二进制体长度自洽。

        这是 §9.1 的核心断言 —— 而且它同时验证了**后续帧仍然对齐**
        (二进制体被正确读掉,没有污染下一帧)。
        """
        path = fixtures_dir / "lfp_500c_scan01.xy"
        if not path.exists():
            pytest.skip("fixture 未生成")

        _send(engine_proc, {"id": 1, "cmd": "projects.create",
                            "payload": {"name": "P"}})
        head, _ = _recv(engine_proc)
        pid = head["data"]["projectId"]

        _send(engine_proc, {"id": 2, "cmd": "datasets.import", "payload": {
            "paths": [str(path)], "project_id": pid,
            "new_sample": {"name": "S"},
        }})
        head, _ = _recv(engine_proc)
        ds_id = head["data"]["imported"][0]["id"]

        _send(engine_proc, {"id": 3, "cmd": "datasets.curve", "payload": {"id": ds_id}})
        head, body = _recv(engine_proc)
        assert head["ok"], head
        data = head["data"]

        # JSON 头里没有大数组
        assert "x" not in data and "y" not in data
        assert "_binary" not in data, "_binary 不该出现在传输的 JSON 里"
        meta = data["__bin__"]
        assert meta["layout"] == ["x", "y"]
        assert len(body) == meta["bytes"] == meta["n"] * 2 * 4

        # 解出来的曲线与点数一致
        import numpy as np

        n = meta["n"]
        x = np.frombuffer(body[: n * 4], dtype="<f4")
        y = np.frombuffer(body[n * 4 :], dtype="<f4")
        assert x.size == y.size == n == data["pointCount"]
        assert abs(float(x[0]) - 10.0) < 0.01
        assert abs(float(x[-1]) - 80.0) < 0.01

        # **关键**:带二进制体的帧之后,协议仍对齐
        _send(engine_proc, {"id": 4, "cmd": "ping", "payload": {}})
        head, body2 = _recv(engine_proc)
        assert head["id"] == 4 and head["data"] == "pong"
        assert body2 == b""

    def test_preview_envelope_includes_background(self, engine_proc, fixtures_dir):
        """背景曲线与主曲线等长,放进同一个信封 —— 后续帧仍要对齐。"""
        path = fixtures_dir / "lfp_500c_scan01.xy"
        if not path.exists():
            pytest.skip("fixture 未生成")
        _send(engine_proc, {"id": 1, "cmd": "projects.create",
                            "payload": {"name": "P"}})
        pid = _recv(engine_proc)[0]["data"]["projectId"]
        _send(engine_proc, {"id": 2, "cmd": "datasets.import", "payload": {
            "paths": [str(path)], "project_id": pid, "new_sample": {"name": "S"},
        }})
        ds_id = _recv(engine_proc)[0]["data"]["imported"][0]["id"]

        _send(engine_proc, {"id": 3, "cmd": "analysis.preview", "payload": {
            "dataset_id": ds_id,
            "pipeline": {"background": {"enabled": True, "method": "asls"}},
        }})
        head, body = _recv(engine_proc)
        assert head["ok"], head
        meta = head["data"]["curve"]["__bin__"]
        assert meta["layout"] == ["x", "y", "background"]
        assert len(body) == meta["bytes"]

        # _binary 不能漏进 JSON —— 它是 bytes,json.dumps 会抛 TypeError,
        # 而且是在主循环里抛,整个引擎进程直接退出。这条断言锁住那个
        # 曾经的崩溃(嵌套在 data.curve 里时被漏掉)。
        assert "_binary" not in head["data"]["curve"]

        _send(engine_proc, {"id": 4, "cmd": "ping", "payload": {}})
        assert _recv(engine_proc)[0]["id"] == 4
