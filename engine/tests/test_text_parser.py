"""解析器测试(§7.3)。

真实仪器文件格式很杂,这里按「格式 × 分隔符 × 编码 × 垃圾头」的组合
覆盖,并且专门测试那些会导致静默错误的坏输入。
"""

from __future__ import annotations

import numpy as np
import pytest

from xrd_engine.importers.text_parser import (
    ParsedTable,
    ParseFailure,
    decode_text,
    detect_delimiter,
    is_numeric_token,
    parse_numeric_table,
    read_table,
)


class TestIsNumericToken:
    @pytest.mark.parametrize("token", [
        "1", "1.0", "-1.5", "+2.5", ".5", "1.", "1e3", "1E3",
        "1.5e-3", "-2.5E+10", "0", "0.0",
    ])
    def test_valid_numbers(self, token):
        assert is_numeric_token(token)

    @pytest.mark.parametrize("token", [
        "", " ", "abc", "1.2.3", "1,5", "NA", "nan", "-", "null",
        "1e", "e3", "--1", "1 2", "#", "2θ",
    ])
    def test_invalid_tokens(self, token):
        assert not is_numeric_token(token)

    def test_whitespace_stripped(self):
        assert is_numeric_token("  1.5  ")


class TestDecodeText:
    def test_utf8(self):
        raw = "2θ 强度\n10.0 100\n".encode()
        text, enc = decode_text(raw)
        assert "强度" in text
        assert enc == "utf-8-sig"

    def test_utf8_bom(self):
        raw = "﻿10.0 100\n".encode()
        text, _enc = decode_text(raw)
        # BOM 必须被吃掉,否则第一列数字解析失败
        assert not text.startswith("﻿")
        assert text.startswith("10.0")

    def test_gb18030_fallback(self):
        raw = "角度 强度\n10.0 100\n".encode("gb18030")
        text, enc = decode_text(raw)
        assert "角度" in text
        assert enc == "gb18030"

    def test_latin1_fallback(self):
        raw = bytes([0xff, 0xfe, 0x41])
        _text, enc = decode_text(raw)
        assert enc == "latin-1"


class TestDetectDelimiter:
    def test_comma(self):
        lines = ["10.0,100", "10.02,120", "10.04,110"]
        assert detect_delimiter(lines) == ","

    def test_tab(self):
        lines = ["10.0\t100", "10.02\t120", "10.04\t110"]
        assert detect_delimiter(lines) == "\t"

    def test_semicolon(self):
        lines = ["10.0;100", "10.02;120", "10.04;110"]
        assert detect_delimiter(lines) == ";"

    def test_whitespace(self):
        lines = ["10.0 100", "10.02 120", "10.04 110"]
        assert detect_delimiter(lines) == r"\s+"

    def test_multiple_spaces(self):
        lines = ["10.0    100", "10.02   120", "10.04   110"]
        assert detect_delimiter(lines) == r"\s+"

    def test_no_delimiter_returns_none(self):
        lines = ["hello world", "foo bar"]
        assert detect_delimiter(lines) is None

    def test_inconsistent_columns_rejected(self):
        """列数忽多忽少的分隔符不该被选中。"""
        lines = ["1,2,3,4,5", "1", "1,2", "1,2,3,4,5,6,7"]
        delim = detect_delimiter(lines)
        # 该数据没有任何分隔符能给出稳定列数
        assert delim is None

    def test_single_column_returns_none(self):
        lines = ["100", "120", "110"]
        assert detect_delimiter(lines) is None


