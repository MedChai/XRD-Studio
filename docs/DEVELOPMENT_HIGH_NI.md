# XRD Studio 高镍无钴正极开发路线

> 文档版本：V0.1
> 
> 编写日期：2026-09-16
> 
> 适用基线：XRD Studio V0.3，Tauri 2 + React/TypeScript + Python sidecar + SQLite

## 1. 文档目的

本文档定义下一阶段的实际开发顺序：

1. 先完成原规划中尚未完成的工程能力：性能链路、处理功能、导出、工程质量。
2. 再实现高镍无钴层状正极的前三类专用分析：
   - Li/Ni 反位混排风险指标。
   - R-3m 层状结构判定。
   - 晶胞参数和结构趋势分析。

本文档是开发执行文档，不是泛功能愿望清单。每个阶段都定义代码范围、接口、数据、测试和出口条件。

## 2. 当前基线与状态修正

### 2.1 已经可复用的能力

- 文件导入、编码回退、列映射、查重、数据复制存储已完成。
- SQLite 中已有 Project / Sample / Dataset / Analysis / Peaks / Preset 等基础对象。
- Python 引擎已有坐标换算、网格重采样、背景、平滑、寻峰、FWHM、面积和峰边界编辑。
- Tauri 已能通过 stdio 长度前缀协议监管 Python 引擎。
- 前端已有处理参数、preview、commit、Undo/Redo、峰表、图表联动和回收站。
- 启动器已经具备开发端口检查、残留 Vite 清理和 Tauri 日志落盘能力。

### 2.2 当前确认未完成项

以下项目优先于高镍分析：

| 编号 | 项目 | 当前状态 | 说明 |
|---|---|---|---|
| O2 | 二进制曲线传输 | ✅ **已完成** (2026-09-18) | `CurvePayload.x/y` 已是 `Float32Array`，走 `[u32 jsonLen][json][binary]` 信封;4751 点曲线的 JSON 头从约 100 KB 降到 200 字节。 |
| O2 | 前端降采样 preview | 已完成第一版 | 统一限制显示点数，分析指标仍使用全量曲线。 |
| O2 | 增量缓存(§9.3) | ✅ **已完成** (2026-09-18) | 曲线解析缓存(2973×)+ 处理链阶段缓存(按参数前缀)。 |
| O3 | Crop 前端闭环 | 已完成第一版 | Inspector 支持范围输入、当前视图填充、preview、commit、Undo/Redo。 |
| O3 | 处理性能基准 | 部分完成 | 引擎侧基准已有(`tests/test_benchmarks.py`);前端感知延迟仍缺。 |
| O4 | 导出 | 部分完成 | 峰表 CSV、处理后数据 CSV、PNG 已形成 Tauri/浏览器双路径;**SVG/PDF 未做**。 |
| O5 | 前端测试 | 未完成 | 尚未建立 Vitest / ESLint 的前端测试基线。 |
| O5 | 工程清理 | 进行中 | 死代码已清(`LabeledSlider`/`PeakRow`/`describeError`、`data_store` 的磁盘缓存对);Vitest/ESLint 仍待建立。 |
| O5 | Preset / 参数复用 UI | **未完成** | 引擎三个命令 + `analysis_presets` 表都在,前端零调用 —— README §1.2 的 S5 场景未落地。 |
| O6 | 安装包 | 已打包 NSIS 安装版 | 启动和打包流程见 [LAUNCHER.md](../LAUNCHER.md)。 |

原 `PROGRESS.md` 中部分状态已经滞后。例如背景曲线前端显示在后续修复中已经完成，但 M3 计划文字仍保留旧描述。开发时以代码和测试结果为准，并在每个阶段结束后同步 `PROGRESS.md`。

> **2026-09-17 更新：** 阶段 F 的混排实现已按 §9 修正三处缺陷（峰本体面积而非整窗积分、
> 分级改用峰高比、R_order 仅在 006/102 分裂时报告），并新增通用晶面(hkl)标注
> `peaks.annotate`（参考表来自 §8.3 峰位模板，已物理反算）。详见 `PROGRESS.md` 第九节。

## 3. 总体开发顺序

