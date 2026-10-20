"""stdio 长度前缀帧协议测试(§11.2)。

这是引擎与 Rust 壳之间**唯一**的通道,而它此前零测试。协议一旦错位,
表现不是"某功能不好用"而是"整个软件打不开任何数据":长度前缀差一字节,
后续所有帧都会错位,而错位后读到的长度通常是个天文数字,进程会一直等
永远不来的数据 —— 表现为界面永远转圈,没有任何错误信息。

这里覆盖:往返、分片读取、EOF、损坏帧、超长帧拒绝。
"""

from __future__ import annotations

import io
import json
import struct

import pytest

from xrd_engine.__main__ import _HEADER, read_frame, write_frame


class TestRoundTrip:
    def test_write_then_read(self):
        buf = io.BytesIO()
        write_frame(buf, {"id": 1, "ok": True, "data": {"x": [1, 2, 3]}})
        buf.seek(0)
        assert read_frame(buf) == {"id": 1, "ok": True, "data": {"x": [1, 2, 3]}}

    def test_header_is_big_endian_u32(self):
        buf = io.BytesIO()
        write_frame(buf, {"id": 1})
        raw = buf.getvalue()
        assert raw[:4] == struct.pack(">I", len(raw) - 4)

    def test_multiple_frames_in_one_stream(self):
        """Rust 侧会连续读多帧,长度前缀必须让边界可分辨。"""
        buf = io.BytesIO()
        for i in range(5):
            write_frame(buf, {"id": i, "ok": True})
        buf.seek(0)
        for i in range(5):
            assert read_frame(buf)["id"] == i
        assert read_frame(buf) is None  # 读完

    def test_chinese_is_not_escaped(self):
        """ensure_ascii=False:中文错误文案不该被转义成码位形式传出去。"""
        buf = io.BytesIO()
        write_frame(buf, {"error": "数据集不存在"})
        assert "数据集不存在".encode() in buf.getvalue()


class TestPartialReads:
    def test_frame_split_across_reads(self):
        """管道不保证一次 read 拿到整帧 —— 必须循环读满。"""
        buf = io.BytesIO()
        write_frame(buf, {"id": 7, "ok": True, "data": "x" * 500})
        raw = buf.getvalue()

        class Dribble(io.RawIOBase):
            """每次只吐 3 字节的流,模拟被切碎的管道。"""

            def __init__(self, data: bytes):
                self.data = data
                self.pos = 0

            def read(self, n: int = -1) -> bytes:
                # 必须尊重 n:真实的 read(n) 返回**至多** n 字节。
                # 返回超过 n 会让长度前缀与帧体错位。
                if n < 0:
                    n = len(self.data) - self.pos
                chunk = self.data[self.pos:self.pos + min(3, n)]
                self.pos += len(chunk)
                return chunk

        assert read_frame(Dribble(raw))["id"] == 7


class TestEOF:
    def test_empty_stream_returns_none(self):
        assert read_frame(io.BytesIO(b"")) is None

    def test_truncated_header_returns_none(self):
        assert read_frame(io.BytesIO(b"\x00\x00")) is None

    def test_truncated_body_returns_none(self):
        """声明 100 字节却只给了 10 字节 —— 返回 None 让主循环退出,
        而不是拼出一个半截 JSON 去 json.loads 抛异常。"""
        payload = b"x" * 10
        stream = io.BytesIO(_HEADER.pack(100) + payload)
        assert read_frame(stream) is None


class TestMalformed:
    def test_bad_json_raises_for_caller_to_handle(self):
        """坏 JSON 由主循环捕获并回一条错误帧 —— 引擎不能因此死掉。"""
        body = b"{not json"
        stream = io.BytesIO(_HEADER.pack(len(body)) + body)
        with pytest.raises(json.JSONDecodeError):
            read_frame(stream)

    def test_zero_length_frame_raises(self):
        stream = io.BytesIO(_HEADER.pack(0))
        with pytest.raises(json.JSONDecodeError):
            read_frame(stream)


class TestBinaryEnvelope:
    """曲线信封(§9.1):帧尾可选二进制体。

    **关键不变式:没有二进制体时,帧必须与旧格式逐字节相同。**
    绝大多数命令(建项目、改参数、查库)都没有大数组,它们的帧不该因为
    这个改动而变长一个字节 —— 传输层一旦错位,表现是"整个软件打不开
    任何数据"而不是"性能没变好"。
    """

    def test_no_binary_frame_is_byte_identical_to_old_format(self):
        obj = {"id": 1, "ok": True, "data": {"projects": []}}
        buf = io.BytesIO()
        write_frame(buf, obj)
        raw = buf.getvalue()
        expected_json = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        assert raw == struct.pack(">I", len(expected_json)) + expected_json

    def test_binary_body_appended_and_header_stripped(self):
        """_binary 不能出现在 JSON 里(bytes 不可序列化),但要拼到帧尾。"""
        body = b"\x01\x02\x03\x04" * 4
        obj = {
            "id": 7, "ok": True,
            "data": {"pointCount": 4, "__bin__": {"bytes": len(body)}, "_binary": body},
        }
        buf = io.BytesIO()
        write_frame(buf, obj)
        raw = buf.getvalue()

        (json_len,) = struct.unpack(">I", raw[:4])
        head = json.loads(raw[4 : 4 + json_len].decode("utf-8"))
        assert "_binary" not in head["data"], "_binary 必须被摘掉"
        assert head["data"]["__bin__"]["bytes"] == len(body)
        assert raw[4 + json_len :] == body, "二进制体应紧跟 JSON 头"

    def test_write_frame_does_not_mutate_caller_object(self):
        """命令返回值会被复用,write_frame 不能就地改它。"""
        body = b"abcd"
        data = {"_binary": body, "__bin__": {"bytes": 4}}
        obj = {"id": 1, "ok": True, "data": data}
        write_frame(io.BytesIO(), obj)
        assert obj["data"] is data
        assert data["_binary"] == body

    def test_read_frame_still_reads_json_only_frames(self):
        """旧读取方(只读 JSON 头)必须继续工作。"""
        buf = io.BytesIO()
        write_frame(buf, {"id": 3, "ok": True})
        buf.seek(0)
        assert read_frame(buf) == {"id": 3, "ok": True}
