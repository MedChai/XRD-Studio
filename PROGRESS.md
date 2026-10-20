# XRD Studio V0.3 — 项目进度汇报

> **日期：** 2026年09月24日
> **状态：** 标注持久化 + 高镍无钴识别修正 + 使用缺口批量修复 + 防卡顿优化 已落地
> **代码基线：** commit `8995df5` + 工作区未提交改动(标注/预设/bounds/导出/性能等)
> **依据文档：** README.md(Rev 2 开发文档)、docs/DEVELOPMENT_HIGH_NI.md、CLAUDE.md

---

## 一、总体结论

软件已经**跑通完整真实链路**：Tauri 窗口中导入 XRD 文件 → 归属实验/批次 →
查看谱图 → 背景/平滑/寻峰实时预览 → 峰表联动 → 多谱对比 → 结构趋势，
全链路由真实 Python 引擎执行（非 mock）。引擎 **392 个自动化测试全部通过**，
Rust/TypeScript 编译零错误，性能基准达标。

**本轮(2026-09-24)交付摘要**：

1. **标注持久化(SCHEMA v6)**：`analyses.annotation_json` 存晶胞+labels+pipelineHash；
   打开图只读库零精修(亚秒见标签)；从未标注且有峰时后台补标(非阻塞)；
   调参保留旧标签+「已过期」提示，点「标注晶面」才刷新；commit 不再删标注。
2. **高镍无钴 (006)/(102) 识别**：domain 双峰配名改用精修胞(不再写死 2.878/14.216)；
   显示名与文献对齐为 (102)/(108)；宽峰(FWHM ≥ 1.2× 间距)降级 ambiguous 不硬给 reliable；
   none 保留近邻弱匹配候选；新增无钴 fixture `cobalt_free_01.xy` 回归。
3. **使用缺口**：实验信息入口(含波长)、导入去掉必坏的「不分组」、命令面板「检测峰」、
   **峰表 bounds 双击编辑**(F2)、**参数预设 UI**(F1)、文件夹导入 needColumns/duplicates、
   SVG 导出、Esc 关趋势、空实验引导、混排积分边界显示、死 key 清理。
4. **防卡顿**：拖动 preview 跳过 FWHM/Area(`compute_peak_metrics:false`)，收尾补全；
   Zustand 全量解构改逐字段选择器(§15)；`_previewTimer` 真 clearTimeout；
   undo 400ms 窗口合并；`_FIT_CACHE` 32→64；purge 补 `clear_stage_cache()`。

开发过程中测试驱动发现了 **26 个会静默出错的真 bug**（第五节 5 个 + 第八节
B1–B12 共 13 个 + 第十二节 B13–B19 共 7 个 + 第十四节 1 个），全部已修复 ——
这正是 README Rev 2「出口条件是测试，不是形容词」原则的兑现。

**其中特别值得记住的一类**：缺陷不是"功能没做"，而是"做了但没生效" ——
引擎算好了却在写回时丢掉（`dominance`）、表建好了却从来不读
（`domain_results`）、按钮画好了却只改个标签（Overlay/Stacked）、
命令实现了却没有任何入口（`presets.*`）。这类问题靠"看代码觉得有"
完全发现不了，只能靠端到端测试与真实数据回归。

---

## 二、里程碑进度