class TestParseNumericTable:
    def test_simple_space_separated(self):
        text = "10.0 100\n10.02 120\n10.04 110\n"
        result = parse_numeric_table(text)
        assert isinstance(result, ParsedTable)
        assert result.data.shape == (3, 2)
        assert result.data[0, 0] == pytest.approx(10.0)
        assert result.dropped_rows == 0

    def test_skips_comment_lines(self):
        text = "# comment\n10.0 100\n# another\n10.02 120\n"
        result = parse_numeric_table(text)
        assert isinstance(result, ParsedTable)
        assert result.data.shape == (2, 2)

    def test_skips_text_header(self):
        text = (
            "XRD Measurement Report\n"
            "Instrument: Bruker D8\n"
            "Anode: Cu\n"
            "10.0 100\n"
            "10.02 120\n"
        )
        result = parse_numeric_table(text)
        assert isinstance(result, ParsedTable)
        assert result.data.shape == (2, 2)
        assert result.header_row_index == 3

    def test_semicolon_prefix_comments(self):
        text = "; header\n; units\n10.0;100\n10.02;120\n"
        result = parse_numeric_table(text)
        assert isinstance(result, ParsedTable)
        assert result.data.shape == (2, 2)

    def test_missing_values_become_nan(self):
        text = "10.0 100\n10.02 -\n10.04 110\n"
        result = parse_numeric_table(text)
        assert isinstance(result, ParsedTable)
        assert np.isnan(result.data[1, 1])
        # 缺值是 NaN 而非丢行,由 Validate 层统计
        assert result.data.shape == (3, 2)

    def test_nan_token(self):
        text = "10.0 100\n10.02 NaN\n"
        result = parse_numeric_table(text)
        assert isinstance(result, ParsedTable)
        assert np.isnan(result.data[1, 1])

    def test_scientific_notation(self):
        text = "10.000,1.2345E+04\n10.020,1.3456e4\n"
        result = parse_numeric_table(text)
        assert isinstance(result, ParsedTable)
        assert result.data[0, 1] == pytest.approx(12345.0)

    def test_inconsistent_row_dropped(self):
        text = "10.0 100\n10.02 120 999 888\n10.04 110\n"
        result = parse_numeric_table(text)
        assert isinstance(result, ParsedTable)
        assert result.dropped_rows == 1
        assert result.data.shape[0] == 2

    def test_no_data_returns_failure(self):
        text = "This is just prose.\nNo numbers here.\n"
        result = parse_numeric_table(text)
        assert isinstance(result, ParseFailure)
        assert "没有找到数据行" in result.reason or "识别" in result.reason

    def test_empty_file(self):
        result = parse_numeric_table("")
        assert isinstance(result, ParseFailure)
        assert "空" in result.reason

    def test_single_column_returns_failure(self):
        text = "100\n120\n110\n"
        result = parse_numeric_table(text)
        assert isinstance(result, ParseFailure)

    def test_failure_carries_preview(self):
        """列映射对话框需要原始预览数据。"""
        text = "col_a col_b\nfoo bar\nbaz qux\n"
        result = parse_numeric_table(text)
        assert isinstance(result, ParseFailure)
        assert result.preview_rows is not None
        assert result.detected_columns >= 2

    def test_trailing_short_rows_padded(self):
        text = "10.0 100\n10.02 120\n10.04\n"
        result = parse_numeric_table(text)
        assert isinstance(result, ParsedTable)
        assert result.data.shape == (2, 2)

    def test_header_and_blank_lines(self):
        text = "\n\n# h\n10.0 100\n\n10.02 120\n\n"
        result = parse_numeric_table(text)
        assert isinstance(result, ParsedTable)
        assert result.data.shape == (2, 2)

    def test_three_columns_preserved(self):
        text = "10.0 100 5.0\n10.02 120 5.1\n"
        result = parse_numeric_table(text)
        assert isinstance(result, ParsedTable)
        assert result.data.shape == (2, 3)

    def test_negative_values(self):
        text = "10.0 -100.5\n10.02 -120.7\n"
        result = parse_numeric_table(text)
        assert isinstance(result, ParsedTable)
        assert result.data[0, 1] == pytest.approx(-100.5)


class TestReadTable:
    def test_real_fixture_xy(self, fixtures_dir):
        path = fixtures_dir / "lfp_500c_scan01.xy"
        if not path.exists():
            pytest.skip("fixture 未生成,请先运行 scripts/make_fixtures.py")
        result = read_table(path)
        assert not isinstance(result, ParseFailure)
        table, _encoding = result
        assert table.data.shape[0] == 3501
        assert table.data.shape[1] == 2
        # §4.3 规定的角度范围
        assert table.data[0, 0] == pytest.approx(10.0)
        assert table.data[-1, 0] == pytest.approx(80.0)

    def test_missing_file(self, tmp_path):
        result = read_table(tmp_path / "nope.xy")
        assert isinstance(result, ParseFailure)
        assert "不存在" in result.reason

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.xy"
        p.write_bytes(b"")
        result = read_table(p)
        assert isinstance(result, ParseFailure)
        assert "空" in result.reason

    def test_gb18030_fixture(self, fixtures_dir):
        path = fixtures_dir / "semicolon_gb.csv"
        if not path.exists():
            pytest.skip("fixture 未生成")
        result = read_table(path)
        assert not isinstance(result, ParseFailure)
        table, encoding = result
        assert encoding == "gb18030"
        assert table.data.shape[1] == 2

    def test_messy_header_fixture(self, fixtures_dir):
        path = fixtures_dir / "messy_header.csv"
        if not path.exists():
            pytest.skip("fixture 未生成")
        result = read_table(path)
        assert not isinstance(result, ParseFailure)
        table, _ = result
        assert table.data.shape[0] == 500
        assert table.header_row_index >= 5

    def test_unparseable_fixture(self, fixtures_dir):
        path = fixtures_dir / "unparseable.txt"
        if not path.exists():
            pytest.skip("fixture 未生成")
        result = read_table(path)
        assert isinstance(result, ParseFailure)
