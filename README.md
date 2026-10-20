# XRD Studio — 产品开发文档 V0.3

> **状态:** Rev 2(可行版,含工程约束与验收标准)
> **产品:** XRD Studio
> **版本目标:** V0.3
> **定位:** XRD 数据管理、查看、基础处理与峰分析桌面软件
> **设计关键词:** 简洁 / 苹果风 / 数据优先 / 图谱优先 / 非破坏性分析
> **本文档与原 v1 草稿的差异:** 新增性能设计、坐标与网格约定、Pipeline 顺序修正、Undo/预设数据模型、打包分发方案、边界条件与验收标准;修正示例数据不自洽问题。

---

# 1. 目标用户与使用场景

## 1.1 用户画像

- 高校 / 企业实验室的材料、化学、物理研究人员与研究生。
- 典型样品体系:电池材料(LFP、NCM)、催化剂、薄膜、纳米粉体。
- 数据来源:实验室粉末衍射仪(Cu/Co/Fe 靶,密封管或 Bragg-Brentano),部分用户有同步辐射数据(d/Q 轴、高点数)。
- 设备环境:**以 Windows 10/11 为主**,少量 macOS。目标用户没有专职软件支持,软件必须"装上就能用"。

## 1.2 V0.3 核心场景(按优先级)

| # | 场景 | 说明 |
|---|------|------|
| S1 | 导入并看图 | 拖入文件 → 归属 Sample → 立即看到干净的谱图 |
| S2 | 基础处理 | 平滑 / 扣背景,实时预览,原始数据永不被改 |
| S3 | 寻峰与读数 | 自动寻峰,查看 2θ / Intensity / FWHM / Area |
| S4 | 系列对比 | 同 Project 多样品 Overlay / Stacked 比较峰位 |
| S5 | 参数复用 | 同一套处理参数应用到整个 Project 的样品(批量趋势分析的前置能力) |
| S6 | 导出 | 图(PNG/SVG/PDF)、峰表(CSV)、处理后数据(CSV/TXT) |

S5 是 v1 草稿遗漏的场景,但对"400/450/500/550°C 四块样品看趋势"这类用户是刚需,已纳入数据模型(见 §8)。

## 1.3 明确不做的(与 v1 一致)

```text
Rietveld Refinement          ICDD PDF 数据库
自动物相鉴定                  复杂 CIF 管理
晶胞精修                     多峰解卷积
仪器宽化校准                  Kα2 Stripping
自动晶粒尺寸工作流            自动结晶度分析
云同步 / 账号 / 多人协作
```

---

# 2. 技术路线

## 2.1 总体选型

维持 v1 决策:

```text
Tauri 2 (外壳)
+ React + TypeScript + Vite (UI)
+ Tailwind CSS + Radix UI + Lucide (组件)
+ Apache ECharts (绘图)
+ Zustand (状态)
+ Python sidecar: NumPy / SciPy (分析引擎)
+ SQLite (元数据)
```

**不采用** PySide6 QWidget 路线,理由与 v1 相同。

## 2.2 为什么必须正视的三个工程现实(v1 未写)

1. **Python sidecar 的启动与打包是本项目最大工程风险**,不是 UI。方案见 §11。
2. **7,000 点以上的曲线数据走 JSON 文本会毁掉"滑块跟手"的体验。** 传输格式必须在 Milestone 2 就定对,见 §9。
3. **Windows 目标机器的环境不可假设干净**(无 WebView2、Defender 拦截、非 ASCII 用户名路径),见 §11.4。

---

# 3. 总体架构

```text
┌────────────────────────────────────────────────────────┐
│                     Tauri 2 Shell                      │
│  窗口 / 文件对话框 / 拖放 / 菜单 / 通知 / sidecar 监管   │
│                                                        │
│  ┌──────────────────────────────────────────────────┐  │
│  │                React UI (WebView)                │  │
│  │  ECharts 绘图 · Zustand 状态 · 本地显示级变换     │  │
│  └───────────────┬──────────────────┬───────────────┘  │
│        IPC(二进制) │        HTTP(本机,仅开发) │          │
│  ┌───────────────▼──────────────────▼───────────────┐  │
│  │           Python Analysis Engine (sidecar)        │  │
│  │  parser · pipeline · peak analysis · 文件存储     │  │
│  └───────────────────────┬──────────────────────────┘  │
│  ┌───────────────────────▼──────────────────────────┐  │
│  │        SQLite + Data Store(引擎独占访问)         │  │
│  └──────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────┘
```

关键决策:

- **生产环境走 Tauri IPC + 二进制协议,不走 HTTP。** FastAPI 只在开发期作为可选调试通道,发布版不包含。理由:少一个端口/进程管理面,鉴权、生命周期、崩溃恢复全部交给 Tauri sidecar 机制。
- **数据库和文件只允许引擎访问**,前端一律通过引擎的 command 接口读写。前端永远不直接开 SQLite。
- **崩溃恢复**:Tauri 监管 sidecar,引擎异常退出时自动重启并提示"分析引擎已重启,请重试上次操作"。

---

# 4. 坐标系统与数据网格约定(新增,开工前必读)

## 4.1 X 轴类型

每个 Dataset 记录 `x_axis_type`:

```text
two_theta   实验室 2θ(度)—— 默认
d           d-spacing(Å)
q           Q(Å⁻¹)
generic     无法判定的其它
```

- 波长(λ)是 **Sample 级 metadata**(可选字段,默认 Cu Kα 1.5406 Å),用于 2θ ↔ d ↔ Q 换算显示。
- V0.3 支持**视图层坐标切换**(X 轴显示为 2θ / d / Q),但**峰表结果永远按采集轴存储**,换算只影响显示,避免结果歧义。
- 换算公式入 `engine/xrd_engine/coords.py`,单元测试用 NIST Si 标准值校验。

## 4.2 网格均匀性——所有算法的前置假设

