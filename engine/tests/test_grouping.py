"""文件名分组测试(importers.grouping)。

分组是"导入文件夹"功能里最容易做错的一环:猜对了只省一次点击,猜错了
用户会在数据落库之后才发现分错组。所以这里的测试重点不是"能不能分组",
而是**不确实时会不会过度合并**。
"""

from __future__ import annotations

from xrd_engine.importers.grouping import (
    ScanGroup,
    common_prefix_name,
    group_paths,
    scan_folder,
)


class TestCommonPrefix:
    def test_single_file_keeps_experiment_number(self):
        """**关键**:单文件时不能把实验编号当序号剥掉。

        `GENBB-1-14-20260915-125048` 里 `14` 是扫描序号该剥,`1` 是实验
        编号必须留。两者都是纯数字段,所以只剥**有明确标记**的尾巴
        (日期/时间戳/scanNN),不碰孤立短数字。
        """
        assert common_prefix_name(["GENBB-1-14-20260915-125048"]) == "GENBB-1-14"

    def test_strips_scan_index(self):
        assert common_prefix_name(["sample_scan01"]) == "sample"

    def test_single_token_name_kept(self):
        assert common_prefix_name(["lfp"]) == "lfp"

    def test_group_prefix_is_shared_part(self):
        """多文件时实验名取组内公共部分,各文件独有的尾部自然被排除。"""
        assert common_prefix_name([
            "GENBB-1-14-20260915-125048",
            "GENBB-1-15-20260915-130209",
        ]) == "GENBB-1"

    def test_empty(self):
        assert common_prefix_name([]) == "未命名实验"


class TestGrouping:
    def test_user_real_filenames_split_into_two_experiments(self):
        """用户的真实命名:GENBB-1-* 与 GENBB-2-* 是两个实验。

        这正是"不能再导入别的数据"那个反馈的场景 —— 三个 GENBB-1 的文件
        应当归到一个实验下,而不是各建一个文件夹。
        """
        paths = [
            r"C:\d\GENBB-1-14-20260915-125048.txt",
            r"C:\d\GENBB-1-15-20260915-130209.txt",
            r"C:\d\GENBB-1-16-20260915-131330.txt",
            r"C:\d\GENBB-2-06-20260915-160058.txt",
        ]
        groups = group_paths(paths)
        names = [g.name for g in groups]
        assert names == ["GENBB-1", "GENBB-2"]
        by_name = {g.name: g for g in groups}
        assert len(by_name["GENBB-1"].paths) == 3
        assert len(by_name["GENBB-2"].paths) == 1

    def test_temperature_series_stay_together(self):
        """温度序列是同一个实验的不同条件,不该被拆开。"""
        paths = [
            "LFP-400C-scan01.xy",
            "LFP-400C-scan02.xy",
            "LFP-450C-scan01.xy",
        ]
        groups = group_paths(paths)
        # 后缀 scanNN 被剥掉后按前缀分:400C 一组、450C 一组
        names = sorted(g.name for g in groups)
        assert names == ["LFP-400C", "LFP-450C"]

    def test_same_prefix_one_group(self):
        paths = ["a-scan01.xy", "a-scan02.xy", "a-scan03.xy"]
        groups = group_paths(paths)
        assert len(groups) == 1
        assert len(groups[0].paths) == 3

    def test_different_prefixes_not_merged(self):
        """**核心风险**:不同实验绝不能被合并成一个。

        合并错了用户很难发现(数据都在,只是归错了组),而分细了用户
        一眼就能看出并手动合并。
        """
        paths = ["GENBB-1-a.txt", "GENBB-2-b.txt", "GENBB-3-c.txt"]
        groups = group_paths(paths)
        assert len(groups) == 3
        assert sorted(g.name for g in groups) == ["GENBB-1", "GENBB-2", "GENBB-3"]

    def test_letter_digit_names_split_on_the_digit(self):
        """`abc123` / `abc456` 是 token 级无公共部分的典型:整串是一个
        token,差异在字符级。差异位是纯数字段而不是日期/scanNN,所以按
        "宁可分细"的口径拆成两组 —— 它也可能是两个不同的样品。"""
        groups = group_paths(["abc123.txt", "abc456.txt"])
        assert len(groups) == 2
        assert sorted(g.name for g in groups) == ["abc123", "abc456"]

    def test_char_level_fallback_when_no_discriminator(self):
        """整批同名(只有扩展名不同)时合成一组,名字取字符公共前缀。"""
        groups = group_paths(["abc.xy", "abc.txt"])
        assert len(groups) == 1
        assert groups[0].name == "abc"

    def test_single_file(self):
        groups = group_paths(["only-one-20260915.txt"])
        assert len(groups) == 1
        assert groups[0].paths == ["only-one-20260915.txt"]

    def test_empty_input(self):
        assert group_paths([]) == []

    def test_every_path_appears_exactly_once(self):
        """无论怎么分组,都不能丢文件或重复文件。"""
        paths = [
            "GENBB-1-14-x.txt", "GENBB-1-15-x.txt", "GENBB-2-06-x.txt",
            "other.xy", "LFP-500C-scan01.xy",
        ]
        groups = group_paths(paths)
        got = sorted(p for g in groups for p in g.paths)
        assert got == sorted(paths)

    def test_group_is_serializable(self):
        g = ScanGroup(name="X", paths=["a.txt"])
        assert g.as_dict() == {"name": "X", "paths": ["a.txt"]}


class TestScanFolder:
    def test_filters_extensions_and_sorts(self, tmp_path):
        (tmp_path / "b.xy").write_text("1 2\n", encoding="utf-8")
        (tmp_path / "a.txt").write_text("1 2\n", encoding="utf-8")
        (tmp_path / "note.md").write_text("x", encoding="utf-8")
        got = scan_folder(tmp_path, {".xy", ".txt"})
        assert [p.split("\\")[-1].split("/")[-1] for p in got] == ["a.txt", "b.xy"]

    def test_does_not_recurse(self, tmp_path):
        """不递归:用户对"这个文件夹"的心智模型不包含子目录。"""
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "inner.xy").write_text("1 2\n", encoding="utf-8")
        (tmp_path / "outer.xy").write_text("1 2\n", encoding="utf-8")
        got = scan_folder(tmp_path, {".xy"})
        assert len(got) == 1

    def test_missing_folder_returns_empty(self, tmp_path):
        assert scan_folder(tmp_path / "nope", {".xy"}) == []

    def test_ignores_directories_named_like_data(self, tmp_path):
        (tmp_path / "weird.xy").mkdir()
        assert scan_folder(tmp_path, {".xy"}) == []
