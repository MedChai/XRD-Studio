# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概况

XRD Studio —— XRD(X 射线衍射)数据管理、查看、基础处理与峰分析桌面软件。目标用户是实验室材料/化学研究者(电池材料 LFP、NCM/高镍正极为主),以 Windows 为主。

三个进程:

```
React UI (WebView)  ──invoke("engine_command")──▶  Tauri 2 Shell (Rust)
                                                        │ stdio 长度前缀帧
                                                        ▼
                                                  Python 分析引擎 (sidecar)
                                                        │ 独占访问
                                                        ▼
                                              SQLite + Data Store 文件
```

**前端永远不直接开 SQLite**;数据库和文件只允许引擎访问,前端一律经 command 接口读写。

文档层级:`README.md` 是产品需求与验收标准(Rev 2,文中 §编号被代码注释大量引用,如 `§7.1`、`§10.4`);`PROGRESS.md` 是当前进度与已修缺陷;`docs/DEVELOPMENT_HIGH_NI.md` 是高镍分析路线;`LAUNCHER.md` 是一键启动脚本说明。README 中的 §编号是代码注释的通用引用方式,改动时请沿用。

## 常用命令

### 引擎(Python,`engine/` 目录下执行)

```bash
engine/.venv/Scripts/python.exe -m pytest          # 全部测试(必须 cd engine 后跑:pytest 配置的 testpaths/pythonpath 都是相对路径)
engine/.venv/Scripts/python.exe -m pytest tests/test_api_commands.py::TestX::test_y   # 单个测试
engine/.venv/Scripts/python.exe -m pytest -k asls  # 按名字过滤
engine/.venv/Scripts/python.exe -m ruff check .    # lint(必须零告警)
```

首次准备环境:

```bat
py -3 -m venv engine\.venv
engine\.venv\Scripts\python.exe -m pip install -e engine
```

`pyproject.toml` 里 `filterwarnings = ["error::DeprecationWarning"]` —— 触发 DeprecationWarning 会直接让测试失败。

### 前端(`app/` 目录下执行)

```bash
npm run dev        # 仅 Vite;浏览器里跑,走 mock 引擎,不开 Tauri 也能完整驱动 UI
npm run build      # tsc && vite build;tsc 零错误是硬要求
npm run tauri dev  # 完整链路(Vite + Tauri 壳 + 真实 Python 引擎)
```

前端目前**没有** ESLint / Vitest,也没有 lint 脚本;类型检查靠 `npm run build` 里的 `tsc`。

### 一键启动(项目根目录,双击即可)

| 文件 | 用途 |
|---|---|
| `start.bat` | 有打包版就直接开,没有退回开发模式 |
| `start-dev.bat` | 强制 `npm run tauri dev`,带热重载 |
| `stop.bat` | 结束应用 + Vite + 引擎;幂等 |

绕开启动器直接双击 `app/src-tauri/target/debug/app.exe` 会白屏 —— 前端地址编译期固定在 `http://localhost:1420`,需要 Vite 一起跑。

### 基准数据

`engine/fixtures/` 下的基准文件全部由脚本生成(固定种子 20260915),**不要手工编辑**:

```bash
cd engine && .venv/Scripts/python.exe -m scripts.make_fixtures
```

测试在 fixture 缺失时 `skip` 而非 `fail`。

## 架构要点

### 1. 引擎命令分发:新增命令只需加一个方法

`engine/xrd_engine/api/commands.py` 的 `Engine.handle()` 用 `getattr(self, "_cmd_" + command.replace(".", "_"))` 分发 —— `datasets.import` → `_cmd_datasets_import`,`domain.high_ni.analyze` → `_cmd_domain_high_ni_analyze`。

约定:

- 错误一律 `raise EngineError("中文人话", detail="技术信息")`;`handle()` 收敛成 `{"ok": false, "error": ..., "detail": ...}`。**traceback 绝不外泄给用户**,兜底分支统一报"引擎内部错误,请重试或查看日志"。
- 表名白名单(`ALLOWED_TABLES`)必须在命令层校验,**不要用 `assert`** —— `python -O` 会移除它,非法表名将直接拼进 SQL(注入面),且 `AssertionError` 会被兜底成"引擎内部错误",用户看不到人话。
- 曲线数据出口统一走 `curve_payload()`,显示降采样与处理链解耦。