SciPy 的 `savgol_filter` / `find_peaks` 假设等间距网格。**必须在 Validate 阶段检测并处理:**

```text
导入时计算 x 的 diff,若 max(diff)/min(diff) - 1 > 1e-3
→ 标记 dataset.grid = "non_uniform"
→ 处理策略(引擎内统一实现,对用户透明):
   - 背景估计(AsLS):按索引跑可接受(AsLS 对非均匀网格本身稳健),
     但 smoothness λ 的物理含义偏移,文档注明
   - Savitzky–Golay:禁止直接使用;先等间距重采样(线性插值到
     N = point_count 的均匀网格),保存重采样参数,结果页标注 "resampled"
   - find_peaks / FWHM / Area:全部在均匀化后的网格上计算,
     峰位回报时反插值回原始坐标
```

非均匀网格检测与重采样是 **Milestone 3 的必做项**,不是"以后再说"。

## 4.3 示例数据规范(修正 v1 的不自洽)

全文示例统一使用以下自洽数据集:

```text
样品      LFP-500C
轴        two_theta = 10.000° → 80.000°
步长      0.020°
点数      3501
主峰示例   2θ = 34.39°(LFP (020)),FWHM ≈ 0.18°,强度 31,542
```

> v1 草稿中"7,001 点 / 峰位 36.521° / 起点 18.423°"组合推出的步长约 0.0026°,物理上不存在,实现者无法用它校验 parser。已修正。

## 4.4 单位与有效数字

| 量 | 存储单位 | 显示 |
|----|---------|------|
| 2θ / d / Q | 原始单位,Float64 | 2θ 3 位小数,d 4 位,q 4 位 |
| Intensity | 原始计数(Float64) | 千分位整数;归一化视图另标 |
| FWHM | 轴单位,Float64 | 3 位小数 |
| Area | counts·deg | 3 位有效数字 |

---

# 5. 设计原则

(与 v1 一致,保留)

- **Plot First**:最大面积永远属于图谱,不是工具栏。
- **Library First**:数据必须属于明确的 Project → Sample → Dataset。用户永远知道"这张谱是谁的"。
- **Original Data First(非破坏性)**:任何处理不覆盖原始数据;保存的是 `RAW + 处理参数 + 分析结果`,而非处理后的曲线。
- **结果必须携带出处**:每条 Peak 数据都能回答"你在哪个处理状态下算出来的"。见 §7.4。

---

# 6. UI 设计系统

以下与 v1 一致,作为施工图纸保留。

## 6.1 风格

> **Apple-inspired Scientific Workspace**(参考 macOS / Linear / Arc 的克制,不是复制)。

大量留白、低饱和、清晰层级、轻量边框、少量阴影、克制动画、圆角、内容优先。

## 6.2 字体

```css
font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter,
             "Helvetica Neue", Arial, "Microsoft YaHei UI", sans-serif;
```

> v1 未考虑中文渲染,Windows 上缺省回退到宋体系会很突兀,已补 YaHei UI。

```text
Large Title   22px / 600     Body       13px / 400
Title         17px / 600     Secondary  12px / 400
Section       14px / 600     Caption    11px / 400
Data          12px / 500 (font-variant-numeric: tabular-nums)
```

## 6.3 主题(与 v1 相同)

### Light

```text
App Background  #F5F5F7      Primary Text    #1D1D1F
Sidebar         rgba(248,248,250,.86)  Secondary Text #6E6E73
Panel           #FFFFFF      Tertiary Text   #9A9AA0
Elevated        rgba(255,255,255,.86)
Divider         rgba(0,0,0,.08)   Hover  rgba(0,0,0,.045)
Selected        rgba(0,122,255,.10)
Accent #007AFF  Danger #FF3B30  Success #34C759  Warning #FF9F0A
```

### Dark

```text
App Background  #171719      Primary Text    #F5F5F7
Sidebar         rgba(27,27,30,.88)  Secondary Text #A1A1A6
Panel           #202023      Tertiary Text   #737378
Elevated        #29292D
Divider         rgba(255,255,255,.09)  Hover rgba(255,255,255,.055)
Selected        rgba(10,132,255,.18)
Accent #0A84FF  Danger #FF453A  Success #30D158  Warning #FF9F0A
```

## 6.4 Radius / Spacing / Shadow / Animation

```text
Radius:  Control 6 · Input 8 · Button 9 · Panel 12 · Card 14 · Dialog 16
         原则:不要所有东西都变成巨大圆角胶囊。
Spacing: 4px grid(4/8/12/16/20/24/32/40);Panel padding 16,Section gap 20
Shadow:  Panel  0 1px 2px rgba(0,0,0,.04), 0 4px 18px rgba(0,0,0,.04)
         Dialog 0 20px 60px rgba(0,0,0,.18)
         禁止:大黑阴影、发光边框、彩色阴影、3D Button
Animation: 120–220ms;
  transition: background-color 140ms ease, color 140ms ease,
              opacity 140ms ease, transform 180ms cubic-bezier(.2,.8,.2,1);
  禁止:bounce、large scale、rotation、>300ms 常规动画。
```

## 6.5 主窗口与布局

默认 1440×900,最低 1280×760。

```text
┌──────────────────────────────────────────────────────────────────┐
│ XRD Studio      LFP Study                          ⌕    + Import │  TopBar 52px
├──────────────────┬──────────────────────────────┬────────────────┤
│ Library          │        XRD Plot              │  Inspector     │
│ 248px(220–360) │      (最大区域)               │ 296px 可折叠    │
│ Projects/Samples │                              │ Processing/    │
│ /Datasets        │                              │ Peaks/Display  │
├──────────────────┴──────────────────────────────┴────────────────┤
│ Peaks 12 ⌃                                                       │  PeakTable
├──────────────────────────────────────────────────────────────────┤
│ ▁▂▅█▇▃▂▁▁▂▄█▆▃▁▁▂▃▅▇█▅▂▁   缩略谱 + 可拖动范围框                  │  RangeOverview 56px
├──────────────────────────────────────────────────────────────────┤
│ 2θ 34.390°   I 31,542   3,501 pts   Saved                        │  StatusBar
└──────────────────────────────────────────────────────────────────┘
```