```text
阶段 A：二进制传输与性能链路（✅ 已完成 2026-09-18）
    ↓
阶段 B：处理功能收尾与端到端验证（第一版已完成）
    ↓
阶段 C：导出闭环（CSV/PNG 第一版已完成；SVG/PDF 未做）
    ↓
阶段 D：前端测试、工程清理与稳定性（⬜ 未开始）
    ↓
阶段 E：高镍分析基础设施与材料档案（✅ 第一版已完成）
    ↓
阶段 F：Li/Ni 混排分析（✅ 第一版已完成）
    ↓
阶段 G：R-3m 层状结构分析（✅ 第一版已完成）
    ↓
阶段 H：晶胞参数与结构趋势（✅ 已完成 2026-09-18）
```

高镍分析的三个核心阶段依赖阶段 E 的材料档案和基础设施。

> **2026-09-18 更新:** 阶段 A 与阶段 H 已完成,详见 `PROGRESS.md` 第十三、十四节。
> 阶段 A 的实装比本文档原设计更完整 —— 除曲线外,还顺带做了**曲线解析缓存**
> 与**处理链阶段缓存**(§9.3 的增量缓存),实测曲线读取 40.14 ms → 0.0135 ms,
> 拖平滑窗口 6.04 ms(背景命中缓存)vs 拖背景 34.19 ms。

## 4. 阶段 A：二进制传输与性能链路

### 4.1 目标

把曲线和背景数据从 JSON 数组改为 Float32 二进制 Envelope，解决大量点数、拖动处理参数和批量分析时的序列化开销。

### 4.2 协议设计

保留现有 stdio 外层帧：

```text
[u32 big-endian payload length]
[UTF-8 JSON header]
[optional binary body]
```

建议 Envelope：

```json
{
  "kind": "curve",
  "version": 1,
  "axis": "two_theta",
  "wavelength": 1.5418,
  "pointCount": 3501,
  "encoding": "float32-interleaved-xy",
  "byteOffset": 0,
  "byteLength": 28008,
  "downsampled": false,
  "sourcePointCount": 3501
}
```

二进制体按以下顺序排列：

```text
x0, y0, x1, y1, x2, y2, ...
```

背景曲线不与主曲线混在同一数组中，单独作为一个 Envelope，避免未启用背景时传输无意义数据。

### 4.3 代码范围

引擎：

- 新增 `engine/xrd_engine/api/envelope.py`。
- 为 `datasets.curve` 和 `analysis.preview` 生成 Envelope 元数据和 Float32 body。
- 保留开发期 JSON fallback，便于纯浏览器 mock 和调试。
- 在协议中保留 `downsampled` 和 `sourcePointCount` 字段。

Rust：

- 扩展 stdio 帧结构，使响应可以携带 JSON header 和 binary body。
- Tauri command 返回可被前端读取的二进制响应。
- 严格检查 body 长度、点数和 Float32 对齐。
- 错误时返回人话错误，不把二进制解析异常泄露到 UI。

前端：

- `CurvePayload` 增加 Envelope 解码层，不让 ECharts 直接感知协议差异。
- `Float32Array` 解码后转换为 ECharts 所需的 tuple 或 dataset。
- 保留 JSON fallback，兼容旧 fixture 和开发 mock。

### 4.4 降采样策略

新增 `downsample` 参数：

```json
{
  "dataset_id": 12,
  "max_points": 5000,
  "x_min": 18.0,
  "x_max": 75.0,
  "mode": "preview"
}
```

第一版采用峰值保留型 min/max bucket，不使用会削弱窄峰的简单均匀抽点：

1. 按 x 范围切分 bucket。
2. 每个 bucket 保留最小 y 点、最大 y 点和必要端点。
3. 保证强峰、窄峰和背景转折不会被直接丢掉。
4. 返回 `downsampled: true`。

### 4.5 出口条件

> **2026-09-18:已完成。** 逐条对照:

- ✅ 15k 点数据 preview 往返不再传输 JSON 大数组 —— 曲线走二进制信封,
  实测 4751 点曲线的 JSON 头从约 100 KB 降到 **200 字节**(PROGRESS 第十四节)。
