# AI_INTERFACE.md — AI 接入指南（RK3562 串口收发工具 V5）

本文件供 AI 智能体（Claude/Codex 等）与自动化脚本快速接入本工具。两种接入方式：**无头捕获**（AI 独占串口，适合自动化闭环）与**控制通道**（与 GUI 共存，人机协作）。

---

## 1. 无头捕获模式（AI 独占串口）

不开界面，抓取串口数据并输出结构化 JSONL，通过退出码判定结果。

### 基本用法

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File <脚本目录>\serial_terminal_v6.ps1 -Headless `
    -Port COM6 -Baud 1500000 -Protocol FireWater -Seconds 10 `
    -Out <输出文件>.jsonl -HitPattern "panic|Oops|HardFault"
```

### 参数一览

| 参数 | 默认 | 说明 |
|------|------|------|
| `-Headless` | — | 进入无头模式（必须） |
| `-Port COM6` | 自动选第一个 | 串口名；回放模式不需要 |
| `-Baud 1500000` | 1500000 | 波特率 |
| `-Protocol X` | FireWater | FireWater / JustFloat / RawData |
| `-Seconds 10` | 10 | 捕获时长（秒） |
| `-Out path.jsonl` | logs\headless_时间.jsonl | JSONL 输出路径 |
| `-HitPattern 正则` | — | 命中即退出码 2（忽略大小写） |
| `-TriggerFile x.json` | — | 事件触发捕获配置（见下） |
| `-ExpectFile x.json` | — | 期望测试步骤（见下） |
| `-FlashCommand "命令"` | — | 捕获前执行的烧录/任意命令（见下） |
| `-FlashTimeoutMs 300000` | 300000 | 烧录命令超时 |
| `-ResetMode rts\|dtr\|command` | — | 捕获前复位方式（见下） |
| `-ResetCommand "命令"` | — | ResetMode=command 时的命令 |
| `-RecordFile x.jsonl` | — | 同时录制原始字节流 |
| `-ReplayFile x.jsonl` | — | 离线回放（与 -Port 互斥） |
| `-Dtr` / `-Rts` | off | 打开串口时的 DTR/RTS 初始电平 |

### 退出码

| 码 | 含义 |
|----|------|
| 0 | 正常（期望测试全过或未启用） |
| 1 | 参数错误/运行异常（stderr 有 HEADLESS_ERROR） |
| 2 | `-HitPattern` 命中 |
| 3 | 期望测试有失败步骤 |
| 4 | 复位命令失败 |
| 5 | 烧录命令失败/超时 |

### JSONL 输出格式（每行一个 JSON 对象）

```jsonc
{"dir":"META","port":"COM6","baud":1500000,"protocol":"FireWater","ms":0,...}   // 会话开始
{"dir":"RX","text":"boot line","ms":123,"ch":[1.5,2.5]}   // FireWater 行；解析成功带 ch 通道值；命中 -HitPattern 带 "hit":true
{"dir":"RX","ch":[1.5,2.5],"ms":130}                       // JustFloat 每帧一行
{"dir":"RX","samples":[100,200,...],"count":n,"ms":140}    // RawData 每块一行
{"dir":"FAULT","text":"CFSR:0x...","decoded":"BFSR.PRECISERR 精确数据总线错误；...","ms":150}  // HardFault 自动解码
{"dir":"EVT","name":"kernel_panic","text":"命中行","file":"logs\\events\\...txt","ms":160}     // 触发事件（分段已落盘）
{"dir":"FLASH","text":"烧录命令输出行","ms":170}           // -FlashCommand 的输出
{"dir":"FLASH_RESULT","exit_code":0,"timed_out":false,"ms":180}
{"dir":"RESET","mode":"rts","ms":190}
{"dir":"TX","text":"发送内容","ms":200}                    // 期望测试的发送
{"dir":"TEST","name":"步骤名","send":"...","expect":"...","pass":true,"elapsed_ms":50,"matched":"..."}
{"dir":"TEST_SUMMARY","total":3,"fail":0,"all_pass":true}
{"dir":"DATA","b64":"base64...","n":123,"ms":210}          // -RecordFile 时的原始字节
{"dir":"SUMMARY","rx_bytes":n,"lines":n,"frames":n,"channels":n,"error_hits":n,"test_total":n,"test_fail":n,"trigger_hits":n,"out":"...","ms":220}
```

AI 建议读取方式（PowerShell）：`Get-Content cap.jsonl | ForEach-Object { $_ | ConvertFrom-Json }`，按 `dir` 字段分流处理；`SUMMARY` 行必读。

### 编排：复位 / 烧录 / 捕获 一条命令

三个前置阶段按顺序执行：**复位（可选）→ 烧录（可选）→ 打开串口捕获**。任一前置阶段失败即中止并给出对应退出码，不会进入捕获。

```powershell
# 纯复位 + 抓启动日志（不用烧录时）
... -Headless -Port COM6 -ResetMode dtr -Seconds 8 -HitPattern "panic|Oops"