RangeOverview 是**通栏**的(横跨 Sidebar + Plot + Inspector):容器底色通栏,
内部左右留出与两栏等宽的内边距,使缩略图与绘图区对齐。

## 6.6 Sidebar / Sample Item / Dataset(与 v1 一致)

- Sample 条目:名称 + 一行副标题(`500 °C · 2 h`),Normal 透明、Hover `rgba(0,0,0,.04)`、Selected `rgba(0,122,255,.10)`。**绝不整行亮蓝白字。**
- 一个 Sample 拥有多个 Dataset —— 核心模型,不把 Sample 与文件等同。
- 搜索、RECENT、PROJECTS 树、`+ New Project`,结构同 v1 §17。

## 6.7 Plot(与 v1 一致的部分从简)

- 默认:白底、无网格、X/Y 轴开、Line 1.5、Legend Auto。不画粗网格/灰底/巨图例。
- 交互:滚轮 Zoom、拖动 Pan、双击 Fit All、触控板 Pinch。Fit All 另有
  PlotHeader 按钮与 `Ctrl+0` 两个入口;双击必须绑 zrender 的 dblclick
  (平移用 moveOnMouseMove,那一动会被当成 pan,ECharts 层级往往不派发)。
- **视口状态用数据坐标(2θ),不是百分比**,存 `plotStore.zoom`。两个图
  (主图与底部范围框)都只做「回放 zoom」与「写回 zoom」,绝不在 dataZoom
  事件里回读对方状态 —— 否则死循环。
- x 轴值域从 `curve` 首尾推导,**不得硬编码**;硬编码会让不在该区间的实测
  谱被裁掉,且 Fit All 无从实现。
- `☑ Show Background` 打开时以低透明度虚线叠加背景曲线;该 series 必须
  `silent`,否则会抢走点击命中导致选峰错位,tooltip 也要取主曲线那条。
- Crosshair + 浮动 tooltip(小、半透明、圆角、不遮数据)。
- Overlay / Stacked 用 Segmented Control;Stacked 提供 Offset Slider。
- Peak Labels:普通态只显示 2θ;选中态显示 Intensity/FWHM —— 否则几十峰形成文字墙。
- Peak Table 默认收起在底部(`Peaks 12 ⌃`),表列:`# / 2θ / Intensity / FWHM / Area`。
- Plot ↔ Table 双向联动(点行 → 图定位+高亮+Inspector 详情;点图内峰 → 表滚动选中)。
- Peak Detail:Position/Intensity/FWHM/Area/Bounds + `[ Edit Bounds ]`,编辑态在图上以极淡色块显示选中区间。

## 6.8 Command Palette

`Ctrl+K`(macOS `⌘K`):搜命令与样品(Import / Open sample / Detect Peaks / Toggle theme / Export)。

## 6.9 Toast / Empty State / Settings

- 成功:`✓ Imported 3 datasets`;失败:人话 + `View details`,**永远不直接展示 Python traceback**。
- 首次进入:居中一句 "Import a diffraction file to get started" + 一个按钮,无大插画。
- Settings 分区:General / Appearance(Theme: System/Light/Dark;Accent)/ Data(存储位置、打开数据目录)/ Plot / About。

## 6.10 语言

界面语言为**简体中文**,无多语言切换。

- 文案集中在 `app/src/i18n/zh.ts`,组件通过 `t.xxx` 引用。不引入 i18n 库:
  当前只有一种语言,库会带来 Provider、语言包加载、持久化三层开销而收益为零。
  `as const` 保留 —— 它让 key 拼写错误在 `tsc` 阶段直接报错。
- 带参数的文案写成函数(`t.import.imported(n)`),不要字符串拼接,否则中文
  语序无法调整。
- **刻意保留英文**的只有三类:XRD 领域通用符号与量名(`2θ` `FWHM` `Area`
  `Intensity` `Prominence`)、算法名(`AsLS` `Poly` `Savgol`)、产品名
  (`XRD Studio`)。样品名占位符与文件格式串属于数据而非文案,也不译。

---

# 7. 分析引擎设计

## 7.1 Pipeline 顺序(修正 v1)

```text
Load
  ↓
Validate          ← 新增硬性检查,见 §7.2
  ↓
Crop              ← 视口裁剪作为可记录步骤入参
  ↓
Background Estimate(AsLS / Polynomial)
  ↓
Background Subtraction
  ↓
Smooth(Savitzky–Golay)
  ↓
Peak Detection
  ↓
Peak Metrics(FWHM / Area)
```

**为什么 Background 在 Smooth 之前(v1 是反的):**

1. SG 在含背景的谱上平滑,背景曲率大的区域会畸变;
2. 更重要的是 **SG 平滑本身压峰高、改变半高宽**——先平滑后测 FWHM 会系统性低估,而用户不知情。对用 FWHM 看晶粒尺寸趋势的电池用户,这是静默偏差。

缓解措施:

- 默认 preview 链里 Smooth 是**关闭**的,峰度量报告在"扣背景后、平滑前"的曲线;用户打开 Smooth 时,Inspector 的 Peaks 区显示提示:"当前 FWHM/Area 在平滑后曲线上计算"。
- 导出峰表时,pipeline 参数快照一并写入(见 §7.4)。

## 7.2 Validate(引擎第一道关,定义边界行为)

```text
- 至少 4 个有效数据点;x 单调(允许降序,内部翻转为升序)
- 非单调/重复 x → 拒绝导入,错误文案人话化
- 全平线(零方差)/ 常数背景 → 允许查看,背景扣除与寻峰给出
  "无可检测峰 / 无可扣背景"提示,不报错
- NaN/Inf → 导入时剔除并计数提示("跳过 3 个无效行")
- 非均匀网格 → 见 §4.2,标记 resampled
- x 范围合理性:two_theta 轴若全部落在 [-5°, 180°] 之外 → 弹列映射
  对话框让用户确认轴(可能拿错了 2θ/计数 列)
```