- ⬜ 100k 点数据首次显示可交互 —— 未单独实测(当前真实数据 4751 点)。
- ✅ 拖动 AsLS 和 SG 参数时，旧请求不会覆盖新请求 —— 前端 `_previewSeq`
  序号丢弃乱序结果;本轮另加**处理链阶段缓存**,拖平滑时背景不再重算
  (6.04 ms vs 拖背景 34.19 ms)。
- ✅ 同一数据集在全量模式和 preview 模式下峰位误差小于 1 个原始采样间隔 ——
  `test_api_commands.py::test_preview_downsample_keeps_full_resolution_metrics`。
- ✅ 增加协议测试、长度校验测试、Float32 解码测试和乱序响应测试 ——
  `test_envelope.py`(10 条)、`test_frame_protocol.py`(14 条)、
  `test_protocol_e2e.py`(5 条,**真的起引擎子进程走完整协议**)。

> 最后那组 e2e 测试当场抓到一个进程内测试看不见的崩溃:信封嵌在
> `data.curve` 里时(`analysis.preview` 的情形)漏摘 `_binary`,
> `json.dumps` 抛 TypeError 让**引擎进程直接退出**。详见 PROGRESS 第 14.3 节。

## 5. 阶段 B：处理功能收尾

### 5.1 Crop 前端闭环

在 Inspector 增加 Crop 区域：

- 开关：启用裁剪。
- `x_min` 和 `x_max` 数值输入。
- 使用当前图表范围作为快捷填充。
- `Apply to visible range` 按钮。
- 输入非法时禁止发送 preview。
- 图表范围框、Crop 参数和主图 x 轴双向同步。

Crop 只改变分析输入，不改动原始数据，也不改变 Dataset 文件。

### 5.2 Preview 语义

处理链顺序固定为：

```text
原始曲线 → Crop → Background → Background subtract → Smooth → Peaks → Metrics
```

需要在 UI 明确区分：

- 原始曲线。
- 当前 preview 曲线。
- 已保存的 analysis 参数。
- 尚未固化的峰结果。

平滑开启时继续显示 FWHM / Area 可能受影响的提示。定量分析默认建议关闭平滑，平滑只用于视觉或寻峰辅助。

### 5.3 性能 harness

新增前端 Performance API 记录：

- 首次绘制时间。
- 切换 Dataset 时间。
- 参数改变到图形更新的时间。
- 旧 preview 被取消的次数。
- 100 次连续滚轮或拖动是否出现异常状态。

结果写入测试日志，不在生产界面永久显示。

### 5.4 出口条件

- Crop 可以从 UI 完整启用、修改、保存、Undo 和恢复。
- 非均匀网格数据的处理结果标记 `resampled`。
- 15k 点 preview 更新目标小于 100ms；如果硬件无法达到，必须在日志中记录真实分位数。
- 处理参数、preview、commit、Undo/Redo 的状态在切换 Dataset 后不会串线。

## 6. 阶段 C：导出闭环

### 6.1 导出优先级

第一优先级：

1. 峰表 CSV。
2. 处理后数据 CSV。
3. 当前图表 PNG。

第二优先级：

4. SVG。
5. PDF。

高镍结构报告在这一阶段只预留导出接口，报告内容在阶段 H 后补齐。

### 6.2 导出内容

峰表 CSV 至少包含：

```text
dataset_id
sample_name
peak_index
position
intensity
fwhm
area
left_bound
right_bound
prominence
manual_override
```

处理后数据 CSV 至少包含：

```text
x
raw_y
background
processed_y
```

文件头部或同目录 metadata 文件必须包含：

- 软件版本。
- Dataset 名称和原始文件哈希。
- x 轴类型和波长。
- Crop、Background、Smooth、Peaks 参数。
- 导出时间。

### 6.3 命令接口

```text
export.peaks
export.data
export.figure
```

通用 payload：

```json
{
  "dataset_id": 12,
  "analysis_id": 8,
  "path": "C:/.../exports/result.csv",
  "overwrite": false
}
```

实际路径必须经过 DataStore 路径安全检查，不允许通过 `..` 逃逸到 DataStore 外部；用户选择的导出路径由 Tauri 文件对话框提供。

### 6.4 出口条件

