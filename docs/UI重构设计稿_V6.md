# 串口收发工具 V6 — UI 层重构设计稿（方案 B）

> 状态：已评审并实施完成 · 2026-10-20（实施结果见第 10.5 节）
> 前置阻塞（已解除）：git 与主脚本的读取限制已放行，现可本地执行 `-SelfTest` / `-WaveTest` / `-ShotPath`。
> 原则：逻辑零改动、可截图回归、每一步可回滚。

---

## 1. 现状诊断

| 问题 | 证据 | 影响 |
|---|---|---|
| 深色主题不彻底：3 个 Label 白底 `#FFFFFF` | `logs/main_dark4.themeaudit.txt` 第 10–12 行 | 深色模式下白色色块刺眼，观感断裂 |
| 系统控件深色化失败 | themeaudit：`SetWindowTheme S_OK on 0` | ComboBox 下拉、滚动条仍是亮色 |
| 顶部两行挤 12+ 控件，无网格对齐 | `main_dark4.png` | 视觉噪音大、找控件靠记忆 |
| RX:TX ≈ 3:1，TX 过窄 | 同上 | 发送记录几乎不可用 |
| 启动横幅在 RX/TX 双区重复刷 | 同上 | 信息冗余 |
| 340KB 单体 `serial_terminal_v5.ps1` | 文件系统 | 改 UI 必动全文件，回归风险高 |
| `_probe_*.ps1` 探针文件散落根目录 | 目录清单 | 污染仓库 |

## 2. 目标与非目标

**目标**
1. 主窗口三段式布局重构 + 主题 bug 清零（themeaudit 全绿）。
2. **全量模块化**（已获用户授权 2026-10-20）：主脚本从 340KB 单体拆为「入口装配 + 分层模块」，逻辑零行为变化。
3. 建立 git 分支化工作流，每个阶段一个 commit，截图回归通过才合并。

**非目标（本期不做）**
- 不迁移 WPF；不改变任何功能的对外行为（协议解析结果、JSONL 格式、控制通道命令表、退出码全部冻结）；
- 不动波形窗口的自绘渲染内核算法（仅外观参数与工具条布局调整）；
- 不改启动器（vbs/cmd）与用户既有习惯（快捷键、默认参数）。

## 3. 新架构（全量模块化）

```
serial_terminal_v5.ps1        ← 仅入口：param 定义、模块加载、模式分派（GUI/Headless/SelfTest）
core/
  dsp.ps1                     ← WaveDsp C# 类（滤波/FFT，Add-Type 定义原样平移）
  protocol.ps1                ← FireWater/JustFloat/RawData 字节级解析器
  serial_io.ps1               ← 串口打开/读写/DTR-RTS 脉冲/接收循环
  headless.ps1                ← 无头捕获、触发、期望测试、录制回放、烧录/复位编排
  control_channel.ps1         ← TCP 9101 JSON 行协议服务
  fault_decode.ps1            ← STM32 HardFault 寄存器解码
ui/
  theme.ps1                   ← ThemeManager：调色板、Apply-Theme、SetWindowTheme、themeaudit 断言
  controls.ps1                ← 控件工厂：New-FlatButton / New-IconButton / New-LabeledCombo
  main_form.ps1               ← 主窗体：连接条/日志区/发送条/状态条，返回控件哈希表
  wave_form.ps1               ← 波形窗口（自绘内核平移，算法零改动）
plugins/                      ← 不变（现有插件机制）
tools/probe/                  ← _probe_*.ps1 归位
```

**依赖方向**（单向，禁止环）：
```
入口 → ui/* → core/*（ui 可调用 core 的解析/DSP）
入口 → core/*（headless 模式不加载 ui/*）
ui/main_form、ui/wave_form → ui/theme、ui/controls
core/* 之间禁止互相依赖（共享状态走 $script: 变量，由入口初始化）
```
**加载方式**：入口头部按序 dot-source：`core/*` → `ui/theme` → `ui/controls` → `ui/main_form` → `ui/wave_form`。headless 模式跳过 `ui/*` 四个文件（秒开、无 WinForms 依赖）。
**接口约定**：
- `New-MainForm` / `New-WaveForm` 返回控件哈希表，事件绑定的 scriptblock 仍由入口提供（`handlers` 哈希传入），模块不反向引用入口函数；
- 解析器接口冻结：`Convert-BytesToFrames -Buffer <byte[]> -Protocol <string>` 返回帧数组，签名改动视为破坏性行为；
- 共享状态集中为 `$script:state` 哈希（连接对象、缓冲、计数器），模块只读写该哈希，不各自声明全局变量。

## 4. 主窗口新布局（三段式）

