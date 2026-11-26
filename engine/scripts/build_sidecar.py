"""构建 Python sidecar(README §11.1 路线 B:PyInstaller **onedir**,禁 onefile)。

产物布局(Tauri `bundle.resources` 按该结构打进安装包):

    app/src-tauri/sidecar/
      xrd-engine.exe
      _internal/          # Python 运行时 + numpy/scipy + xrd_engine

Rust 侧 `sidecar_command()` 在生产环境找的是主程序同目录的
`xrd-engine.exe`;onedir 的 exe 靠自身旁的 `_internal/` 加载依赖,两者
必须一起出现在安装目录根下。

onefile 在冷启动解压 + Defender 扫描下可超过 10s(README §11.1 明令禁止)。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    # scripts/build_sidecar.py -> engine -> repo root
    return Path(__file__).resolve().parents[2]


def build(dist_root: Path, work_root: Path) -> Path:
    root = _repo_root()
    engine = root / "engine"
    entry = engine / "scripts" / "sidecar_entry.py"

    if not entry.is_file():
        raise SystemExit(f"entry not found: {entry}")

    # PyInstaller 自己的 dist 中转目录:--name xrd-engine 会生成
    # <tmp>/xrd-engine/{xrd-engine.exe,_internal},再整体落到 dist_root。
    stage_parent = work_root.parent / "pyi-dist"
    stage = stage_parent / "xrd-engine"

    for p in (dist_root, work_root, stage_parent):
        if p.exists():
            shutil.rmtree(p)

    # 不 collect-all scipy —— 会把几百个 tests 模块扫进分析,包体暴涨。
    # 标准 hook + 入口对 xrd_engine 的 import 图足够。
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        "xrd-engine",
        "--distpath",
        str(stage_parent),
        "--workpath",
        str(work_root),
        "--specpath",
        str(work_root),
        "--paths",
        str(engine),
        "--collect-submodules",
        "xrd_engine",
        "--exclude-module",
        "xrd_engine.tests",
        "--exclude-module",
        "pytest",
        "--exclude-module",
        "IPython",
        # 引擎只走 stdio;窗口子系统由 hide_console 的 CREATE_NO_WINDOW
        # 抑制闪窗,stderr 仍经父进程管道写入 engine.log。
        "--console",
        str(entry),
    ]
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=engine, check=True)

    exe = stage / "xrd-engine.exe"
    internal = stage / "_internal"
    if not exe.is_file():
        raise SystemExit(f"missing {exe}")
    if not internal.is_dir():
        raise SystemExit(f"missing {internal}")

    dist_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(stage, dist_root)
    exe_out = dist_root / "xrd-engine.exe"
    internal_out = dist_root / "_internal"

    # 冒烟:不设 XRD_DATA_ROOT 时 main() 应立即以码 2 退出(§__main__)。
    # 必须继承完整环境 —— 清空 PATH 会让部分系统 DLL 解析失败,掩盖真问题。
    env = {k: v for k, v in os.environ.items() if k != "XRD_DATA_ROOT"}
    smoke = subprocess.run(
        [str(exe_out)],
        input=b"",
        capture_output=True,
        timeout=60,
        env=env,
    )
    err = smoke.stderr.decode("utf-8", "replace")
    if smoke.returncode != 2 or "XRD_DATA_ROOT" not in err:
        raise SystemExit(
            f"sidecar smoke failed: rc={smoke.returncode} stderr={err[:400]!r}"
        )

    # 镜像到 target/release:start.bat 的 run 模式直接开裸 release exe,
    # 不走 NSIS 安装目录,靠 lib.rs「与主程序同目录找 xrd-engine.exe」。
    tauri_root = root / "app" / "src-tauri"
    target_root = tauri_root / (os.environ.get("CARGO_TARGET_DIR") or "target")
    release_dir = target_root / "release"
    if release_dir.is_dir():
        shutil.copy2(exe_out, release_dir / "xrd-engine.exe")
        dest_internal = release_dir / "_internal"
        if dest_internal.exists():
            shutil.rmtree(dest_internal)
        shutil.copytree(internal_out, dest_internal)

    size_mb = sum(
        f.stat().st_size for f in dist_root.rglob("*") if f.is_file()
    ) / (1024 * 1024)
    print(f"OK {exe_out} ({size_mb:.1f} MiB total onedir)", flush=True)
    return dist_root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=None,
        help="onedir 输出目录(默认 <repo>/app/src-tauri/sidecar)",
    )
    parser.add_argument(
        "--work",
        default=None,
        help="PyInstaller workpath(默认 <repo>/engine/build/pyi-sidecar)",
    )
    args = parser.parse_args()
    root = _repo_root()
    out = Path(args.out) if args.out else root / "app" / "src-tauri" / "sidecar"
    work = Path(args.work) if args.work else root / "engine" / "build" / "pyi-sidecar"
    build(out, work)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