| 里程碑 | 状态 | 说明 |
|---|---|---|
| **M1 App Shell** | ✅ 完成 | 全部布局组件 + 设计 token + 双主题 + 键盘可达 |
| **M2 Import & Plot** | ✅ 完成 | 导入/解析/SQLite/Data Store/真实绘图/**二进制 Envelope** 已通并验证；sidecar 已选定 **PyInstaller onedir** 并出 NSIS 安装包 |
| **M3 Processing** | ✅ 第一版完成 | 背景/平滑/裁剪/preview 状态机/重采样已在引擎落地；Crop UI、当前视图范围填充和处理范围同步已接通 |
| **M4 Peaks** | 🟡 引擎+UI 完成 | 寻峰/FWHM/Area + FWHM 2% 精度验收已过；bounds 双击编辑(F2)已接 `peaks.update_bounds`；**待办：** 灵敏度映射表人工校准(需 5–10 张实测谱评审) |
| **M5 Export & Presets & Undo** | 🟡 第一版完成 | Undo/Redo、峰表 CSV、处理后数据 CSV、PNG、**SVG**、**预设 UI(F1, S5)** 已实现；**待办：** PDF 导出、`material_profiles` UPDATE 去重 |
| **M6 Polish & Installer** | 🟡 安装包已出 | NSIS 安装包可用（`build-installer.bat` → `XRD Studio_0.3.0_x64-setup.exe`，含 onedir sidecar）；**待办：** §11.4 环境矩阵（干净 Win10 LTSC / 中文用户目录 / SmartScreen） |

**README §1.2 场景补充**(不属 M1–M6，但已交付):

| 场景 | 状态 |
|---|---|
| S4 系列对比（Overlay / Stacked 多谱对比） | ✅ 已实现(PROGRESS 第十三节) |
| 结构趋势图（`docs/DEVELOPMENT_HIGH_NI.md` 阶段 H） | ✅ 已实现(PROGRESS 第十三节) |

---

## 三、已交付内容（按层）

### 3.1 Python 分析引擎（33 模块 · 约 7,600 行 · 392 个测试 · ruff 零告警）

| 模块 | 对应规范 | 要点 |
|---|---|---|
| `coords.py` | §4.1 | 2θ ↔ d ↔ Q 换算，NIST Si (111) 认证值交叉校验 |
| `processing/grid.py` | §4.2 | 均匀性检测（1e-3 判据）、线性重采样（防过冲专项测试）、峰位反插值映射 |
| `importers/text_parser.py` | §7.3 | CSV/TXT/XY/DAT；4 种分隔符自动识别；UTF-8-BOM/GB18030/Latin-1 编码回退；垃圾头/注释行/科学计数/缺失值占位处理 |
| `importers/validate.py` | §7.2 | 全部边界行为：NaN 剔除、重复 x、降序翻转、全平线、角度合理性提示 |
| `importers/loader.py` | §42 | 列启发式猜测，猜不准交列映射（宁问不猜错） |
| `processing/background.py` | §7.3 | AsLS（稀疏三对角，λ 10²–10¹⁰）+ 多项式（迭代剔峰 + x 归一化防病态） |
| `processing/smoothing.py` | §7.3/§7.1 | SG 约束校验（奇数窗口、10% 限幅）+ 宽化量估算供 UI 提示 |
| `processing/pipeline.py` | §7.1 | Crop→BG→Subtract→Smooth→Peaks→Metrics 链序（v1 修正项），平滑影响度量的标记传递 |
| `peaks/detection.py` | §7.3/§37 | find_peaks 语义参数映射表、peak_widths 半高交点 FWHM、邻峰边界截断 |
| `peaks/metrics.py` | §38 | 局部极小值边界、梯形积分（端点插值）、边界编辑重算 |
| `database/db.py` | §8.2 | 基础表 + 材料档案/领域结果 schema、WAL、查重/树查询 |
| `services/data_store.py` | §8.3/§8.4 | 复制导入 + SHA-256 + metadata.json、路径逃逸防护、缓存/导出目录 |
| `api/commands.py` | §10.4 | 导入、处理、导出、材料档案和高镍分析命令，错误协议收敛为人话 |
| `high_ni/` | DEVELOPMENT_HIGH_NI | Li/Ni 混排筛查、R-3m 层状特征、晶胞参数 |
| `high_ni/indexing.py` | §3 铁律 | **晶体学指数化唯一事实来源**:允许反射筛选、全谱晶胞精修、按强度贡献占比归属 |
| `high_ni/trend.py` | 阶段 H | 结构趋势序列组装(温度/批次/顺序 × a/c/c·a⁻¹/晶胞体积/I003-I104/FWHM/Rwp) |
| `peaks/annotate.py` | 阶段 F/G | 晶面(hkl)标注;三态置信度 reliable/ambiguous/none;晶胞精修缓存 |
| `processing/downsample.py` | §9 | min-max bucket 降采样(只影响显示,指标仍用全量) |
| `importers/grouping.py` | 用户反馈 | 文件名分组(先求公共前缀再看第一个差异位),只建议不落库 |
| `api/envelope.py` | §9.1 | 曲线二进制信封:Float32 交错数组打包/解包 |
| `__main__.py` | §11.2 | stdio 长度前缀帧协议 + 可选二进制体 + ping 心跳;有真实子进程 e2e 测试 |
| `scripts/make_fixtures.py` | §12.0 | 固定种子生成 17 个 fixture（LFP/NCM 合成谱、非均匀网格、GB18030、垃圾头、NaN、降序…） |

### 3.2 Tauri Shell（Rust，382 行，编译零错误零警告）

- **Sidecar 监管**（§11.2）：spawn / stdin-stdout 帧桥接 / 请求 id 路由 / stderr 落 `logs/engine.log`
- **二进制透传**（§9.1）：`read_frame` 读回帧尾二进制体，`engine_command` 返回 `ipc::Response` 走原始字节通道（不走 serde，否则每个字节会变成一个 JSON 数字）
- **崩溃语义**：EOF 时失败全部 pending 请求并回收句柄，下次调用自动重启
- **启动互斥**：并发请求不重复拉起引擎（实测修复前 4 实例→修复后 1 实例）
- Data Store 定位 `%LOCALAPPDATA%\XRDStudio`（§11.4 中文路径规避）
- dev 环境自动定位 `engine/.venv` 的 Python；`XRD_ENGINE` 环境变量可注入（为打包实验预留）

### 3.3 React 前端（28 个 ts/tsx · 约 6,200 行 · tsc 零错误 · 构建通过）

- **设计系统落地**（§6）：全部 token 以 CSS variable 固化、Light/Dark、tabular-nums、140–180ms 克制动效、毛玻璃层
- **全布局**（§15–§35）：TopBar / LibrarySidebar / PlotHeader / ECharts 图谱 / PeakDrawer / Inspector / StatusBar / ImportSheet / CommandPalette / SettingsDialog / Toast
- **交互规范实现**：Crosshair + 状态栏读数、峰标签(三态配色:reliable/ambiguous/none)、Plot↔Table 双向联动、AsLS λ 对数刻度滑块、Advanced 折叠
- **多谱对比（S4）**：真叠加/真堆叠（各占一条 lane，主曲线在最上层）、对比 chip 条、每条曲线各自归一化
- **结构趋势（阶段 H）**：温度/批次/顺序 × a/c/c·a⁻¹/晶胞体积/I003-I104/FWHM/Rwp；未分析的点画空心、不连线不插值；点数据点回到该图谱
- **实验层级**：项目 > 实验 > 批次(可选) > 数据，四层都可重命名；「新建实验」按钮；实验信息编辑弹窗
- **导入归属二选一**：新建实验 / 已有实验（「不分组」已移除 —— 引擎 sample_id NOT NULL,选了必报错）；文件夹导入带分组确认（可改名），needColumns/duplicates 进入与单文件相同的映射/去重 UI
- **preview 状态机**（§10.3）：30ms 节流 + 序号 cancel 语义 + 300ms autosave + Saved/Computing 状态
- **Undo/Redo**（§10.5）：有界快照栈，复用 preview→commit 通路
- **前端本地显示层**（§9.2）：归一化/Offset 零引擎往返
- 平滑偏差提示（§7.1）：Inspector 显示「当前 FWHM / Area 在平滑后曲线上计算」
- 开发期 mock 引擎：浏览器里 `npm run dev` 不开 Tauri 也能完整驱动 UI

### 3.4 测试与基准

- **392 个 pytest**：单元（coords/grid/parser/validate/high-Ni/indexing）+ 数值精度（FWHM 2%、AsLS 背景还原、面积解析解）+ 集成（API 全流程含查重、列映射、软删除、导出、材料档案、批次、趋势）+ **真实子进程协议测试**（`test_protocol_e2e.py`：真的起引擎、用真实帧对话）+ 标注生命周期/无钴 fixture 回归
- **分组算法测试**（`test_grouping.py`）：重点在"不同实验绝不能被合并"
- **缓存正确性测试**（`TestStageCache`）：含两条反向用例（改背景/改 crop 必须重算）
- 性能基准（§9.4 harness，3× 红线防抖）：

| 项目 | 实测 | §9 目标 | 结论 |
|---|---|---|---|
| AsLS 15k 点 | 41 ms | 30 ms | 红线内，后续可缓存求解器优化 |
| AsLS 100k 点 | 315 ms | 300 ms | 红线内，同上 |
| 全链 15k 点 | 81 ms | 100 ms | ✅ |
| 全链 100k 点 | 655 ms | — | 印证降采样 preview 必要性（设计正确） |
| 导入 15k 点 | 103 ms | 1 s | ✅ |

---

## 四、已验证的用户主流程（§77 Success Flow）

```
拖入/选择 .xy 文件 → 建项目/实验 → 立即看图(真实渲染)
→ Inspector 开背景(AsLS) → 调灵敏度 → 峰表出现 → 点峰联动定位
→ 加入对比叠加 → 填温度 → 看结构趋势
```

已在真实 Tauri 窗口端到端验证通过(数据实际落盘 `%LOCALAPPDATA%\XRDStudio`)。
高镍识别另在用户的真实 Rigaku SmartLab 数据(4751 点)上回归:
14 个峰检出、11 个获得晶面归属、无禁阻反射、a=2.885 c=14.291 Rwp=0.084。

本轮根据使用反馈修复：**「创建不了新项目」**——此前 UI 无入口调用 `projects.create`（顶栏按钮是摆设），现已补三个入口：顶栏项目下拉（切换+新建）、侧栏 `+ New Project`、命令面板命令；空库时 Import 会引导先建项目而非静默失败。

> **勘误:**本节此前称主流程「已端到端验证通过」,但其中的**「点峰联动定位」
> 实际从未生效** —— 图表 click 回调闭包捕获了首渲染的空 peaks 数组,而那个
> effect 只在挂载时跑一次。本轮已修复(见第八节 B1),并补上浏览器验证。

---

## 五、测试驱动发现并修复的真 Bug

| # | 问题 | 后果（若不修） | 修法 |
|---|---|---|---|
| 1 | 寻峰强度范围用 P99.5–P0.5，细网格上窄峰占比 <0.2% 导致 range 坍缩 | **基线噪声检出 312 个假峰** | 改 max − median（对噪声稳健且覆盖最强峰） |
| 2 | 峰边界谷底搜索两侧比较方向写反 | Area 退化成 FWHM 量级（少 23%） | 修正比较方向 + 噪声用例覆盖 |
| 3 | pipeline 的 Area 误用 FWHM 边界 | 违反 §38「默认边界取局部极小值」 | 统一局部极小值边界 |
| 4 | Rust `ensure_running` 检查-启动间隙并发竞态 | 首屏并发命令重复拉起引擎（4 实例） | 启动全程持互斥锁 |
| 5 | AsLS/文案中 `{x!g}` 无效转换符 | 运行期 f-string 崩溃 | `{:g}` + ruff 语法级把关 |

另有 2 处测试期望写错（NIST Kα1 vs Kα 加权波长混用），已修正并在注释中记录该坑。

---

## 六、当前风险与缺口

| 风险/缺口 | 等级 | 计划 |
|---|---|---|
| 灵敏度映射表未经真实谱人工校准 | 中 | M4 出口条件，需要你提供 5–10 张典型实测谱 |
| 前端感知指标（切样 <150ms、绘制 <300ms）未自动化测量 | 中 | 引擎侧基准已有（`test_benchmarks.py`），前端感知仍缺 |
| PDF 导出未实现 | 中 | SVG 已可用；PDF 按需补 |
| 前端 Vitest/ESLint 尚未建立 | 中 | 补齐状态机、导入分组、对比模式等纯逻辑测试 |
| `material_profiles` 无 UPDATE/DELETE | 低 | 前端每次「保存档案」都新增重复行 |
| 高镍识别单物相假设 | 中 | 含杂相(残碱/尖晶石)时未建模峰报 `none`;UI 应提示"存在未归属峰" |
| Bundle 1.4MB（ECharts 全量引入） | 低 | 拆包 + 按需注册图表组件 |
| 中文路径/无 WebView2/Defender 环境矩阵 | 中 | 安装包已出，发布前按 §11.4 在干净 VM 过一遍 |

已解决(本轮移出):

- ~~Sidecar 打包未决策~~ → 路线 B **PyInstaller onedir** + NSIS resources 同目录 `xrd-engine.exe`（`engine/scripts/build_sidecar.py`，2026-09-24）
- ~~曲线传输仍是 JSON `number[]`~~ → 二进制 Envelope 已实装(第十四节)
- ~~二进制 Envelope 未实现~~ → 同上
- ~~结构趋势图未实现~~ → 第十三节
- ~~多谱对比(Overlay/Stacked)是空壳~~ → 第十三节
- ~~Preset 无 UI 入口~~ → Inspector 参数预设区(F1, 2026-09-24)
- ~~`peaks.update_bounds` 无 UI 入口~~ → 峰表双击左界/右界(F2, 2026-09-24)
- ~~SVG 导出未实现~~ → 命令面板「导出当前图表 (SVG)」(F6)
- ~~标注不持久化/调参即丢~~ → SCHEMA v6 annotation_json + stale 提示(2026-09-24)

---

## 七、下一步计划（按序）

1. **Preset / 参数复用 UI(S5)**——引擎三个命令与 `analysis_presets` 表都在，缺的是入口；这是 README §1.2 的核心场景
2. **`peaks.update_bounds` 的 Bounds 编辑 UI**——M4 出口条件之一
3. **前端 Vitest/ESLint 基线**——锁住 preview 取消、Crop 校验、Undo/Redo、导入分组、对比模式等纯逻辑
4. **SVG / PDF 导出**——README §1.2 S6
5. **高镍参考样品基线**——支持用户参考样品分布，替代当前仅有的经验提醒阈值
6. **安装包环境矩阵**（§11.4）——干净 Win10 LTSC、中文用户目录、Defender 开启下过一遍 NSIS 安装包；必要时把 WebView2 从 embedBootstrapper 升到 offlineInstaller

已完成(移出本清单):

- ~~Sidecar 打包决策实验~~ → 路线 B PyInstaller onedir，NSIS 包已出（`build-installer.bat`）
- ~~二进制 Envelope 实装~~ → 第十四节
- ~~结构趋势图~~ → 第十三节

---

## 附：仓库状态速览

```
commits:  4(初始 → bugfix → 竞态/基准 → 项目创建)
engine:   33 py 模块 · 392 tests · ruff clean · ~7.6k 行
app:      30 tsx/ts/css · tsc zero-error · build passed · ~6.5k 行
rust:     382 行 · cargo check 零警告
fixtures: 18 个可再生基准文件(seed 20260915,含 cobalt_free_01)
引擎命令: 30 个(前端实际调用含 presets/bounds/list)
DB:       SCHEMA_VERSION = 6(幂等迁移,不重建表;v6 加 annotation_json)
运行环境: Python 3.14.7 · Rust 1.98.1(MSVC) · Node 24 · Tauri 2.11
```

---

## 八、本轮修复(用户 6 项使用反馈 + 排查发现的 13 个缺陷)

用户在真实使用后提出 6 个问题。排查后确认**第 2 条和第 3 条是同一个 bug**,
另有 13 个已确认缺陷一并关闭。分三个 commit 落地,每个都保持测试与类型检查全绿。

### 8.1 用户反馈的 6 项

| # | 反馈 | 定位 | 修法 |
|---|---|---|---|
| 1 | 需要中文操作界面 | 文案中英混杂硬编码在 13 个文件里,无 i18n 机制 | 新增 `app/src/i18n/zh.ts` 作为单一来源,`as const` 让拼写错误在 tsc 阶段报错;保留 2θ/FWHM/AsLS 等领域术语与产品名不译 |
| 2 | 软件中间有小圆点 | **与第 3 条同源**:Radix `Slider.Root` 不注入 `position: relative`,而 Thumb 是 `position: absolute` 且为 Track 的**兄弟**节点(`.slider-track` 的定位上下文管不到它)。包含块退化成视口 → 手柄按**视口宽度**定位。1440 窗口下 Offset 默认值 0 → 50%×1440 = 720px,正好是绘图区水平正中 | `.slider-root` 补定位上下文,并把 4 处调用点收口到新增的 `RangeSlider` 封装(类名固定在组件内,"忘写 class"不再可能) |
| 3 | Processing 的滑块点击后显示异常 | 同上 —— 手柄不在轨道上,用户抓不到 | 同上 |
| 4 | 不能删除/重命名 | 引擎侧 `meta.rename`/`meta.soft_delete` 早已存在但**前端零入口**,且两命令都有缺陷;`db.restore` 实现了却无命令暴露 | 新增 `trash.list`/`meta.restore`/`meta.purge`,补 RenameDialog 与 TrashDialog,侧栏三层各加菜单 |
| 5 | 没有回到全局显示的按钮 | 无 fit/reset 实现,只有 `App.tsx` 的一句过时注释 | Fit All 三个入口(PlotHeader 按钮、双击图表、Ctrl+0)+ 命令面板 |
| 6 | 下方要加滑块滑动看峰 | 底部只有峰表与状态栏,无任何滑块 | 新增通栏 `RangeOverview`:整谱缩略图 + 可拖动范围框,与主图双向同步 |

### 8.2 第 2/3 条的因果验证

不靠"看起来修好了",用真实浏览器做了反向验证:注入 `position: static` 复现旧行为后,
三个手柄中心从 1261/1175/1372(各自轨道内)变为 **542 / 66 / 720** —— 正是各自百分比
× 视口宽度 1440 的结果(λ=1e5 → 37.5%×1440=540;window=7 → 4.08%×1440=59;
offset=0 → 50%×1440=720)。Offset 恰好落在 720 = 绘图区正中,与用户报告完全吻合。

### 8.3 一并修复的 13 个缺陷

| 编号 | 问题 | 后果 |
|---|---|---|
| B1 | 图上点峰选不中 | click 回调闭包捕获首渲染的空 peaks 数组(effect 只在挂载时跑)。**§35 联动从未生效**。另补 markPoint 处理 —— 峰标记圆点的 `componentType` 是 `"markPoint"` 而非 `"series"`,只判 series 会让用户点圆点完全没反应,而圆点恰恰是"峰"最自然的点击目标 |
| B2 | 列映射导入必然失败 | `setLastSampleId` 被放在 needColumns 的早返回之后。**凡是需要手选列的 CSV 都导不进来** |
| B2b | 引擎侧同一条链上还有一个 | `loader` 的「列数 > MAX_AUTO_COLUMNS 就要求列映射」判断在「调用方已显式给列号」之前 → **宽表永远导不进来**(选完列第二次调用仍撞在同一分支) |
| B3 | 背景曲线算了但从不显示 | 引擎已返回、TS 类型已声明,但无 series 也无 UI 开关 → 用户无法判断扣背景是否合理 |
| B4 | undo/redo 后残留 preview 覆盖曲线 | `undo()`/`redo()` 没递增 `_previewSeq`,拖滑块后立刻 Ctrl+Z 会被旧请求覆盖 |
| B5 | `meta.rename`/`soft_delete` 缺校验 | 空名静默成功;白名单只靠 `assert`(`python -O` 下变成注入面);**软删除不级联但查重也没过滤父级** → 删项目后重导同一文件仍判重并提示一个看不见的路径 |
| B6 | Toast 定时器泄漏 | 第二次 toast 会被第一条的定时器提前 2.2 秒清掉 |
| B7 | 窗口标题仍是脚手架默认值 | 显示 `Tauri + React + Typescript` |
| B8 | x 轴硬编码 10–80 | 不在该区间的实测谱被裁掉;Fit All 也无从实现。mock 数据恰好是 10–80°,所以从 M1 起一直隐身 |
| B9 | 「检测峰」没写 peaks 表 | 按钮只发 `analysis.commit`,而它只写 `pipeline_json` → 数据库 peaks 表始终为空 |
| B10 | 选中峰后表格不滚动 | §6.7 要求"点图内峰 → 表滚动选中" |
| B11 | PeakDrawer 缺 `shrink-0` 且高度上限固定 | 1280×760 下配合底部范围框会把图表区挤塌 |
| B12 | 导入遇重复即整体中止 | 引擎的 `import_anyway` 无人调用;`duplicates` 也没带 `datasetId`,无法"打开已有" |

### 8.4 Trash 语义(隐式级联)

删除只标记被直接删除的那一行,**不级联写子级的 `deleted_at`**。子对象因父级不可见而
一并从 Library 消失;恢复父级时它们自动全部回来 —— 因为从未被写过 `deleted_at`。

级联写会让「删项目 → 恢复项目」无法区分「这些样品是删项目时一起删的」还是「用户本来
就单独删过」,要正确处理就得再加一张操作日志表。隐式级联零迁移(`SCHEMA_VERSION`
保持 1),且恢复语义天然正确。恢复时**向上**级联恢复父级(否则用户点了恢复却看不到
东西),**向下不**级联 —— 这个不对称是隐式级联的直接好处,已在代码注释与测试里写明。

### 8.5 本轮验证抓到的自造 bug

写完不算完,用浏览器实测时抓到两个自己刚写出的 bug,均已修复:

1. `RangeOverview` 在 `curve` 为 null 时 `return null`,而初始化 effect 依赖为空数组
   **永不重试** → 范围框永久不出现。改为始终渲染容器。
2. `XRDChart` 的 setOption effect 依赖里没有 `curve` 本身 —— 同一份曲线切换数据集时
   `seriesData` 的 useMemo 返回同一引用,effect 不重跑,**图表根本不重绘**。这正是
   计划阶段就标记为"高风险"的那一项。

### 8.6 验证

- 引擎:215 个 pytest 全绿(基线 177,本轮 +38:test_trash.py 32 个 + 列映射往返契约 2 个 + 其他);ruff 零告警
- 前端:tsc 零错误;构建通过
- 浏览器实测(真实 Chrome,非 jsdom):圆点消失且手柄在轨道内、界面全中文(含设置五个
  tab)、重命名/删除/回收站完整路径/恢复/彻底删除二次确认、通栏范围框与绘图区对齐、
  滚轮正向同步、拖框反向同步、Ctrl+0 与双击 Fit All、缩放后切换数据集回到全域
  (与基准截图逐像素一致)、点峰选中并展开峰表、关峰标签标记即清除、背景虚线开则现
  关则无、Detect Peaks 后「峰未固化」消失、40 次连续滚轮无死循环无报错

### 8.7 尚未处理

- `LabeledSlider`(`controls.tsx`)现为死代码,待清理
- 前端仍无测试框架(README §12.0 声明的 `eslint + vitest` 尚未建立)。本轮的缺陷
  绝大多数是 jsdom 测不了的那一类,但 B4/B2/B6 三个纯状态机缺陷值得用 vitest 锁住
- 底部范围框在 1280×760 + 峰表展开时图表区约剩 380px,后续可考虑范围框可折叠

---

## 九、本轮(2026-09-17)高镍混排修复 + 晶面(hkl)标注

> 新增 `docs/DEVELOPMENT_HIGH_NI.md` 之外的功能落地,并修复了用户指出的
> **Li/Ni 混排计算逻辑问题** —— 用户怀疑是对的,有三处实质缺陷。

### 9.1 混排逻辑修复(`high_ni/analysis.py`)

**缺陷 1(最严重):Area(003) 用整窗积分,不是峰本体面积。** `_observe_single` 此前
`area = np.trapezoid(corrected, x)` 是对 003 峰窗(18.6±2.2°)整体积分,旁峰/隆起背底
全被算进 "Area(003)"。改为 `_peak_bounds`(半高交点 + ±FWHM 裕量)收敛峰本体边界,
再 `integrate_area` 积分 —— 与用户峰表 Area 同口径。实测边界从整窗(16.4–20.8°)收敛到
峰本体(18.34–18.86°)。

**缺陷 2:分级改用峰高比。** 面积比会被 `FWHM_003/FWHM_104` 系统性压低(实测清晰分层
样高度比 1.33 被压到面积比 1.14 → 误判 `high`)。文献惯用的混排筛查量是峰高比
I(003)/I(104)>1.2,已改为按高度比分级,面积比仅作参考报告。

**缺陷 3:R_order 语义收紧。** `R_order = [A(006)+A(102)]/A(101)` 现要求 006/102 真正
分裂(`splitStatus` 非 unresolved/not_found)才报告,否则返回 `None` + 警告。

**新增暴露:** `mixing.peaks_area_bounds`(003/104/101 峰本体边界)与三个 raw 指标
(面积比/高度比/r_order)现在都返回并展示在 Inspector,证据可追溯。

**数值验证**(合成层状谱):
```
修复前: area_ratio=1.14  height_ratio=1.33  risk="high"(误判)
修复后: area_ratio=1.14  height_ratio=1.33  risk="low"(正确)
        peaks_area_bounds: 003=[18.35,18.85] 104=[44.15,44.75]
```

### 9.2 晶面(hkl)标注

图谱与峰表此前只有 2θ 数值,没有晶面归属。新增引擎侧 `peaks/annotate.py`:

- **参考表**:`high_ni/profiles.annotate_refs()` 用 R-3m 高镍晶胞(a=2.878, c=14.216)
  反算 7 个典型反射的 2θ,物理自洽。
- **匹配**:按峰位最近匹配,容差 0.15°;`reliable / ambiguous / none` 三档置信度。
- **接入**:`analysis.preview` / `peaks.detect` / `peaks.annotate` 三个命令都返回并
  持久化 `hkl` / `hkl_confidence`(schema v2→v3,`peaks` 表加两列,幂等迁移)。
- **前端**:图上峰标签优先显示 `(003)` 等晶面(无则退回 2θ);峰表加「晶面」列;
  Inspector 新增「标注晶面」按钮;导出的峰表 CSV 带 hkl 列。
- **通用性**:参考表与高镍分析模块解耦,非层状样品峰落在容差外时返回 `none`,不硬套。

### 9.3 涉及文件

| 文件 | 改动 |
|---|---|
| `engine/xrd_engine/high_ni/analysis.py` | 混排三缺陷修复 + 暴露边界/raw 指标 |
| `engine/xrd_engine/high_ni/profiles.py` | 阈值改峰高比键 + `annotate_refs()` |
| `engine/xrd_engine/peaks/annotate.py` | **新建** 晶面匹配引擎 |
| `engine/xrd_engine/models/types.py` | `Peak` 加 hkl / hkl_confidence |
| `engine/xrd_engine/database/db.py` | schema v3(peaks 表加两列,幂等迁移) |
| `engine/xrd_engine/api/commands.py` | preview/detect/annotate 带 hkl;export CSV 加列 |
| `engine/tests/test_high_ni.py` | +5 混排回归测试 |
| `engine/tests/test_annotate.py` | **新建** 5 个晶面标注单测 |
| `app/src/types/engine.ts` | PeakResult / mixing 类型加 hkl / bounds |
| `app/src/i18n/zh.ts` | 峰表「晶面」列 / 标注按钮 / raw 指标标签 |
| `app/src/features/plot/XRDChart.tsx` | 标签优先显示 hkl |
| `app/src/features/peaks/PeakDrawer.tsx` | 「晶面」列 |
| `app/src/features/processing/Inspector.tsx` | 标注晶面按钮 + raw 指标展示 |
| `app/src/services/engine.ts` | mock 支持 annotate + bounds |

### 9.4 验证

- 引擎 242 个 pytest 全绿(基线 231,本轮 +11),ruff 零告警
- 前端 tsc 零错误,`npm run build` 通过
- 端到端实测(真实引擎):导入 NCM 合成谱 → preview 峰带 hkl → detect 持久化 →
  annotate 返回 5 个 reliable 标注;domain.high_ni.analyze 返回正确 risk 分级与边界

---

## 十、本轮(2026-09-17)高镍峰识别重构:晶体学指数化

> 用户反馈两条:**(006) 被标成 (102)**、**很多峰不显示晶面**。
> 在用户提供的真实 Rigaku SmartLab 数据上复现并定位到根因,重写了识别链路。

### 10.1 根因

**(a) 双峰名字与文献不一致 —— 后已按文献对齐显示名。**

1. 枚举/拟合的消光规则是 `-h+k+l ≡ 0 (mod 3)`(R-3m 阳离子 3a 位点)。
   文献在双峰上常写 `(102)`/`(108)`(h-k+l 约定),与本约定下的
   (012)/(018) 是**同一物理反射**(d 相同),只是名字对调。
   对外 label 已统一用文献名 `(102)`/`(108)`(`indexing._LITERATURE_HKL`);
   不要把显示名拿回 `allowed_hkl` 当禁阻判断。
   层状正极实验上的分工是:**(006) 弱、(102) 强**。

2. **逐峰最近邻匹配不看物理角色。** (006)/(102) 理论间距仅约 0.29–0.36°,
   最近邻只看几何距离,观测峰稍偏一点判定就翻转。

**(b) 很多峰不显示晶面 —— 三个独立原因。**

1. 参考表只有 7 条硬编码反射。(015)/(107)/(113)/(116)/(202)/(024) 等
   **在构造上就不可能被标注**;实测谱 14 个峰有 9 个在表外。
2. 只有 0.15° 绝对容差且**无零点校正**,`zero_shift` 是硬编码 0.0 的桩。
3. **峰值检测在高角失效**:prominence 门限对整谱强度范围取值
   (实测 max−median=87567,medium 档门限 4378),而高角弱峰
   (113)=4592、(116)=2808、(027)=2058、(208)=1775 直接落选。

另有两个静默缺陷:

- `_cmd_peaks_annotate` 用 `round(position, 6)` 做 `WHERE position = ?`,
  而表里存的是未舍入双精度值 —— **UPDATE 匹配 0 行且不报错**,命令仍
  返回 count>0,只有重载/导出时才暴露。
- 前端合并标注结果用固定 0.01° 容差,与引擎 0.15° 不匹配 → 常整批匹配
  不上,toast 却报成功。

### 10.2 新算法

新建 `engine/xrd_engine/high_ni/indexing.py`,作为**唯一事实来源**:

1. **反射由晶体学枚举**,不写死:`-h+k+l ≡ 0 (mod 3)` 筛选 + Bragg 反算,
   范围由数据决定(实测一张谱枚举出 22 个允许反射)。
2. **每张谱精修 (a, c, 零点)**:a/c 网格搜索 + 线性最小二乘(振幅非负)。
   实测用户数据精修出 a=2.884 / c=14.278,而旧表写死 2.878/14.216 ——
   这个差异足以让 (006)/(102) 的归属翻转。
3. **峰形建模**:Kα1+Kα2 双线(按 Bragg 重算 Kα2 位置,不用固定 Δ)、
   随角度线性展宽的 pseudo-Voigt、多项式背景(吸收低角非晶包)。
4. **归属用"邻域内相对强度贡献占比"**,不用最近邻距离;窗口随峰宽缩放
   (固定 ±0.6° 时 (110) 有 65% 面积漏进 (108) 窗口,(108) 被永久判成 (110))。
5. **置信度三态**:reliable / ambiguous(双峰未分开,返回候选而非丢弃)/ none。

峰值检测同步改为**局部噪声门限**(MAD 估计),并对灵敏度三档放宽
height 门限 —— 旧实现里 height 与灵敏度无关,medium 与 high 检出集合
完全相同,滑块在最需要它的档位上失效。

### 10.3 实测效果(用户真实 SmartLab 数据)

| | 修复前 | 修复后 |
|---|---|---|
| 带晶面标注的峰 | 5 / 14 | **11 / 14** |
| 38.12° 处 | (102) 误按禁阻语义处理 | **(102) reliable**(文献显示名) |
| 64.24 / 64.58° | (108) 被吞 / (110) | **(108) / (110) 各自正确** |
| medium 档检出 | 8 个 | **14 个** |
| low/medium/high | medium == high(滑块失效) | **严格单调** |
| 高角 (015)(107)(113)(116)(024) | 全部无标注 | **全部 reliable** |
| 精修晶胞 | 无(写死 2.878/14.216) | **a=2.884 c=14.278, Rwp=0.084** |

未标注的 3 个峰(21.1/21.8/22.5°)经查是低角非晶包上的起伏,不属于
R-3m 物相 —— 报 `none` 是正确的保守行为。

### 10.4 涉及文件

| 文件 | 改动 |
|---|---|
| `engine/xrd_engine/high_ni/indexing.py` | **新建** 晶体学指数化 + 全谱拟合 + 峰归属 |
| `engine/xrd_engine/peaks/annotate.py` | 重写:新增 `annotate_high_ni`;通用路径需显式参考表 |
| `engine/xrd_engine/peaks/detection.py` | prominence 改局部噪声门限;height 随灵敏度放宽 |
| `engine/xrd_engine/high_ni/profiles.py` | `annotate_refs` 委托给 indexing;双峰显示名用文献 (102)/(108);窗口收窄防重叠 |
| `engine/xrd_engine/high_ni/analysis.py` | 双峰分量按理论位置定位与配对(不再按 2θ 升序 zip) |
| `engine/xrd_engine/api/commands.py` | 标注走新路径;传样品波长;按主键写回 |
| `engine/xrd_engine/models/types.py` | `Peak` 加 `row_id` |
| `engine/scripts/make_fixtures.py` | NCM fixture 峰位改由晶胞反算 |
| `engine/tests/test_indexing.py` | **新建** 13 个晶体学/拟合/归属测试 |
| `engine/tests/test_annotate.py` | 重写:13 个(含禁阻规则、双峰、波长) |
| `engine/tests/test_high_ni.py` | 合成谱改用真实晶胞间距 |
| `app/src/features/plot/XRDChart.tsx` | 三态视觉可辨(ambiguous 弱化色 + 斜体) |
| `app/src/features/peaks/PeakDrawer.tsx` | 晶面列区分「未分开」;悬停显示归属依据 |
| `app/src/features/processing/Inspector.tsx` | 合并容差自适应;展示精修晶胞/零点/Rwp |
| `app/src/services/engine.ts` | mock 参考表同步文献名 (102)/(108) |

### 10.5 验证

- 引擎 **263 个 pytest 全绿**(基线 242,本轮 +21),ruff 零告警
- 前端 tsc 零错误,`npm run build` 通过
- 端到端(真实引擎 + 真实数据):导入 → detect → annotate → **数据库读回**
  → CSV 导出,全链路晶面一致;`peaks.annotate` 的 `updated=14` 证实
  持久化丢失已修

---

## 十一、本轮(2026-09-18)切换数据集卡顿

> 用户反馈:**多次切换不同的图就会很卡**。定位到上一轮引入的性能回归。

### 11.1 根因

上一轮把晶面标注改成全谱晶胞精修后,**`analysis.preview` 每次都会调用它**,
而前端在切换数据集、拖动任何一个滑块时都会发 preview ——
于是每切一次图就要重跑一次全谱精修。

实测单次 `refine_cell` **12.0 秒**(4751 点):

| 环节 | 耗时 |
|---|---|
| `_probe_shape`(峰形粗扫) | 1.0 s |
| 晶胞网格 1624 格 × 每格一次 BVLS 解(5.9 ms) | 11.0 s |

晶胞只取决于原始曲线与波长,**与处理参数无关** —— 同一数据集完全不必重算。

### 11.2 修复(三层,互不替代)

1. **粗到细三层搜索**(`indexing.refine_cell`)。残差面在 (a,c) 上实测
   光滑单峰(沿 c 的 RSS 曲线只有一个极小、无次峰),所以粗网格只要能
   落进正确盆地即可。改为 0.02 粗扫(138 格)+ 两轮细化(各 441 格),
   格点数 1624 → 1020,**12.0 s → 4.5 s**,晶胞与 Rwp 完全一致。

2. **精修结果按数据集缓存**(`annotate._FIT_CACHE`)。
   **这一层才是关键** —— 重复切换同一张图时直接命中缓存,不再重算。
   缓存键带**数据指纹**(点数、x 首尾、y 和、波长)而非仅 dataset_id:
   数据集删除后 id 可能复用,只按 id 缓存会拿到上一条曲线的晶胞,
   标签会静默错位。`meta.purge` 路径显式失效对应条目。

3. **前端不再在 peaks 变化时重新定位视口**(`XRDChart` 的 auto-zoom)。
   该 effect 依赖 `[selectedPeak, peaks]`,而 peaks 是每次 preview 都被
   整体替换的新数组 —— 拖任何滑块、切任何数据集都会把视口重新拽回
   ±4° 窗口,用户表现为"图自己跳走了",且每次 preview 多一轮
   setZoom → dataZoom → 重绘。改用 ref 记住已定位的峰序号区分两种情况。

### 11.3 实测

模拟 7 个数据集来回切换 3 轮(共 21 次切换):

| | 修复前 | 修复后 |
|---|---|---|
| 总耗时 | 约 252 s | **18.6 s** |
| 首次访问某数据集 | 12 s | 2.0–4.5 s |
| 再次切换 | 12 s | **89 ms** |

引擎测试套件本身也从 272 s 降到 107 s(测试大量走 preview 路径)。

### 11.4 涉及文件

| 文件 | 改动 |
|---|---|
| `engine/xrd_engine/high_ni/indexing.py` | 三层粗到细网格(`_COARSE_STEP`) |
| `engine/xrd_engine/peaks/annotate.py` | 晶胞精修缓存 + 数据指纹 + `invalidate_fit_cache` |
| `engine/xrd_engine/api/commands.py` | 传 `dataset_id` 进标注;purge 时失效缓存 |
| `app/src/features/plot/XRDChart.tsx` | auto-zoom 加 ref 去重,不再随 peaks 抖动 |
| `engine/tests/test_indexing.py` | +2 缓存行为测试(命中/按指纹失效/不污染) |

### 11.5 验证

- 引擎 **265 个 pytest 全绿**(+2),ruff 零告警
- 前端 tsc 零错误,`npm run build` 通过
- 端到端:7 数据集 × 3 轮切换,晶面标注结果前后完全一致(14 peaks / 11 labeled)

## 十二、本轮(2026-09-18)缺陷修复 + 实验层级

用户问"现在还有什么没开发的内容",并选定下一阶段范围。先做了一次
需求-实现对照(README Rev 2 §1–§16 + docs/DEVELOPMENT_HIGH_NI.md),
结论是:地基是扎实的(25 个引擎命令、23 个被前端调用、处理链/寻峰/回收站
全都真实可用),**缺口集中在"最后一公里" —— 引擎算好了、表建好了、
按钮画好了,但没有接上。**

### 12.1 查出的真缺陷(不是"没做",是"做了但没生效")

| # | 缺陷 | 后果 |
|---|---|---|
| B13 | `dominance` / `hkl_candidates` 在引擎里算完就丢 | 峰表 tooltip「归属依据:该反射贡献 X%」**永远显示 0%**,候选永远为空。0% 不是"没算",它是"这个反射不贡献强度"——一个相反的意思 |
| B14 | `domain_results` 只写不读(全仓库只有 INSERT/DELETE) | 切走再切回同一数据集要重算 1.5–4.5 s,而库里那份结果一直都在 |
| B15 | `presets.*` / `peaks.list` / `peaks.update_bounds` 五个命令前端零调用 | README §1.2 的核心场景 **S5 参数复用**完全没落地,`analysis_presets` 表在生产路径恒空 |
| B16 | 两个读取路径都丢了波长 | `datasets.curve` 与 `_load_dataset_curve` 只传 `x_axis_type`,波长静默退回默认 Cu Kα。Co 靶数据整体错位 |
| B17 | `_cmd_presets_save` 不校验 project | 非法输入变成「引擎内部错误」,违反 §68 人话要求 |
| B18 | `_cmd_export_figure` 不做溯源 | 前端传的 `dataset_id` 被静默丢弃,PNG 导出没有任何溯源头 |
| B19 | `read_frame` 只读一次头部 | 管道短读会让长度前缀与帧体错位,之后读到的长度是天文数字 → **进程一直等永远不来的数据,界面永远转圈且无任何错误信息** |

**B19 是写帧协议测试时发现的**:测试用每次只吐 3 字节的流模拟被切碎的管道,
头部读取直接返回 None。帧体的读取原本就循环了,头部却没有。

### 12.2 用户反馈:层级表达错误

原话:"每次导入文件选完项目后要我输入样品名称,然后他就是一个单独的
文件夹,不能再导入别的数据了,只有单个数据,正确的层级应该是,
项目>实验名称>数据,且都要支持重命名"。

核实结论:**引擎侧本来就支持**复用样品(`datasets.import` 接 `sample_id`)、
支持一次传多个 `paths`,前端也有这条代码路径 —— **缺的是入口**。用户在
UI 里没有任何办法把一批文件归到同一个实验下。

本机数据库的实证:5 个 Sample 的名字是 `00 / 111 / 111 / 新样品 / 1`,
`temperature`/`duration`/`material` 全部为空 —— 用户把"样品名"当临时占位符
在用,因为软件没给他别的地方放"实验"这个概念。

### 12.3 已实现

**缺陷修复**:B13/B14/B16/B17/B18/B19 已修复;**B15 只做了一半** ——
`presets.save` 补了参数校验,但 Preset 与 `peaks.update_bounds` 的
**UI 入口仍未做**(前端零调用),详见第六节风险表。

`peaks` 表加 `dominance`/`hkl_candidates`
两列(`SCHEMA_VERSION` 3→4,幂等迁移,老库数据保留);新增
`domain.high_ni.get` 命令读回已存结果,`source_hash` 不匹配时返回
`stale: true` 让 UI 提示重算。

**实验层级**:
- `batches` 表(`SCHEMA_VERSION` 4→5)+ `datasets.batch_id` + `samples.links_json`
- 层级:项目 > 实验(sample)> 批次(batch,可选)> 数据(dataset)
- `datasets.import` 新增 `batch_id`/`batch_name`,**同名批次复用**(不复用的话
  用户分两次导入同一批文件会得到两个同名批次,界面上完全看不出区别)
- 新增 `datasets.scan_folder`:扫描文件夹 → 按文件名建议分组(**不落库**)
- 新增 `samples.create` / `samples.update` —— 此前样品只能作为导入的副作用
  产生,"先建实验再导数据"做不到
- 侧栏新增「新建实验」按钮与批次层级渲染;四层都可重命名
- `library.tree` 带出 `material`/`batch`/`wavelength`/`notes`/`batches`

### 12.4 分组算法:改过一次

第一版按"逐个剥掉尾部数字段"取名,在用户的真实文件名上**必然失败**:

```
GENBB-1-14-20260915-125048.txt
```
`1` 是实验编号必须留,`14` 是扫描序号该剥,而两者都是纯数字段,单看一个
文件名无法区分。第一版剥掉了 `1`,把 `GENBB-1` 与 `GENBB-2` 并成了一个。

第二版改为**先求全组公共前缀,再看第一个差异位**:若差异位全是序号尾巴
(`scan01`/`scan02`)则合成一组,否则按该位置取值分组。命名策略上明确
**不做"名字互为前缀就合并"** —— 那会把 `GENBB-1` 与 `GENBB-10` 并成一个,
而字符串前缀关系与"是不是同一个实验"没有关系。**不确定时宁可分细**:
分细了用户一眼能看出并手动合并,分粗了数据已经落库、用户很难发现归错了。

### 12.5 验证

```
引擎:.venv/Scripts/python.exe -m pytest tests/   → 327 passed
     .venv/Scripts/python.exe -m ruff check .    → All checks passed
前端:npm run build                               → tsc 零错误,构建通过
```

新增测试:`test_grouping.py`(19 条,含"不同实验绝不合并"这条核心风险)、
`test_frame_protocol.py`(10 条)、`TestBatches`/`TestFolderScan`/
`TestSampleMetadata`/`TestAnnotationPersists`/`TestHighNiReadback`/
`TestPresetValidation`/`TestWavelengthPropagation`/`TestFigureExportProvenance`。

真实数据端到端(本机 5 个数据集 + 1 个 project):14 峰 / 11 标注保持不变,
dominance 全部非空且能落库读回,候选列表非空。

## 十三、本轮(2026-09-18)多谱对比 + 结构趋势 + 白屏修复

### 13.1 白屏(用户报告「开 start 启动完界面不显示」)

**根因是我上一轮引入的。** 在 `ImportSheet` 里加「已有实验」下拉时写成:

```tsx
const projectSamples = useProjectStore((s) =>
  s.samples.filter((x) => x.project_id === project),   // ← 每次都是新数组
);
```

Zustand v5 **去掉了默认的浅比较**,`useSyncExternalStore` 每轮渲染都判定
"快照变了" → 无限重渲染 → React 抛 `Maximum update depth exceeded` 并
**卸载整棵树**。因为 `ImportSheet` 是常驻挂载的(空状态也要能导入),
一处写错就是整个界面白屏。

**验证方式**:搭 jsdom 渲染环境把真实组件挂上去,先**回退修复确认能复现**
(`Maximum update depth exceeded`),再验证修复后挂载成功。只跑 `npm run build`
看不出这个问题 —— 它是运行时才炸的,tsc 完全看不见。

已写入 CLAUDE.md 第 15 条,含控制台判据 `The result of getSnapshot should be
cached to avoid an infinite loop`。

### 13.2 多谱对比(S4 系列对比)—— 此前是空壳

`PlotHeader` 的 Overlay/Stacked 按钮可点,但 `mode` 在全前端唯一的消费点是
改 y 轴名字,**切了图毫无变化**;`selectionStore.hiddenCurves` 定义后零调用。
比"没这个按钮"更糟 —— 用户以为功能在。

- `plotStore` 新增 `compareCurves` / `addCompareCurve` / `removeCompareCurve`,
  上限 6 条(再多颜色分不清、图例挤成一团)
- 数据集行菜单加「加入对比 / 移出对比」;`PlotHeader` 加 chip 条,点 × 移除
- `XRDChart` 的 series 改为数组构造:对比曲线 + 背景线 + 主曲线

**两个关键取舍:**

1. **每条对比曲线用各自的 maxY 归一化**,不共用当前曲线的标度。共用的话
   强度差 10 倍的样品在图上看起来一样高,叠加就失去意义 —— 那是"比较"和
   "拼接"的区别。
2. **对比曲线 `silent: true`**,不参与点击选峰。否则用户点一条参考曲线的峰,
   选中的却是当前数据集的峰,张冠李戴。

stacked 模式每条曲线占一条 lane(间距 55),主曲线永远在最上层 —— 它是当前
分析对象,放中间会让用户以为那也是一条参考曲线。已用独立的数学验证确认
overlay 全部落同一基线、stacked 各 lane 互不重叠。

### 13.3 结构趋势(阶段 H 唯一没做的部分)

- 新增 `high_ni/trend.py` + `trend.series` 命令 + `TrendPanel`
- x 轴:煅烧温度 / 批次 / 导入顺序;y 轴:a / c / c·a⁻¹ / 晶胞体积 /
  I(003)/I(104) / FWHM / Rwp
- 点数据点 → 切回该数据集主图

**修掉一个会给出错误结论的口径问题。** 晶胞参数在本仓库有**两条计算路径**:

1. `indexing.refine_cell` —— 全谱精修,**带零点校正**(晶面标注走这条)
2. `analysis._lattice_result` —— 只取 3–7 个峰最小二乘,**零点恒为 0**

两者在同一张谱上给出不同的 a/c。混画在同一条趋势线上,用户看到的"结构演变"
里有一部分其实是**换了算法**,而不是样品真的变了 —— 这比不给趋势图更糟,
因为它看起来非常像一个结论。

现在 `domain.high_ni.analyze` 的结果里并列存 `fullCell`(全谱精修)与
`lattice`(少峰),趋势图**只用 fullCell**,并在面板底部写明数据来源。

**未分析的点画成空心,不连线、不插值。** 直接跳过会让曲线在缺失处看起来
连续(用户以为那里有数据),插值更糟 —— 它是编的。

**温度解析不把非数值当 0**:用户会填 "750C"、"750 ℃"、"室温"。取不到数值
的排到序列末尾,而不是当 0(当 0 会排到最前面,趋势图开头凭空多一个点)。

### 13.4 验证

```
引擎:.venv/Scripts/python.exe -m pytest tests/   → 349 passed(原 327)
     .venv/Scripts/python.exe -m ruff check .    → All checks passed
前端:npm run build                               → tsc 零错误,构建通过
启动器:5 个脚本非 ASCII 行数均为 0(§9 硬要求)
```

真实数据端到端:`fullCell` a=2.885 c=14.291 Rwp=0.084 正确写入并读回;
趋势图 4 个点中 1 个有值、3 个为 `None`(未分析),符合预期。

## 十四、本轮(2026-09-18)二进制 Envelope + 增量缓存

### 14.1 二进制曲线信封(§9.1)

曲线此前走 JSON `number[]`,要穿过**四次 JSON 序列化**(前端 stringify →
Rust from_str → Rust to_vec → 引擎 json.loads),回程再来四次。

新协议:`[u32 BE jsonLen][json header][binary body]`,数组按
`__bin__.layout` 顺序切成连续 `f32[n]` 段。

**关键设计:这个格式是旧格式的严格超集。** 没有二进制体时字节序列与
`[u32 jsonLen][json]` 完全一致 —— 绝大多数命令(建项目、改参数、查库)
的帧不会因此变长一个字节。这是刻意的:传输层一旦错位,表现是
"整个软件打不开任何数据"而不是"性能没变好"。

四层都改了:

| 层 | 改动 |
|---|---|
| 引擎 `api/envelope.py`(新) | `pack_arrays` / `unpack_arrays` |
| 引擎 `__main__.py` | `write_frame` 拼二进制体,并从 data 里摘掉 `_binary` |
| Rust `lib.rs` | `read_frame` 读回二进制体;`engine_command` 返回 `ipc::Response`(原始字节通道,不走 serde) |
| 前端 `engine.ts` | `unwrapResponse` / `attachBinary` → `Float32Array` |

实测:4751 点曲线的 JSON 头从约 100 KB 降到 **200 字节**,38 KB 走二进制。

**前端 `CurvePayload.x/y` 类型从 `number[]` 改为 `Float32Array`。**
mock 也一并改 —— mock 形状与真实引擎不一致的话,浏览器开发模式会
验证到一个现实里不存在的行为。

### 14.2 增量缓存(§9.3)

**曲线解析缓存**:`_load_dataset_curve` 此前每次 preview 都重新读盘 +
重新解析文本。现在按 `(stored_path, file_hash, x_axis_type, wavelength)`
指纹缓存。实测 **40.14 ms → 0.0135 ms(2973×)**。`datasets.curve` 也改为
走这条路径 —— 它原本重复了一遍读盘逻辑,两处分叉会静默给出不同的曲线。

**处理链阶段缓存**:按**参数前缀**缓存。背景只依赖 (crop, background),
平滑依赖 (crop, background, smoothing)。实测:

```
拖平滑窗口(背景命中缓存)  6.04 ms
拖背景 smoothness        34.19 ms
差值 = AsLS 背景的净成本  28.16 ms
```

AsLS 背景实测 23 ms,SG 平滑只有 0.38 ms —— 差 60 倍。用户拖平滑时
背景一点没变,不缓存就是让他为没变的计算等 60 倍时间。

键必须是**整段前缀**,不能只放单个字段:用户同时改了 crop 和 background
时,只比 background 会命中一个用旧 crop 算出来的背景 —— 曲线看着正常,
数值全错。测试里有这条反向用例。

**删掉了 `data_store` 的 `save_curve_cache` / `load_curve_cache`。**
它们全仓库零调用,而且方向是错的:每次 preview 往磁盘写两个 `.npy`
不是"缓存",是额外的 IO。§9.3 要的是进程内缓存。

### 14.3 端到端测试抓到的真 bug:嵌套信封

写完后加了一组**真的起引擎子进程、用真实帧对话**的测试
(`tests/test_protocol_e2e.py`),立刻抓到一个进程内测试看不见的崩溃:

```
TypeError: Object of type bytes is not JSON serializable
when serializing dict item '_binary'
when serializing dict item 'curve'      ← 注意这一层
```

`analysis.preview` 的曲线嵌在 `data.curve` 里,而 `datasets.curve` 在
`data` 顶层。我最初只在**顶层**找 `_binary`/`__bin__`,于是 preview
的二进制体没被摘掉 → `json.dumps` 抛 TypeError → **而且是在主循环里抛,
整个引擎进程直接退出**,前端看到的是"分析引擎意外退出"。

同一个疏漏在**四个地方**都存在,全部改为递归:

| 位置 | 症状 |
|---|---|
| 引擎 `write_frame` | 进程崩溃(最严重) |
| Rust `read_frame` | 二进制体留在管道里,**污染后续每一帧** |
| 前端 `attachBinary` | preview 的曲线永远解不出来,图上没有曲线且不报错 |
| 测试 `_recv` | 读错长度,后续帧全错位 |

**为什么进程内测试发现不了**:它们直接调 `Engine.handle`,根本不经过
`write_frame`。传输层的问题只能在传输层上测。

### 14.4 验证

```
引擎:.venv/Scripts/python.exe -m pytest tests/   → 392 passed(原 376)
     .venv/Scripts/python.exe -m ruff check .    → All checks passed
Rust:cargo check                                 → 零错误
前端:npm run build                               → tsc 零错误,构建通过
```

新增测试:

- `test_envelope.py`(10 条):切片顺序、截断拒绝、float32 精度
- `test_frame_protocol.py::TestBinaryEnvelope`(4 条):含"无二进制时
  字节与旧格式**完全相同**"
- `test_protocol_e2e.py`(5 条):**真的起引擎子进程走完整协议**,
  含"带二进制体的帧之后协议仍对齐"
- `TestStageCache`(7 条):含两条反向用例(改背景/改 crop 必须重算)
- `TestAnnotationLifecycle`(6 条):空表 annotate 落库、commit 保 annotation_json、
  state 恢复、stale hash、preview restore 合并、detect 保存标注
- `TestCobaltFreeFixture`:无钴 (006)/(102) 归属不互换;杂相报 none
- `test_migration_v5_to_v6_is_idempotent`:annotation_json 加列幂等

前端拆包逻辑单独验证过:顺序反了会把 y 读成 x(证明 layout 顺序真的重要),
截断会抛错而不是画出半条曲线;嵌套信封(preview)能正确解出
x / y / background 三个数组。

## 十五、本轮(2026-10-05~07)电化学模块:LAND dQ/dV 衰减归因(M0–M5)

用户需求:LAND(蓝电)循环数据拖进软件,自动算 dQ/dV、追踪相变峰、
把容量衰减定量拆成 极化/活性物质/窗口截断 三分量并给结论。
计划书 docs/PLAN_ECHEM.md,开发文档 docs/DEVELOPMENT_EC.md。

### 15.1 交付(engine 侧)

- `xrd_engine/electrochem/` 四模块:land_parser / dqdv / peaks / attribution,
  仅 numpy+scipy(openpyxl 为新增打包依赖)
- `ec.*` 命令 12 个(preview/import/cells/overview/dqdv_compute/state/
  **result**/curve/waterfall/attribution_run/get/export),曲线走二进制信封
