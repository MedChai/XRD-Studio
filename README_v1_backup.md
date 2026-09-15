# XRD Studio V0.3 — 产品与界面设计规范

> **状态：** Draft v1
> **产品：** XRD Studio
> **版本目标：** V0.3
> **定位：** XRD 数据管理、查看、基础处理与峰分析桌面软件
> **设计关键词：** 简洁 / 苹果风 / 数据优先 / 图谱优先 / 非破坏性分析

---

# 1. 产品定位

XRD Studio 不只是一个“打开 XRD 文件并画图”的工具。

它应该是一个围绕：

```text
Project
   ↓
Sample
   ↓
Dataset
   ↓
Analysis
   ↓
Peaks
```

建立起来的 XRD 数据工作台。

核心目标：

```text
数据不会乱
样品不会找不到
原始数据不会被破坏
图谱查看足够舒服
基础分析足够快速
界面足够漂亮
```

用户进入软件后，应当可以非常自然地完成：

```text
拖入 XRD
    ↓
归属 Sample
    ↓
立即看图
    ↓
平滑 / 扣背景
    ↓
自动寻峰
    ↓
查看 FWHM / Area
    ↓
导出结果
```

---

# 2. 技术路线

## 2.1 不再使用传统 PySide6 QWidget UI

PySide6 并不是不能做漂亮界面。

但如果目标是：

```text
Apple-style UI
现代动画
毛玻璃
高质量弹窗
Command Palette
精细 Hover
漂亮 Sidebar
响应式 Inspector
```

使用 Web UI 技术会更加自然。

因此 V0.3 推荐：

```text
Tauri 2
+
React
+
TypeScript
+
Python Analysis Engine
```

---

# 3. 总体架构

```text
┌─────────────────────────────────────────────────┐
│                 XRD Studio                      │
│                                                 │
│                 Tauri 2                         │
│                                                 │
│  ┌───────────────────────────────────────────┐  │
│  │              React UI                     │  │
│  │                                           │  │
│  │ React + TypeScript                        │  │
│  │ Tailwind CSS                              │  │
│  │ Radix UI                                  │  │
│  │ Lucide                                    │  │
│  │ Apache ECharts                            │  │
│  │ Zustand                                   │  │
│  └─────────────────────┬─────────────────────┘  │
│                        │                        │
│                        ▼                        │
│  ┌───────────────────────────────────────────┐  │
│  │        Python Analysis Engine             │  │
│  │                                           │  │
│  │ NumPy                                     │  │
│  │ SciPy                                     │  │
│  │ Pandas                                    │  │
│  │ XRD Parser                                │  │
│  │ Peak Analysis                             │  │
│  └─────────────────────┬─────────────────────┘  │
│                        │                        │
│                        ▼                        │
│               SQLite + Data Store              │
└─────────────────────────────────────────────────┘
```

---

# 4. 技术栈

## Desktop

```text
Tauri 2
```

负责：

```text
窗口
安装包
系统文件访问
拖拽文件
系统菜单
Python Sidecar
系统通知
```

## Frontend

```text
React
TypeScript
Vite
Tailwind CSS
Radix UI
Lucide Icons
Zustand
```

## Plot

```text
Apache ECharts
```

主要负责：

```text
XRD curve
Zoom
Pan
Crosshair
Peak marker
Multiple curves
Stacked curves
Tooltip
Legend
```

## Analysis Engine

```text
Python

NumPy
SciPy
Pandas
```

## Database

```text
SQLite
```

## Packaging

```text
Tauri Installer
+
PyInstaller Python Sidecar
```

最终用户只安装：

```text
XRD Studio Setup.exe
```

不要求用户安装：

```text
Python
Node.js
SQLite
```

---

# 5. 为什么不是 Electron

Electron 也可以实现整个软件。

但 XRD Studio 优先使用 Tauri。

原因：

```text
Tauri
│
├── 更轻
├── 使用系统 WebView
├── UI 同样可以使用 React
├── 可以访问本地文件
├── 支持外部 sidecar
└── 更符合单机科研工具
```

