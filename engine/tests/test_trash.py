"""回收站与重命名(§10.5 Trash 语义)。

覆盖本轮新增/重写的四个命令:`meta.rename`、`meta.soft_delete`、
`meta.restore`、`trash.list`、`meta.purge`。

核心语义是**隐式级联**:软删除只标记被直接删除的那一行,子对象因为父级
不可见而一并从 Library 消失;恢复父级时子对象自动回来,因为它们从未被
写过 deleted_at。这一组测试把该语义固化下来 —— 将来若有人"顺手"改成
级联写,test_soft_delete_project_hides_descendants 会立刻变红。
"""

from __future__ import annotations

import pytest

from xrd_engine.api.commands import Engine


@pytest.fixture
def engine(tmp_path):
    eng = Engine(tmp_path / "store")
    yield eng
    eng.close()


@pytest.fixture
def with_dataset(engine, fixtures_dir):
    """走一遍最小成功流程,返回 (engine, project_id, sample_id, dataset_id)。"""
    path = fixtures_dir / "lfp_500c_scan01.xy"
    if not path.exists():
        pytest.skip("fixture 未生成")
    pid = engine.handle("projects.create", {"name": "LFP Study"})["data"]["projectId"]
    r = engine.handle("datasets.import", {
        "paths": [str(path)],
        "project_id": pid,
        "new_sample": {"name": "LFP-500C", "temperature": "500"},
    })
    assert r["ok"], r
    return engine, pid, r["data"]["sampleId"], r["data"]["imported"][0]["id"]


class TestRename:
    def test_rename_updates_name(self, with_dataset):
        engine, _, sample_id, _ = with_dataset
        r = engine.handle(
            "meta.rename", {"table": "samples", "id": sample_id, "name": "LFP-600C"}
        )
        assert r["ok"], r
        assert r["data"]["name"] == "LFP-600C"
        tree = engine.handle("library.tree", {})["data"]["samples"]
        assert tree[0]["name"] == "LFP-600C"

    def test_rename_rejects_empty_name(self, with_dataset):
        engine, _, sample_id, _ = with_dataset
        r = engine.handle(
            "meta.rename", {"table": "samples", "id": sample_id, "name": "   "}
        )
        assert not r["ok"]
        assert "不能为空" in r["error"]
        # 库里名字没被改坏
        row = engine.db.query_one(
            "SELECT name FROM samples WHERE id = ?", (sample_id,)
        )
        assert row["name"] == "LFP-500C"

    def test_rename_rejects_missing_row(self, with_dataset):
        engine, _, _, _ = with_dataset
        r = engine.handle(
            "meta.rename", {"table": "samples", "id": 99999, "name": "X"}
        )
        assert not r["ok"]
        assert "不存在" in r["error"]

    def test_rename_rejects_soft_deleted_row(self, with_dataset):
        engine, _, sample_id, _ = with_dataset
        engine.handle("meta.soft_delete", {"table": "samples", "id": sample_id})
        r = engine.handle(
            "meta.rename", {"table": "samples", "id": sample_id, "name": "X"}
        )
        assert not r["ok"]
        assert "回收站" in r["error"]

    def test_rename_rejects_bad_table(self, with_dataset):
        engine, _, _, _ = with_dataset
        r = engine.handle("meta.rename", {"table": "peaks", "id": 1, "name": "X"})
        assert not r["ok"]
        # 必须是人话,不是"引擎内部错误"(白名单要在命令层显式执行)
        assert "引擎内部错误" not in r["error"]

    def test_rename_updates_updated_at(self, with_dataset):
        engine, _, sample_id, _ = with_dataset
        engine.db.execute(
            "UPDATE samples SET updated_at = datetime('now', '-1 day') WHERE id = ?",
            (sample_id,),
        )
        before = engine.db.query_one(
            "SELECT updated_at FROM samples WHERE id = ?", (sample_id,)
        )["updated_at"]
        engine.handle(
            "meta.rename", {"table": "samples", "id": sample_id, "name": "新名字"}
        )
        after = engine.db.query_one(
            "SELECT updated_at FROM samples WHERE id = ?", (sample_id,)
        )["updated_at"]
        assert after != before