- schema v7:ec_cells/ec_cycles/ec_analyses;软删除/回收站/恢复/purge
  全生命周期接入(含 npz 曲线文件的级联清理)
- "算一次存结果 + hash 判过期":dqdv_state.paramsHash、归因 sourceDqdvHash;
  **ec.dqdv_result 把峰/轨迹/参数快照从 ec_analyses 读回** —— 重启软件后
  峰轨迹页直接可用,前端打开 cell 时自动拉取并把 UI 参数对齐快照

### 15.2 真实数据校准(样例 170214_005_7,184 圈 0.2C 长循环)

积分容量 vs 蓝电循环表逐圈偏差中位 **0.006%**(QA 阈值 2%);
倍率分组精确识别 0.1C×3 + 0.2C×181;归因结论(方法 B,低置信度已标注):
极化 81% + H2→H3 漂移 145mV 贴截止线(截断证据)—— 与 9 年老化电池
预期一致。

### 15.3 真实数据暴露的 10 个设计修正(全在 DEVELOPMENT_EC.md §5)

要点:电流随容量自动重标定(倍率分组按圈聚类)、时间格式混用
(time 对象 + D-HH:MM:SS)、电压抖动(running-max 单调化)、放电符号、
宽峰噪声(二阶差分 MAD/√6)、截断语义(数据边界 vs 窗口)、轨迹容差
自适应峰宽、归因不依赖轨迹连续性、方法 A 极化方向、不完整末圈剔除。