### 2. 前端新增命令必须同步 mock

`app/src/services/engine.ts` 用 `"__TAURI_INTERNALS__" in window` 判断:非 Tauri 时 `engineCommand()` 派发到同文件的 mock。**加了引擎命令但没加 mock 分支,浏览器开发模式会直接 `mock: 未实现命令 X`**。mock 的数学形状刻意与引擎一致(§4.3 的 LFP 示例谱)。

mock 也必须与引擎**行为**一致,而不只是接口:晶面标注的 mock 参考表同样只含 R-3m 允许反射(见下面第 3 条)。mock 若保留现实里不存在的行为,浏览器开发模式会验证到假象。

### 3. 高镍峰识别走晶体学指数化,不要再写死 2θ

`engine/xrd_engine/high_ni/indexing.py` 是层状 R-3m 识别的**唯一事实来源**,所有理论 2θ 都由 (hkl, a, c, λ) 反算。改动识别逻辑前务必先读它的模块 docstring。

三条不可违背的规则:

- **允许反射由 `-h+k+l ≡ 0 (mod 3)` 筛选**(R-3m 阳离子 3a 位点)。枚举/拟合用这套消光规则;**对外显示名**对双峰用文献惯用的 `(102)`/`(108)`(`indexing._LITERATURE_HKL`,与 h-k+l 约定同一物理反射,d 相同)。不要把显示名 (102) 拿回 `allowed_hkl` 当禁阻判断。层状正极实验分工是 **(006) 弱、(102) 强**。
- **晶胞每张谱精修**,不用固定值。实测用户数据精修出 a=2.884/c=14.278 而旧表写死 2.878/14.216,这个差异足以让 (006)/(102) 归属翻转。
- **归属按邻域内相对强度贡献占比**,不按最近邻距离;窗口随峰宽缩放。固定 ±0.6° 时 (110) 有 65% 面积漏进 (108) 窗口,(108) 被永久判成 (110)。

置信度是**三态**:`reliable` / `ambiguous`(双峰未分开,**返回候选列表**) / `none`。双峰物理上未分开时如实报 ambiguous,不要硬给一个标签 —— 旧逻辑最大的问题正是在不确定时给 `reliable`。

`peaks/annotate.py` 的通用路径 `annotate_peaks()` **要求显式传 `reference_table`**;不传会抛错,不再静默回退到高镍表。

**性能红线:标注必须走缓存。** 正式标注(`peaks.detect` / `peaks.annotate` / `domain.high_ni.analyze`)都会调用 `annotate_high_ni` / `refine_cell`;交互 `analysis.preview` 传 `annotate=false` **不**调用它,标签由 `analyses.annotation_json` 的 labels 快照按位置恢复(`restoreAnnotation`)。全谱精修单次 1.5–4.5 秒,不缓存就是"切几次图就卡死"。所以:

- 调用时**必须传 `dataset_id`**,否则不缓存(`annotate._FIT_CACHE`)。
- 缓存键带数据指纹(点数、x 首尾、y 和、波长),不是仅 id —— 数据集删除后 id 可能复用,只按 id 缓存会拿到上一条曲线的晶胞,标签静默错位。
- 删数据集的路径(`_remove_files`)必须调 `invalidate_fit_cache`。
- 改 `refine_cell` 的网格参数前先读它的注释:粗到细三层搜索是为了把单次精修从 12 s 压到 4.5 s,粗扫步长超过 0.02 会跳进错误盆地。

### 4. Pipeline 顺序是有意的,不要"顺手"调整

```
Load → Validate → Crop → Background → Subtract → Smooth → Peaks → Metrics
```

**Background 必须在 Smooth 之前**(README Rev 2 对 v1 的修正):SG 平滑会压低峰高、展宽半高宽,先平滑后测 FWHM 会系统性偏大,而用户对此毫不知情 —— 对用 FWHM 看晶粒尺寸趋势的用户这是静默偏差。配套约定:

- Smooth 默认关闭。
- Smooth 开启时 `ProcessingResult.metrics_affected_by_smoothing` 置位,UI 必须显示提示。
- 每次求值都从原始曲线整链重跑,不做跨请求状态累积(非破坏性原则在引擎层的体现)。

### 5. Trash 是隐式级联,不是写子级 `deleted_at`