```
┌──────────────────────────────────────────────────────────────┐
│ ① 连接条 (h=36)  [COM8 — CH340 ▾][1500000 ▾][8N1 ▾] [⚡连接] [↻] │
│    高级参数收起为「⋯」按钮弹出面板（数据位/校验/停止位/流控/DTR/RTS）│
├──────────────────────────────────────────────────────────────┤
│ ② 日志区 (弹性)   RX 70% │ TX 30%（SplitContainer，比例可拖）     │
│    各区带独立工具行：RX[清空][导出][高亮正则] TX[清空][重发]        │
├──────────────────────────────────────────────────────────────┤
│ ③ 发送条 (h=88)  [多行输入............................][发送▶]    │
│    [HEX□] [换行 CRLF▾] [追加换行☑] [发送后清空☑] [历史▾]          │
├──────────────────────────────────────────────────────────────┤
│ ④ 状态条 (h=24)  ●未连接 │ 帧 0 │ 0 B/s │ 📈波形 │ ⚙设置 │ 格式说明 │
└──────────────────────────────────────────────────────────────┘
```

关键决策：
- **「波形窗口」「主题」「自动保存日志」等低频控件移入状态条右侧/设置弹出层**，顶部只留连接相关——解决顶部拥挤的根因是"所有控件平权"，重构按使用频率分层。
- RX:TX 默认 7:3，SplitContainer 可拖，比例记忆进 `gui_settings.txt`。
- 启动横幅只进 RX；TX 区首行改为提示水印（灰字，输入即消）。
- 间距规范：外边距 12，控件间距 8，区段间距 12；统一 `Segoe UI 9pt`（中文回退微软雅黑）。
- 图标用 Segoe MDL2 Assets 字符（⚡↻📈⚙▶），零资源文件依赖。

## 5. 主题系统修复清单

| 编号 | 修复项 | 方法 |
|---|---|---|
| T1 | label_port / label_status / label_hint 白底 | 三处硬编码 `BackColor=White` 改走 `$script:theme.BgPanel`；themeaudit 加断言：深色模式下任何控件 BackColor 亮度 >200 即 FAIL |
| T2 | SetWindowTheme 未生效（on 0） | 对所有 ComboBox/TextBox/按钮句柄统一 `SetWindowTheme(hWnd,'DarkMode_Explorer',null)`，并在 audit 中输出成功计数 |
| T3 | 主题切换残留 | `Apply-Theme` 改为递归遍历控件树 + 按类型分派配色表（集中在 theme.ps1 一张表） |
| T4 | 滚动条/标题栏深色 | Win10/11 用 `DwmSetWindowAttribute(DWMWA_USE_IMMERSIVE_DARK_MODE)`；滚动条随 FlatScroll 自绘或接受系统默认（记录为已知限制） |

回归手段：现有 `-ShotPath/-ShotTheme` 截图钩子保留，新增 themeaudit 断言后 `run_stability_tests.ps1` 自动比对。

## 6. 波形窗口微调（不动内核）

- 顶部两行工具条合并为一行 + 溢出菜单（「预设/插件/复制AI格式」进溢出）；
- 通道面板收窄（眼睛图标列与色块列合并为一列色块按钮，单击显隐、双击改名）；
- 其余渲染/交互逻辑零改动。

## 7. 实施步骤与 git 计划

前置：用户放行 `.git` 读取与主脚本写入规则；确认 `git status` 干净（不干净先提交存量）。

分支：`feature/v6-ui-rework`，从 `main` 拉出。

| # | commit | 内容 | 验证 |
|---|---|---|---|
| 1 | `chore: 清理探针文件，更新 .gitignore` | `_probe_*.ps1` 移入 `tools/probe/`；`.gitignore` 补 `logs/`、`wave_export_default.txt` | 目录干净 |
| 2 | `fix(theme): 修复白底标签与 SetWindowTheme` | 第 5 节 T1+T2 | themeaudit 断言全绿 + 截图对比 |
| 3 | `refactor(core): 抽出 dsp/protocol/fault_decode` | 纯移动，行为冻结 | SelfTest 通过 |
| 4 | `refactor(core): 抽出 serial_io/headless/control_channel` | 纯移动 | 无头捕获退出码 + JSONL 格式 diff 一致 |
| 5 | `refactor(ui): 抽出 theme/controls/main_form/wave_form` | 纯移动 | SelfTest + WaveTest + 截图像素 diff 容差内 |
| 6 | `refactor(state): 全局变量收敛为 $script:state` | 仅命名与归集 | 全量测试 |
| 7 | `feat(ui): 主窗口三段式布局` | 第 4 节 | 截图人工确认 + 全功能点检表 | ✅ 已落地（ade5bc5 / 6137437 / 5aec287 / 86ace7d / 68638c2） |
| 8 | `feat(ui): 波形窗工具条合并` | 第 6 节 | WaveTest + 截图 | ⛔ 主动放弃，见 10.5 |
| 9 | `docs: 更新 README_V6` | 新架构图、新布局说明、回滚方式 | — |

合并策略：每 commit 验证通过才推进；commit 3–6 是**纯移动重构**（行为冻结，靠测试背书），7–8 才是行为变更。全部完成后逐条合入 `main`（保留历史，不 squash，便于 bisect）。
回滚：任何阶段异常 `git checkout main` 即回；V3.2 启动器始终可用兜底。

## 8. 风险与缓解

