"""Data Store 文件存储服务(§8.3 / §8.4)。

导入 = 记录 source path + 复制进 Data Store;分析永远基于软件自己的
copy,用户移动/重命名/删除外部原文件不影响 Dataset。

目录结构:

    <data_root>/
    ├── database/xrdstudio.db
    ├── projects/<项目编号-名称>/<ULID>/<原文件名> + metadata.json
    ├── projects/<项目编号-名称>/<样品编号-名称>/蓝电数据/<数据名>.cex
    ├── data/<ULID>/original.<ext> (电化学)
    ├── cache/
    ├── exports/
    └── logs/

stored_path 一律相对 data_root 存储,Data Store 可整体迁移(§8.4)。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import string
import threading
from contextlib import suppress
from datetime import datetime
from pathlib import Path

import numpy as np

_ALPHABET = string.ascii_lowercase + string.digits
_CEX_FOLDER = "蓝电数据"
_WINDOWS_DEVICE_NAME = re.compile(r"CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[1-9¹²³]", re.IGNORECASE)


def generate_ulid(rng: np.random.Generator | None = None) -> str:
    """生成类 ULID 的唯一目录名(时间前缀 + 随机后缀)。"""
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    rng = rng or np.random.default_rng()
    suffix = "".join(rng.choice(list(_ALPHABET), size=8))
    return f"{ts}{suffix}"


def sha256_of_file(path: str | Path, chunk_size: int = 1 << 20) -> str:
    """流式计算文件 SHA-256(§8.4 查重与溯源)。"""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


class DataStore:
    """管理 Data Store 目录与导入复制。线程安全。"""

    def __init__(self, data_root: str | Path):
        self.root = Path(data_root).resolve()
        self._lock = threading.Lock()
        for sub in ("database", "projects", "data", "cache", "exports", "logs"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # --- 路径 ---

    @property
    def db_path(self) -> Path:
        return self.root / "database" / "xrdstudio.db"

    def abs_path(self, stored_path: str) -> Path:
        """相对 stored_path → 绝对路径。拒绝逃逸出 data_root。"""
        p = (self.root / stored_path).resolve()
        p.relative_to(self.root.resolve())
        return p

    # --- 导入 ---

    def project_file(self, folder: str, key: str, filename: str) -> Path:
        for component in (folder, key, filename):
            if not component or Path(component).name != component or component in (".", ".."):
                raise ValueError("项目存储路径无效")
        if filename.casefold() == "metadata.json":
            filename = "source-" + filename
        return self.abs_path(str(Path("projects", folder, key, filename)))

    def import_file(
        self,
        source: str | Path,
        metadata: dict,
        *,
        project_folder: str | None = None,
    ) -> tuple[str, str]:
        """复制外部文件进 Data Store。

        返回 (stored_path 相对路径, sha256)。
        metadata.json 记录 source_path 与导入信息,供审计。
        """
        src = Path(source)
        if not src.is_file():
            raise FileNotFoundError(f"源文件不存在:{src}")

        with self._lock:
            ulid = generate_ulid()
            dest = (self.project_file(project_folder, ulid, src.name) if project_folder is not None
                    else self.abs_path(f"data/{ulid}/original{src.suffix.lower()}"))
            dest_dir = dest.parent
            dest_dir.mkdir(parents=True, exist_ok=False)
            try:
                shutil.copy2(src, dest)
                file_hash = sha256_of_file(dest)
                meta = {
                    "original_filename": src.name, "source_path": str(src.resolve()),
                    "file_hash": file_hash, "imported_at": datetime.now().isoformat(timespec="seconds"),
                    **metadata,
                }
                (dest_dir / "metadata.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception:
                self.remove_stored(dest.relative_to(self.root).as_posix())
                raise

        return dest.relative_to(self.root).as_posix(), file_hash

    def import_sample_cex(
        self, source: Path, project_folder: str, sample_folder: str, name: str,
    ) -> tuple[str, str]:
        """复制快照到样品的蓝电目录，返回实际归档路径及 SHA-256。"""
        if not isinstance(name, str):
            raise ValueError("请输入数据名称")
        name = name.strip()
        if name.casefold().endswith(".cex"):
            name = name[:-4].rstrip()
        if (not name or name.endswith(".") or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
                or _WINDOWS_DEVICE_NAME.fullmatch(name.split(".", 1)[0].rstrip())):
            raise ValueError("请输入可用作 Windows 文件名的数据名称")
        if len(name.encode("utf-16-le")) // 2 > 240:
            raise ValueError("数据名称过长，请缩短后再试")

        directory = self.project_file(project_folder, sample_folder, _CEX_FOLDER)
        if directory != self.root / "projects" / project_folder / sample_folder / _CEX_FOLDER:
            raise ValueError("样品的蓝电数据目录不可用")
        with self._lock, source.open("rb") as src:
            directory.mkdir(parents=True, exist_ok=True)
            dest = directory / f"{name}.cex"
            copy_number = 2
            while True:
                try:
                    output = dest.open("xb")
                    break
                except FileExistsError:
                    dest = directory / f"{name} ({copy_number}).cex"
                    copy_number += 1
            try:
                with output:
                    shutil.copyfileobj(src, output, 1 << 20)
                digest = sha256_of_file(dest)
            except Exception:
                dest.unlink(missing_ok=True)
                raise
        return dest.relative_to(self.root).as_posix(), digest

    # --- 删除 ---

    # 注:这里曾有一对 save_curve_cache / load_curve_cache(把处理结果存成
    # .npy 到 cache/)。**已删除** —— 它全仓库零调用,而且方向是错的:
    # §9.3 要的是"拖动滑块时不重算",那需要的是**进程内**缓存(纳秒级),
    # 而不是每次 preview 都往磁盘写两个 .npy。缓存实现见
    # api/commands.py 的 Engine._curve_cache。

    def remove_stored(self, stored_path: str) -> bool:
        """清理单个托管副本及其专属缓存。"""
        if not stored_path:
            return False
        try:
            file_path = self.abs_path(stored_path)
        except ValueError:
            return False

        relative = Path(stored_path)
        parts = relative.parts
        if len(parts) == 5 and parts[0] == "projects" and parts[3] == _CEX_FOLDER:
            if relative.suffix.lower() != ".cex" or file_path != self.root / relative:
                return False
            with self._lock:
                if not file_path.is_file():
                    return False
                file_path.unlink()
                for folder in (file_path.parent, file_path.parent.parent):
                    with suppress(OSError):
                        folder.rmdir()
            return True

        ulid_dir = file_path.parent
        parts = file_path.relative_to(self.root).parts
        if not ((len(parts) == 3 and parts[0] == "data") or
                (len(parts) == 4 and parts[0] == "projects")):
            return False
        if not ulid_dir.is_dir():
            return False

        shutil.rmtree(ulid_dir, ignore_errors=True)
        return not ulid_dir.exists()

    # --- 导出 ---

    def export_path(self, filename: str) -> Path:
        """exports/ 下的导出目标路径,自动避免重名覆盖。"""
        base = self.root / "exports"
        stem, suffix = Path(filename).stem, Path(filename).suffix
        candidate = base / f"{stem}{suffix}"
        i = 1
        while candidate.exists():
            candidate = base / f"{stem}_{i}{suffix}"
            i += 1
        return candidate