### 15.4 顺带修的存量缺陷

- `_cmd_samples_create` 重复定义 + 孤儿死代码(前轮遗留,行为碰巧一致);
- `meta.purge` 的 table 分发缺 `batches` 分支 —— 软删的批次被彻底删除时
  会**错误地删除同 id 的 Project**;`_purge_all` 的 order 表缺
  `batches` 会 KeyError;
- `purge_project` 丢弃 `purge_sample` 返回的 stored_path(ec 落地时改为
  累积返回)。

### 15.5 前端集成(app/src/features/echem)

- 侧栏样品下挂 cell 树(懒加载)、导入向导(preview → 归属三选一 → 查重拦截)、
  CellPage 四页签:概览(测试信息/圈统计/容量趋势)/ dQ/dV(参数面板 +
  单圈曲线 + 瀑布 canvas)/ 峰轨迹(轨迹列表 + 峰位/面积 vs 圈数)/ 归因
  (三分量堆叠 + 结论卡);CSV 导出按钮对接 ec.export
- 打开 cell 经 **ec.dqdv_result 读回**峰/轨迹/参数快照:重开软件无需重算,
  「参数已修改」徽标只在用户改动参数后出现(compute 后清零)
- 浏览器开发模式有全量 mock(ec.* 12 条形状与引擎一致),tsc + vite build 零错误

