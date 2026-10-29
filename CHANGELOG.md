# 更新日志

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/) 与
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式。

## [Unreleased]

### 新增
- **日志区等宽字体 + 自动折行**（字符画错位与长行横滚问题的根治）：日志主区经 QSS `#log_view` 切等宽字体（`theme.mono_font()`，Windows 命中 Consolas，CJK 字形 Qt 自动回落 YaHei UI）——字符画/框线图/HEX 列/IDF 时间戳按终端「1 字符=1 格」网格对齐，鲁班猫 motd 猫画不再被全局比例字体压歪；折行从 NoWrap 改 WidgetWidth + `WrapAtWordBoundaryOrAnywhere`——长行/长 HEX 串超出视口宽自动断到下一行，窗口缩放即时重排。注意字体必须走 QSS 注入：样式表字体优先级压过 `setFont`；`theme` 模块会在建 QApplication 前被导入，`DARK_QSS/LIGHT_QSS` 兼容常量改 PEP 562 懒生成（模块级 `_build_qss` 在无 app 时调 `QFontDatabase` 会原生崩溃）。自测扩至 49 组（新增 `test_log_wrap_mono_art`：等宽命中/折行配置/缩放重排/猫画逐字节保真）；视觉证据 `_verify/evidence/ansi_motd_art_mono.png`（复现：`_verify/ansi_motd_art_shot.py`，内嵌板上 00-header 渲染真字节）
- **ANSI 终端渲染**（日志区新开关「ANSI 渲染」，默认开，设置键 `log/ansi_render`）：串口回传的 SGR 转义按设备指定着色——16 色（按日志底色明暗自动切深/浅两套端末调色板，鲁班猫等 Linux 串口控制台、ESP-IDF 日志色码直接受益）、256 色、真彩；粗体/斜体/下划线/删除线/反显/暗淡生效；清屏、光标移动、OSC 标题、字符集切换等对滚动日志无意义的序列自动剥除。渲染以字符格式直插文档：文本零损耗（查找 / 悬停 HEX⇄文本互转按原文工作），空格天然保排版——框线图与 ASCII 艺术字不再塌陷；设备指定色对日志底对比度 <2.0 时朝文字色混合保底。条目原文与自动保存日志始终记录原始解码结果（含转义码）。新增 `qt/core/ansi_render.py`（纯逻辑段解析）与自测 3 组；视觉证据 `_verify/evidence/ansi_render_{dark,light}.png`（复现：`_verify/ansi_demo_shot.py`）

### 修复
- 对比度审计脚本页签清单未随 v1.3「Linux 支持」页更新（7→8）导致无法运行，已修正（现覆盖 436 项，两主题 0 违规）

## [1.3.0] - 2026-10-28

### 新增
- **ESP32 支持**：
  - 连接层——「打开串口时不拉 DTR/RTS」防复位选项（DTR/RTS 经自动下载电路映射 IO0/EN）；「复位 / 下载模式」设备线控按钮（esptool ClassicReset 语义）+ DTR/RTS 实时切换；断线自动重连（每 2s 重试，适配烧录后 COM 重枚举）；端口下拉按 VID/PID 标注芯片（CP210x/CH340/CH341/FT232/ESP32 JTAG/ESP32-S2 CDC）；波特率预设补 74880/921600/2000000
  - 日志层——ESP-IDF 日志解析（等级着色，开关默认开）、复位横幅 `rst:0x…` 装饰行、Guru Meditation panic 高亮、backtrace pc 地址自动收集 + 「解析 backtrace」对话框（addr2line，自动探测 IDF 工具链，ELF/工具路径记忆）
- **Linux 适配**（按需求度决策，见 `docs/research/调研_Linux串口生态与适配决策.md`）：打开失败按平台给处置提示（dialout 权限 / ModemManager·brltty 抢占 / Windows 占用）；「设备屏」页更名 **「Linux 支持」** 并入主线（v2 M1/M2 合并：网络串口 URL 直通 + UDP H.264 投屏原型 + server-rk 板端骨架）
- **外观标准化**：自定义主题——窗口背景/控件底/正文文字/强调色/日志底/日志文字六键可调（基准主题决定曲线色等），即时 WCAG 对比度复核并在设置页警示不达标项，重启保持；页面显示设置（STM32 / Linux 支持页可关，记忆保持）

### 修复
- 主窗顶行加控件导致最小宽度超窗（ESP32 线控按钮移至日志行右段）；测试假端口实现与真实 SerialWorker 新签名同步