Electron 可以作为备选路线，但不是首选。

---

# 6. Qt 的备选方案

如果未来一定要回到 Qt，唯一建议考虑的是：

```text
PySide6 + Qt Quick / QML
```

而不是：

```text
PySide6 + QWidget
```

不过当前项目正式设计路线：

> **Tauri + React。**

---

# 7. 设计原则

## 7.1 Plot First

整个软件最大的区域必须是：

```text
XRD Plot
```

而不是：

```text
Toolbar
Parameters
Buttons
Menus
```

---

## 7.2 Library First

XRD 数据必须属于明确的：

```text
Project
Sample
Dataset
```

用户永远应该知道：

> 这张谱是谁的？

---

## 7.3 Original Data First

所有分析必须：

> 非破坏性。

永远不能直接覆盖原始 XRD。

处理流程：

```text
RAW
 │
 ├── Crop
 │
 ├── Smooth
 │
 ├── Background
 │
 ├── Normalize
 │
 └── Peak Detection
```

保存的是：

```text
RAW
+
Processing Parameters
+
Analysis Result
```

---

# 8. UI 风格

目标：

> **Apple-inspired Scientific Workspace**

不是完全复制 macOS。

而是采用苹果式设计原则：

```text
大量留白
低饱和
清晰层级
轻量边框
少量阴影
克制动画
圆角
内容优先
```

参考视觉感觉：

```text
macOS
Apple Settings
Finder
Linear
Arc
Notion Calendar
```

---

# 9. 字体系统

字体：

```css
font-family:
  -apple-system,
  BlinkMacSystemFont,
  "Segoe UI",
  Inter,
  "Helvetica Neue",
  Arial,
  sans-serif;
```

字号：

```text
Large Title      22px / 600
Title            17px / 600
Section          14px / 600
Body             13px / 400
Secondary        12px / 400
Caption          11px / 400
Data             12px / 500
```

数字：

```css
font-variant-numeric: tabular-nums;
```

特别适用于：

```text
2θ
Intensity
FWHM
Area
```

---

# 10. Light Theme

```text
App Background       #F5F5F7
Sidebar               rgba(248,248,250,.86)
Panel                 #FFFFFF
Elevated              rgba(255,255,255,.86)

Primary Text          #1D1D1F
Secondary Text        #6E6E73
Tertiary Text         #9A9AA0

Divider               rgba(0,0,0,.08)
Hover                 rgba(0,0,0,.045)
Selected              rgba(0,122,255,.10)

Accent                #007AFF
Danger                #FF3B30
Success               #34C759
Warning               #FF9F0A
```

---

# 11. Dark Theme

```text
App Background       #171719
Sidebar              rgba(27,27,30,.88)
Panel                #202023
Elevated             #29292D

Primary Text         #F5F5F7
Secondary Text       #A1A1A6
Tertiary Text        #737378

Divider              rgba(255,255,255,.09)
Hover                rgba(255,255,255,.055)
Selected             rgba(10,132,255,.18)

Accent               #0A84FF
Danger               #FF453A
Success              #30D158
Warning              #FF9F0A
```

---

# 12. Radius

```text
Small Control        6px
Input                8px
Button               9px
Panel               12px
Card                14px
Dialog              16px
```

原则：

> 不要所有东西都变成巨大圆角胶囊。

---

# 13. Spacing

采用：

```text
4px Grid
```

主要间距：

```text
4
8
12
16
20
24
32
40
```

常见：

```text
Panel Padding       16px
Section Gap         20px
Control Gap          8px
```

---

# 14. Shadow

普通 Panel：

```css
box-shadow:
  0 1px 2px rgba(0,0,0,.04),
  0 4px 18px rgba(0,0,0,.04);
```

Dialog：

```css
box-shadow:
  0 20px 60px rgba(0,0,0,.18);
```

禁止：