### 15.6 验证

```
引擎:.venv/Scripts/python.exe -m pytest    → 439 passed(原 395,新增 44)
     .venv/Scripts/python.exe -m ruff check . → All checks passed
     npm run build(app)                     → tsc + vite 零错误(2577 modules)
真实样例:smoke(12 命令全链)+ 重启读回(新 Engine 同 data_root,
     峰519/轨迹12/hash 一致/曲线直读)+ 整链 M0–M3 复验通过
打包:PyInstaller onedir 构建 + NSIS 安装包重打(含本轮前端与 ec.dqdv_result)
```

新增测试:parser 9(容量/倍率/质量反推/不完整圈/回退/缺列/归一)
dqdv 7(峰位/容量/归一/放电符号/抖动不重复计数/参数校验)
peaks 8(双窗/贴窗峰存活/数据边截断/宽峰检出/轨迹连续性/空洞/峰宽容差/截断不进轨)
attribution 8(纯极化/纯活性/闭合/剔除/方法A已知分量/方法A前置校验/结论规则)
commands 13(导入概览/计算曲线/参数hash变更/**dqdv_result 重启读回**/
     未计算先返回/归因流程与stale/先决条件/导出/查重/软删恢复/预览不落库/坏文件/可序列化)

### 15.7 本轮(2026-10-08)循环表按名取列 + 行右键定位文件

