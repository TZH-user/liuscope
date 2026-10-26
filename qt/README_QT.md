# LiuScope（柳镜，原 serialscope-qt）Qt 主线

用 PySide6（Qt for Python）实现的主线前端。**自 v1.1.0 起 Qt 实现转正为主线**
（经压力/交互/真机浸泡测试验收）；v1.2.0 起软件更名 LiuScope（中文别名「柳镜」）。
PowerShell 实现封版于 v1.0.0，已整体移至 ps-legacy 分支作零安装存档。

## 运行与自测

```powershell
python -m pip install PySide6-Essentials pyserial pyqtgraph   # 一次性环境准备（已装可跳过）
python qt/main.py                # 运行（想不占控制台可用 pythonw qt\main.py）
python qt/main.py --selftest     # 离屏自测（无需显示器/串口，33 组断言）
python qt/headless_main.py --help   # 无头捕获 CLI（不开界面，供 AI/脚本调用）
```

打包（单文件 exe，`--windowed` = 启动不弹 cmd 黑窗；产物名随 v1.2.0 更名为 LiuScope）：

```powershell
python -m PyInstaller --noconfirm --onefile --windowed --paths qt --name LiuScope qt/main.py
```

## 为什么试 Qt

主线的一切 UI 痛点都源自 WinForms 无样式系统：深色主题要逐控件自绘 + Win32 消息劫持。
Qt 的 QSS 是纯 CSS 写法，一套声明全局生效；波形用 pyqtgraph（GPU 加速、百万点级）；
代价是分发要带 Python 运行时（PyInstaller 打包实测约 101 MB，主线零安装优势会失去）。

## 里程碑（合并评估的验收线）

- **M1 骨架闭环（已完成，ec9f08f）**：模块化工程（core 纯逻辑 / ui 分层）、连接/断开、
  RX/TX 着色日志、行缓冲 + 空闲落显 + 积压常显（与主线同构语义）、离屏自测
- **M2 波形（已完成，34b7f68）**：pyqtgraph 曲线 + FireWater/JustFloat/RawData 三协议
  （core/wave_state.py 逐字对齐主线 core/protocol.ps1）+ 滤波（滑动平均/零相位低通，
  core/dsp.py 对齐 C# WaveDsp）+ 视图模型（跟随/历史/平移/窗口宽度）+ 通道芯片改名持久化 +
  CSV 导出 + 模拟数据
- **M3 收发增强（已完成，b989cc4）**：ASCII/HEX 双格式收发、换行模式（无/LF/CR/CRLF）、
  发送历史（⬆/⬇ 回翻）、日志查找条（高亮 + 第 i/N 处）、文件分块发送（4 KiB/20ms）
- **M4 外围（已完成，cc14aab）**：Modbus RTU 调试台（8 功能码手动事务 + Watch 合并轮询，
  core/modbus.py 逐字对齐主线插件）、检测更新（GitHub Releases API，线程池后台 + 24h 节流）、
  PyInstaller 打包与体积实测
- **M4.5 频谱仪（已完成，6a4f464/8e04c31）**：FFT dB 谱（core/dsp.py 移植 C# WaveDsp）+
  峰值保持/指数平均 + 手动 RBW + 参考电平标度 + 瀑布图（最新在顶）+ Marker + 主频标注