```text
大黑阴影
发光边框
彩色阴影
3D Button
```

---

# 15. 主窗口

默认：

```text
1440 × 900
```

最低：

```text
1280 × 760
```

布局：

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ XRD Studio       LFP Study                            Search     + Import    │
├─────────────────┬────────────────────────────────────┬──────────────────────┤
│                 │                                    │                      │
│ Library         │                                    │ Inspector            │
│                 │                                    │                      │
│ Projects        │                                    │ Processing           │
│ Samples         │              XRD Plot              │ Peaks                │
│ Datasets        │                                    │ Display              │
│                 │                                    │                      │
│                 │                                    │                      │
├─────────────────┴────────────────────────────────────┴──────────────────────┤
│ 2θ 36.521°    Intensity 14,253     LFP-500C     7,001 pts       Saved     │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

# 16. Top Bar

高度：

```text
52px
```

布局：

```text
XRD Studio     [LFP Study ▾]                ⌕       + Import
```

只让：

```text
+ Import
```

成为明显 Primary Button。

其它功能尽量 Icon 化。

---

# 17. Sidebar

默认：

```text
248px
```

允许：

```text
220px → 360px
```

结构：

```text
Library

⌕ Search

RECENT

● LFP-500C
● NCM811-03


PROJECTS

⌄ LFP Study
   ● LFP-400C
   ● LFP-450C
   ● LFP-500C              2
   ● LFP-550C

⌄ NCM Study
   ● NCM811-01
   ● NCM811-02


＋ New Project
```

---

# 18. Sample Item

不要传统 Windows TreeView。

使用：

```text
┌────────────────────────────────┐
│ ● LFP-500C                   2 │
│   500 °C · 2 h                 │
└────────────────────────────────┘
```

Normal：

```text
transparent
```

Hover：

```text
rgba(0,0,0,.04)
```

Selected：

```text
rgba(0,122,255,.10)
```

绝对不要：

```text
整行亮蓝色
白色文字
```

---

# 19. Dataset

展开 Sample：

```text
⌄ ● LFP-500C

      scan-01.xy
      scan-02.xy
```

一个 Sample 可以拥有：

```text
多个 Dataset
```

这是核心数据模型，不能把 Sample 和文件直接等同。

---

# 20. Plot Area

整个 App 最大的区域。

默认：

```text
Background    White
Grid          Off
X Axis        On
Y Axis        On
Legend        Auto
Line Width    1.5
```

不要默认画出：

```text
粗网格
灰色背景
巨大 Legend
```

---

# 21. Plot Header

```text
LFP-500C / scan-01.xy

                       Fit   Crosshair   Stack   ···
```

工具只在需要时出现。

不要做传统：

```text
20 个小图标 Toolbar
```

---

# 22. Plot Interaction

滚轮：

```text
Zoom
```

拖动：

```text
Pan
```

双击：

```text
Fit All
```

Trackpad：

```text
Pinch Zoom
```

---

# 23. Crosshair

开启：

```text
                 │
                 │
───────────────  ●  ───────────────
                 │
                 │
```

Floating Tooltip：

```text
2θ       36.521°
I        14,253
```

要求：

```text
小
半透明
圆角
不遮大面积数据
```

---

# 24. 多曲线

顶部：

```text
╭─────────┬─────────╮
│ Overlay │ Stacked │
╰─────────┴─────────╯
```

使用 Segmented Control。

---

# 25. Overlay

```text
Intensity
│
│          /\               Sample A
│    /\   /  \
│___/  \_/____\________
│
│         /\                Sample B
│___/\___/  \_______________
└─────────────────────────── 2θ
```

用于：

```text
峰位比较
```

---

# 26. Stacked

```text
Sample C          /\       /\
─────────────────/──\─────/──\──

Sample B      /\       /\
─────────────/──\─────/──\─────

Sample A   /\        /\
──────────/──\──────/──\────────
```

提供：

```text
Offset Slider
```

---

# 27. Inspector

宽：

```text
296px
```

