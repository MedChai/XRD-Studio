# 电化学模块(LAND dQ/dV 衰减归因)— 开发文档

- **日期**:2026-10-05 ~ 10-06
- **计划书**:[PLAN_ECHEM.md](./PLAN_ECHEM.md)(方法论与验收标准的完整版本)
- **范围**:M0–M3(engine 侧)+ M4(前端)+ M5(打包)。Li 半电池为主,
  全电池 LLI/LAM 接口预留。

## 1. 模块结构

```
xrd_engine/electrochem/
├── land_parser.py   LAND xlsx 解析 → 记录数组 + 圈统计(M0)
├── dqdv.py          恒流段 dQ/dV(M1)
├── peaks.py         相变窗峰检测 + 跨圈轨迹(M2)
└── attribution.py   衰减三分量归因(M3)
```

仅依赖 numpy(+scipy.signal,缺失降级);openpyxl 为新增打包依赖。

## 2. 数据流

```
LAND xlsx ──ec.import──▶ data/<ULID>/original.xlsx + records.npz(免二次解析)
                        ec_cells / ec_cycles 落库
records.npz ──ec.dqdv_compute(显式触发)──▶ dqdv_<hash>.npz(曲线)
                                            + 峰/轨迹存 ec_analyses.result_json
峰结果 ──ec.attribution_run(显式触发)──▶ 归因分量存 ec_analyses
```

"算一次存结果、参数变更只亮过期徽标"与 XRD 标注哲学一致:
`ec.dqdv_state.paramsHash` 供前端判过期;归因行记录 `sourceDqdvHash`,
`ec.attribution_get.stale` 据此标记。

## 3. 命令清单(§10.4 的增补)

| 命令 | 说明 |
|---|---|
| `ec.preview` | dry-run 解析(不落库),导入向导用 |
| `ec.import` | 导入(归属三选一,同 datasets.import);查重按 file_hash |
| `ec.cells` | 样品下 cell 列表(侧栏) |
| `ec.cell_overview` | 元信息 + 全部圈统计(趋势图数据源) |
| `ec.dqdv_compute` | 全 cell dQ/dV + 峰 + 轨迹(显式触发,约 1–2s) |
| `ec.dqdv_state` / `ec.dqdv_result` | 状态 / 读回峰轨迹+参数快照(重启免重算) |
| `ec.dqdv_curve` / `ec.dqdv_waterfall` | 单圈曲线 / 瀑布矩阵(二进制信封) |
| `ec.attribution_run` / `ec.attribution_get` | 归因计算/查询(含 stale) |
| `ec.export` | CSV:圈统计 / 峰 / 归因 |

曲线数组走 §9.1 二进制信封(layout `["v","dqdv"]`;瀑布 matrix 单字段,
v 轴走 JSON —— 信封要求等长数组,矩阵与轴长度必然不同)。

## 4. 数据库(v7)

`ec_cells`(挂 sample,与 datasets 平级,软删除)、`ec_cycles`
(UNIQUE(cell_id, cycle_index))、`ec_analyses`(kind=dqdv/attribution,
is_active 单行活跃)。`ALLOWED_TABLES`/`_TABLE_DEPTH`/`trash_rows`/
`purge_*` 均已覆盖 ec_cells(回收站可见、恢复、项目级清理连带 npz)。

## 5. 真实数据暴露的设计修正(为什么代码长这样)

样例:170214_005_7(386k 记录,184 圈,0.1C×3 后 0.2C,2.7–4.3V,
活性物质 1.767mg,解析 21s/导入一次)。

1. **倍率分组不能按固定阈值**:蓝电"倍率充电 0.2*C"按容量自动重标定
   电流 → 按圈中位电流对主倍率取比值聚类(land_parser._rate_groups)。
2. **时间列混用** datetime.time 与 "D-HH:MM:SS"(超 24h)→ parse_time_value
   统一折算累计秒;时间回退(重启)计入 QA 且积分不计。
3. **dQ/dV 电压抖动**:恒流段电压小幅回跳,按记录插值会重复计数 →
   只取创新高点(running max/min)构造单调 V-Q 序列。
4. **放电方向符号**:V 反转升序后 Q 递减,dQ/dV 取负才是"每伏释放容量"。
5. **宽峰噪声估计**:窗口原始 MAD 把峰体当噪声;"曲线−大窗平滑"残差在
   陡峭上升沿又把曲率当噪声 → 二阶差分 MAD/√6(peaks._residual_noise)。
6. **截断峰语义**:窗口是人为区间,**数据边界**(充电截止 4.3V)切峰才是
   截断 → 半高交叉在数据内缺失 = truncated,不进轨迹、不统计面积;
   检测在窗口外扩 60mV 上做(贴窗峰的真实突出度仍要在完整数据上看)。
7. **轨迹容差自适应峰宽**:固定 30mV 会把宽峰顶点游移切碎
   (真实数据放电主轨迹曾碎成 3 段)→ tol = max(30mV, 0.4×fwhm)。
8. **归因不依赖轨迹连续性**:方法 B 用"放电 H1H2 每圈最强非截断峰"
   序列 + 缺圈线性插值(build_tracks 碎了归因不断)。
9. **方法 A 极化方向**:极化 = 回访容量 − 主倍率容量(低倍率容量更高),
   符号写反会全塞进活性分量 —— 测试用例钉死。
10. **不完整末圈**:末圈放电 < 60%×前圈 → incomplete,归因剔除
    (真实样例第 184 圈即测试中途停止)。

## 6. 归因方法摘要(详见 PLAN_ECHEM.md §2.3)

- **方法 A**(有 ≥2 个不同时间点的低倍率回访圈,置信度 high):
  极化 = 回访插值 − 主倍率;活性 = 回访自身衰减;截断 = 残差。
- **方法 B**(无回访圈,置信度 low,notes 建议补测):
  活性 = 放电 H1H2 主峰面积萎缩率;极化 = 峰位下移 × 峰高;截断 = 残差。
- 三分量按构造闭合(和=总衰减,超出按比例压回)。
- 截断证据:充电 H2→H3 truncated 峰统计(峰位漂移 + 数量)。
- 半电池局限(写进结论):锂片界面阻抗无法从"极化"中剥离。

## 7. 验证

- 引擎测试 **437 passed**(新增 42:parser 9 / dqdv 7 / peaks 8 /
  attribution 8 / commands 10)+ ruff 零告警。
- 真实样例整链:导入 → 366 曲线/467 峰/1.2s → 归因(方法 B,极化 81%
  + H2→H3 漂移 145mV 截断证据,与 9 年老化电池的预期一致)。
- 积分容量 vs 蓝电循环表逐圈偏差:中位 0.006%,最大 0.2%(QA 阈值 2%)。
- 合成夹具(engine/tests/ec_fixtures.py)带真值断言:峰位 <5mV、
  分量恢复 ±5%、方法 A/B/闭合性/不完整圈剔除均有独立用例。

## 8. 打包注意

- 依赖新增 openpyxl ≥3.1(pyproject dependencies);xlrd(.xls 旧格式)
  暂未启用 —— 真正需要时再加。
- PyInstaller 对函数内 `import openpyxl` 可静态检出;打包后需跑
  `ec.import` 冒烟(§11.1 的流程)确认 onedir 里带上了。

## 9. 后续(未做)

- csv/txt 导出格式解析(蓝电其它软件版本);
- GITT/EIS/恒压段阻抗演化(挂同一 ec 命名空间);
- 全电池 Dahn LLI/LAM(`ec_analyses.kind` 已预留);
- 电化学-结构联合趋势页(趋势图数据源接 ec_cycles)。