**循环表解析稳健性**(用户质疑"导出表缺列还能正确识别吗"→ 核实出真隐患):
- 旧实现按**固定列位置**读 `row[0..5]`(序号/充放容量/比容量/效率)。
  记录表(dQ/dV 主数据源)本来就是同义词按名匹配、只硬性要求
  time/current/voltage/state 四列,缺"充电中压/放电中压"无影响(记录表
  根本没有这两列);但循环表若因导出勾选变化导致 0–5 位移位,ref 容量与
  质量反推会**静默读错**(能量列读成容量 → 貌似合理的错质量)。
- `_attach_cycle_table` 改为:前 6 行找表头行、按 `_CYCLE_SYNONYMS`
  同义词定位各列;缺列给 nan(比容量缺失 → 不反推质量);认不出表头才
  回退旧蓝电固定 0..5 布局。中压列(循环表 7/8 位、记录表本无)读不读
  都不受影响。
- 真实样例回归不变:184 圈、mass 1.768mg、capacity_dev_med 5.8e-5。

**行右键"在资源管理器中显示"**(用户:定位文件,要数据时不用翻目录):
- 引擎新命令 `meta.file_path {table, id} → {path, kind: source|stored}`:
  只认 datasets/ec_cells;原文件(source_path)优先,不在了回落库内副本,
  两处都没有才报人话错;回收站里也允许定位。
