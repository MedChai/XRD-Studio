# XRD Studio V0.3 — 项目进度汇报

> **日期：** 2026年09月16日
> **状态：** Milestone 1 完成，Milestone 2 完成约 80%
> **代码基线：** commit `380a10a`（本轮修完 6 项使用反馈）
> **依据文档：** README.md（Rev 2 开发文档）§12 里程碑与出口条件

---

## 一、总体结论

软件已经**跑通完整真实链路**：Tauri 窗口中导入 XRD 文件 → 归属 Sample → 查看谱图 → 背景/平滑/寻峰实时预览 → 峰表联动，全链路由真实 Python 引擎执行（非 mock）。引擎 **177 个自动化测试全部通过**，Rust/TypeScript 编译零错误，性能基准达标。

开发过程中测试驱动发现了 **5 个会静默出错的真 bug**（详见第五节），全部已修复——这正是 README Rev 2「出口条件是测试，不是形容词」原则的兑现。

---

## 二、里程碑进度

| 里程碑 | 状态 | 说明 |
|---|---|---|
| **M1 App Shell** | ✅ 完成 | 全部布局组件 + 设计 token + 双主题 + 键盘可达 |
| **M2 Import & Plot** | 🟡 80% | 导入/解析/SQLite/Data Store/真实绘图已通并验证；**待办：** sidecar 打包决策实验（§11.1）、二进制 Envelope（§9.1）、fixture 全量回归清单 |
| **M3 Processing** | 🟡 大部分提前完成 | 背景/平滑/裁剪/preview 状态机/重采样已在引擎落地并有测试；**待办：** 前端降采样 preview 路径实装 |
| **M4 Peaks** | 🟡 引擎侧完成 | 寻峰/FWHM/Area/边界编辑 + FWHM 2% 精度验收已过；**待办：** 灵敏度映射表人工校准（§12 出口条件，需真实谱评审） |
| **M5 Export & Presets & Undo** | 🟡 部分完成 | Preset 与 Undo 已实现；导出 UI 未做（有 toast 占位） |
| **M6 Polish & Installer** | ⬜ 未开始 | 按计划最后进行 |

---

## 三、已交付内容（按层）

### 3.1 Python 分析引擎（约 5,000 行，177 个测试，ruff 零告警）

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
| `database/db.py` | §8.2 | 6 表软删除 schema、WAL、查重/树查询 |
| `services/data_store.py` | §8.3/§8.4 | 复制导入 + SHA-256 + metadata.json、路径逃逸防护、缓存/导出目录 |
| `api/commands.py` | §10.4 | 20 个命令全实现，错误协议收敛为人话（traceback 零泄漏，有断言测试） |
| `__main__.py` | §11.2 | stdio 长度前缀帧协议 + ping 心跳，端到端实测通过 |
| `scripts/make_fixtures.py` | §12.0 | 固定种子生成 17 个 fixture（LFP/NCM 合成谱、非均匀网格、GB18030、垃圾头、NaN、降序…） |

### 3.2 Tauri Shell（Rust，323 行，编译零错误零警告）

- **Sidecar 监管**（§11.2）：spawn / stdin-stdout 帧桥接 / 请求 id 路由 / stderr 落 `logs/engine.log`
- **崩溃语义**：EOF 时失败全部 pending 请求并回收句柄，下次调用自动重启
- **启动互斥**：并发请求不重复拉起引擎（实测修复前 4 实例→修复后 1 实例）
- Data Store 定位 `%LOCALAPPDATA%\XRDStudio`（§11.4 中文路径规避）
- dev 环境自动定位 `engine/.venv` 的 Python；`XRD_ENGINE` 环境变量可注入（为打包实验预留）

### 3.3 React 前端（约 2,800 行，tsc 零错误，构建通过）