`projects`/`samples`/`datasets` 三表有 `deleted_at`,删除只标记**被直接删除的那一行**;子对象因父级不可见而一并从 Library 消失,恢复父级时它们自动全部回来 —— 因为从未被写过 `deleted_at`。

- 恢复时**向上**级联恢复父级(否则用户点了恢复却看不到东西),**向下不**级联。这个不对称是隐式级联带来的好处,不是遗漏。
- `analyses`/`peaks`/`analysis_presets` **没有** `deleted_at`:可见性完全由父级决定,加列只会制造需要同步的冗余状态。
- `meta.purge` 是唯一真正删磁盘(`data/<ULID>/`)的入口;软删除刻意不碰文件,以便恢复时数据完整。删磁盘失败只忽略不抛异常(此时 DB 行已删,抛异常只会让用户误以为整件事失败)。
- 所有跨层级查询必须一并判父级 `deleted_at IS NULL`。

### 6. Preview 状态机(§10.3)

```
idle →(参数变化) previewing(30ms 节流发引擎,序号丢弃乱序结果)
previewing →(300ms 无新输入) committing(PATCH analyses,落库)
```

`analysisStore.patchPipeline()` 是唯一入口。**Undo/Redo 复用同一条 preview→commit 通路**,且必须同样递增 `_previewSeq` —— 否则"拖滑块后立刻 Ctrl+Z"时,先前排队的 preview 会在之后返回并覆盖曲线。

"停止调整 = 300ms 无输入"是全局唯一定义。

### 7. 前端状态职责边界

`app/src/stores/index.ts` 里 6 个 store 各自单一职责:`appStore`(主题)、`projectStore`(库树 + 回收站)、`plotStore`(视图态)、`analysisStore`(pipeline + preview 状态机 + undo 栈)、`selectionStore`(联动中枢:点表格行 ↔ 点图中峰)、`uiStore`(弹窗/toast)。

- `analysisStore.DEFAULT_PIPELINE` 的默认值**必须与 `engine/xrd_engine/models/types.py` 的 dataclass 默认值严格一致**。
- **缩放窗口用数据坐标(2θ)而不是百分比** —— 百分比是相对当前曲线值域的,切换数据集后旧百分比会落在新曲线的错误位置,表现为"图是空的"且极难排查。
- 纯显示层参数(Normalize / Offset / 曲线显隐 / 横轴 2θ/d/Q 视图)在前端就地生效,**不走引擎**;算法类参数(Crop/Background/Smooth/Peaks)必须进引擎。
- 横轴视图换算走 `app/src/services/coords.ts`(**镜像** `engine/xrd_engine/coords.py` 的公式,改一边要同步另一边);zoom 与峰表永远按**采集轴**存,切视图只换图上 x 映射(README §4.1)。

### 8. i18n:所有用户可见文案走 `t`

`app/src/i18n/zh.ts` 是唯一来源,经 `app/src/i18n/index.ts` 导出 `t`。用了 `as const`,拼写错误在 tsc 阶段就报错。保留不译:产品名、领域术语(2θ / FWHM / AsLS / hkl 等)。

### 9. 启动器脚本必须保持纯 ASCII

`_launcher.ps1`(**含注释**)、`_common.bat`、`start.bat`、`start-dev.bat`、`stop.bat` 都必须纯 ASCII。Windows PowerShell 5.1 读无 BOM 文件时按系统 ANSI 代码页(本机 GBK)解码,UTF-8 中文注释会被解成乱码,某些字节序列解码后恰好以**反引号**结尾 —— 反引号转义掉行尾换行,会**吞掉下一行整行**。症状极具迷惑性:语法检查通过,只是哈希表悄悄少了 8 个键,对应文案变空字符串。

面向用户的中文由 `_launcher.ps1` 的 `J` 函数在运行时按码位拼出,再以 UTF-8 打印。改动后核对:

```bash
grep -n '[^ -~]' _launcher.ps1    # 应当没有任何输出
```

### 10. 引擎与 Tauri 的路径/注入约定