# 烧录（STM32CubeProgrammer 串口 ISP）+ 复位 + 抓启动验证
... -Headless -Port COM6 -Baud 115200 `
    -FlashCommand "STM32_Programmer_CLI -c port=COM6 br=115200 -w firmware.hex -v -rst" `
    -Seconds 8 -HitPattern "panic|HardFault" -ExpectFile expect.json

# ST-LINK 烧录（不占串口）+ RTS 复位 + 捕获
... -Headless -Port COM6 `
    -FlashCommand "STM32_Programmer_CLI -c port=SWD -w firmware.hex -v -rst" `
    -ResetMode rts -Seconds 8

# 鲁班猫 rkdeveloptool 刷机 + 抓 1.5M 启动日志
... -Headless -Port COM6 -Baud 1500000 `
    -FlashCommand "rkdeveloptool wl 0x0 system.img" `
    -Seconds 15 -HitPattern "Kernel panic|Starting kernel"
```

**复位模式说明**（三种，覆盖常见自动复位电路）：
- `rts` / `dtr`：对目标串口打 150ms 电平脉冲（RTS/DTR → RESET 的板载电路自动复位，极性因板而异，两种都试）。
- `command`：执行任意命令作为"复位"（如 `-ResetCommand "STM32_Programmer_CLI -c port=SWD -rst"`，或 rkdeveloptool 的复位子命令）。
- GUI 连接中直接复位不会断开连接（复位按钮）；无头模式复位用独立短连接（不干扰后续捕获参数）。

### 事件触发捕获配置（-TriggerFile）

```json
[
  { "name": "kernel_panic", "pattern": "Kernel panic", "before_lines": 50, "after_lines": 100 },
  { "name": "assert", "pattern": "Assertion failed", "once": true }
]
```
命中时把前 N 行 + 后 M 行写入 `logs\events\<name>_<时间>.txt`，并在 JSONL 输出 `EVT` 行指向该文件。`once:true` 只捕获第一次。

### 期望测试步骤（-ExpectFile）

```json
[
  { "name": "uboot版本", "send": "version\r", "expect": "U-Boot 20\\d\\d", "timeout_ms": 2000 },
  { "name": "等内核启动", "expect": "Welcome to Buildroot", "timeout_ms": 30000 },
  { "name": "hex发送", "send": "A1 55", "hex": true, "newline": "None" }
]
```
| 字段 | 说明 |
|------|------|
| `send` | 发送内容；`hex:true` 时按 HEX 解析（如 `"A1 55"`） |
| `newline` | `None`/`CR`/`LF`/`CRLF`，默认 CRLF |
| `expect` | 等待匹配的正则（忽略大小写）；省略则只等待超时 |
| `timeout_ms` | 默认 3000 |

### 回放（-ReplayFile）

`-RecordFile`（或 GUI 录制按钮）产生的 JSONL（`DATA` 行含 base64 字节流）可通过 `-Headless -ReplayFile session.jsonl` 离线重放——走完整的协议解析/触发/输出管线，可换 `-Protocol`/`-HitPattern`/`-TriggerFile` 反复分析，不占板子。回放模式下 `-FlashCommand`/`-ResetMode`/`-ExpectFile` 不可用。

---

## 2. AI 控制通道（GUI 共存模式）

GUI 启动自动监听 `tcp://127.0.0.1:9101`（换端口 `-ControlPort`，关闭 `-NoControlPort`）。协议：TCP 连接后每发一行 JSON 请求（`\n` 结尾）回一行 JSON 响应。适合人看界面、AI 同时读数/发命令的协作场景。

