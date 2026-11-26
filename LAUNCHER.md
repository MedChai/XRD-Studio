# 一键启动 / 关闭

三个入口，双击即可，不需要开终端、不需要记命令。

| 文件 | 用途 |
|---|---|
| `start.bat` | **日常启动**。优先打开版本号最高的完整本地发布版，找不到发布版时进入开发模式 |
| `stop.bat` | **一键关闭**。结束应用 + Vite + Python 分析引擎。没在运行时双击也无害 |
| `start-dev.bat` | **改代码时启动**。强制走 `npm run tauri dev`，带热重载，即使已有打包版本也走开发模式 |

## 为什么用脚本，而不是直接双击 app.exe

`app/src-tauri/target/debug/app.exe` 是 dev 构建，前端地址编译期就固定指向
`http://localhost:1420`（Vite）。单独双击它会白屏——它需要 Vite 一起跑。
`start.bat` 负责把「Vite → Tauri 壳 → Python 引擎」这条链按顺序拉起来。

另外 `engine/.venv` 必须存在：Rust 壳在命令到达时才去 spawn 引擎，venv 缺失
的话报错是 `[WinError 2] %1 is not a valid Win32 application`，很难看懂。
`start.bat` 在启动前先检查，缺了就给出补救命令。

## 关闭语义

- **关掉软件窗口** → 启动器会自动收尾：引擎和 Vite 都是 Tauri 的子进程，
  Windows 上父进程退出不会顺带杀掉它们，所以启动器等 app 退出后再扫一遍进程树。
- **关掉启动器那个黑窗口** → 应用仍在跑。用 `stop.bat` 收尾。
- `stop.bat` 是幂等的：重复双击、没在运行时双击，都只是打印一行提示。

## 实现

```
start.bat / start-dev.bat / stop.bat    薄包装：设置环境、调 _launcher.ps1
        └── _common.bat                 路径解析、环境检查、工具定位、模式判定
        └── _launcher.ps1               全部逻辑与中文文案
```

`_common.bat` 的两种调用方式：`full`（启动，检查 venv 与工具链）和
`minimal`（关闭，只解析路径）。`stop.bat` 刻意**不**检查工具链——Node / Rust /
venv 被挪走或弄坏之后，你仍然必须能把软件关掉。

### 维护注意：`_launcher.ps1` 必须保持纯 ASCII

包括注释在内。Windows PowerShell 5.1 读取无 BOM 文件时用的是系统 ANSI 代码页
（本机 GBK），不是 UTF-8。UTF-8 中文注释会被解码成乱码，而某些字节序列解码后
恰好以**反引号**结尾——反引号会转义掉行尾换行，把**下一行整行吞掉**。这个 bug
的表现极具迷惑性：脚本语法检查通过，只是哈希表悄悄少了 8 个键（23 → 15），
对应文案变成空字符串。

所以面向用户的中文全部由 `J` 函数在运行时按码位拼出来，再以 UTF-8 打印
（`start.bat` 已经先 `chcp 65001`）。改动后请核对：

```bash
grep -n '[^ -~]' _launcher.ps1    # 应当没有任何输出
```

### 进程识别方式

- **应用**：按路径匹配 `app/src-tauri/target/` 和 `XRDStudio/releases/` 下本项目的主程序与分析引擎。
- **Python 引擎**：按命令行匹配 `xrd_engine`（venv 的 `python.exe` 在 Python 3.14 下
  会再拉起一个 base 解释器子进程，所以正常状态下你会看到两个 python 进程，这不是 bug）。
- **进程树**：`start` 时把根进程 PID 连同**启动时间**一起写进
  `%ROOT%\XRDStudio\logs\launcher.pid`；`stop` 时比对启动时间，
  避免 Windows 复用 PID 导致误杀无关进程树。
- **开发端口**：开发启动前检查 `1420` 的 IPv4/IPv6 监听。能确认属于本项目的
  残留 Vite/Node 进程会自动清理；无法确认归属时只显示 PID、进程名和命令行，
  不会自动结束别的程序。
- **启动日志**：Tauri/npm 的标准输出和错误分别写入
  `%ROOT%\XRDStudio\logs\tauri.stdout.log` 与
  `%ROOT%\XRDStudio\logs\tauri.stderr.log`。启动失败时，启动器会直接
  打印错误日志末尾内容。

## 打包安装包（给别的电脑用）

双击 `build-installer.bat`（或在 `app` 下跑 `npx tauri build --bundles nsis`）。
产物：

```text
app\src-tauri\target\release\bundle\nsis\XRD Studio_0.3.0_x64-setup.exe
```

安装包已打进 Python 分析引擎（PyInstaller **onedir**，与主程序同目录的
`xrd-engine.exe` + `_internal\`），装完即可用，**不需要**本仓库、Node、
Rust 或 `engine\.venv`。WebView2 用 embedBootstrapper（缺运行时会静默装）。

仓库根目录若有 `XRDStudio-0.3.0-x64-setup.exe`，是同一次构建的拷贝，直接
发给用户即可。

## 前置依赖

Node.js LTS（含 npm）、Rust 工具链（cargo）、以及已建好的 `engine/.venv`：

```bat
py -3 -m venv engine\.venv
engine\.venv\Scripts\python.exe -m pip install -e "engine[dev,packaging]"
```

`packaging` extra 含 PyInstaller，只在打安装包时需要。

## 已知边界

- 启动器所在的路径**不能含 `&`**（`_common.bat` 用 `for /f` 调 `where` 定位工具，
  未加引号会歧义）。当前路径 `D:\XRD自动解析软件` 没问题。
- 本地发布版可放在 `XRDStudio/releases/<版本号>/`，包含 `XRD Studio.exe`、`xrd-engine.exe` 和 `_internal/`。`start.bat` 会比较此目录、本地构建目录和当前用户安装目录中的完整版本，并继续使用项目的 `XRDStudio/library/` 数据库。