- Data Store 根目录:`%LOCALAPPDATA%\XRDStudio`(规避 Program Files 权限与中文用户目录),含 `database/ data/ cache/ exports/ logs/`。
- 生产环境走 stdio 长度前缀帧 `[u32 BE 长度][UTF-8 JSON]`,Rust 侧负责进程监管、请求 id 路由、stderr 落 `logs/engine.log`;HTTP 只在开发期可选,**发布版不包含**。
- Rust `ensure_running()` 的启动**全程持互斥锁** —— 并发请求(如首屏 `Promise.all`)若穿过"检查未运行 → spawn"的间隙会重复拉起 sidecar(实测曾出现 4 个实例)。
- `XRD_ENGINE` 环境变量可强制指定引擎可执行文件;dev 构建自动定位 `engine/.venv`。

### 11. 曲线降采样只影响显示

`DISPLAY_MAX_POINTS = 5000`(前端)+ `select_minmax_indices()`(引擎):按 bucket 取 y 最小/最大点,避免窄峰被抽掉。**峰、FWHM、Area 等一切指标都在全量数据上算**,降采样只交付给主图与范围概览渲染。

### 12. 层级是四层,批次是可选层

```text
Project(项目) > Sample(实验) > Batch(批次,可选) > Dataset(数据)
```

UI 上 Sample 显示为「实验」—— 用户的心智模型就是这个。**批次不给就不建**,
数据集直接挂实验下,不要强迫用户维护一个他不需要的层级。

- `batches` 表有 `deleted_at`,隐式级联与其他三张表一致;`ALLOWED_TABLES`
  与 `_TABLE_DEPTH` 必须同步(samples 深 1、batches 深 2、datasets 深 3)。
- 同名批次**复用**(`datasets.import` 按 `sample_id + name` 查)。不复用的话
  用户分两次导入同一批文件会得到两个同名批次,界面上完全看不出区别,
  数据看似归好组了实际被劈成两半。
- 样品元数据(温度/时长/材料/批次/波长/备注)是**结构趋势图的自变量**
  (docs/DEVELOPMENT_HIGH_NI.md §11.4),`library.tree` 必须带出来,
  否则用户填了也看不到。

### 13. 文件名分组:不确定时宁可分细

`importers/grouping.py` 的判据是**先求全组公共前缀,再看第一个差异位**:

- 差异位全是序号尾巴(`scan01`/`scan02`)→ 合成一组(同实验多次扫描)
- 否则按该位置取值分组(`GENBB-1` / `GENBB-2` 是两个实验)

**不要改回"逐个剥掉尾部数字段"。** 那个思路在用户的真实命名上必然失败:
`GENBB-1-14-20260915-125048` 里 `1` 是实验编号、`14` 是扫描序号,两者都是
纯数字段,单看一个文件名无法区分 —— 旧实现剥掉了 `1`,把两个实验并成一个。

**也不要加"名字互为前缀就合并"的启发式**:那会把 `GENBB-1` 与 `GENBB-10`
并成一个。字符串前缀关系与"是不是同一个实验"无关。

`datasets.scan_folder` **只做建议,不落库**。分组结果必须经 UI 确认
(可改名)才调 `datasets.import`。理由:猜对只省一次点击,猜错的代价是
数据已经落库、用户很难发现哪几条归错了。

### 14. 归属依据必须一路带到 UI

`Peak.dominance`(该反射在邻域内贡献的强度占比)与 `hkl_candidates` 从
`assign_peaks` 算出后,要经 `_annotate_pipeline_peaks` → `peaks` 表 →
`peaks.list` → 前端峰表 tooltip 全程不断。

**在中途任何一环丢掉,前端就会显示「归属依据:该反射贡献 0%」** —— 而 0%
不是"没算",它是"这个反射不贡献强度",一个相反的意思。这比不显示更糟。
`peaks` 表的这两列由 `SCHEMA_VERSION` 4 的迁移加上,不要删。

### 15. Zustand 选择器绝不能返回新对象/新数组

```ts
// ✗ 白屏:选择器每次调用都返回新数组引用
const list = useProjectStore((s) => s.samples.filter((x) => x.project_id === id));

// ✓ 先取原始引用,派生交给 useMemo
const all = useProjectStore((s) => s.samples);
const list = useMemo(() => all.filter((x) => x.project_id === id), [all, id]);
```

Zustand v5 **去掉了默认的浅比较**,`useSyncExternalStore` 每轮渲染都判定
"快照变了"→ 无限重渲染 → React 抛 `Maximum update depth exceeded` 并**卸载
整棵树**。因为 `ImportSheet` 这类组件是常驻挂载的(空状态也要能导入),
一处写错就是**整个界面白屏**,而不是某个局部失效。