允许折叠。

折叠后 Plot 自动扩展。

结构：

```text
PROCESSING

Smoothing
Background


PEAKS

Detection
Labels


DISPLAY

Normalization
Offset
Line


SAMPLE

Metadata
```

---

# 28. Normalization

```text
Normalization

○ Raw
○ Max = 1
● Max = 100
```

即时响应。

永远不修改 Raw。

---

# 29. Smoothing

V0.3：

```text
Savitzky–Golay
```

UI：

```text
Smoothing                    ●

Method
Savitzky–Golay

Window
────────●──────── 7

Polynomial
[ 2 ▾ ]
```

参数变化：

```text
立即 Preview
```

用户停止调整：

```text
Autosave
```

---

# 30. Background

V0.3 支持：

```text
AsLS
Polynomial
```

UI：

```text
Background                   ●

Method
[ AsLS               ▾ ]

Smoothness
[ 100000                ]

Asymmetry
[ 0.010                 ]

☑ Show Background
```

Background curve：

```text
细虚线
低透明度
```

---

# 31. Peak Detection

默认界面：

```text
Peak Detection

Sensitivity

Low ───────●──── High

Minimum Height

[ 5 ] %

Minimum Distance

[ 0.10 ] °


☑ Show Labels


[ Detect Peaks ]
```

---

# 32. Advanced Peak Settings

折叠：

```text
⌄ Advanced
```

内部：

```text
Prominence
Width
Relative Height
```

普通用户不需要一打开就面对 SciPy 参数。

---

# 33. Peak Labels

Normal：

```text
        36.52°
          │
         /\
        /  \
```

Selected：

```text
36.521°
Intensity 31,542
FWHM 0.183°
```

原则：

> 普通状态只显示峰位置。

否则几十个峰会形成文字墙。

---

# 34. Peak Table

默认收起在底部：

```text
──────────────────────────────────────
Peaks   12                         ⌃
```

展开：

```text
┌────┬─────────┬─────────────┬────────┬──────────┐
│ #  │ 2θ      │ Intensity   │ FWHM   │ Area     │
├────┼─────────┼─────────────┼────────┼──────────┤
│ 1  │ 18.423  │ 5,234       │ 0.210  │ 1,142    │
│ 2  │ 25.731  │ 18,233      │ 0.164  │ 2,784    │
│ 3  │ 36.521  │ 31,542      │ 0.183  │ 4,233    │
└────┴─────────┴─────────────┴────────┴──────────┘
```

---

# 35. Plot / Table 联动

点击：

```text
Peak Table Row
```

发生：

```text
Plot 自动移动到对应区域
Peak Highlight
Inspector 显示 Peak Detail
```

点击图中的峰：

```text
Peak Table 自动 Scroll
对应 Row Selected
```

---

# 36. Peak Detail

Inspector：

```text
Peak

Position
36.521°

Intensity
31,542

FWHM
0.183°

Area
4,233

Bounds
35.91° — 37.03°

[ Edit Bounds ]
```

---

# 37. FWHM

V0.3：

```text
Half Maximum Intersection
```

计算：

```text
FWHM = Xright - Xleft
```

V0.3 不处理复杂重叠峰解卷积。

---

# 38. Peak Area

```text
Area = ∫ I(x) dx
```

默认峰边界：

```text
local minima
```

用户可以编辑：

```text
Left     35.91°
Right    37.03°
```

选中区域在 Plot 中以极淡颜色显示。

---

# 39. Import

用户可以：

```text
Drag & Drop
```

或者：

```text
+ Import
```

---

# 40. Import Sheet

```text
Import XRD Data

3 files


Project

[ LFP Study                  ▾ ]


Sample

[ + Create New Sample          ]


Sample Name

[ LFP-500C                     ]


                    Cancel   Import
```

不要做巨大的导入 Wizard。

---

# 41. V0.3 文件支持

```text
CSV
TXT
XY
DAT
```

自动识别：

```text
comma
tab
space
semicolon
```