- **设计系统落地**（§6）：全部 token 以 CSS variable 固化、Light/Dark、tabular-nums、140–180ms 克制动效、毛玻璃层
- **全布局**（§15–§35）：TopBar / LibrarySidebar / PlotHeader / ECharts 图谱 / PeakDrawer / Inspector / StatusBar / ImportSheet / CommandPalette / SettingsDialog / Toast
- **交互规范实现**：Crosshair + 状态栏读数、峰标签普通态只显示 2θ、Plot↔Table 双向联动、Segmented Overlay/Stacked、AsLS λ 对数刻度滑块、Advanced 折叠
- **preview 状态机**（§10.3）：30ms 节流 + 序号 cancel 语义 + 300ms autosave + Saved/Computing 状态
- **Undo/Redo**（§10.5）：有界快照栈，复用 preview→commit 通路
- **前端本地显示层**（§9.2）：归一化/Offset 零引擎往返
- 平滑偏差提示（§7.1）：Inspector 显示「当前 FWHM / Area 在平滑后曲线上计算」
- 开发期 mock 引擎：浏览器里 `npm run dev` 不开 Tauri 也能完整驱动 UI

### 3.4 测试与基准

- 177 个 pytest：单元（coords/grid/parser/validate）+ 数值精度（FWHM 2%、AsLS 背景还原、面积解析解）+ 集成（API 全流程含查重、列映射、软删除）
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
拖入/选择 .xy 文件 → 建项目/样品 → 立即看图(3,501 点真实渲染)
→ Inspector 开背景(AsLS) → 调灵敏度 → 峰表出现 → 点峰联动定位
```

已在真实 Tauri 窗口端到端验证通过(数据实际落盘 `%LOCALAPPDATA%` 下的 XRDStudio)（数据实际落盘 `%LOCALAPPDATA%\XRDStudio`）。

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
| **Sidecar 打包未决策**（§11.1 路线 A uv 内嵌运行时 vs 路线 B PyInstaller onedir） | 高 | M2 硬性出口条件；已预留 `XRD_ENGINE` 注入点，下一步即做 |
| 曲线传输仍是 JSON number[]（生产须换 Float32 Envelope） | 高 | 下一步：`api/envelope.py` + Rust 二进制透传 + 前端 typed array |
| 灵敏度映射表未经真实谱人工校准 | 中 | M4 出口条件，需要你提供 5–10 张典型实测谱 |
| 前端感知指标（切样 <150ms、绘制 <300ms）未自动化测量 | 中 | M3 用 Performance API 补进 harness |
| 导出（CSV/PNG/SVG/PDF）未实现 | 中 | M5 主体 |
| Bundle 1.4MB（ECharts 全量引入） | 低 | M5 拆包 + 按需注册图表组件 |
| 中文路径/无 WebView2/Defender 环境矩阵 | 低（暂） | M6 前逐项过（§11.4） |

---

## 七、下一步计划（按序）

1. **二进制 Envelope 实装**（§9.1）—— 引擎侧 Float32 交错数组 + Rust 帧透传 + 前端 `new Float32Array` 直喂 ECharts，并用 harness 实测拖动延迟
2. **Sidecar 打包决策实验**（§11.1）—— 两条路线各出包，纯净 Win10 VM 测冷启动与 SmartScreen，结论成文
3. **fixture 全量导入回归**（§12 M2 出口）—— 17 个 fixture 跑「导入成功或正确进列映射」断言，M2 收官
4. M3 收尾：降采样 preview、背景曲线虚线显示、Crop UI
5. M4 校准：**需要你提供一批典型实测谱**（实验室常规 + 同步辐射各若干），供灵敏度评审

---

## 附：仓库状态速览

```
commits:  4(初始 → bugfix → 竞态/基准 → 项目创建)
engine:   23 py 模块 · 177 tests · ruff clean · ~5.0k 行
app:      25 tsx/ts/css · tsc zero-error · build passed · ~2.8k 行
rust:     323 行 · cargo check 零警告
fixtures: 17 个可再生基准文件(seed 20260915)
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