| 风险 | 概率 | 缓解 |
|---|---|---|
| dot-source 拆分后 `$script:` 变量作用域问题 | 中 | dot-source 共享同一 script 作用域，拆分只做函数级移动；commit 3–6 各跑一次完整测试 |
| 模块加载顺序导致函数未定义（前向引用） | 中 | 按依赖方向定死加载序；入口启动时做 `Get-Command` 完整性自检，缺函数即报清晰错误 |
| headless 误加载 ui 模块拖慢/报错 | 低 | 入口按模式分支加载，headless 路径不 touch WinForms |
| 布局重排打断肌肉记忆 | 低 | 连接条控件顺序保持不变；设置弹出层提供「旧布局」开关（保留一个版本周期） |
| Flat 控件 + 系统主题混搭闪烁 | 低 | 双缓冲开启；截图回归覆盖 |
| 安全规则再次拦截写入 | 中 | 动工前先用小文件写入验证；被拦则回退为输出补丁文件由用户手动应用 |

## 10. 执行方式变更（2026-10-20 补充）

沙箱实测结论：项目目录被安全中心规则保护——**PowerShell/Bash 对该目录的读、写、执行全被拦截，git（`.git\HEAD`）也被拦截**；仅宿主 Read/Write/Edit 可用。因此：

1. **commit 3-5 改为「机械拆分器」落地**：`tools\split_modules.ps1`
   - 只搬**按名字显式点名**的函数；未点名的一律留在入口 —— 增量推进，不赌。
   - 顶层可执行代码（状态初始化、主窗体构建、事件绑定、SelfTest/Headless/WaveTest、ShowDialog）**全部留在入口且位置不动**，加载顺序风险为零。
   - 模块文件以 **UTF-8 带 BOM** 写出（实测：无 BOM 的 UTF-8 在 PS5.1 下中文被按 GBK 解析成乱码，直接导致解析失败）。
   - 自带备份（`serial_terminal_v5.ps1.bak_<时间戳>`）、幂等保护（检测到已有加载器即中止）、零匹配保护（清单全不命中即中止并打印实际函数名）。
   - 用法：`powershell -File tools\split_modules.ps1`（预演）→ 核对计数 → `-Apply -Verify`（自动跑 `-SelfTest`）。
2. **commit 6 降范围**：不做 `$script:*` → `$script:state.*` 全局重命名。理由：无 git 安全网且无法本地执行测试，8000 行级重命名风险远大于收益。改为在模块头部注释中登记状态归属。
3. **commit 7 已执行 / commit 8 主动放弃**：阻塞解除后（可本地跑 `-WaveTest`）commit 7 已落地，坐标/高度断言同步改完（顶部收起 82 / 展开 124，原 `panel_top.Height -eq 142` 作废）。commit 8（波形窗工具条合并为一行 + 溢出菜单）**决定不做**，理由见 10.5。
4. **插件维护性**（新增交付）：`docs\插件接入指南.md` + `plugins\_template\plugin.example.ps1`，固化合同（Register-WavePlugin / Get-WavePluginSnapshot / WaveDsp）、UTF-8 BOM 要求、资源释放与异常隔离约定、提交自检清单。

### 10.5 波形窗工具条「合并为一行」——决定不做

原计划（第 6 节）：把波形窗顶部两行工具条压成一行，多出来的「预设 / 插件 / 复制AI格式」进溢出菜单。

**复核结论：不合并。** 三条理由：

1. **一行装不下。** 默认窗体宽 1260px，仅第一行（模拟数据 → 暂停 → 清空 → 导出 → 全不选 → 自动Y轴 → 自动范围 → Ymin/Ymax → 窗口 → 协议 → 复制AI格式）就已经排到 x=1250，余量为零。第二行（滤波 / 强度 / 预设 / 存预设 / 删 / 插件区 585px）整行 596px 起步，物理上塞不进去。
2. **溢出菜单会伤害高频操作。** 滤波、窗口、协议、预设是调波形时反复切换的旋钮，藏进二级菜单等于把一次点击变成两次，而且看不见当前值——对一个要靠「看数字」判断信号的调试工具，这是负优化。
3. **两行 76px 并不算贵。** 波形窗 720px 高，工具条占 10.5%，换来所有控件平铺可见。主窗体「丑」的根因是控件平权 + 无网格（已通过分组重排解决），波形窗不存在这个问题。

**保留现状**：两行分组 + 分隔线（数据源 | 通道 | 视图 | 时间窗 | 协议 ／ 滤波 | 预设 | 插件），全部控件可见、全部带 ToolTip。

**触发重开本议题的条件**：波形窗默认宽度降到 1100px 以下，或工具条控件数再增加 4 个以上。届时优先做的是「可折叠第二行」而不是「合并 + 溢出」。

## 9. 全功能点检表（commit 5 验收用）

连接/断开 · 收发 ASCII/HEX · 自动保存日志 · 自动连接 · 复位/烧录按钮 · 三协议波形 · 滤波 · 导出三种格式 · 录制回放 · 频谱插件 · 模拟数据 · 控制通道 ping/status/send · 无头捕获退出码 · 主题切换 · 截图自检