class TestSoftDelete:
    def test_soft_delete_rejects_bad_table(self, with_dataset):
        engine, _, _, _ = with_dataset
        r = engine.handle("meta.soft_delete", {"table": "peaks", "id": 1})
        assert not r["ok"]
        assert "引擎内部错误" not in r["error"]

    def test_soft_delete_missing_row(self, with_dataset):
        engine, _, _, _ = with_dataset
        r = engine.handle("meta.soft_delete", {"table": "samples", "id": 99999})
        assert not r["ok"]
        assert "不存在" in r["error"]

    def test_soft_delete_is_idempotent_guarded(self, with_dataset):
        engine, _, sample_id, _ = with_dataset
        assert engine.handle(
            "meta.soft_delete", {"table": "samples", "id": sample_id}
        )["ok"]
        second = engine.handle("meta.soft_delete", {"table": "samples", "id": sample_id})
        assert not second["ok"]
        assert "回收站" in second["error"]

    def test_soft_delete_reports_cascaded_count(self, with_dataset):
        engine, pid, _, _ = with_dataset
        r = engine.handle("meta.soft_delete", {"table": "projects", "id": pid})
        assert r["ok"], r
        assert r["data"]["cascadedDatasets"] == 1

    def test_soft_delete_project_hides_descendants(self, with_dataset):
        """**把隐式级联固化下来**:删 project 只写 project 那一行的
        deleted_at,子级保持 NULL —— 恢复时它们才能自动全部回来。"""
        engine, pid, sample_id, ds_id = with_dataset
        engine.handle("meta.soft_delete", {"table": "projects", "id": pid})

        assert engine.handle("library.tree", {})["data"]["samples"] == []
        # 子级没有被写 deleted_at(隐式级联,不是级联写)
        assert engine.db.query_one(
            "SELECT deleted_at FROM samples WHERE id = ?", (sample_id,)
        )["deleted_at"] is None
        assert engine.db.query_one(
            "SELECT deleted_at FROM datasets WHERE id = ?", (ds_id,)
        )["deleted_at"] is None


class TestDuplicateAcrossTrash:
    def test_duplicate_ignores_deleted_project(self, with_dataset, fixtures_dir):
        """最隐蔽的一条:项目被删后重新导入同一文件,不应再判重。

        真实场景是——删掉项目 A(里面已有 scan01),新建项目 B,再导入
        同一个 scan01。若不补父级过滤,引擎会判重并提示一个属于已删项目
        A 的路径:用户在 Library 里根本看不到那条记录,无从处理。
        """
        engine, pid_a, _, _ = with_dataset
        path = fixtures_dir / "lfp_500c_scan01.xy"

        pid_b = engine.handle("projects.create", {"name": "新项目"})["data"]["projectId"]
        engine.handle("meta.soft_delete", {"table": "projects", "id": pid_a})

        r = engine.handle("datasets.import", {
            "paths": [str(path)],
            "project_id": pid_b,
            "new_sample": {"name": "重新导入"},
        })
        assert r["ok"], r
        assert r["data"]["duplicates"] == []
        assert len(r["data"]["imported"]) == 1

    def test_duplicate_ignores_deleted_dataset(self, with_dataset, fixtures_dir):
        engine, pid, sample_id, ds_id = with_dataset
        path = fixtures_dir / "lfp_500c_scan01.xy"
        engine.handle("meta.soft_delete", {"table": "datasets", "id": ds_id})
        r = engine.handle("datasets.import", {
            "paths": [str(path)], "project_id": pid, "sample_id": sample_id,
        })
        assert r["ok"], r
        assert r["data"]["duplicates"] == []
        assert len(r["data"]["imported"]) == 1

    def test_duplicate_still_detected_after_restore(self, with_dataset, fixtures_dir):
        """确认补的父级过滤没有过度过滤:恢复后又该判重。"""
        engine, pid, sample_id, ds_id = with_dataset
        path = fixtures_dir / "lfp_500c_scan01.xy"
        engine.handle("meta.soft_delete", {"table": "datasets", "id": ds_id})
        engine.handle("meta.restore", {"table": "datasets", "id": ds_id})

        r = engine.handle("datasets.import", {
            "paths": [str(path)], "project_id": pid, "sample_id": sample_id,
        })
        assert r["ok"], r
        assert len(r["data"]["duplicates"]) == 1

    def test_duplicate_reports_dataset_id(self, with_dataset, fixtures_dir):
        """duplicates 要带 datasetId,前端才能提供「打开已有」。"""
        engine, pid, sample_id, ds_id = with_dataset
        path = fixtures_dir / "lfp_500c_scan01.xy"
        r = engine.handle("datasets.import", {
            "paths": [str(path)], "project_id": pid, "sample_id": sample_id,
        })
        assert r["data"]["duplicates"][0]["existing"]["datasetId"] == ds_id