- 前端:`RowMenuItems` 加"在资源管理器中显示"(⋯ 菜单与右键共用);
  新组件 `RowContextMenu`(光标锚定、portal 到 body 绕开侧栏
  backdrop-filter 的包含块、点外/Esc/滚动关闭、贴边夹取);侧栏
  项目/样品/批次/数据集/cell 全部行接上右键。Tauri 走
  tauri-plugin-opener 的 revealItemInDir(explorer /select);浏览器 dev
  降级为复制路径 + toast 路径,不静默失败。

**验证**:pytest **445 passed**(439 + parser 2 + meta.file_path 4)、
ruff 通过、tsc + vite build 零错误、真实样例回归、NSIS 安装包重打。

### 15.8 本轮(2026-10-08)电化学界面三批次优化(0.3.0 → 0.4.0)

用户报 5 问(Inspector 残留/坐标轴不能调/横轴从 0 起/充放不能同图/
不能多圈叠加)+ 只读审计出 7 项同类问题,归 A/B/C/D 四类、分三批全部落地:

**批次1(用户 5 问)**:
- A1 电化学模式隐藏右侧 Inspector(整栏 XRD 处理控件,只会误导)
- B2 `EcLineChart` xAxis 补 `scale:true` —— ECharts value 轴默认强制含 0,
  dQ/dV 电压轴从 0 起的根因(与 y 轴行为对齐)
