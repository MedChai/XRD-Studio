"""Data Store 文件存储服务(§8.3 / §8.4)。

导入 = 记录 source path + 复制进 Data Store;分析永远基于软件自己的
copy,用户移动/重命名/删除外部原文件不影响 Dataset。

目录结构:

    <data_root>/
    ├── database/xrdstudio.db
    ├── data/<ULID>/original.<ext> + metadata.json
    ├── cache/
    ├── exports/
    └── logs/

stored_path 一律相对 data_root 存储,Data Store 可整体迁移(§8.4)。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import string
import threading
from datetime import datetime
from pathlib import Path

import numpy as np

_ALPHABET = string.ascii_lowercase + string.digits


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
        self.root = Path(data_root)
        self._lock = threading.Lock()
        for sub in ("database", "data", "cache", "exports", "logs"):
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

    def import_file(
        self,
        source: str | Path,
        metadata: dict,
    ) -> tuple[str, str]:
        """复制外部文件进 Data Store。

        返回 (stored_path 相对路径, sha256)。
        metadata.json 记录 source_path 与导入信息,供审计。
        """
        src = Path(source)
        if not src.is_file():
            raise FileNotFoundError(f"源文件不存在:{src}")

        file_hash = sha256_of_file(src)

        with self._lock:
            ulid = generate_ulid()
            dest_dir = self.root / "data" / ulid
            dest_dir.mkdir(parents=True, exist_ok=False)
            dest = dest_dir / f"original{src.suffix.lower()}"
            shutil.copy2(src, dest)

            meta = {
                "original_filename": src.name,
                "source_path": str(src.resolve()),
                "file_hash": file_hash,
                "imported_at": datetime.now().isoformat(timespec="seconds"),
                **metadata,
            }
            (dest_dir / "metadata.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        stored = f"data/{ulid}/{dest.name}"
        return stored, file_hash

    # --- 曲线读写 ---

    def save_curve_cache(
        self,
        dataset_id: int,
        x: np.ndarray,
        y: np.ndarray,
        kind: str = "processed",
    ) -> None:
        """处理结果缓存(§9.3),存 .npy 到 cache/,可随时删除重建。"""
        cache_dir = self.root / "cache"
        np.save(cache_dir / f"ds{dataset_id}_{kind}_x.npy", x)
        np.save(cache_dir / f"ds{dataset_id}_{kind}_y.npy", y)

    def load_curve_cache(
        self, dataset_id: int, kind: str = "processed"
    ) -> tuple[np.ndarray, np.ndarray] | None:
        cache_dir = self.root / "cache"
        xf = cache_dir / f"ds{dataset_id}_{kind}_x.npy"
        yf = cache_dir / f"ds{dataset_id}_{kind}_y.npy"
        if xf.exists() and yf.exists():
            return np.load(xf), np.load(yf)
        return None

    # --- 删除 ---

    def remove_stored(self, stored_path: str) -> bool:
        """永久删除一个 dataset 的存储目录 `data/<ULID>/`。

        只在回收站的「彻底删除」时调用 —— 软删除不碰磁盘,所以磁盘占用
        会一直累积到用户主动清理。

        越界保护:除了 abs_path 的逃逸检查,这里还要求 ULID 目录的父目录
        必须就叫 `data`。少了这一道,一个被构造成 `data/../database/x` 的
        stored_path 就能删掉数据库目录。
        """
        if not stored_path:
            return False
        try:
            file_path = self.abs_path(stored_path)
        except ValueError:
            return False

        ulid_dir = file_path.parent
        if ulid_dir.parent.name != "data":
            return False
        if not ulid_dir.is_dir():
            return False

        shutil.rmtree(ulid_dir, ignore_errors=True)
        return not ulid_dir.exists()

    def remove_curve_cache(self, dataset_id: int) -> None:
        """清掉该 dataset 的处理结果缓存(可重建,删不掉也无害)。"""
        cache_dir = self.root / "cache"
        for kind in ("processed", "raw"):
            for axis in ("x", "y"):
                (cache_dir / f"ds{dataset_id}_{kind}_{axis}.npy").unlink(
                    missing_ok=True
                )

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