---

# 42. Column Mapping

解析失败时：

```text
Data Preview

Column 1            Column 2

10.000              235
10.020              241
10.040              239


X Axis

[ Column 1 ▾ ]


Y Axis

[ Column 2 ▾ ]


                         Import
```

---

# 43. Drag State

用户拖文件到软件：

```text
┌────────────────────────────────────┐
│                                    │
│           Drop XRD Data            │
│                                    │
│      CSV · TXT · XY · DAT          │
│                                    │
└────────────────────────────────────┘
```

仅增加淡蓝 Overlay。

不使用复杂动画。

---

# 44. Command Palette

快捷键：

```text
Ctrl + K
```

macOS：

```text
⌘ + K
```

界面：

```text
┌──────────────────────────────────────┐
│ Search commands or samples...        │
├──────────────────────────────────────┤
│ Import XRD Data                      │
│ Open LFP-500C                        │
│ Detect Peaks                         │
│ Toggle Dark Mode                    │
│ Export Current Plot                 │
└──────────────────────────────────────┘
```

---

# 45. Sample Metadata

字段：

```text
Sample Name
Sample ID
Batch
Project
Material / Formula
Preparation Date
Temperature
Duration
Tags
Notes
```

V0.3：

```text
只有 Sample Name 必填
```

---

# 46. 数据结构

```text
Workspace

└── Project

    ├── Sample

    │   ├── Dataset

    │   │   └── Analysis

    │   │       └── Peaks

    │   └── Dataset

    └── Sample
```

---

# 47. SQLite

数据库：

```text
xrdstudio.db
```

---

# 48. projects

```sql
id
name
description

created_at
updated_at
deleted_at
```

---

# 49. samples

```sql
id
project_id

name
sample_code
batch
material

temperature
duration
preparation_date

notes

created_at
updated_at
deleted_at
```

---

# 50. datasets

```sql
id
sample_id

name

original_filename
source_path
stored_path

file_hash

imported_at

x_min
x_max
point_count

deleted_at
```

---

# 51. analyses

```sql
id
dataset_id

smooth_enabled
smooth_method
smooth_parameters_json

background_enabled
background_method
background_parameters_json

normalization_mode

offset

peak_detection_parameters_json

created_at
updated_at
```

---

# 52. peaks

```sql
id
analysis_id

position
intensity

fwhm
area

left_bound
right_bound

manual_override

created_at
updated_at
```

---

# 53. Data Store

```text
XRD Studio/
│
├── database/
│   └── xrdstudio.db
│
├── data/
│   ├── 01JABC.../
│   │   ├── original.xy
│   │   └── metadata.json
│   │
│   └── 01JDEF.../
│       └── original.csv
│
├── cache/
│
├── exports/
│
└── logs/
```

---

# 54. Import Data Safety

导入：

```text
Original File
      │
      ├── Save source path
      │
      └── Copy
             ↓
      XRD Studio Data Store
```

软件分析：

```text
软件自己的 copy
```

而不是依赖外部文件。

所以即使用户：

```text
移动
重命名
删除
```

原来的外部文件，XRD Studio 中的 Dataset 仍然存在。

---

# 55. SHA-256

导入时保存：

```text
file_hash
```

用途：

```text
Duplicate Detection
Data Traceability
```

重复时：

```text
This dataset already exists.

LFP Study
LFP-500C
scan-01.xy

[ Open Existing ]

[ Import Anyway ]
```

---

# 56. Analysis Pipeline

Python：

```text
Load
 │
 ▼
Validate
 │
 ▼
Crop
 │
 ▼
Smooth
 │
 ▼
Background Estimate
 │
 ▼
Background Subtraction
 │
 ▼
Normalize
 │
 ▼
Peak Detection
 │
 ▼
Peak Metrics
```

---

# 57. Savitzky–Golay

使用：

```text
scipy.signal.savgol_filter
```

主要参数：

```text
window_length
polyorder
```

UI 必须提前保证：