- B1 `EcLineChart` 双轴 inside dataZoom(共享 RoamController):滚轮在光标处
  区域缩放横纵轴、横向拖动平移 x/纵向拖动平移 y、双击复位全范围(=Fit All
  手感);`filterMode:"none"` 缩放一条轴不引起另一条轴按可见数据重算
- C1 dQ/dV 方向加「充放」:同圈充+放双曲线同图(放电虚线,图例"圈 n · 充/放")
- C2 圈号多选叠加:上限 6(对齐 compareCurves/6 色)、`COMPARE_COLORS` 循环
  配色 + chip 可删、缺圈容忍(部分"没有曲线"只画取到的);瀑布方向改为
  块内自带 Segmented,与曲线方向解耦(充放同图后两者语义已不同)

**批次2**:
- A2 StatusBar 电化学模式整栏换「电化学分析」标签:2θ 光标/点数/峰固化
  徽标/管线状态全是谱图语境,selectCell 不清 analysisStore,留着是残值
- D1 循环总览加「充电容量」勾选:按倍率组同色虚线叠加,勾选后图例加
  ·放/·充 后缀(不勾保持原样);轴名 capacityAxisAny 联动
- D2 每张电化学图右上角 PNG 导出按钮:`exportEcFigure` 复用引擎
  `export.figure` 但不带 dataset_id(旁车溯源本就可选);按钮与 echarts
  容器分层(兄弟节点,不混 init 容器);浏览器 dev 降级 `<a download>`

**批次3**:
- C3 瀑布图左侧 36px 画圈号刻度(顶/中/底,行数少去重),x 电压标签
  同步 pl-36px 对齐 —— 此前第几行是第几圈只能心算
- D3 Ctrl+Z/Ctrl+0 电化学模式禁用(撤销栈/fitAll 都是谱图的),Ctrl+I
  电化学模式开 LAND 导入
- D4 TopBar「+」电化学模式开 EcImportSheet(XRD 导入面板在该语境必错)
- D5 测试信息 QA 四行走 i18n(qaRecords/qaBackwards/qaInterval/qaCapacityDev)
- D6 dQ/dV 默认圈改首圈(原 len/4 处的怪异默认);CellPage 内容区
  `key={cellId}` 重挂载,页签局部状态不串 cell

**过程坑**:PowerShell `Get-Content -Raw` 把 UTF-8 按 GBK 读,改版本号时
搅坏 Cargo.toml 唯一中文 description → cargo 解析失败;按 git 原文
逐字节修复验证。教训:改带中文的配置文件用 Edit/node,不用 PS 文本管道。

**验证**:tsc + vite build 零错误、pytest **445 passed**(引擎本轮零改动)、
ruff 通过、打包版 sidecar 冒烟(导入 184 圈 / 峰519 / 轨迹12 / 重启读回
hash 一致 / meta.file_path)、NSIS **XRD Studio_0.4.0_x64-setup.exe** 重打。
