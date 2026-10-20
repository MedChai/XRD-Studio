"""曲线二进制信封(api/envelope.py,§9.1)。

信封的价值是省掉 JSON 文本往返,但它的风险也在这里:一旦切片错位,
用户看到的不是报错而是**一张画错的谱图** —— 曲线少半段、或者 x 与 y
串位。所以这里的重点是把"错位"这条路堵死。
"""

from __future__ import annotations

import numpy as np
import pytest

from xrd_engine.api.envelope import BIN_KEY, pack_arrays, unpack_arrays


class TestRoundTrip:
    def test_round_trip_is_exact(self):
        x = np.linspace(10.0, 80.0, 3501)
        y = np.sin(x) * 1000 + 5000
        header, body = pack_arrays([("x", x), ("y", y)])
        got = unpack_arrays(header, body)
        # float32 精度内完全一致
        assert np.allclose(got["x"], x, atol=1e-4)
        assert np.allclose(got["y"], y, rtol=1e-6)

    def test_header_describes_the_body(self):
        x = np.arange(10.0)
        y = np.arange(10.0) * 2
        header, body = pack_arrays([("x", x), ("y", y)])
        assert len(body) == header["bytes"]
        assert header["n"] == 10
        assert header["layout"] == ["x", "y"]
        assert header["bytes"] == 10 * 2 * 4  # 两个 f32 数组

    def test_order_is_preserved(self):
        """layout 是有序的:第 k 个字段对应第 k 段连续 f32[n]。

        顺序错了 x 会读到 y 的值 —— 图能画出来,但完全是错的。
        """
        a = np.array([1.0, 2.0])
        b = np.array([9.0, 8.0])
        header, body = pack_arrays([("x", a), ("y", b)])
        got = unpack_arrays(header, body)
        assert list(got["x"]) == [1.0, 2.0]
        assert list(got["y"]) == [9.0, 8.0]

    def test_three_arrays_background(self):
        x = np.arange(5.0)
        y = np.arange(5.0) + 10
        bg = np.arange(5.0) + 100
        header, body = pack_arrays([("x", x), ("y", y), ("background", bg)])
        got = unpack_arrays(header, body)
        assert list(got["background"]) == list(bg)


class TestSafety:
    def test_empty_arrays_gives_empty_body(self):
        header, body = pack_arrays([])
        assert header == {} and body == b""

    def test_unequal_lengths_rejected(self):
        """不等长无法用单一的 n 描述,切片会错位 —— 必须显式拒绝。"""
        with pytest.raises(ValueError, match="等长"):
            pack_arrays([("x", np.arange(10.0)), ("y", np.arange(9.0))])

    def test_truncated_body_rejected(self):
        """截断的曲线画出来是"谱图少了后半段",用户看不出是传输问题。"""
        header, body = pack_arrays([("x", np.arange(10.0))])
        with pytest.raises(ValueError, match="长度不符"):
            unpack_arrays(header, body[:-4])
        assert header["n"] == 10

    def test_oversized_body_rejected(self):
        header, body = pack_arrays([("x", np.arange(10.0))])
        with pytest.raises(ValueError, match="长度不符"):
            unpack_arrays(header, body + b"\x00\x00\x00\x00")

    def test_float32_is_enough_precision(self):
        """float32 的 7 位有效数字远超 2θ / 强度的显示精度(§4.4)。

        用 float64 会让传输量翻倍而用户看不出差别 —— 这条测试把"看不出
        差别"钉住:2θ 保留 3 位小数、强度取整,都必须一致。
        """
        x = np.array([18.645678901234, 64.21987654321])
        y = np.array([75417.123456, 10172.987654])
        header, body = pack_arrays([("x", x), ("y", y)])
        got = unpack_arrays(header, body)
        for i in range(2):
            assert round(float(got["x"][i]), 3) == round(float(x[i]), 3)
            assert round(float(got["y"][i])) == round(float(y[i]))

    def test_bin_key_is_collision_proof(self):
        """描述键用双下划线前缀,与业务字段不可能撞名。"""
        assert BIN_KEY.startswith("__") and BIN_KEY.endswith("__")