- **M5 功能补齐（已完成，44d1ff8→a053ad2，v0.5.0-qt）**：按主线盘点清单全量复刻——
  - A 档：清空记录、RX/TX 字节统计、全时间戳日志（yyyy-MM-dd HH:mm:ss.fff）、
    端口热插拔 2s 自动刷新、新设备自动连接（CH340 优选）、设置记忆
    （口/波特率/收发格式/换行，gui_settings_qt.ini）、Ctrl+F 时间模式查找（输入时刻直跳头行）
  - B 档：自动日志落盘（logs/serial_auto_log_*.txt，懒创建、SYS 不落）、日志导出、
    行悬停 0.4s HEX⇄文本/十进制/float32 互转提示、STM32 HardFault 寄存器 dump 自动解码
    （CFSR/HFSR 逐位口径）、波形页「格式说明」+ AI 接入说明一键复制
  - C 档：XY 相位图（1/2/4 正方格 + 余晖衰减 + 固定/自适应量程）、波形场景预设
    （wave_presets.txt INI 往返 + 一键应用）、会话录制（JSONL DATA b64）与 GUI 回放、
    AI 控制通道（TCP 127.0.0.1:9101 JSON 行协议，命令族对齐主线）、
    无头捕获模式（qt/headless_main.py：捕获/回放/触发器/期望测试/复位烧录，退出码 0/2/3/4/5 对齐主线）
- **M6 合并评估**：真机（RK3562/CH340）回归，由你拍板：合入 master（主线退役或双轨） /
  继续观察 / 关闭支线

## 与主线的已知差异（有意为之或待真机回归）

- AI 控制通道不含 expect/set_hit_pattern/reset/flash 四个命令（期望测试与烧录走 headless CLI，
  语义等价、宿主面更干净）
- 日志为单区合并视图（主线是 RX/TX 双框），悬停互转/查找对合并视图生效
- 查找时间模式弱高亮上限 500 处（两万条日志全量高亮会卡）
- 浅色主题与 Demo 模式（-Demo 演示截图链路）明确豁免

## 目录

```
qt/
  main.py               入口（--selftest 转自测）
  headless_main.py      无头捕获 CLI（采集/回放/触发/期望测试/复位烧录）
  core/rx_buffer.py     行缓冲/空闲落显/积压（纯逻辑，假时钟可测）
  core/serial_worker.py QThread 串口读写（端口在工作线程打开）
  core/hexutil.py       HEX 文本互转（0x 前缀/多分隔符，非法 token 报错）
  core/ports.py         串口扫描/展示文本/CH340 识别与优选
  core/wave_state.py    三协议解码 + 视图模型 + 滤波缓存（纯逻辑，可测）
  core/dsp.py           滤波/FFT 幅度谱/瀑布色带（numpy，对齐主线 C# WaveDsp）
  core/modbus.py        CRC16/组帧/响应解析/Watch 合并轮询（纯逻辑，可测）
  core/update_check.py  版本比较 + GitHub Releases 检查（opener 可注入）
  core/settings.py      gui_settings_qt.ini 读写（QSettings IniFormat）
  core/auto_log.py      自动日志落盘（懒创建）+ 导出文本构建
  core/log_find.py      时间戳查询识别/解析/头行定位
  core/text_format.py   行悬停 HEX⇄文本/十进制/float32 互转
  core/fault_decode.py  STM32 HardFault dump 逐位解码
  core/ai_doc.py        AI 接入说明三协议文案
  core/wave_presets.py  波形场景预设 INI 往返
  core/session_record.py 会话录制 JSONL 与回放读取
  core/control_channel.py AI 控制通道（TCP JSON 行协议，api 注入式）
  core/headless.py      无头捕获编排（回放可测，串口工厂注入）
  ui/main_window.py     主窗体（终端/波形/频谱/Modbus 四页签 + 帮助菜单 + 热插拔 + 设置记忆）
  ui/wave_window.py     pyqtgraph 波形页（+ 预设/录制/格式说明/XY 开关）
  ui/xy_view.py         XY 相位图（余晖累积 + 正方格 IQ 视图）
  ui/spectrum_panel.py  频谱仪页（FFT dB 谱 + 瀑布 + Marker）
  ui/modbus_panel.py    Modbus 调试台页（transport 注入式收发）
  ui/find_bar.py        查找条（文本 + 时间定位双模式）
  ui/log_hover.py       日志行悬停互转提示
  ui/history_edit.py    发送历史输入框
  ui/theme.py           深色 QSS + 日志行配色
  selftest.py           离屏自测（33 组：RX 缓冲/HEX/主窗体/终端外观/热插拔/时间查找/自动日志/
                        悬停互转/HardFault+AI 文档/预设/XY/录制/控制通道/headless/协议/波形页/
                        M3/Modbus×2/更新×2/频谱×2 + 集成收口 9 组：连接按钮状态/TX 右对齐+
                        悬停互转/显示点数+悬停读数/XY 新参数/频谱联动导出/主题切换/
                        AI 通道扩展/STM32 假 CLI/七页签集成）
```