### 命令速查

| 请求 | 响应要点 | 说明 |
|------|----------|------|
| `{"cmd":"ping"}` | `ok, tool` | 存活探测 |
| `{"cmd":"status"}` | `connected, port, baud, protocol, frame_total, frame_count, channels, ui_skip, pending_display, sim_on, recording, hit_pattern` | 当前状态快照 |
| `{"cmd":"list_ports"}` | `ports[]` | 可用串口 |
| `{"cmd":"send","text":"version\r","hex":false,"newline":"CRLF"}` | `ok` | 发送（进 TX 记录与日志） |
| `{"cmd":"read_lines","count":50,"filter":"正则"}` | `lines[{t,dir,text,hit}]` | 读最近记录（新→旧方向按时间升序）；`hit` 为命中行 |
| `{"cmd":"wave_snapshot","count":500}` | `protocol, channels[{name,values[]}]` | 最近 N 帧波形数值 |
| `{"cmd":"set_protocol","protocol":"JustFloat"}` | `protocol` | 切换协议（清空波形缓冲） |
| `{"cmd":"wave_clear"}` | `ok` | 清空波形缓冲 |
| `{"cmd":"wave_sim","on":true}` | `sim_on` | 开/关内置模拟数据源（无需真实硬件即可测试波形链路） |
| `{"cmd":"record","on":true}` | `recording, file` | 开始/停止录制 |
| `{"cmd":"expect","steps":[...]}` | `total, fail, all_pass, steps[]` | 期望测试（执行期间 GUI 短暂阻塞） |
| `{"cmd":"set_hit_pattern","pattern":"panic\|HardFault"}` | `pattern` | 设置/清除（空串）RX 命中高亮 |
| `{"cmd":"reset","mode":"rts\|dtr\|command","command":"..."}` | `mode` 或 `exit_code,lines` | 复位板卡（command 模式自动断开/重连串口） |
| `{"cmd":"flash","command":"...","timeout_ms":300000}` | `success, exit_code, lines` | 执行烧录/命令，成功自动重连串口 |

错误响应统一为 `{"ok":false,"error":"..."}`。

### 调用示例（PowerShell 片段）

```powershell
function Invoke-V5Control {
    param([string]$Json)
    $c = [Net.Sockets.TcpClient]::new('127.0.0.1', 9101)
    $s = $c.GetStream()
    $b = [Text.Encoding]::UTF8.GetBytes($Json + "`n")
    $s.Write($b, 0, $b.Length)
    $buf = New-Object byte[] 262144
    $n = $s.Read($buf, 0, $buf.Length)
    $c.Close()
    [Text.Encoding]::UTF8.GetString($buf, 0, $n) | ConvertFrom-Json
}
Invoke-V5Control '{"cmd":"status"}'
Invoke-V5Control '{"cmd":"send","text":"version\r"}'
```

---

## 3. AI 典型工作流

1. **刷机/烧录验证闭环**：`-Headless -FlashCommand ... -HitPattern "panic|HardFault" -ExpectFile expect.json` → 看退出码 + SUMMARY 行。
2. **偶发故障抓现场**：常开 GUI + 控制通道，AI 设 `set_hit_pattern`，出错后用触发分段文件 + `read_lines` 取上下文。
3. **波形分析**：`set_protocol` → `wave_snapshot` 取数值 → 离线算频率/RMS；或 `record` 录制后 `-ReplayFile` 反复解析。
4. **人机协作**：人操作 GUI，AI 通过控制通道读 `status`/`read_lines`/`wave_snapshot` 辅助判断。

注意：AI 执行本工具命令前请确认串口号与波特率；`flash`/`reset` 的 command 模式会执行任意命令行，调用方需自行保证命令安全。