## 7.3 各算法约定

### 解析器(Milestone 2)

支持 `CSV / TXT / XY / DAT`;自动识别分隔符(comma/tab/space/semicolon);跳过以 `#`、`;`、`%` 开头的注释行与文件头垃圾行;识别科学计数法;编码 UTF-8 优先,失败回退 GB18030(中文实验室常见),再回退 Latin-1。**解析失败 → 列映射对话框**(预览前几行,选 X 列 / Y 列)。

### Smoothing

`scipy.signal.savgol_filter`。UI 保证:`window_length` 为奇数且 `3 ≤ w ≤ 501` 且 `polyorder < window_length`,窗口限幅不超过数据长度的 10%。

### Background

- **AsLS**:参数 λ(smoothness)、p(asymmetry)、n_iter。
  **UI 滑块 λ 用 log2 刻度(10²–10¹⁰),禁止裸输入框**——v1 的示例值 `100000` 只是该区间的一个点,裸输入会让用户不知道量级意义。
  默认 λ=10⁵、p=0.01、n_iter=10。实现:`scipy.sparse` 三对角求解。
- **Polynomial**:order 1–8;自动提示拟合残差过大时建议换 AsLS。
- 背景曲线显示为细虚线、低透明度;`☑ Show Background` 可关。

### Peak Detection

`scipy.signal.find_peaks`,前端只暴露三个语义参数:

```text
Sensitivity(Low–High)→ 映射为 prominence = sensitivity_map[k] × max(I)
Minimum Height(5% 默认)→ height
Minimum Distance(0.10° 默认)→ distance = min_dist / x_step(换算成点数)
```

Advanced(折叠区):Prominence / Width / Relative Height 直通。映射表在 `engine/peaks/mapping.py`,按 §12.2 的基准谱校准后固化。

### FWHM / Area

- FWHM:半高交点法(线性插值求左右交点),V0.3 不做解卷积。
- Area:∫I(x)dx(梯形法),默认边界取局部极小值,用户可编辑 Bounds。
- 两者都在"当前启用的处理链输出"上计算,处理链状态随结果保存。

## 7.4 结果溯源(新增)

每条 Analysis 保存完整快照:

```json
"pipeline_snapshot": {
  "background": {"method": "asls", "lambda": 1e5, "p": 0.01},
  "smoothing": {"enabled": false},
  "peaks": {"sensitivity": "medium", "min_height_pct": 5},
  "grid": "uniform", "resampled": false
}
```

导出 Peak CSV 时,文件头以注释行附带该快照的精简版,保证结果可复现。

---

# 8. 数据模型(含 v1 缺失的两块)

## 8.1 层级

```text
Workspace └ Project ─┬─ Sample ─┬─ Dataset ─── Analysis ─── Peaks
                     │          └─ Dataset
                     └─ AnalysisPreset(项目级,新增)
```

## 8.2 SQLite 表(`xrdstudio.db`)

### projects
```sql
id, name, description, created_at, updated_at, deleted_at
```

### samples
```sql
id, project_id, name, sample_code, batch, material,
temperature, duration, preparation_date, radiation_target, wavelength,
notes, tags, created_at, updated_at, deleted_at
```
> 新增 `wavelength`(默认 Cu Kα 1.5406),支撑 §4.1 坐标换算。仅 Sample Name 必填。

### datasets
```sql
id, sample_id, name, original_filename, source_path, stored_path,
file_hash, imported_at,
x_axis_type,        -- two_theta | d | q | generic
x_min, x_max, step, point_count,
grid,               -- uniform | non_uniform
created_at, updated_at, deleted_at
```

### analyses
```sql
id, dataset_id, preset_id NULL,
pipeline_json,       -- 完整处理链快照(§7.4),单一 JSON 字段取代 v1 的
                     -- 十几个分散列 —— 处理链演进时不再改表结构
is_active,           -- 同一 dataset 可有多个历史 analysis,active 唯一
created_at, updated_at
```

### peaks
```sql
id, analysis_id, position, intensity, fwhm, area,
left_bound, right_bound, manual_override,
created_at, updated_at
```

### analysis_presets(新增,支撑 S5)
```sql
id, project_id, name, pipeline_json, created_at, updated_at
```
交互:Inspector 处理区底部 `Save as Preset…` / `Apply Preset ▾`(对本 Project 全部或所选 Dataset 批量应用)。

### undo_history(新增,见 §10.5)
不落库,内存 + 会话级;落库的只有最终参数。

**Trash 语义(§10.5)**:`projects` / `samples` / `datasets` 三表有 `deleted_at`,
删除为**隐式级联** —— 只标记被直接删除的那一行,子对象的可见性由父级决定
(查询时一并判 `deleted_at IS NULL`)。恢复父级时子对象自动全部回来,因为它
们从未被写过 `deleted_at`。级联写会让「删项目→恢复项目」无法区分子对象是
跟着删的还是用户本来就单独删过,要正确处理就得再加一张操作日志表。

`analyses` / `peaks` / `analysis_presets` **没有** `deleted_at`:它们的可见性
完全由父级决定,加列只会制造需要同步的冗余状态。

`meta.purge` 是唯一真正删磁盘(`data/<ULID>/`)的入口;软删除刻意不碰文件,
以便恢复时数据仍然完整。

## 8.3 Data Store 目录(与 v1 一致)

```text
XRD Studio/
├── database/xrdstudio.db
├── data/<ULID>/original.<ext> + metadata.json
├── cache/          -- 重采样与背景曲线缓存(可随时删)
├── exports/
└── logs/           -- 引擎日志(用户可见入口:Settings → About → Open Logs)
```

## 8.4 导入安全(与 v1 一致)

