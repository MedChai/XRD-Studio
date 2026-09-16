/** 界面文案(中文)。
 *
 * 为什么用集中常量而不是 i18n 库:
 * 本软件当前只有中文一种语言,引入 i18next/react-intl 会带来 Provider、
 * 语言包加载与持久化三层开销,收益为零。集中常量的两个实际好处是
 * ①文案有单一来源,统一措辞只需改这里;②`as const` 让 key 拼写错误在
 * `tsc` 阶段直接报错 —— 这正是放弃 i18n 库后最需要的补偿。
 *
 * 约定:
 *  - 带参数的文案写成函数,不要用字符串拼接(中文语序与英文不同,
 *    拼接后无法调整)。
 *  - 科学术语与品牌缩写保留英文,理由见下。
 */

/** 刻意保留英文的文案 —— 译成中文反而降低可读性。
 *
 *  - `2θ` / `FWHM` / `Area` / `Intensity` / `Prominence`:XRD 领域的通用符号
 *    与量名,中文文献里也直接写 FWHM。
 *  - `AsLS` / `Poly` / `Savgol`:算法名。
 *  - `XRD Studio`:产品名。
 *  - 样品名占位符(`LFP-500C`)与文件格式(`CSV · TXT · XY · DAT`):数据,不是文案。
 */
export const t = {
  app: {
    name: "XRD Studio",
  },

  topbar: {
    search: "搜索 (Ctrl+K)",
    settings: "设置",
    import: "导入",
  },

  project: {
    /** 顶栏项目切换器:未选中任何项目 */
    none: "未选择项目",
    empty: "还没有项目",
    newProject: "新建项目",
    newProjectEllipsis: "新建项目…",
    dialogTitle: "新建项目",
    nameLabel: "名称",
    namePlaceholder: "LFP Study",
    descriptionLabel: "描述(可选)",
    descriptionPlaceholder: "选填",
    cancel: "取消",
    create: "创建",
    creating: "创建中…",
    created: (name: string) => `已创建项目 ${name}`,
  },

  library: {
    title: "数据库",
    search: "搜索",
    empty: "还没有样品。点击「导入」开始。",
    newProject: "新建项目",
    importNewSample: "导入 / 新建样品",
  },

  status: {
    saved: "已保存",
    saving: "保存中…",
    computing: "计算中…",
    points: (n: string) => `${n} 点`,
  },

  plot: {
    current: "当前谱图",
    placeholder: "XRD 图谱",
    /** 曲线显示模式。中文比英文窄,不存在 Segmented 宽度问题 */
    overlay: "叠加",
    stacked: "堆叠",
    crosshair: "十字光标",
    toggleMode: "叠加模式切换",
  },

  inspector: {
    processing: "处理",
    background: "背景扣除",
    method: "方法",
    smoothness: "平滑度",
    asymmetry: "不对称度",
    order: "阶数",
    smoothing: "平滑",
    window: "窗口长度",
    peaks: "寻峰",
    sensitivity: "灵敏度",
    sensitivityLow: "低",
    sensitivityMed: "中",
    sensitivityHigh: "高",
    minHeight: "最小峰高",
    minDistance: "最小峰距",
    advanced: "高级",
    prominence: "显著度",
    width: "峰宽",
    labels: "峰标签",
    detectPeaks: "检测峰",
    detectUpdated: "峰检测已更新",
    detected: (n: number) => `已检测到 ${n} 个峰并保存`,
    display: "显示",
    normalization: "归一化",
    normalizationValue: "最大值 = 100",
    offset: "纵向偏移",
    showBackground: "显示背景",
    needBackgroundFirst: "请先在上方启用背景扣除",
    smoothingWarning: "⚠ 当前 FWHM / Area 在平滑后曲线上计算",
    statusSaved: "已保存",
    statusPreviewing: "计算中…",
    statusCommitting: "保存中…",
    statusNoDataset: "选择左侧样品开始",
  },

  peaks: {
    title: "峰列表",
    colIndex: "#",
    colPosition: "2θ",
    colIntensity: "强度",
    colFwhm: "FWHM",
    colArea: "面积",
    empty: "未检出峰",
  },

  import: {
    title: "导入 XRD 数据",
    mappingTitle: "选择数据列",
    pickFile: "点击选择文件",
    formats: "CSV · TXT · XY · DAT",
    fileCount: (n: number) => `${n} 个文件`,
    projectLabel: "项目",
    newSampleLabel: "新样品名",
    newSamplePlaceholder: "LFP-500C",
    colHeader: (n: number) => `第 ${n} 列`,
    xAxis: "X 轴列",
    yAxis: "Y 轴列",
    cancel: "取消",
    import: "导入",
    importing: "导入中…",
    needProject: "请先创建项目",
    imported: (n: number) => `导入 ${n} 个数据集`,
    failed: (n: number) => `${n} 个文件无法读取`,
    done: "完成",
    duplicate: (project: string, sample: string, dataset: string) =>
      `重复数据:${project} / ${sample} / ${dataset}`,
  },

  command: {
    importData: "导入 XRD 数据",
    newProject: "新建项目",
    detectPeaks: "检测峰",
    exportPeaks: "导出峰表 (CSV)",
    exportPending: "导出功能在 Milestone 5 落地",
    switchToLight: "切换浅色模式",
    switchToDark: "切换深色模式",
    groupCommands: "命令",
    groupSamples: "样品",
    placeholder: "搜索命令或样品…",
    noMatch: "无匹配结果",
    title: "命令面板",
  },

  settings: {
    tabGeneral: "通用",
    tabAppearance: "外观",
    tabData: "数据",
    tabPlot: "绘图",
    tabAbout: "关于",
    appearance: "外观",
    theme: "主题",
    themeSystem: "跟随系统",
    themeLight: "浅色",
    themeDark: "深色",
    accent: "强调色 · 蓝(Graphite 暂未开放)",
    version: "V0.3 · 开发中",
    techStack: "Tauri 2 · React · ECharts · Python Engine",
    nonDestructive: "数据非破坏性管理,原始谱永不被修改。",
    pending: (tab: string) => `${tab}设置将在 Milestone 5–6 落地`,
  },

  empty: {
    hint: "拖入衍射数据文件开始分析",
    importButton: "导入 XRD 数据",
    dropHere: "拖入 XRD 数据",
  },
} as const;