class TestTrashList:
    def test_trash_list_three_kinds(self, with_dataset):
        engine, pid, sample_id, ds_id = with_dataset
        engine.handle("meta.soft_delete", {"table": "datasets", "id": ds_id})
        engine.handle("meta.soft_delete", {"table": "samples", "id": sample_id})
        engine.handle("meta.soft_delete", {"table": "projects", "id": pid})

        r = engine.handle("trash.list", {})
        assert r["ok"], r
        items = r["data"]["items"]
        assert r["data"]["total"] == 3
        # kind 是表名,前端可以直接透传给 meta.restore / meta.purge
        assert {i["kind"] for i in items} == {"projects", "samples", "datasets"}

        by_kind = {i["kind"]: i for i in items}
        # 每条都带完整层级路径,供前端显示 "项目 / 样品 / 文件名"
        assert by_kind["datasets"]["projectName"] == "LFP Study"
        assert by_kind["datasets"]["sampleName"] == "LFP-500C"
        assert by_kind["datasets"]["name"] == "lfp_500c_scan01.xy"
        assert by_kind["samples"]["projectName"] == "LFP Study"

    def test_trash_only_lists_directly_deleted(self, with_dataset):
        """隐式级联的直接体现:删项目时,里面的样品与数据集**不**各自
        出现在回收站里 —— 只有项目这一条。恢复项目即全部回来。"""
        engine, pid, _, _ = with_dataset
        engine.handle("meta.soft_delete", {"table": "projects", "id": pid})
        items = engine.handle("trash.list", {})["data"]["items"]
        assert len(items) == 1
        assert items[0]["kind"] == "projects"

    def test_trash_list_empty_when_nothing_deleted(self, with_dataset):
        engine, _, _, _ = with_dataset
        r = engine.handle("trash.list", {})
        assert r["ok"]
        assert r["data"]["total"] == 0

    def test_trash_list_order_stable(self, with_dataset):
        """datetime('now') 是秒级,同一批删除大量并列 —— 排序键必须补
        kind, id,否则两次打开回收站的顺序会不同。"""
        engine, pid, sample_id, ds_id = with_dataset
        for table, rid in (("datasets", ds_id), ("samples", sample_id), ("projects", pid)):
            engine.handle("meta.soft_delete", {"table": table, "id": rid})
        a = engine.handle("trash.list", {})["data"]["items"]
        b = engine.handle("trash.list", {})["data"]["items"]
        assert [(i["kind"], i["id"]) for i in a] == [(i["kind"], i["id"]) for i in b]


class TestRestore:
    def test_restore_simple(self, with_dataset):
        engine, _, sample_id, _ = with_dataset
        engine.handle("meta.soft_delete", {"table": "samples", "id": sample_id})
        r = engine.handle("meta.restore", {"table": "samples", "id": sample_id})
        assert r["ok"], r
        assert engine.handle("library.tree", {})["data"]["samples"][0]["name"] == "LFP-500C"

    def test_restore_rejects_not_in_trash(self, with_dataset):
        engine, _, sample_id, _ = with_dataset
        r = engine.handle("meta.restore", {"table": "samples", "id": sample_id})
        assert not r["ok"]
        assert "不在回收站" in r["error"]

    def test_restore_project_brings_back_descendants(self, with_dataset):
        """隐式级联的核心验收:恢复项目 → 里面的样品与数据集全部自动回来。"""
        engine, pid, _, ds_id = with_dataset
        engine.handle("meta.soft_delete", {"table": "projects", "id": pid})
        assert engine.handle("library.tree", {})["data"]["samples"] == []

        engine.handle("meta.restore", {"table": "projects", "id": pid})
        tree = engine.handle("library.tree", {})["data"]["samples"]
        assert len(tree) == 1
        assert tree[0]["datasets"][0]["id"] == ds_id

    def test_restore_dataset_cascades_parents(self, with_dataset):
        """父级还在回收站时,恢复子级要一并向上恢复 —— 否则用户点了恢复
        却什么也看不到。

        场景必须是"数据集先被单独删,随后其父级也被删":只有这样数据集
        才在回收站里(隐式级联下,只删项目不会让数据集进回收站)。
        """
        engine, pid, sample_id, ds_id = with_dataset
        engine.handle("meta.soft_delete", {"table": "datasets", "id": ds_id})
        engine.handle("meta.soft_delete", {"table": "samples", "id": sample_id})
        engine.handle("meta.soft_delete", {"table": "projects", "id": pid})

        r = engine.handle("meta.restore", {"table": "datasets", "id": ds_id})
        assert r["ok"], r
        kinds = {a["kind"] for a in r["data"]["restoredAncestors"]}
        assert kinds == {"samples", "projects"}
        # 恢复后确实可见了
        tree = engine.handle("library.tree", {})["data"]["samples"]
        assert tree[0]["datasets"][0]["id"] == ds_id

    def test_restore_dataset_needs_no_cascade_when_parent_alive(self, with_dataset):
        engine, _, _, ds_id = with_dataset
        engine.handle("meta.soft_delete", {"table": "datasets", "id": ds_id})
        r = engine.handle("meta.restore", {"table": "datasets", "id": ds_id})
        assert r["ok"], r
        assert r["data"]["restoredAncestors"] == []
        assert engine.handle("library.tree", {})["data"]["samples"][0]["datasets"][0]["id"] == ds_id

    def test_restore_without_cascade(self, with_dataset):
        """cascade_parent=False 时只恢复自己 —— 已知取舍:此时它在 tree 里
        仍不可见(父级还在回收站)。把行为固定下来,避免以后被误当 bug。"""
        engine, _, sample_id, ds_id = with_dataset
        engine.handle("meta.soft_delete", {"table": "datasets", "id": ds_id})
        engine.handle("meta.soft_delete", {"table": "samples", "id": sample_id})

        r = engine.handle(
            "meta.restore",
            {"table": "datasets", "id": ds_id, "cascade_parent": False},
        )
        assert r["ok"], r
        assert r["data"]["restoredAncestors"] == []
        # 数据集自身的 deleted_at 已清,但父级仍在回收站 → tree 里看不到
        assert engine.db.query_one(
            "SELECT deleted_at FROM datasets WHERE id = ?", (ds_id,)
        )["deleted_at"] is None
        assert engine.handle("library.tree", {})["data"]["samples"] == []

    def test_restore_bad_table(self, with_dataset):
        engine, _, _, _ = with_dataset
        r = engine.handle("meta.restore", {"table": "peaks", "id": 1})
        assert not r["ok"]
        assert "引擎内部错误" not in r["error"]


