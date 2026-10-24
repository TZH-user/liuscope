# RK3562 串口收发工具 V6

V6 = V5 全部功能 + 模块化重构 + 界面重排。功能与协议行为与 V5 完全一致（AI 接口、无头捕获、控制通道命令表、退出码全部冻结），详见 README_V5.md；本文件只记 V6 的差异与新架构。

## 启动方式

- 双击 `launch_serial_terminal_v6.vbs`（无黑框，推荐）或 `launch_serial_terminal_v6.cmd`
- 手动：`powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File .\serial_terminal_v6.ps1`

## 本包内容

```
串口调试助手-V6/
  serial_terminal_v6.ps1   主脚本（入口）
  launch_serial_terminal_v6.vbs / .cmd   启动器
  core\   5 个模块（serial_io / protocol / fault_decode / headless / control_channel）
  ui\     3 个模块（theme / wave_form / main_form）
  plugins\spectrum\         频谱插件（随包可用）
  plugins\_template\        插件模板（.example.ps1，不会被引擎加载）
  docs\                     设计稿 + 插件接入指南
  gui_settings.txt          默认配置（深色主题 / 1500000 / ASCII，端口留空自动探测）
  logs\                     空目录，运行时生成自动日志
  README_V5.md / AI_INTERFACE.md   V5 功能与 AI 接口文档（V6 行为与之冻结一致）
  run_stability_tests.ps1 / test_spectrum_plugin.ps1   测试脚本
```

本包为**仅运行时**分发：不含一次性重构工具 `tools\split_modules.ps1` 与开发探针 `tools\probe\`，也不含 V3.2 旧版本。

## V6 差异一览

| 类别 | 变化 |
|---|---|
| 架构 | 单体 8122 行脚本拆为「入口（约 2200 行）+ 8 个模块」，133 个函数全部归类 |
| 主题 | 深色 = VS Code Dark+（蓝强调 #0E639C / 细边框 #3E3E42），浅色 = Fluent（白卡片 / Fluent 蓝 #0F6CBD） |
| 布局 | 顶部收敛为两行（连接行 / 参数行）；状态行抽出为底部状态条；板卡操作行默认收起 |
| 日志区 | SYS 只进 RX 区（不再双区重复）；RX:TX 按 7:3 自动分配；隐藏常驻横向滚动条 |
| 控件 | 下拉框 OwnerDraw 自绘（DropDownList 白底根因修复）；波特率可编辑框走 WM_CTLCOLOREDIT 上色；分隔条 3px 中间色融背景 |
| 发送区 | 8px 网格重排，面板高 170→146 |
| 审计 | themeaudit 输出 alpha 感知（Transparent 不再误报白底），深色主题亮度>200 打 `THEME_FAIL` |

## 模块地图

```
serial_terminal_v6.ps1   入口：param、C# 控件类（DarkComboBox/DarkCheckBox/...）、状态初始化、
                         模块加载、SelfTest/Headless/WaveTest、主窗体构建与事件绑定、ShowDialog
core/
  serial_io.ps1          串口 IO / 端口探测 / 日志与消息管线 / 设置持久化（最先加载）
  protocol.ps1           波形协议解析与缓冲（FireWater/JustFloat/RawData，纯逻辑）
  fault_decode.ps1       STM32 HardFault 解码
  headless.ps1           无头捕获 / 触发 / 期望测试 / 录制回放
  control_channel.ps1    AI 控制通道（TCP 9101 JSON 行协议）
ui/
  theme.ps1              双主题 token（$script:ui_palettes）与 apply_theme / 主题审计
  wave_form.ps1          波形窗口：构建、渲染、导出、插件装载、模拟数据源
  main_form.ps1          主窗体行为：板卡行收展、会话保存、端口下拉同步
docs/
  UI重构设计稿_V6.md      设计决策与 commit 计划
  插件接入指南.md         波形窗插件合同与自检清单