- 导出的 CSV 可重新读取并保留必要处理参数。
- PNG 导出与当前图表的 x 范围、叠加模式、峰标签状态一致。
- 重名文件默认不覆盖，自动生成新文件名或要求确认。
- 导出失败不改变 analysis 和峰数据。

## 7. 阶段 D：前端测试与工程质量

### 7.1 测试基线

新增：

- Vitest。
- React Testing Library，覆盖纯状态和关键控件行为。
- ESLint。
- 前端类型检查和构建纳入统一脚本。

第一批测试重点：

1. preview 序号取消和乱序响应。
2. Undo/Redo 后旧 preview 不得覆盖新状态。
3. Toast 定时器不会提前清除新消息。
4. Crop 输入校验。
5. Envelope 解码和 fallback。
6. 导出参数生成。

### 7.2 工程清理

- 删除未使用的 `LabeledSlider`，或统一迁移到 `RangeSlider`。
- 更新 `PROGRESS.md` 测试数量和实际状态。
- 为命令接口建立单一 command registry，避免 README、Python、Rust、TS 四处漂移。
- 增加错误分类：输入错误、算法警告、引擎错误、导出错误、环境错误。
- 所有用户可见错误不显示 Python traceback。

### 7.3 出口条件

- `pytest` 全绿。
- `ruff` 全绿。
- `tsc`、`eslint`、`vitest`、前端 build 全绿。
- `cargo check` 和 Tauri dev 启动通过。
- 关键状态机有自动化测试，不依赖人工点击才能验证。

## 8. 阶段 E：高镍分析基础设施

前三类材料分析都依赖统一的材料档案、峰位模板和结果存储。

### 8.1 材料档案

新增高镍层状材料 Profile，至少包含：

```text
profile_id
project_id
name
family = high_ni_layered
formula
ni_fraction
li_tm_ratio
dopants_json
nominal_wavelength
instrument_name
reference_dataset_id
thresholds_json
created_at
updated_at
```

建议把 Profile 作为 Project 级对象，允许同一 Project 下多个 Dataset 共用。

### 8.2 数据库迁移

使用 schema version 2，新增：

```sql
CREATE TABLE material_profiles (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    family TEXT NOT NULL,
    formula TEXT NOT NULL DEFAULT '',
    ni_fraction REAL,
    li_tm_ratio REAL,
    dopants_json TEXT NOT NULL DEFAULT '{}',
    wavelength REAL,
    instrument_name TEXT NOT NULL DEFAULT '',
    reference_dataset_id INTEGER,
    thresholds_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE domain_results (
    id INTEGER PRIMARY KEY,
    analysis_id INTEGER NOT NULL REFERENCES analyses(id),
    profile_id INTEGER REFERENCES material_profiles(id),
    domain TEXT NOT NULL,
    result_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    source_hash TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_domain_results_analysis
    ON domain_results(analysis_id, domain);
```

采用 JSON 保存 domain 结果，避免每增加一个材料指标就迁移一次表结构；关键结果仍必须有稳定的 TypeScript/Python 类型定义。

### 8.3 高镍峰位模板

第一版支持手工修正峰窗，不把 2θ 固定成单一数字。模板应按波长和材料 Profile 计算或配置：

```text
(003)
(101)
(006)
(102)      ← 文献显示名(与消光指数 (0,1,2) 同一物理反射)
(104)
(108)      ← 文献显示名(与消光指数 (0,1,8) 同一物理反射)
(110)
```