class TestPurge:
    def test_purge_rejects_non_trashed(self, with_dataset):
        engine, _, sample_id, _ = with_dataset
        r = engine.handle("meta.purge", {"table": "samples", "id": sample_id})
        assert not r["ok"]
        assert "回收站" in r["error"]

    def test_purge_dataset_removes_rows_and_files(self, with_dataset):
        engine, _, _, ds_id = with_dataset
        stored = engine.db.query_one(
            "SELECT stored_path FROM datasets WHERE id = ?", (ds_id,)
        )["stored_path"]
        stored_abs = engine.store.abs_path(stored)
        assert stored_abs.exists()

        engine.handle("meta.soft_delete", {"table": "datasets", "id": ds_id})
        r = engine.handle("meta.purge", {"table": "datasets", "id": ds_id})
        assert r["ok"], r

        # 三张表都没有残留
        assert engine.db.query_one(
            "SELECT id FROM datasets WHERE id = ?", (ds_id,)
        ) is None
        assert engine.db.query_one(
            "SELECT id FROM analyses WHERE dataset_id = ?", (ds_id,)
        ) is None
        # ULID 目录被删掉
        assert not stored_abs.parent.exists()

    def test_purge_project_removes_presets(self, with_dataset):
        engine, pid, _, _ = with_dataset
        engine.handle("presets.save", {
            "project_id": pid, "name": "P", "pipeline": {"peaks": {"sensitivity": "low"}},
        })
        engine.handle("meta.soft_delete", {"table": "projects", "id": pid})
        r = engine.handle("meta.purge", {"table": "projects", "id": pid})
        assert r["ok"], r
        # 外键开着,孤儿 preset 会让 DELETE FROM projects 直接失败
        assert engine.db.query_one(
            "SELECT id FROM analysis_presets WHERE project_id = ?", (pid,)
        ) is None

    def test_purge_all_empties_trash(self, with_dataset):
        engine, pid, sample_id, ds_id = with_dataset
        for table, rid in (("datasets", ds_id), ("samples", sample_id), ("projects", pid)):
            engine.handle("meta.soft_delete", {"table": table, "id": rid})

        r = engine.handle("meta.purge", {"all": True})
        assert r["ok"], r
        assert r["data"]["purged"] == 3
        assert engine.handle("trash.list", {})["data"]["total"] == 0

        data_dir = engine.store.root / "data"
        leftover = [d for d in data_dir.iterdir() if d.is_dir()]
        assert leftover == []


class TestDetectByDatasetId:
    def test_detect_by_dataset_id_matches_analysis_id(self, with_dataset):
        """前端手里只有 dataset_id,不该为了一个内部 id 多往返一次。"""
        engine, _, _, ds_id = with_dataset
        engine.handle("analysis.commit", {
            "dataset_id": ds_id, "pipeline": {"peaks": {"sensitivity": "medium"}},
        })
        analysis = engine.db.query_one(
            "SELECT * FROM analyses WHERE dataset_id = ? AND is_active = 1", (ds_id,)
        )
        by_analysis = engine.handle("peaks.detect", {"analysis_id": analysis["id"]})
        by_dataset = engine.handle("peaks.detect", {"dataset_id": ds_id})
        assert by_dataset["ok"], by_dataset
        assert [p["position"] for p in by_dataset["data"]["peaks"]] == [
            p["position"] for p in by_analysis["data"]["peaks"]
        ]

    def test_detect_requires_one_of_the_ids(self, with_dataset):
        engine, _, _, _ = with_dataset
        r = engine.handle("peaks.detect", {})
        assert not r["ok"]
        assert "analysis_id" in r["error"] or "dataset_id" in r["error"]