plugins/_template/       插件模板（.example.ps1，不会被引擎加载）
```

## 模块开发三条红线

1. **路径一律用 `$script:app_root`**，不要用 `$PSScriptRoot`——模块内 `$PSScriptRoot` 指向模块自身目录，会把 settings/logs/预设写错位置（V6 已踩过并修复）。
2. **模块文件必须 UTF-8 带 BOM**——PS5.1 对无 BOM 文件按 GBK 解析，中文直接解析失败。
3. **不要在模块里包函数壳去 dot-source 插件/子模块**——函数内 dot-source 的定义落入局部作用域，返回即消失；必须在脚本顶层作用域执行。

## V6 追加功能

### XY 相位图（IQ 看相位）

波形窗口通道面板底部的「XY 相位图」区：每个窗口把 X 通道画横轴、Y 通道画纵轴成相位轨迹，适合 IQ 信号正交性/星座图判断。

- **窗口数 1/2/4**（RIGOL 式多窗），格子保持正方形，每窗口独立分组框（X 源 / Y 源 / 量程 / 分格）
- **量程默认固定 ±8、8 分格**，刻度随每格显示在窗内；「自适应」才是按数据自动缩放
- **余晖**：关 / 0.5s / 1s / 2s / 5s / 常驻，旧轨迹按时间常数淡出（磷光屏效果），量程切换时轨迹按比例迁移不清空

### 日志行悬停格式互转

鼠标悬停 RX/TX 日志区任意行约 0.4s：HEX 行自动解码成「文本(UTF-8) / 十进制 / float32(小端)」；文本行给出 UTF-8 HEX。悬停条目头行（`[时间] RX`）等效于悬停其正文行。预览超 64 字节截断加省略号。

### 缓冲积压监控（协议格式/波特率不对的早期信号）

连接状态下状态条**常显**「积压 N B ↑/↓/→」= 未成行文本 + 波形协议里等帧尾/等换行的未解析字节数（即「已接收但还没显示」的量，内部变量 `$script:rx_backlog_bytes`）。正常收发时该值短暂存在（当前行未收完），静默约 0.5s 内由空闲落显（`flush_rx_line_if_idle`，无换行残行不再等 8KB 强制线）清零；持续增长约 2s 变黄色、4s 变红色；JustFloat 找不到帧尾（>64KB 失步）或 FireWater 行缓冲超长丢弃时，日志区给一条节流（5s）SYS 提示。

### 发送区：文件发送 + 历史回翻

- **文件发送**：选择文件按原样字节流分块发送（4KiB/20ms，大文件不卡界面）；发送中再点一次停止；完成/中断都进 TX/SYS 日志。换行追加与 HEX 转换对文件发送不生效。
- **发送历史**：⬆/⬇ 像 cmd 一样回翻已发送内容（光标在第一行按 ⬆ / 最后一行按 ⬇ 才生效，多行编辑时方向键仍是移动光标）；⬇ 走到底恢复正在编辑的草稿；连续重复不重复入栈，上限 200 条。

### 日志查找栏（Ctrl+F）与时间戳定位

- **Ctrl+F** 呼出查找栏（Esc 关闭），**Enter/下一个、Shift+Enter/上一个（或 F3 / Shift+F3）** 循环跳转，计数标签显示「第 m/N 处」，到头自动回绕，不分大小写。
- **输入即搜**：打字过程中 150ms 防抖后自动从头匹配——命中即时高亮（全部命中弱色底 + 当前命中强色底），行自动滚到视口中间；Enter/按钮才继续从当前位置向后/向前跳。
- **输入时间戳直接定位**：查询形如 `20:57` / `20:57:38` / `20:57:38.5` / `2026-10-24 20:57:38` 时，跳到第一条时间 ≥ 该时刻的日志条目头行（只给时刻时日期取日志首条日期），头行高亮并居中；继续 Enter 逐条向后，Shift+Enter 反向。
- 搜索范围为 RX 日志区（含 SYS）；Modbus 插件事务、普通收发都会进这个日志，可统一检索。

### Modbus RTU 插件（plugins\modbus_rtu）

波形窗口的 **Modbus** 页签即主站调试台（与波形同窗，不再另开窗口）（设备侧为从站，主窗口先连接串口），分**静态事务**与**实时监视**两个页签：

- **事务页（静态手动）**：功能码 01/02/03/04（读线圈/离散输入/保持/输入寄存器）、05/06（写单线圈/单寄存器）、0F/10（写多线圈/多寄存器）；起始地址与写入值支持十进制和 `0x` 十六进制；CRC16-Modbus 自动生成与响应校验；异常帧（01~0B）中文说明；寄存器解码 HEX / 十进制 / 有符号 / **float32(ABCD)** / **float32(CDAB)**；可勾选轮询；每次事务回写主窗口日志（Ctrl+F 可检索）。
- **实时监视页（动态 live watch）**：把读地址收进 Watch 表，按 **键 = 从站:功能码:起始地址** 匹配更新（同键重复添加 = 原位改数量/格式）；轮询引擎把同 (从站,功能码) 的项**合并成一次读请求**（近邻间隙 ≤4 合并，单次上限 125 寄存器 / 2000 线圈），响应按地址偏移切回各行；值变化闪色（数值涨绿 / 跌红 / 其余琥珀）；每行可独立格式解码；双击行**回填到事务区**改参数或写入；写操作不进监视（只读轮询）。
- 两个页共用串口：手动事务与监视轮询在 UI 线程天然串行；事务发送前的清缓冲可能吞掉监视一轮在途响应，该轮保持旧值、下周期自愈。
- 遵守插件合同：不改宿主一行代码，崩溃只禁用自身；删掉 `plugins\modbus_rtu\` 目录即完全移除。

## 已知取舍

- **波形窗工具条保持两行，不合并为一行 + 溢出菜单。** 默认窗体宽 1260px，第一行已排满到 x=1250；第二行的滤波 / 强度 / 预设 / 插件（约 596px）物理上塞不进同一行。而滤波、窗口、协议、预设是调波形时反复切换的旋钮，藏进二级菜单会看不见当前值、把一次点击变两次——对靠「看数字」判信号的调试工具是负优化。完整决策记录见 `docs\UI重构设计稿_V6.md` 第 10.5 节。
- **历史浏览滑块、RX/TX 文本区滚动条为 Win32 原生绘制**，深色主题下仍呈亮色（已知限制，非自绘可覆盖范围）。

## 验证

```powershell
# 协议解析 + 缓冲 + 离屏渲染
powershell -NoProfile -ExecutionPolicy Bypass -STA -File .\serial_terminal_v6.ps1 -SelfTest
# 端到端：真实窗体 + 波形 + 导出 + 录制 + 控制通道 + 布局断言
powershell -NoProfile -ExecutionPolicy Bypass -STA -File .\serial_terminal_v6.ps1 -WaveTest
# 主题回归截图（ShotPath 必须为纯 ASCII 路径）
powershell -NoProfile -ExecutionPolicy Bypass -STA -File .\serial_terminal_v6.ps1 -ShotPath C:\Temp\shot.png -ShotTheme Dark -ShotMode screen
# 稳定性套件（大流量回放 / 畸形数据 fuzz / GUI 长跑）
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_stability_tests.ps1 -SkipGuiLongRun
```

## 与 V5 的关系

V6 是 V5 的模块化 + 换肤版本，行为冻结项（协议解析结果、JSONL 格式、控制通道命令表、退出码）与 V5 完全一致。本包是独立分发目录，与旧版 `串口调试助手-V3.1` 目录互不干扰，可并存；旧版如需继续使用，仍从原目录启动。