```text
window_length = odd
polyorder < window_length
```

---

# 58. Background

Method A：

```text
Asymmetric Least Squares
```

参数：

```text
lambda
p
iterations
```

Method B：

```text
Polynomial
```

参数：

```text
order
```

---

# 59. Peak Detection Engine

主要：

```text
scipy.signal.find_peaks
```

参数：

```text
height
distance
prominence
width
```

Frontend 不直接把这些参数全部暴露。

而是映射成：

```text
Sensitivity
Minimum Height
Minimum Distance
```

---

# 60. Frontend ↔ Python

开发阶段可使用：

```text
Local FastAPI
```

例如：

```text
POST  /api/v1/datasets/import

GET   /api/v1/datasets/{id}/curve

POST  /api/v1/analyses/{id}/process

POST  /api/v1/analyses/{id}/peaks/detect

PATCH /api/v1/peaks/{id}
```

生产环境：

```text
Tauri
  ↓
launch
  ↓
Python Sidecar
```

服务只绑定：

```text
127.0.0.1
```

不暴露局域网。

---

# 61. Curve Data

V0.3：

```json
{
  "x": [10.0, 10.02, 10.04],
  "y": [235, 241, 239]
}
```

普通 XRD 数据直接 JSON 即可。

未来如果出现：

```text
500,000+
1,000,000+
```

数据点，再考虑：

```text
Binary
Arrow
Shared Memory
```

V0.3 不提前优化不存在的问题。

---

# 62. React Structure

```text
App

├── AppShell

│   ├── TopBar

│   ├── LibrarySidebar
│   │
│   ├── Workspace
│   │   ├── PlotHeader
│   │   ├── XRDChart
│   │   └── PeakDrawer
│   │
│   ├── Inspector
│   │
│   └── StatusBar

├── ImportSheet

├── CommandPalette

├── SettingsDialog

└── ToastProvider
```

---

# 63. Frontend Features

```text
features/

├── library/
├── import/
├── plot/
├── processing/
├── peaks/
├── export/
└── settings/
```

---

# 64. Zustand Stores

```text
appStore

projectStore

libraryStore

plotStore

analysisStore

uiStore
```

避免：

```text
one giant global store
```

---

# 65. Project Structure

```text
xrd-studio/
│
├── app/
│   │
│   ├── src/
│   │   ├── components/
│   │   ├── features/
│   │   ├── stores/
│   │   ├── services/
│   │   ├── styles/
│   │   └── types/
│   │
│   └── src-tauri/
│
├── engine/
│   │
│   ├── xrd_engine/
│   │   ├── api/
│   │   ├── database/
│   │   ├── importers/
│   │   ├── processing/
│   │   ├── peaks/
│   │   ├── models/
│   │   └── services/
│   │
│   └── tests/
│
└── docs/
```

---

# 66. Animation

普通：

```text
120–220 ms
```

推荐：

```css
transition:
  background-color 140ms ease,
  color 140ms ease,
  opacity 140ms ease,
  transform 180ms cubic-bezier(.2,.8,.2,1);
```

禁止：

```text
bounce
large scale
rotation
>300ms normal animations
```

---

# 67. Empty State

首次进入：

```text
                    XRD Studio


              Your XRD workspace


      Import a diffraction file to get started.


             [ Import XRD Data ]


         CSV · TXT · XY · DAT
```

不需要巨大插画。

---

# 68. Toast

成功：

```text
✓ Imported 3 datasets
```

失败：

```text
Couldn’t read this file

View details
```

不要直接显示：

```text
Python traceback
```

---

# 69. Settings

```text
General

Appearance

Data

Plot

About
```

Appearance：

```text
Theme

[ System ] [ Light ] [ Dark ]


Accent

● Blue

○ Graphite
```

---

# 70. Export Figure

```text
Export Figure


Format

[ PNG ▾ ]


Size

[ 2400 × 1600 ]


Background

● White

○ Transparent


Scale

[ 2× ]


                 Cancel     Export
```