控制台里的判据是这句:`The result of getSnapshot should be cached to avoid
an infinite loop`。看到它就是这个原因,不用往别处找。

返回原始值(`s.trash.length`、`s.currentProjectId`)的选择器是安全的;
返回对象字面量 `(s) => ({a: s.a})` 同样不安全,一样要拆开。

### 16. 多谱对比:各自归一化,且不参与点击

`plotStore.compareCurves` 是**显示层的参考曲线**,与 `analysisStore.curve`
(当前分析对象)是两回事 —— 别把对比曲线塞进 analysisStore,那会让"当前
数据集"与"对比曲线"两个概念混在一起。

- **每条曲线用各自的 maxY 归一化**。共用当前曲线的标度会让强度差 10 倍的
  样品看起来一样高,叠加就失去意义。
- **对比曲线 `silent: true`**。不静默的话,点一条参考曲线的峰会选中当前
  数据集的峰,张冠李戴。
- stacked 模式下主曲线永远在最上层 lane —— 它是分析对象,放中间会被
  误认成参考曲线。
- 上限 6 条。再多颜色分不清,图例也会挤成一团。

### 17. 晶胞参数有两条路径,不可混画

| 路径 | 位置 | 零点校正 |
|---|---|---|
| `refine_cell`(全谱精修) | `high_ni/indexing.py` | **有** |
| `_lattice_result`(3–7 峰最小二乘) | `high_ni/analysis.py` | **无**(恒 0) |

两者在同一张谱上给出**不同的 a/c**。`domain.high_ni.analyze` 的结果里并列
存两份(`fullCell` / `lattice`),结构趋势图**只用 `fullCell`**。

把两条路径的值画进同一条趋势线,用户看到的"结构演变"里有一部分其实是
**换了算法**而不是样品变了 —— 这比不给趋势图更糟,因为它看起来非常像
一个结论。`high_ni/trend.py` 的模块 docstring 有完整说明。

### 18. 趋势图的缺失值必须是 None,不是 0

`trend.py` 里没算过的数据集返回 `value: None`,UI 画成空心点、不连线、
不插值。原因:

- 当成 0 会画成"晶胞塌了",是一个错误的结论。
- 跳过会让曲线在缺失处看起来连续,用户以为那里有数据。
- 插值更糟 —— 它是编的。

温度解析同理:用户会填 "750C"、"750 ℃"、"室温",取不到数值的排到序列
**末尾**而不是当 0(当 0 会排到最前面,趋势图开头凭空多一个点)。

### 19. 曲线走二进制信封,帧格式是旧格式的超集

```
[u32 BE jsonLen][json header][binary body]
```

数组按 `data.__bin__.layout` 的顺序切成连续 `f32[n]` 段,长度写在
`__bin__.bytes`。**长度前缀只覆盖 JSON 头** —— 没有二进制体时字节序列
与旧的 `[u32 jsonLen][json]` 完全一致。

改这一层前先想清楚:传输层一旦错位,表现是"整个软件打不开任何数据"
而不是"性能没变好"。所以:

- 引擎侧唯一出口是 `curve_payload()`,二进制体挂在返回值的 `_binary`
  键上,由 `write_frame` 取出拼到帧尾并摘掉 —— 直接 `json.dumps`
  `curve_payload` 的返回值(测试、dev_server)不会因为 bytes 报错。
- Rust 侧 `engine_command` 返回 `tauri::ipc::Response`。**不要改回
  `Value`** —— serde 会把二进制体转成 JSON 数组(每字节一个数字),
  比原来的 `number[]` 还慢。
- 前端 `unwrapResponse` 校验长度,对不上就抛错。截断的曲线画出来是
  "谱图少了后半段",用户完全看不出是传输问题。
- `CurvePayload.x/y` 是 **Float32Array**,不是 `number[]`。mock 必须
  返回同样的类型。

### 20. `__bin__` / `_binary` 的查找必须递归

信封**不一定在 `data` 顶层**:`datasets.curve` 在顶层,而
`analysis.preview` 嵌在 `data.curve` 里。四处查找必须都是递归的:

| 位置 | 只查顶层的后果 |
|---|---|
| 引擎 `write_frame` 的 `_strip_binary` | `json.dumps` 抛 TypeError,**引擎进程直接退出** |
| Rust `read_frame` 的 `find_bin_len` | 二进制体留在管道,**污染后续每一帧** |
| 前端 `attachBinary` | preview 曲线解不出来,图上没曲线且**不报错** |
| 测试 `_recv` | 读错长度,后续帧全错位 |

