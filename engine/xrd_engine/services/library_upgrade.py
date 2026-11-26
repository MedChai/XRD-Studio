"""首次使用软件目录时，从旧版资料库创建独立快照。"""

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from ..database.db import Database
from .data_store import DataStore


def prepare_library(target: Path, legacy: Path) -> None:
    target, legacy = target.resolve(), legacy.resolve()
    db_relative = Path("database", "xrdstudio.db")
    if target == legacy or (target / db_relative).is_file() or not (legacy / db_relative).is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".xrd-upgrade-", dir=target.parent) as temp:
        stage = Path(temp, "library")
        store = DataStore(stage)
        # SQLite backup includes committed WAL frames; copying the .db file alone can lose them.
        uri = (legacy / db_relative).as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as src, closing(sqlite3.connect(store.db_path)) as dst:
            src.backup(dst)
        for name in ("data", "projects", "exports"):
            if (legacy / name).is_dir():
                shutil.copytree(legacy / name, stage / name, dirs_exist_ok=True)
        db = Database(store.db_path)
        try:
            rows = db.query_all(
                """SELECT d.id, d.stored_path, d.original_filename, s.project_id
                   FROM datasets d JOIN samples s ON s.id=d.sample_id
                   WHERE d.stored_path LIKE 'data/%'"""
            )
            for row in rows:
                old = store.abs_path(row["stored_path"])
                if old.parent.parent != stage / "data" or not old.is_file():
                    continue
                folder = db.project_storage_folder(row["project_id"])
                new = store.project_file(folder, old.parent.name, row["original_filename"])
                new.parent.parent.mkdir(parents=True, exist_ok=True)
                old.parent.rename(new.parent)
                if new.name != old.name:
                    (new.parent / old.name).rename(new)
                db.execute("UPDATE datasets SET stored_path=? WHERE id=?",
                           (new.relative_to(stage).as_posix(), row["id"]))
        finally:
            db.close()
        # Only publish a complete snapshot. Concurrent starters may both prepare one.
        try:
            stage.rename(target)
        except OSError:
            if not (target / db_relative).is_file():
                raise
