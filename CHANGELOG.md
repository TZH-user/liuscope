# 更新日志

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/) 与
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式。

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