这四处**曾经全都是只查顶层**,是 `tests/test_protocol_e2e.py`(真的起
子进程走完整协议)抓出来的 —— 进程内测试直接调 `Engine.handle`,不经过
`write_frame`,所以看不见。**改传输层后必须跑那组 e2e 测试。**

### 21. 缓存键必须是整段参数前缀

`processing/pipeline.py` 的阶段缓存按**参数前缀**分组:

- 背景只依赖 `(crop, background)`
- 平滑依赖 `(crop, background, smoothing)`

只放单个字段会出事:用户同时改 crop 与 background 时,只比 background
会命中一个**用旧 crop 算出来的背景** —— 曲线看着正常,数值全错。

实测 AsLS 背景 23 ms、SG 平滑 0.38 ms(差 60 倍),所以这个缓存对
"拖平滑窗口"这个最常见操作收益最大。

曲线解析缓存(`Engine._curve_cache`)同理,指纹用
`(stored_path, file_hash, x_axis_type, wavelength)` 而不是 id ——
数据集删除后 id 会复用。删数据集时两个缓存都要清(见 `_remove_files`)。

## 当前状态与已知缺口

### 无 UI 入口的引擎能力

| 命令 | 对应需求 |
|---|---|
| `peaks.list` | 峰表本可走它读库(现走 preview/state 内存峰) |

`presets.*`(S5)与 `peaks.update_bounds`(M4)已有 UI 入口 ——
Inspector 参数预设区、峰表双击左界/右界。

### 已知缺口

- **灵敏度映射表未经真实谱人工校准**(`peaks/detection.py` 的 `SENSITIVITY_PROMINENCE_FACTOR` 与 `SENSITIVITY_LOCAL_SIGMA`),需 5–10 张实测谱评审。`CALIBRATION_VERSION` 仍是 `0.1-initial`。
- **高镍识别单物相假设**:只建 R-3m 层状相。样品含杂相(残碱 Li₂CO₃/LiOH、尖晶石)时未建模的峰会报 `none` 而非误标 —— 这是刻意的保守行为;UI 已有"存在 N 个未归属峰"警告(Inspector),但识别本身仍不建杂相模型。
- **(006)/(102) 在 FWHM ≳ 0.35° 时物理上不可分**(间距/FWHM ≈ 1.0)。此时应返回 `ambiguous` 或只有一个分量,不要试图硬分。宽峰时 `assign_peaks` 已用 1.2× 间距触发降级。
- PDF 导出未实现;CSV、PNG、SVG 已可用。
- Sidecar 打包路线已定:**PyInstaller onedir**(`build-installer.bat` 出 NSIS 安装包,sidecar 为同目录 `xrd-engine.exe`);`XRD_ENGINE` 注入点保留,用于指定自定义引擎可执行文件。
- 前端无测试框架(Vitest/ESLint 都没有);类型检查靠 `npm run build` 里的 `tsc`。
- `material_profiles` 无 UPDATE/DELETE,前端每次「保存档案」都新增重复行。

### 环境事实(不是缺口,但会影响判断)

- 数据库 `SCHEMA_VERSION = 7`;加列走 `db.py` 的 `_MIGRATIONS` 幂等迁移,**不重建表**。
  迁移的 `version` 字段是**真门控**(读一次 `PRAGMA user_version`,小于目标才跑),
  早期版本的它被忽略过,别改回去。历史:v5 = `datasets.batch_id`+`samples.links_json`,
  v6 = `analyses.annotation_json`(标注持久化);`batches`/`ec_*` 建表走 `_SCHEMA`
  的 `CREATE IF NOT EXISTS`。
- **用户真实数据在 `%LOCALAPPDATA%\XRDStudio\data\<ULID>\original.txt`**
  (Rigaku SmartLab,Cu Kα1=1.540593,5–100°,步长 0.02°,4751 点),是验证识别
  算法的宝贵资产 —— 改动 `indexing.py` 后应在其上回归。
  **跑任何写操作前先确认 project/sample id**:这份库是用户的实际工作数据,
  不是测试夹具。只读检查用 `library.tree` 先看清楚层级再动。