## v1.2.0 里程碑（LiuScope 更名与收口）

- **更名与版本**：`APP_VERSION` 1.1.0 → 1.2.0，软件更名 **LiuScope**（中文别名「柳镜」，
  原 serialscope）；窗口标题、关于页（品牌/别名/作者 折柳/仓库）、更新检查 URL 与
  User-Agent、AI 接入文档同步更名。PowerShell 存档版（v1.0.0 封版）整体移至 ps-legacy 分支。
- **设置/关于页 + 双主题**：主题深/浅色即时切换并写 ini 记忆（重启保持），全局 QSS +
  pyqtgraph 绘图区 + 曲线 16 色 + 频谱轨迹 + 芯片边框 + XY 格配色整套随主题；
  「曲线填充」开关迁到设置页（设置键 `wave/fill_under`，即时生效）；关于页含作者与许可证。
- **STM32 烧录页**：基于 STM32CubeProgrammer CLI——CLI 四级路径解析（设置键 → C:\ST →
  PATH → 盘符扫描）、ST-LINK 探测/多探针选择、连接与芯片信息、软/硬复位、hex/bin 烧录
  （-v 校验、bin 基址可改）、全片擦除与读保护解除（危险二次确认）、OB 读取、CLI 原始日志。
- **波形页改造**：工具行「窗口」→「显示点数」；「导出」菜单按钮（PNG/CSV）；绘图区右键
  全中文菜单（恢复自动缩放/导出/显示网格）；XY 相位图补齐每窗分格与全局 线/点 画法、
  余晖量程切换拉伸迁移与换源保留、进入即回铺最近窗口；悬停十字线 + 读数框
  （首行 x + 每条可见曲线一行同色 y，取真实采样点）。
- **频谱页改造**：谱图与瀑布频率轴 (0, fs/2) 双向联动（同频点垂直对齐）；导出 PNG
  （谱+瀑布拼图 + QPainter 参数标注）；导出 CSV（最新一帧 / 最近 N 秒，N 超采集时长
  自动钳制）；「清空」一键复位；Marker 改走纯计算消除鼠标副作用。
- **终端页改造**：连接按钮状态化（手动/自动/丢失/失败全路径同步）；TX 行聊天式右对齐
  （文档级 RightTab 制表位 + 行首制表符，随视口宽度重排）；悬停互转修复（空正文条目、
  TX 制表行兼容）。
- **AI 控制通道规范化**：COMMANDS 单一事实命令表（19 条）；read_lines 增强（follow=true
  持续推送：interval 节流 0.05~60s、max_lines 上限 2000，stop/断开退出）；
  spectrum_snapshot/xy_snapshot/modbus_recent/stm32_status/settings_list/get/set
  （7 个可写键，改后 UI 控件同步，返回 before/after）；AI_INTERFACE.md 全量重写。
- **集成收口**：离屏自测扩到 33 组（新增连接按钮状态/TX 右对齐+悬停互转/显示点数+
  悬停读数/XY 新参数/频谱联动与导出/主题切换/AI 通道扩展/STM32 假 CLI/七页签集成）；
  修复 ControlChannel socket 延迟析构在嵌套事件循环触发 access violation 的真实缺陷；
  main.py 启动主题统一走 apply_saved_theme()。