支持：

```text
PNG
SVG
PDF
```

---

# 71. Export Data

类型：

```text
Raw Data

Processed Data

Peak Table
```

格式：

```text
CSV
TXT
```

Peak Table：

```text
peak_no
two_theta
intensity
fwhm
area
left_bound
right_bound
```

---

# 72. Autosave

自动保存：

```text
Sample Metadata
Processing Parameters
Peak Bounds
Normalization
Offset
Display State
```

原则：

> 尽量不出现 Save / Apply。

不可逆操作除外。

---

# 73. Undo / Redo

支持：

```text
Parameter Change
Peak Bound Change
Rename
Plot Display Change
```

快捷键：

```text
Ctrl/Cmd + Z

Ctrl/Cmd + Shift + Z
```

---

# 74. Delete

删除：

```text
Delete “LFP-500C”?

This sample contains 2 datasets.


Cancel

Move to Trash
```

数据库使用：

```text
deleted_at
```

V0.3 默认软删除。

---

# 75. V0.3 功能范围

## Library

```text
Project
Sample
Dataset
Search
Recent
Rename
Metadata
Trash
Autosave
```

## Import

```text
CSV
TXT
XY
DAT

Drag & Drop
Separator Detection
Column Mapping
Duplicate Detection
```

## Plot

```text
Zoom
Pan
Crosshair
Fit

Overlay
Stacked

Normalize
Y Offset

Multiple Datasets

Legend
Line Appearance
```

## Processing

```text
Savitzky–Golay

AsLS Background

Polynomial Background

Background Subtraction

Live Preview
```

## Peaks

```text
Automatic Peak Detection

Peak Position

Peak Intensity

Peak Labels

FWHM

Peak Area

Automatic Bounds

Manual Bounds

Peak Table

Plot ↔ Table Linking
```

## Export

```text
Raw CSV
Raw TXT

Processed CSV
Processed TXT

Peak CSV

PNG
SVG
PDF
```

---

# 76. V0.3 不做

明确排除：

```text
Rietveld Refinement

ICDD PDF Database

Automatic Phase Identification

Complex CIF Management

Cell Refinement

Multi-Peak Deconvolution

Instrument Broadening Calibration

Kα2 Stripping

Automatic Crystallite Size Workflow

Automatic Crystallinity Analysis

Cloud Sync

Account System

Multi-user Collaboration
```

这些以后再做。

---

# 77. V0.3 Success Flow

用户应该能够做到：

```text
Drag XY
  ↓
Create Sample
  ↓
See Plot
  ↓
Enable Background
  ↓
Detect Peaks
  ↓
See Peak Table
```

这个流程如果需要看教程：

> UI 设计就还没完成。

---

# 78. 首页最终草图

```text
╭──────────────────────────────────────────────────────────────────────────────╮
│ ◉ XRD Studio       LFP Study                             ⌕      ＋ Import    │
├───────────────────┬───────────────────────────────────┬──────────────────────┤
│                   │                                   │                      │
│ Library           │ LFP-500C                          │ Inspector            │
│                   │                                   │                      │
│ ⌕ Search          │               36.52°              │ PROCESSING           │
│                   │                 │                 │                      │
│ RECENT            │                /\                 │ Smoothing       ●    │
│ ● LFP-500C        │       /\      /  \       /\       │ Window          7    │
│ ● NCM811-03       │ _____/  \____/    \_____/  \___  │                      │
│                   │                                   │ Background      ●    │
│ PROJECTS          │                                   │ AsLS                 │
│                   │                                   │                      │
│ ⌄ LFP Study       │                                   │ PEAKS                │
│   ● LFP-400C      │                                   │                      │
│   ● LFP-450C      │                                   │ Sensitivity          │
│   ● LFP-500C      │                                   │ ━━━━━━━●━━━━         │
│      scan-01.xy   │                                   │                      │
│      repeat.xy    │                                   │ [ Detect Peaks ]     │
│   ● LFP-550C      │                                   │                      │
│                   │                                   │ DISPLAY              │
│ ⌄ NCM Study       │                                   │ Max = 100            │
│   ● NCM811-01     │                                   │ Offset 0             │
│                   │                                   │                      │
├───────────────────┴───────────────────────────────────┴──────────────────────┤
│ Peaks 12 ⌃      2θ 36.521°     I 14,253      7,001 points        Saved     │
╰──────────────────────────────────────────────────────────────────────────────╯
```