> **2026-09-24 修订:** 双峰第二个分量对外统一用**文献名 `(102)`/`(108)`**
> (h-k+l 约定)。枚举/拟合仍走消光规则 `-h+k+l ≡ 0 (mod 3)` 下的
> (0,1,2)/(0,1,8)——两套名字 d 相同,见 `indexing._LITERATURE_HKL`。
> **不要**把显示名 (102) 拿回 `allowed_hkl` 当禁阻判断。
>
> R-3m (#166) 阳离子 3a 位点的散射因子是 `1 + ω^(h+2k+2l) + ω^(2h+k+l)`
> (ω=exp(2πi/3)),非零当且仅当 `-h+k+l ≡ 0 (mod 3)`。按此消光规则
> `(001)(100)(011)(111)` 禁阻;`(006)(012)(104)(018)(110)(113)` 允许,
> 对外双峰名写 (102)/(108)。
>
> 层状正极的实验分工是 **(006) 弱、(102) 强**。
>
> **实现:** `high_ni/indexing.py` 的**全谱晶胞精修 + 邻域强度贡献占比归属**。
> 理由见 `PROGRESS.md` 第十节:最近邻只看几何距离,不看这个峰由哪条
> 理论线在供强度;而 (006)/(102) 间距仅约 0.36°,(108)/(110) 约 0.49°,
> 观测峰稍偏一点判定就翻转。晶胞也从写死的 2.878/14.216 改为**每张谱精修**
> (实测用户数据精修出 2.884/14.278,这个差异足以让归属翻转)。

默认以峰窗和相对顺序识别，允许用户在异常峰位移动时扩大窗口或重新指定参考峰。

### 8.4 统一结果结构

Python 类型和 TS 类型保持一致：

```json
{
  "domain": "high_ni_layered",
  "profileId": 1,
  "datasetId": 12,
  "sourceHash": "...",
  "peaks": {},
  "mixing": {},
  "layered": {},
  "lattice": {},
  "warnings": [],
  "confidence": "medium"
}
```

## 9. 阶段 F：Li/Ni 反位混排分析

### 9.1 目标

提供可追溯的相对混排风险筛查，而不是直接声称测得 Li/Ni 占位率。

### 9.2 输入与预处理

分析必须记录：

- 原始曲线哈希。
- 波长。
- 是否做零点校正。
- 背景算法和参数。
- 是否使用平滑。
- 峰拟合方法。
- 峰面积积分区间。

定量指标默认使用扣背景后的积分面积，不使用未经处理的单点峰高。平滑默认关闭；如果使用平滑，结果必须带 warning。

### 9.3 指标

核心指标：

```text
I003_I104_area_ratio = Area(003) / Area(104)
I003_I104_height_ratio = Height(003) / Height(104)
```

辅助指标：

```text
R_order = [Area(006) + Area(102)] / Area(101)
```

对于重叠峰，必须先进行双峰或多峰拟合，禁止直接把重叠包络面积当成单个峰面积。

### 9.4 风险分级

不硬编码一个对所有样品都适用的阈值。优先级如下：

1. 有用户参考样品时，以参考样品分布建立基线。
2. 没有参考样品时，显示指标值和低置信度提示。
3. 文献经验阈值只能作为“经验提醒”，不能作为最终结论。

> **2026-09-17 分级口径修正：** 默认阈值(1.20/1.30)按文献惯用的**峰高比**
> I(003)/I(104) 语义标定。面积比会被 `FWHM_003/FWHM_104` 系统性压低(003 固有展宽
> 小于 104),用面积比分级会把清晰分层样误判成高混排风险。故 `_risk_from_height_ratio`
> 以**峰高比**为主分级指标,面积比仍返回给用户作参考,不作为分级依据。

建议结果：

```text
mixingRisk: low | medium | high | unknown
referenceMode: user_baseline | literature_hint | none
confidence: high | medium | low
```

### 9.5 API

```text
domain.high_ni.analyze
```

payload：

```json
{
  "dataset_id": 12,
  "analysis_id": 8,
  "profile_id": 1,
  "modules": ["mixing"],
  "fit": true
}
```

返回：

```json
{
  "mixing": {
    "i003_i104_area_ratio": 1.38,
    "i003_i104_height_ratio": 1.21,
    "r_order": 0.42,
    "risk": "low",
    "confidence": "medium",
    "evidence": ["003/104 area ratio", "006/102 fit"]
  }
}
```

### 9.6 测试

- 人工构造不同 003 强度的合成谱，验证比值方向正确。
- 改变背景斜率，验证局部背景处理不会造成大幅漂移。
- 加入择优取向模拟，验证结果置信度会下降或出现 warning。
- 加入 006/102 重叠，验证拟合面积不等于包络峰高。
- 同一数据重复分析，结果必须可复现。

## 10. 阶段 G：R-3m 层状结构分析

### 10.1 目标

判断图谱是否具有高镍层状 α-NaFeO₂ 型结构的主要特征，并给出证据链：峰位、峰分裂、拟合质量和异常峰提示。

### 10.2 核心峰组

第一版固定分析以下峰组：

```text
(003)
(101)
(006)/(102)      ← 双峰对(文献显示名)
(104)
(108)/(110)
```

### 10.3 双峰分裂指标

对 `(006)/(102)` 和 `(108)/(110)` 分别计算：

```text
peak_separation
valley_ratio = valley_intensity / min(peak1_intensity, peak2_intensity)
single_fit_error
double_fit_error
fit_improvement
```

输出：

```text
splitStatus: clear | weak | unresolved | not_found
```

不能只用某个固定谷底阈值。必须同时考虑：

- 仪器分辨率。
- 采样步长。
- 峰宽。
- 噪声。
- 波长和零点偏移。

### 10.4 R-3m 结构结论

建议结论分为：

```text
layeredOrder: good | acceptable | degraded | unknown
```

结论必须附证据：

```text
evidence:
  - 006/102 双峰拟合改善明显
  - 108/110 分裂较弱
  - 003/104 峰位顺序正常
  - 峰宽高于参考样品
```

“R-3m 良好”只表示当前图谱与层状结构特征一致，不等于证明样品不存在表面岩盐重构。普通粉末 XRD 更接近体相平均信息，表面重构必须保留外部表征提示。

### 10.5 API 与 UI

复用 `domain.high_ni.analyze`，模块参数：

```json
{
  "modules": ["layered"]
}
```

Inspector 新增“层状结构”区域：

- 结构结论。
- `(006)/(102)` 分裂状态。
- `(108)/(110)` 分裂状态。
- 拟合残差。
- 一键跳转对应图谱区域。
- 显示参考样品叠加线。

## 11. 阶段 H：晶胞参数与结构趋势

### 11.1 目标

从峰位得到六方晶系近似晶胞参数，并支持同一 Project 内样品趋势比较。

### 11.2 计算方法

由 Bragg 定律计算晶面间距：

```text
2 d sin(theta) = lambda
```

六方晶系使用：

```text
1 / d(hkl)^2 =
    4/3 * (h^2 + h*k + k^2) / a^2
    + l^2 / c^2
```

第一版不使用单个峰直接决定晶胞参数，而是：

1. 对多个可靠峰进行联合最小二乘拟合。
2. 优先使用 `(003)`、`(104)`、`(101)`、`(006)/(102)`、`(108)/(110)`。
3. 对重叠峰使用拟合中心，不使用包络中心。
4. 输出拟合残差和参数不确定度。
5. 如果零点校正未完成，降低置信度。

### 11.3 输出指标

```text
a
c
c_over_a
cell_volume
fit_rms
zero_shift
uncertainty_a
uncertainty_c
```

### 11.4 趋势视图

> **状态:✅ 已实现(2026-09-18)。** 面板在 `app/src/features/trend/TrendPanel.tsx`,
> 数据由 `trend.series` 命令提供,组装逻辑在 `engine/xrd_engine/high_ni/trend.py`。
> 命令面板(Ctrl+K)里的「结构趋势」是入口。
>
> **实现时发现并修掉一个会给出错误结论的口径问题:** 晶胞参数在本仓库有
> **两条计算路径** —— `refine_cell`(全谱精修,**带**零点校正)与
> `_lattice_result`(3–7 峰最小二乘,**零点恒 0**)。两者在同一张谱上给出
> 不同的 a/c,混画在同一条趋势线上,用户看到的"结构演变"里有一部分其实是
> **换了算法**。现在 `domain.high_ni.analyze` 并列存 `fullCell` 与 `lattice`,
> 趋势图**只用 `fullCell`**(与晶面标注同源),并在面板底部写明来源。
>
> **未分析的点画成空心,不连线、不插值**:当 0 会画成"晶胞塌了",跳过会让
> 曲线看起来连续,插值是编的。

新增“结构趋势”页面或面板：

- x 轴可选煅烧温度、批次、掺杂量、循环次数。
- y 轴可选 `I003/I104`、`R_order`、`a`、`c`、`c/a`、FWHM。
- 支持按 Sample metadata 分组。
- 支持选择参考样品和显示偏差。
- 支持点击趋势点回到原始图谱和拟合证据。

### 11.5 结果解释边界

- `a`、`c`、`c/a` 主要用于同体系、同仪器、同波长下的趋势比较。
- 不建议直接使用跨仪器、跨波长、跨组成的固定阈值。
- 峰位变化可能来自成分、应力、氧缺陷、样品位移、充放电状态等多个因素。
- 结构指标必须显示测试条件和校正状态。

## 12. 前端界面规划

### 12.1 Inspector 新增区域

```text
材料档案
  Profile
  化学式
  Ni 含量
  Li/TM 比
  波长
  参考样品

高镍结构分析
  [运行分析]
  Li/Ni 混排风险
  R-3m 层状结构
  晶胞参数

证据
  [003/104]
  [006/102]
  [108/110]
  [104]

批次比较
  [加入当前样品]
  [打开趋势图]
```

### 12.2 结果展示原则

- 结论必须和数值、峰拟合、原始图谱联动。
- 不使用只有绿色/红色的单一评分。
- 所有结果显示置信度。
- 所有 warning 可跳转到具体峰区。
- 显示“基于当前 XRD 的筛查结果”，避免过度解释。

## 13. 测试数据与验收集

### 13.1 需要建立的 fixture

至少包含：

1. 理想层状结构合成谱。
2. 降低 `(003)` 强度的混排模拟谱。
3. `(006)/(102)` 分裂清晰和分裂模糊两组谱。
4. `(108)/(110)` 分裂清晰和分裂模糊两组谱。
5. 峰位整体偏移的样品位移模拟谱。
6. 高背景、强噪声和非均匀网格谱。
7. 003、104 峰展宽谱。
8. 含额外弱峰的杂相模拟谱。

### 13.2 验收指标

工程阶段：

- 处理、导出、Envelope 和前端状态机测试全绿。
- 15k 点 preview 性能有自动记录。
- 导出的 CSV 能被再次导入。

高镍阶段：

- 峰标签正确率 100%（在已标注 fixture 上）。
- 合成谱中强度改变能正确改变 `I003/I104` 方向。
- 峰位偏移不会被错误判定为峰缺失。
- 双峰拟合优于单峰拟合时，`fit_improvement` 为正。
- 同一输入重复运行结果一致。
- 结果中始终保存 source hash、处理参数和分析版本。

## 14. 推荐开发任务拆分

### Sprint 1：性能与协议

- Envelope 类型定义。
- Rust 二进制桥接。
- 前端 Float32 解码。
- 降采样算法。
- 端到端基准。

### Sprint 2：处理与导出

- Crop UI。
- Preview harness。
- CSV 数据和峰表导出。
- PNG 导出。
- 导出元数据。

### Sprint 3：质量与基础设施

- Vitest / ESLint。
- 清理死代码。
- Schema version 2。
- Material Profile。
- Domain result 存储。

### Sprint 4：混排与层状结构

- 峰窗识别。
- Pseudo-Voigt 基础拟合。
- `I003/I104` 和 `R_order`。
- `(006)/(102)`、`(108)/(110)` 分裂。
- 高镍 Inspector。

### Sprint 5：晶胞与趋势

- 多峰晶胞拟合。
- `a/c/c_over_a`。
- 参考样品。
- 批次趋势图。
- 结构分析结果导出。

## 15. 后续路线

1. 更完整的 Pseudo-Voigt 多峰拟合。
2. 用户自定义杂相模板。
3. 高镍材料的残余锂化合物筛查。
4. 充放电或循环前后结构变化追踪。
5. Le Bail / Pawley 拟合。
6. 在有标准样品、仪器函数和约束条件后，再评估 Rietveld 和占位率精修。

## 16. 最终原则

1. 原始数据永远不改。
2. 所有结构指标都必须可追溯到具体峰和处理参数。
3. 经验阈值只能作为提示，不能冒充普适物理常数。
4. XRD 筛查结果和材料最终结构结论分开表达。
5. 先把日常判断做得稳定、可复现，再增加复杂精修。