### 文档
- README 功能一览补 ESP32 / 自定义主题章节；`docs/research/调研_Linux串口生态与适配决策.md`（竞品特性对照与三档决策）；自测扩至 45 组（ESP32 连接层 5 组 / 日志层 3 组 / 外观与页面 1 组）

## [1.2.1] - 2026-10-27

### 新增
- 应用图标「垂柳 × 示波水线」（`qt/assets/`，SVG 源 + 多尺寸 ico；窗口/任务栏/exe/README 全接入；`make_icon.py` 可再生）
- 浅色/深色主题对比度审计：`_verify/theme_contrast_audit.py` 共 394 项检查（两主题 × 7 页签语义配对 + 渲染像素抽样），selftest 新增 THEME CONTRAST 组固化关键断言
- 浅色截图：`docs/screenshots/main_light.png`、`settings_light.png`
- 用户手册 v1.2 新功能篇：`docs/用户手册_v1.2.md`（快速上手/STM32 烧录页 / AI 通道速用 / 频谱导出口径 / 故障排查 FAQ）
- AI 通道端到端验收：19 题 100 分考卷 + 真机真 socket 实测 **97.5/100**（`_verify/ai_exam.md` / `ai_exam_report.md`）
- 调研文档：`docs/research/调研_测试与交接最佳实践.md`、`docs/research/调研_调试工具与低延迟投屏.md`；下一版设计稿 `docs/设计_Linux设备互联_v2.md`
- CI：tag 构建随 exe 产出 `SHA256SUMS.txt`；`CHANGELOG.md` 建立（SemVer + Keep a Changelog）

### 修复
- 浅色主题下工具行小按钮（#tool）浅灰底白字、禁用按钮灰底白字不可辨识 → 统一蓝底白字（#0E639C，对比 6.4）；禁用态改弱化蓝灰底白字
- Modbus 事务闪烁高亮/回落文本、频谱导出警告色、波形悬停读数首行等处硬编码深色值在浅色下低对比 → 全部改随主题取色
- 暗色主题下单选钮（频谱 CSV 导出口径选择等）indicator 不渲染 → 补 QRadioButton QSS（两主题像素验证）
- AI 通道 `settings_set points` 越界值原样接受 → 按 §1.4 文档口径钳制 50..200000（E2E B-03）
- AI 通道 `stm32_status` 缺文档声称的 `note` 字段 → 按 CLI/连接状态派生明确提示（E2E Q13①）
- 波形页「恢复自动缩放」不还原被历史滑块拖窄的横向宽度 → 还原宽度到「显示点数」档位（E2E B-04）
- 修复 selftest `test_wave_display_and_hover` 探针恰好落在两采样点中点导致的稳定挂（探针偏移避开中点）

### 文档
- AI_INTERFACE.md：§3「程序化 export_csv/export_png」更正为频谱页 GUI 按钮（AI 通道无此命令）；§1.4 `paused` 补充「只冻结波形页，频谱由频谱页冻结按钮单独控制」

## [1.2.0] - 2026-10-26

更名 **LiuScope（柳镜）**（原名 serialscope）。详见 [Release v1.2.0](https://github.com/TZH-user/liuscope/releases/tag/v1.2.0)。

### 新增
- 设置页（主题明暗切换、曲线填充迁移、STM32 CLI 路径）与关于页（作者：折柳）
- STM32 烧录页：STM32CubeProgrammer CLI 封装（探针枚举/连接/软硬复位/带校验烧录/全片擦除/读保护解除/选项字节读取）
- AI 控制通道规范化：19 条命令 + help 自描述 + follow 连续输出 + settings_set 全键 UI 同步跟随（支持中文别名）
- 波形页悬停十字读数（首行 x + 每条可见曲线「名字: y 值」）；波形 PNG/CSV 导出
- 频谱导出 PNG（标注采样率/FFT/窗/RBW/主频）与 CSV（最新一帧 / 最近 N 秒，钳制到实际采集时长）+ 清空显示；频谱历史缓冲上限（6000 帧 / 2000 万单元格）
- 频谱图与瀑布图频率轴双向联动对齐

### 修复
- 终端页连接按钮状态化（已连接显示「断开」）
- 终端页 TX 行聊天式右对齐（行序不变，窗口缩放自动重排）
- 波形页「窗口」改名「显示点数」；XY 模式补齐量程/窗口数/分格/余晖等参数；曲线查看复选框边缘换色
- 波形页右键菜单全中文；日志行悬停 HEX⇄文本互转

### 结构
- master 仅保留 Qt 主线；PowerShell 存档（v1.0.0 封版）移至 ps-legacy 分支；GitHub Actions CI（离屏自测 + tag 打包）