---

# 79. 开发路线

采用：

> **Vertical Slice**

不要：

```text
先写完所有数据库
↓
再写所有 Python
↓
最后才做 UI
```

---

# 80. Milestone 1 — App Shell

先完成：

```text
Tauri

React

Apple-style Theme

Top Bar

Sidebar

Fake XRD Plot

Inspector

Peak Drawer
```

重点：

> 此阶段即使没有真实算法，也必须已经像成品。

---

# 81. Milestone 2 — Import & Plot

完成：

```text
Drag Drop

XRD Parser

Python Engine

SQLite

Sample Management

Real XRD Plot

Sample Switching
```

做到这里：

> 已经是能正常使用的 XRD Viewer。

---

# 82. Milestone 3 — Processing

增加：

```text
Smoothing

Background

Normalization

Y Offset

Overlay

Stacked
```

---

# 83. Milestone 4 — Peaks

增加：

```text
Peak Detection

Peak Labels

FWHM

Area

Peak Table

Manual Bounds
```

---

# 84. Milestone 5 — Polish

专门花一个阶段处理：

```text
Hover

Animations

Loading

Error States

Empty States

Keyboard

Performance

Export

Installer
```

UI Polish 不是开发最后顺手改一点。

它是正式开发阶段。

---

# 85. 开发顺序

```text
01 App Shell

02 Fake XRD Curve

03 Sidebar Sample Switching

04 Real File Import

05 SQLite Persistence

06 Processing Pipeline

07 Peak Detection

08 Peak Table

09 Export

10 UI Polish

11 Installer
```

---

# 86. 性能目标

目标：

```text
启动主界面            < 2s

切换普通 Sample       感知 < 150ms

普通图谱绘制          感知 < 300ms

参数 UI 响应          < 100ms

Python Processing     不阻塞 UI
```

架构：

```text
React UI
   │
   ├── UI Update
   │
   └── Python Worker
          │
          ▼
        Result
```

---

# 87. 产品未来方向

## V0.4

```text
Single Peak Fitting

Gaussian

Lorentzian

Pseudo-Voigt

Manual Add Peak

Manual Remove Peak

Batch Analysis

Analysis Preset

Figure Template
```

---

## V0.5

```text
CIF Import

Calculated XRD Pattern

Experimental vs Calculated

Crystallite Size

Instrument Broadening Presets
```

---

## V0.6+

```text
Phase Candidate Search

COD Integration

Batch Trend Analysis

Experiment Comparison

Report Generation
```

---

## V1.0

数据模型稳定后再考虑：

```text
Phase Identification

Structure Refinement

Plugin System

External Databases

Automated Workflows
```

---

# 88. 最终产品原则

XRD Studio V0.3 不需要马上成为：

```text
Jade
HighScore
TOPAS
GSAS-II
```

第一阶段最重要的是成为：

> **一个真正好用、好看、数据不会乱的 XRD 工作台。**

核心竞争力：

```text
样品管理清楚

XRD 数据清楚

界面清楚

图谱漂亮

交互快速

分析直观

原始数据安全

结果容易导出
```

技术组合最终确定为：

```text
Tauri 2

React

TypeScript

Tailwind CSS

Radix UI

Lucide

Apache ECharts

Zustand

Python

NumPy

SciPy

Pandas

SQLite
```

设计方向：

> **Apple-inspired minimal UI + Scientific Workstation**

核心原则：

> **Library first. Plot first. Original data first. Simplicity first.**
