# RK3562 串口收发工具 V5

V5 基于 V3.2（高吞吐接收修复版）升级，新增 **FireWater 实时波形** 功能，参考 VOFA+ 的 FireWater 数据协议（https://www.vofa.plus/plugin_detail/?name=firewater ）。开源同类实现可参考 Serial-Studio（https://github.com/Serial-Studio/Serial-Studio ）与 SerialPlot（https://github.com/hyOzd/serialplot ）。

## V5 新增功能

- **三种数据协议**（波形窗口工具栏「协议」下拉切换，切换时自动清空旧缓冲）：
  - **FireWater**（文本，默认）：设备端每行输出 `ch0,ch1,ch2,...` 并以换行 `\n` 结束，即一帧。例如 `printf("%f,%f\n", a, b)`。支持负数、小数、科学计数法（如 `1.5e-3`），单行最多 32 通道，行首 `$` 视为命令行自动跳过，含 ANSI 控制序列的行会先清洗再解析。
  - **JustFloat**（二进制）：N 个 float32（小端）+ 固定帧尾 `00 00 80 7F`。通道数 = 帧长/4，帧间可变化，帧尾丢失自动重同步，适合高采样率多通道。
  - **RawData**（原始字节流）：每个字节（0~255）直接作为一个采样点，单通道连续波形，适合 ADC 等高速采样流。
  - 三种协议都在**字节级**解析，与主界面接收格式（ASCII/HEX）显示互不影响。
- **格式说明与不匹配提示**：状态栏右侧「格式说明」按钮随时打开各协议的数据格式与单片机发送示例；若串口有数据流入但 1 秒以上一帧都没解析到，状态栏会提示"疑似协议不匹配！请核对「格式说明」"。
- **FireWater 协议解析**详细行为：
  - 非数据行（日志文本）自动忽略，含逗号的坏行计入"忽略"统计。
- **实时波形窗口**：主界面右上角「波形窗口」按钮打开（关窗即停止解析，不占 CPU）。
  - 深色主题、自动/手动 Y 轴、网格刻度、通道图例实时显示最新值。
  - 每通道 16 色自动配色，右侧面板可单独勾选显示/隐藏通道。
  - 暂停/继续、清空、显示窗口 256~10000 帧可选。
  - 点数超过像素宽度时按 **每列 min/max 抽稀**，保留尖峰不失真。
  - **导出**：点击「导出」弹出对话框，可选择导出类型与通道：
    - **PNG 图片**——当前波形视图按 2 倍分辨率重绘（清晰不失真），仅包含勾选的通道；
    - **CSV 数据**——逗号分隔（Excel 直接打开），仅包含勾选通道的列；
    - **TXT 数据**——制表符分隔，带导出时间/通道/帧数注释头。
  - 对话框保持打开，可连续导出多次（换类型/换通道）；每次导出后「导出结果」栏显示文件完整路径，点「打开文件夹」在资源管理器中定位该文件。
  - 导出时选择的目录自动记忆为**默认路径**（保存在脚本目录 `wave_export_default.txt`），下次导出自动定位；窗口内同时显示当前默认路径。
- **滚轮缩放**：鼠标移到纵轴区或曲线区滚动滚轮缩放 Y 范围（以鼠标处数值为锚点，缩放后自动切到手动 Y 轴）；鼠标移到横轴区滚动滚轮调整显示窗口帧数（50~200000，历史浏览时保持窗口中心不变）。
- **拖动平移**：鼠标在横轴区或曲线区按住左键水平拖动 = 左右平移查看历史数据（右侧新数据照常接收）；在纵轴区或曲线区垂直拖动 = 上下平移 Y 范围（手动 Y 轴时生效）。拖到最右端自动恢复实时跟随。
- **三点范围滑块**（波形窗口底部，仿 VOFA+）：左红点 = 窗口起点，绿点 = 中心，右紫点 = 窗口终点，选中范围有青色高亮条。
  - 拖**中间绿点**：整体平移查看数据（窗口宽度不变）；
  - 拖**两侧红/紫点**：改变显示范围宽度，绿点自动保持在两点正中（以中心对称缩放）；
  - 最小窗口 50 帧；窗口宽度变化同步到工具栏「窗口」框；拖到最右端或点「自动范围」回实时。
  - 历史浏览时状态栏追加「历史浏览」提示，滑块上方显示橙色提示文字。
- **自动范围**：滚轮缩放或手动改过 Y 轴后，点「自动范围」一键恢复自动 Y 轴 + 1000 帧窗口 + 实时跟随。
- **波形滤波**（工具栏第二行）：`关 / 滑动平均 / 零相位低通` + 强度参数（滑动平均=窗口点数，低通=alpha 倒数，越大越平滑）。
  - **非破坏性视图**：原始数据永远保留在缓冲里，滤波按当前参数对整段缓冲重算——**切换滤波/参数立即对旧数据生效**，暂停后也可反复改参数对比效果。
  - **零相位低通**采用双向滤波（前向 + 反向 EMA），无相位滞后，滤波后波形与原波形对齐，不会右移。
  - 计算核心为内嵌 C#（`WaveDsp` 类，启动时编译），20 万点重算毫秒级；结果按（数据版本|模式|参数）缓存，数据流动时增量命中。
  - 滤波只影响显示；导出 CSV/TXT 始终输出原始数据（保真）。