导入 = 记录 source path + **复制**进 Data Store;分析永远基于软件内的 copy。用户移动/重命名/删除外部原文件不影响 Dataset。SHA-256 查重:

```text
This dataset already exists. (Project / Sample / 文件名)
[ Open Existing ]  [ Import Anyway ]
```

路径注意:`stored_path` 一律相对 Data Store 根目录存,Data Store 可整体迁移;安装与运行前检测非 ASCII 路径,若安装目录含中文且引发 sidecar 问题,回退 `%LOCALAPPDATA%\XRDStudio`(见 §11.4)。

---

# 9. 性能设计(新增,v1 完全缺失)

## 9.1 传输:二进制,不是 JSON

v1 说"普通数据 JSON 即可"低估了成本:3,501–15,000 点转 JSON 文本约 50–200 KB/次,滑块拖动时每秒数十次往返,直接击穿 §9.4 的目标。规定:

```text
引擎 → 前端:曲线数据一律 ArrayBuffer(Float32,xy 交错),
  Tauri IPC 支持二进制(invoke 返回 tauri::ipc::Response / 自定协议);
  前端 new Float32Array(buffer) 零解析,直接喂 ECharts。
前端 → 引擎:控制命令(参数、id)走 JSON 字符串 —— 命令是小数据,没问题。
曲线元信息(axis/unit/range/hash)走 JSON 头 + 二进制数据体的信封格式:
  { "kind": "curve", "id": "...", "n": 3501 } + f32[n][2]
```

ECharts 大数据集用 `appendData`/`typedArray` dataset 路径 + `large: true`,峰值渲染关 animation。

## 9.2 分层职责:哪些计算在前端,哪些必须进引擎

```text
前端(纯显示,零往返,毫秒级):
  - Normalization(Max=1 / Max=100):对已缓存的 Float32 数组就地缩放
  - Stacked Offset
  - Line 粗细/颜色/显隐、坐标显示换算(2θ↔d↔Q 只是换 x 映射,可用查表)
引擎(涉及算法,异步):
  - Crop / Background / Smooth / Peaks 及一切 metric
```

滑块分两类:

- **纯显示类**(Offset/Normalize):前端即时生效,不走引擎。
- **引擎类**(SG window、AsLS λ):拖动中**节流发送**(rAF + 30ms debounce,只发最新值),引擎计算带 `cancel` 语义(新请求作废排队中的旧请求),返回后直接 setOption。预览结果进 cache,不落库。

## 9.3 后端节流与缓存

- 引擎对同一 dataset 的处理链做增量缓存:只改 Smooth 参数时不重算 Background。
- AsLS 稀疏求解 15k 点 < 30ms、100k 点 < 300ms(实测基准入库 CI),超时降级:拖动中用降采样序列(≤5k 点)算 preview,松手后全量精算。

## 9.4 性能验收指标(取代 v1 的口号式目标)

| 指标 | 目标 | 测法 |
|------|------|------|
| 冷启动到 Shell 可交互 | < 2s | 秒表 ×10,90 分位 |
| Sidecar 就绪(首条命令可用) | 后台 < 5s,期间 UI 全功能可浏览已缓存数据 | 同上 |
| 切换 Sample(已缓存) | < 150ms | Performance API |
| 首次绘制 3.5k 点 | < 300ms | 同上 |
| 滑块参数 → 图上更新 | < 100ms(降采样 preview 路径) | 同上 |
| 导入 15k 点文件(含哈希+复制) | < 1s | 端到端计时 |
| 内存(单 Project,10 样品) | < 800MB 常驻 | 任务管理器采样 |

## 9.5 什么时候才需要更重的方案

超过 500k 点的文件(同步辐射高分辨):Milestone 5 实测;若确实进入日常场景,上"服务器端 LTTB 降采样 + 缩放窗口重取"方案。**V0.3 不为 1M 点设计,但 Envelope 协议留 `downsampled: true` 标志位**,以后不改协议。

---

# 10. 前端设计

## 10.1 组件结构(同 v1)

```text
App ── AppShell(TopBar / LibrarySidebar / Workspace(PlotHeader/XRDChart/
       PeakDrawer) / Inspector / StatusBar)
    ── ImportSheet / CommandPalette / SettingsDialog / ToastProvider
```

```text
features/: library · import · plot · processing · peaks · export · settings
```

## 10.2 Stores(Zustand,补充职责边界)

```text
appStore       主题/窗口/全局状态
projectStore   Project/Sample/Dataset 树(cache-first:数据来自引擎)
plotStore      视图态:缩放窗口/overlay/multi-select 曲线
analysisStore  当前 Analysis 参数 + preview 状态机(见 10.3)
selectionStore 选中 sample/dataset/peak —— 联动逻辑的中枢
uiStore        弹窗/palette/toast
```

## 10.3 Preview 状态机(回答 v1 未定义的 autosave 语义)

```text
idle →(参数变化) previewing(只发引擎类参数,取消旧请求)
previewing →(300ms 无新输入) committing(PATCH analyses,写 undo 栈,落库)
committing → idle
失败:回滚到 commit 前参数 + toast
```

"停止调整 = 300ms 无输入"是全局唯一定义,状态栏 `Saved` 徽标由 committing 成功驱动。

## 10.4 与引擎的接口(command 清单)