- **插件系统**：波形窗口每次打开时扫描 `plugins\*\plugin.ps1`，工具栏第二行「插件」区出现入口按钮。
  - 插件合同：`Register-WavePlugin -Name -Description -OpenPanel` 注册入口；`Get-WavePluginSnapshot -Channel -Count` 拉取最近波形数据；宿主的 `WaveDsp` C# 类（滤波/FFT）可直接使用。
  - 单插件加载/执行异常只禁用自身，不影响宿主（runtime_error.log 记录）。
  - **内置频谱插件**：对选中通道做汉宁窗 FFT（512~8192 点可选），显示单边幅度谱 + 频率刻度 + 主频标注（峰值虚线）；采样率可设（默认 1000Hz），自动刷新可开关。FPGA/DDS 信号的主频和杂散一眼可见。
- **模拟数据**：点「模拟数据」无需硬件即可测试波形——按当前协议自动生成 3 通道测试信号（带噪声正弦 / 余弦 / 调制拍频，FireWater/JustFloat 约 50 帧/s，RawData 约 800 采样/s），走真实接收管线（RX 区和自动日志同样可见，二进制协议正文显示乱码属正常），再点一次停止。
- 主状态栏显示已解析的波形帧数；波形窗口底部状态栏显示实时帧率。

## 设备端发送示例

```c
printf("%.4f,%.4f\n", voltage, current);   // 两通道，帧尾 \n
```

## COM 列表设备识别与自动连接

- **COM 下拉列表带设备描述**：列表项显示为 `COM8 — USB-SERIAL CH340` 格式（描述来自系统 WMI `Win32_PnPEntity`，涵盖 CH340/CP210x/FTDI/PL2303 等所有 Ports 类设备）；连接、复位、控制通道等所有功能都自动从显示文本中提取真实 COM 号，不受描述影响。
- **后台自动探测**：每 2 秒在后台 runspace 异步查询一次设备列表（不卡 UI），插入/拔出设备后列表自动更新，无需手动点「刷新串口」；点「刷新串口」则立即同步刷新。
- **新设备自动连接**：状态栏右侧「新设备自动连接」勾选（默认开，状态记忆在脚本目录 `gui_settings.txt`）。插入新串口设备（如 CH340）后 2 秒内自动按当前波特率/参数连接，SYS 记录会显示检测与连接结果。注意：
  - **启动时已插着的设备不会自动连接**（只建立基线），需要手动连；拔出再插入视为新设备会自动连接。
  - 已连接状态或勾选关闭时不触发；连接失败（如被其他程序占用）会记录到 SYS 并不重试，直到设备重新插拔。

## 启动方式

- **推荐**：双击 `launch_serial_terminal_v5.vbs`——全程无黑框、无闪烁。
- 或双击 `launch_serial_terminal_v5.cmd`——控制台窗口隐藏，仅启动瞬间任务栏轻微闪动，不常驻。

手动在 PowerShell 中执行：

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File .\serial_terminal_v5.ps1
```

## 使用要点

1. 连接串口后点「波形窗口」，在「协议」下拉框选择与设备输出一致的数据格式（不确定就点「格式说明」对照）；没有硬件时点「模拟数据」先看效果。
2. 默认自动 Y 轴；取消勾选后可手动输入 Ymin/Ymax（回车或焦点离开生效），滚轮缩放或垂直拖动也会自动切到手动模式。
3. 「窗口」选择显示的帧数：帧率高的场景建议 500~2000，超出像素部分自动 min/max 抽稀（RawData 模式按采样点计）。
4. 关闭波形窗口即停止解析和模拟数据，正文显示与日志不受影响。
5. 主界面顶部「板卡操作」行（复位/执行并抓取/清除高亮）可通过行首 `−`/`+` 按钮收起或展开，收起后界面更紧凑。

## 性能建议

- 波形解析在接收线程上执行（PowerShell），极高波特率 + 超高帧率时建议把窗口调小，或临时关闭自动日志减小负载。
- 缓冲最多保留 20 万帧（约 20 MB），超出自动丢弃最旧数据；导出 CSV 得到的是缓冲内的完整数据。

## 推荐参数

- `COM6` / `1500000` / `8N1` / Flow control = None
- 如果无输出，再退回 `115200 8N1 None`。

## 自测

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -File .\serial_terminal_v5.ps1 -SelfTest
```