```text
invoke("engine_command", { cmd, payload })  — 全部经 Rust 转发到 sidecar
                                              (stdio 长度前缀帧,见 11.2)

—— 库与项目 ——
projects.list / create { name, description? }
samples.create  { project_id, name, temperature?, duration?, material?,
                  batch?, wavelength?, notes? }
samples.update  { id, name?, temperature?, duration?, material?, batch?,
                  wavelength?, notes? }
library.tree    → { samples[], batches[] }
meta.rename     { table, id, name }
meta.soft_delete { table, id } → { cascadedDatasets }
meta.restore    { table, id, cascade_parent? } → { restoredAncestors[] }
meta.file_path  { table: datasets|ec_cells, id } → { path, kind: source|stored }
                (右键"在资源管理器中显示";原文件不在时回落库内副本)
trash.list      { limit? } → { items[], total }
meta.purge      { table, id } | { all: true }

—— 数据与处理 ——
datasets.import { paths[], project_id, sample_id?, new_sample?,
                  batch_id?, batch_name?, x_column?, y_column?,
                  x_axis_type?, wavelength?, import_anyway? }
datasets.curve  { id, max_points?, x_min?, x_max? } → Envelope(binary)
datasets.scan_folder { folder } → { files[], groups[] }   (只建议,不落库)
analysis.preview { dataset_id, pipeline, max_points? } → Envelope(binary, curve)
analysis.commit  { dataset_id, pipeline }

—— 峰 ——
peaks.detect      { analysis_id | dataset_id } → { peaks[], annotation }
peaks.list        { analysis_id } → { peaks[] }
peaks.annotate    { analysis_id | dataset_id } → { annotated[], annotation }
peaks.update_bounds { peak_id, left, right } → { peak }

—— 高镍分析 ——
profiles.list / create
domain.high_ni.analyze { dataset_id, profile_id?, modules?, fit? }
domain.high_ni.get     { dataset_id | analysis_id } → { found, result, stale }
trend.series           { project_id, metric?, x_axis? } → { points[] }

—— 导出 ——
export.figure / export.data / export.peaks

—— 电化学(LAND dQ/dV 归因,详见 docs/DEVELOPMENT_EC.md) ——
ec.preview          { path } → { session, qa, n_cycles, mass_mg_estimate }
ec.import           { paths[], project_id, sample_id? | new_sample?,
                      active_mass_mg?, import_anyway? }
ec.cells            { sample_id } → { cells[] }
ec.cell_overview    { cell_id } → { session, qa, cycles[] }
ec.dqdv_compute     { cell_id, params? } → { peaks[], tracks[], paramsHash }
ec.dqdv_state       { cell_id } → { computed, paramsHash }
ec.dqdv_result      { cell_id } → { computed, params, peaks[], tracks[] }
                    (读回上次固化的明细+参数快照,重启后免重算)
ec.dqdv_curve       { cell_id, cycle, direction } → Envelope(binary, v/dqdv)
ec.dqdv_waterfall   { cell_id, direction, step } → Envelope(binary, matrix)
ec.attribution_run  { cell_id, params? } → { cycles[], conclusion, ... }
ec.attribution_get  { cell_id } → { computed, stale, ... }
ec.export           { cell_id, kind: cycles|peaks|attribution }

—— 预设 ——
presets.save / list / apply
```

`peaks.detect` 同时接受 `dataset_id` —— 前端手里通常只有它,不关心 analysis
的内部 id,支持它能省掉一次多余往返。

**曲线数据的信封**(§9.1):`data` 里不再有 `x`/`y` 数组,取而代之的是
`data.__bin__`(描述)+ 帧尾的二进制体。JSON 头约 200 字节,数组按
`layout` 顺序切成连续 `f32[n]` 段。前端解成 `Float32Array` 直喂 ECharts。

> **2026-09-18 修订:** 上表按实际实现重写。原清单里的 `datasets.list` /
> `datasets.get` / `analysis.get_or_create` **从未实现**(功能被内联进
> `library.tree` 与 `_active_analysis`),而本轮新增的
> `samples.create` / `samples.update` / `datasets.scan_folder` /
> `domain.high_ni.get` / `trend.series` 也不在旧清单里。
>
> **2026-09-24 修订:** `presets.*`(S5,Inspector 参数预设区)与
> `peaks.update_bounds`(M4,峰表双击左界/右界)已接上 UI;
> `peaks.list` 仍无前端调用(峰表走 preview/state 内存峰)。
>
> **2026-10-06 修订:** 新增 `ec.*` 命名空间(电化学 LAND 数据,v7 schema:
> `ec_cells`/`ec_cycles`/`ec_analyses`)。`meta.*` 的 table 白名单与回收站
> 同步扩展了 `ec_cells`。engine 依赖新增 `openpyxl`。

`trash.list` 的 `kind` 用**表名**(`projects`/`samples`/`batches`/`datasets`/
`ec_cells`),前端可以
把 `item.kind` 直接透传给 `meta.restore` / `meta.purge`,不必再维护一张
「单数 ↔ 表名」映射表。

开发期可以按需起 FastAPI 复用同一引擎核心库做 curl 调试,但**接口以 command schema 为单一事实源**(Pydantic 模型 TS 类型由 `ts-pydantic`/手工同步,CI 校验一致性)。

## 10.5 Undo / Redo(定义实现方式,不再悬空)

- 范围:**参数类操作**(pipeline 参数、peak bounds、normalize/offset 等显示参数、重命名)。导入/删除 Dataset 不进 undo 栈(删除走 Trash 语义,用恢复代替撤销)。
- **Trash 语义**:删除 = 移到回收站,用**恢复**代替撤销。恢复时若父级仍在回收站,一并向上恢复父级(否则用户点了恢复却看不到东西);恢复父级时**不**向下级联 —— 子对象从未被标记删除,这是隐式级联(§8.2)带来的直接好处,不是遗漏。「彻底删除」不可恢复,必须二次确认。
- 实现:`analysisStore` 维护有界快照栈(50 步),快照即 pipeline_json 深拷贝;`Ctrl+Z / Ctrl+Shift+Z`。
- Undo 触发一次 preview→commit,复用 §10.3 状态机,不另写通路。

## 10.6 拖放实现注意(Tauri 已知坑,提前绕)

- 使用 `tauri-plugin-upload`(其 `onDragDropEvent` 给出**规范化后的真实文件路径**),不要信 WebView 给的 `files[].path`(Windows 下带 URL 编码与 `\\?\` 前缀)。
- 拖入即进入 Import Sheet(淡蓝 Overlay + "Drop XRD Data / CSV · TXT · XY · DAT"),多文件一次 Sheet 内完成归属。

---

# 11. 打包与分发(新增,v1 最大盲区)

## 11.1 Sidecar 打包:两条路线,先做决策实验

| 路线 | 做法 | 风险 |
|------|------|------|
| A(默认尝试)| **uv 管理的内嵌 Python 运行时**:安装期解压一个冻结 venv(python-standalone 构建 + pip 依赖),引擎以 `python.exe -m xrd_engine` 启动 | 体积约 60–90MB;解压慢一次性 |
| B(备选) | PyInstaller onedir(**禁止 onefile**) | onefile 在冷启动解压 + AV 扫描下实测可达 10s+;onedir 好得多但仍是打包产物特征 |

**Milestone 2 第一周做决策实验**:路线 A/B 各打一个包,在纯净 Win10 虚拟机(无 Developer Mode、路径含中文)测冷启动、Defender SmartScreen 表现。**不通过就换路线,不带病进 Milestone 3。** 本条为 Milestone 2 的硬性出口条件。

## 11.2 通信协议

生产路径:**Tauri sidecar(stdio) + 长度前缀帧**:`[u32 len][json_header][optional binary payload]`。Rust 侧负责进程监管(退避重启、stderr 入日志文件)、请求 id 路由、二进制透传(不经 JSON 序列化)。绑定端口的 HTTP 方案仅存在于开发调试。

## 11.3 安装包

- Windows:`.msi`(Tauri bundler,MSI 更适合企业/学校统装)+ NSIS `.exe` 双产物。
- **捆绑 WebView2 Fixed Version Runtime 或 Evergreen Bootstrapper**:Win10 老镜像/LTSC 无预装;启动检测失败时显示人话引导页(内置离线 bootstrapper 静默安装优先)。
- 代码签名:第一版可无(接受 SmartScreen 警告 + 官网写清楚),商业分发时补 OV/EV 证书 —— 列入发布前 checklist,不是里程碑。

## 11.4 Windows 环境矩阵(发布前逐项过)

```text
□ 干净 Win10 LTSC(无 WebView2、无 VC++ redist)
□ Win11 家庭版,用户目录含中文(“C:\Users\张三”)
□ 安装到 D:\ 非默认路径;Data Store 在 OneDrive 同步目录内(需警告)
□ Defender 实时防护开启,完整导入→分析→导出流程
□ 高 DPI 125/150/200%
□ 断网冷启动(无遥测依赖,软件必须全功能)
```

---

# 12. 开发计划与验收标准

> 方法论不变:**Vertical Slice**。每阶段产出可运行、可给人用的软件,不做分层瀑布。
> 变化:**每阶段出口条件改为可测清单**,并前移三个高风险验证(打包实验、网格检测、性能基准)。

> **本文档是需求与验收标准,不记录实时进度。** 各里程碑的当前完成情况、
> 已修缺陷与下一步计划见 `PROGRESS.md`;高镍路线的阶段状态见
> `docs/DEVELOPMENT_HIGH_NI.md`。下面各节的 `□` 是**验收条件**,
> 勾选状态以 `PROGRESS.md` 为准。
>
> 截至 2026-09-18 的概览:M1/M3 完成;M2 差 sidecar 打包决策实验;
> M4 差灵敏度映射表校准与 Bounds 编辑 UI;M5 差 **Preset UI(S5)** 与 SVG/PDF;
> M6 未开始。**S4 系列对比**与**结构趋势图**不在 M1–M6 里,已额外交付。

## 12.0 仓库与工程基线

```text
xrd-studio/
├── app/            # Tauri + React(src/, src-tauri/)
├── engine/         # xrd_engine(python 包)+ tests/
├── fixtures/       # 基准谱数据(授权可公开者;合成谱由脚本生成)
└── docs/
CI: ruff + pytest(引擎)· tsc + eslint + vitest(前端)· cargo clippy
```

引擎测试覆盖:parser(每种格式 × 分隔符 × 编码 × 垃圾头)、coords(NIST Si)、非均匀网格重采样、AsLS/SG 数值回归(blessed 输出比对)、find_peaks 映射表。

## Milestone 1 — App Shell(目标:像成品)

TopBar/Sidebar/Inspector/StatusBar/Import Sheet/命令面板骨架,假数据(§4.3 的 3,501 点合成谱)驱动完整布局,Light/Dark/4px grid/动画规范全部落地。

出口:
```text
□ 1440×900 与 1280×760 两档布局无破版
□ 键盘可达所有主要控件(Tab 序 / Cmd+K)
□ 设计 token(色板/圆角/阴影/动效时长)以 CSS variable 固化,
  不允许组件内硬编码色值
```

## Milestone 2 — Import & Plot + 工程决策

拖放/导入/解析/列映射/SHA-256 查重/SQLite/Data Store 复制存储/真实曲线(二进制 Envelope)/Sample 切换。
**并行硬性任务:§11.1 sidecar 打包决策实验、非均匀网格检测(§4.2)、性能表 §9.4 的基准 harness 建立(CI 跑)。**

出口:
```text
□ fixtures 中全部真实仪器导出文件(≥15 个,覆盖 4 种扩展名、
  2 种编码、垃圾头、科学计数、降序 x)导入成功或正确进入列映射流程
□ 3,501 点首次绘制 < 300ms(§9.4 harness 实测)
□ 打包实验结论成文,选定路线 A/B,记录冷启动数据
□ 外部原文件删除后,重开软件 Dataset 完好(导入安全回归测试)
```

## Milestone 3 — Processing

Background(AsLS/Polynomial)+ Smooth + Crop + Normalize/Offset 前端本地化 + 非均匀网格重采样 + Preview 状态机(§10.3)+ 增量缓存。

出口:
```text
□ 滑块 preview 往返 < 100ms(含 15k 点降采样路径)
□ Background→Smooth 顺序及 §7.1 平滑提示在 UI 正确呈现
□ 非均匀网格数据集:全部处理功能可用,结果标注 resampled
□ 拖动过程中取消旧请求:无乱序结果闪现(日志验证 cancel)
```

## Milestone 4 — Peaks

检测/三参数映射/Advanced 折叠/FWHM/Area/Bounds 编辑/Peak Table/双向联动/峰标签。

出口:
```text
□ 校准集验收:10 张实验室常规谱 + 5 张同步辐射谱 + 5 张合成噪声谱,
  “Sensitivity 三档”每档的检出峰集合由 2 名材料方向人工评审,
  可接受率 ≥ 80%;不达标 → 只调映射表,不动架构
□ Plot↔Table 联动点击正确率 100%(自动化测试)
□ FWHM 合成高斯谱误差 < 2%(pytest)
```

## Milestone 5 — Export & Presets & Undo

图导出(PNG/SVG/PDF,2×/指定像素)、数据/峰表导出(含 pipeline 快照)、**Analysis Presets 保存/批量应用(S5)**、Undo/Redo。

出口:
```text
□ 导出 CSV 头部含可复现的处理参数
□ Preset 应用到 10 样品 Project 总耗时 < 5s,过程中 UI 不冻结
□ Ctrl+Z 50 步内参数操作全部可逆,与 commit 状态机一致
```

## Milestone 6 — Polish & Installer

Hover/Loading/Error/Empty 全状态清单、错误文案审查(traceback 零泄漏)、性能达标(§9.4 全表)、§11.4 环境矩阵全绿、安装包双产物 + WebView2 检测引导、崩溃恢复(sidecar 强杀测试)。

**UI Polish 是一个正式阶段,不是最后顺手改** —— 与 v1 立场一致,且时间按总工期 20% 预留。

## 开发顺序总览

```text
01 Shell → 02 假曲线 → 03 切换 → 04 真实导入 → [A] sidecar 打包实验(并行)
→ 05 SQLite → 06 二进制 Envelope + 曲线 → [B] 网格检测/重采样(并行)
→ 07 Processing(含性能 harness)→ 08 Peaks → 09 Presets+Undo
→ 10 Export → 11 Polish → 12 Installer+环境矩阵
```

---

# 13. 风险登记表

| 风险 | 等级 | 缓解 | 验证点 |
|------|------|------|--------|
| Sidecar 冷启动慢 / AV 误报 | **高** | §11.1 双路线,MS2 决策实验 | MS2 出口 |
| 滑块 preview 不跟手 | **高** | 二进制传输+分层职责+降采样 | MS3 出口 |
| 真实仪器文件格式五花八门 | 中高 | fixtures 池 + 列映射兜底 | MS2 出口 |
| 寻峰默认参数与用户预期不符 | 中 | 人工评审校准映射表 | MS4 出口 |
| 中文路径/编码 | 中 | GB18030 回退、LOCALAPPDATA 兜底 | MS2/MS6 |
| 非均匀网格算法失真 | 中 | §4.2 强制检测重采样 | MS3 出口 |
| ECharts 极端数据性能 | 低中 | 降采样标志位预留 | MS5 实测 |

---

# 14. V0.4+ 展望(同 v1,微调)

```text
V0.4  单峰拟合(Pseudo-Voigt 优先)/ 手动增删峰 / 批量分析深化 / 图形模板
V0.5  CIF 导入与理论谱对比 / Scherrer 晶粒尺寸(依赖仪器宽化预设)/ Kα2 剥离评估
V0.6+ 物相候选检索(COD)/ 批次趋势分析 / 报告生成
V1.0  数据模型冻结后:物相鉴定 / 结构精修接口 / 插件系统
```

其中 Preset/批量(S5)已提前进 V0.3(Milestone 5),因数据模型依赖不能后置。

---

# 15. 首页草图(同 v1 §78,状态栏数字按 §4.3 修正)

```text
╭──────────────────────────────────────────────────────────────────────────╮
│ ◉ XRD Studio    LFP Study                                 ⌕   ＋ Import  │
├────────────────────┬─────────────────────────────┬───────────────────────┤
│ Library            │ LFP-500C / scan-01.xy       │ Inspector             │
│ ⌕ Search           │              34.39°         │                       │
│ RECENT             │                │            │ PROCESSING            │
│ ● LFP-500C         │                /\           │ Background  ●         │
│ ● NCM811-03        │      /\       /  \    /\    │ AsLS  λ 10^5          │
│                    │ _____/  \_____/    \__/  \__│ Smoothing  ○          │
│ PROJECTS           │                             │  ⚠ FWHM 基于平滑后曲线 │
│ ⌄ LFP Study        │                             │                       │
│   ● LFP-400C       │                             │ PEAKS                 │
│   ● LFP-450C       │                             │ Sensitivity ━━●━━━━   │
│   ● LFP-500C   2   │                             │ [ Detect Peaks ]      │
│      scan-01.xy    │                             │ APPLY PRESET ▾        │
│      scan-02.xy    │                             │                       │
│   ● LFP-550C       │                             │ DISPLAY               │
│ ⌄ NCM Study        │                             │ Max = 100  Offset 0   │
├────────────────────┴─────────────────────────────┴───────────────────────┤
│ Peaks 12 ⌃   2θ 34.390°   I 31,542   3,501 pts   Cu Kα      Saved        │
╰──────────────────────────────────────────────────────────────────────────╯
```

---

# 16. 最终产品原则(不变)

V0.3 不需要成为 Jade / HighScore / TOPAS / GSAS-II。第一阶段的目标:

> **一个真正好用、好看、数据不会乱、算出来的数字可追溯的 XRD 工作台。**

核心竞争力:样品管理清楚、原始数据安全、图谱漂亮、交互跟手、分析直观、结果可复现、导出方便、装上就能用。

设计方向:**Apple-inspired minimal UI + Scientific Workstation**。
核心原则:**Library first. Plot first. Original data first. Simplicity first. —— 外加一条:Measurable before shippable.(每个出口条件是测试,不是形容词)**