输出 `SELFTEST_OK` 表示协议解析、缓冲和离屏渲染均正常。`-WaveTest` 为真实窗体端到端测试（含控制通道/录制/导出）。

## AI 调试扩展（无头捕获 / 控制通道）

### 无头捕获模式（不开界面，供 AI/脚本调用）

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\serial_terminal_v5.ps1 -Headless `
    -Port COM6 -Baud 1500000 -Protocol FireWater -Seconds 10 `
    -Out logs\cap.jsonl -HitPattern "panic|Oops|HardFault"
```

- 输出 JSONL（每行一个 JSON 对象）：`META` 会话参数、`RX` 数据行（FireWater 行带 `text`，解析成功带 `ch` 通道值；JustFloat 每帧带 `ch`；RawData 每块带 `samples`）、`FAULT` HardFault 自动解码、`EVT` 触发事件、`TX`/`TEST` 发送与测试记录、`DATA` 原始字节（录制时）、`SUMMARY` 汇总。
- **退出码**：`0` 正常；`1` 参数/运行错误；`2` 命中 `-HitPattern`（正则，忽略大小写）；`3` 期望测试有失败。
- 不指定 `-Port` 时自动选第一个可用串口。

### 关键事件触发捕获（-TriggerFile）

配置为 JSON 数组，命中正则时自动把前 N 行 + 后 M 行存到 `logs\events\<名称>_<时间>.txt`：

```json
[
  { "name": "kernel_panic", "pattern": "Kernel panic", "before_lines": 50, "after_lines": 100 },
  { "name": "assert", "pattern": "Assertion failed", "before_lines": 20, "after_lines": 30, "once": true }
]
```

### STM32 HardFault 自动解码

接收内容出现 `CFSR/HFSR/MMFAR/BFAR` 寄存器 dump 时自动解码为具体原因（PRECISERR、IMPRECISERR、INVSTATE、NOCP、FORCED、出错地址等），GUI 中以 SYS 消息显示、无头模式写入 `FAULT` 行。适配 `CFSR:0x...` 与 `CFSR=0x...` 两种常见打印格式。

### 期望式命令测试（-ExpectFile）

JSON 数组步骤：发送 → 等待正则 → PASS/FAIL，结果写入 JSONL（`TEST`/`TEST_SUMMARY`），全部通过退出码 0，否则 3：

```json
[
  { "name": "uboot版本", "send": "version\r", "expect": "U-Boot 20\\d\\d", "timeout_ms": 2000 },
  { "name": "进内核", "expect": "Welcome to Buildroot", "timeout_ms": 30000 }
]
```

### 会话录制与回放（-RecordFile / -ReplayFile）

- GUI 波形窗口「录制」按钮或无头 `-RecordFile session.jsonl`：把全部 RX 原始字节带毫秒时间戳存为 JSONL（`DATA` 行，base64）。
- `-Headless -ReplayFile session.jsonl`：离线重放，走同样的协议解析/触发/输出管线——复盘现场不用再占板子，换协议/换正则可反复分析。

### AI 控制通道（GUI 模式自动开启）

GUI 启动时自动监听 `tcp://127.0.0.1:9101`（`-ControlPort 9101` 换端口，`-NoControlPort` 关闭）。协议为 JSON 行：连接后每发一行 JSON 请求即回一行 JSON 响应。

| 命令 | 说明 |
|------|------|
| `{"cmd":"ping"}` | 存活探测 |
| `{"cmd":"status"}` | 连接状态/协议/帧数 |
| `{"cmd":"send","text":"version\r","hex":false,"newline":"CRLF"}` | 发送（写 TX 记录） |
| `{"cmd":"read_lines","count":50,"filter":"正则"}` | 读最近 N 条 RX/TX 记录 |
| `{"cmd":"wave_snapshot","count":500}` | 最近 N 帧各通道数值 |
| `{"cmd":"set_protocol","protocol":"JustFloat"}` | 切换协议（清缓冲） |
| `{"cmd":"wave_clear"}` | 清空波形缓冲 |
| `{"cmd":"record","on":true}` | 开始/停止会话录制 |
| `{"cmd":"expect","steps":[...]}` | 运行期望测试步骤（期间界面短暂阻塞） |

PowerShell 调用示例：

```powershell
$req = '{"cmd":"status"}' + "`n"
$c = [Net.Sockets.TcpClient]::new('127.0.0.1', 9101)
$s = $c.GetStream(); $b = [Text.Encoding]::UTF8.GetBytes($req); $s.Write($b, 0, $b.Length)
Start-Sleep -Milliseconds 300; $buf = New-Object byte[] 65536; $n = $s.Read($buf, 0, $buf.Length)
[Text.Encoding]::UTF8.GetString($buf, 0, $n); $c.Close()
```

## 回滚方式

V3.2 文件未覆盖，直接双击 `launch_serial_terminal_v3_2.cmd` 即可回退。
