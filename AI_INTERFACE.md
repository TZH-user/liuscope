# AI_INTERFACE.md — LiuScope（柳镜，原名 serialscope-qt，串口调试助手 V6 Qt）AI 接入指南

本文件供 AI 智能体（Claude/Codex 等）与自动化脚本快速接入本工具。两种接入方式：**AI 控制通道**（与 GUI 共存，人机协作，默认开启，推荐）与**无头捕获**（AI 独占串口，适合自动化闭环）。

---

## 1. AI 控制通道（GUI 共存模式，默认开启）

GUI 启动自动监听 `tcp://127.0.0.1:9101`。协议：TCP 连接后每发一行 JSON 请求（`\n` 结尾），回一行 JSON 响应（UTF-8）；一条连接可连续发多条请求。

- 命令在 GUI 主线程事件循环执行——GUI 弹模态对话框期间响应会排队，不会丢失。
- 错误响应统一为 `{"ok":false,"error":"…"}`；未知命令报错并提示用 help。
- 首次接入建议先发 `{"cmd":"help"}` 拿结构化命令表（命令名/参数/说明/示例），本文档与 help 同源。

### 1.1 命令总表

| 命令 | 参数 | 说明 |
|------|------|------|
| `ping` | — | 存活探测 |
| `help` | — | 结构化命令表 + follow 语义 |
| `status` | — | 连接/协议/帧数/录制状态快照 |
| `list_ports` | — | 可用串口列表 |
| `send` | `text`（hex=true 时为 HEX 串）、`hex=false`、`newline="CRLF"` | 发送数据（进 TX 记录与日志；未连接报错） |
| `read_lines` | `count=20`、`filter=""`（正则）、`follow=false`、`interval=0.5`、`max_lines=200` | 读最近记录（时间升序）；follow=true 进入持续推送（见 1.3） |
| `stop` | — | 退出 follow 推送（也可发一行纯文本 `stop`，或直接断开） |
| `wave_snapshot` | `count=500` | 各通道最新 N 点波形数值 + 元信息 |
| `spectrum_snapshot` | `count=0`（0=全部，>0 截高频端最新 N 点） | FFT 频点+dB、主频、频谱参数、瀑布最近帧（每频点 RGB）与累计采集时长 |
| `xy_snapshot` | `count=512` | XY 相位图各窗口最新轨迹与窗口参数 |
| `modbus_recent` | `count=20` | 最近 Modbus 事务摘要 |
| `stm32_status` | — | STM32 页状态（页未挂接/未连接返回明确提示） |
| `settings_list` | — | 关键设置一览 |
| `settings_get` | `key` | 读单个设置值 |
| `settings_set` | `key`、`value` | 改设置并同步 UI（见 1.4）；返回改前/改后值 |
| `set_protocol` | `protocol` | 切换协议（清空波形缓冲；= settings_set key=protocol，向后兼容保留） |
| `wave_clear` | — | 清空波形缓冲 |
| `wave_sim` | `on` | 开/关内置模拟数据源（无需真实硬件即可测试链路） |
| `record` | `on` | 会话录制（原始字节流 JSONL，可回放） |

### 1.2 示例 JSON 行（可直接发送）

```jsonc
{"cmd":"ping"}
{"cmd":"help"}
{"cmd":"status"}
{"cmd":"list_ports"}
{"cmd":"send","text":"version\r","newline":"LF"}
{"cmd":"send","text":"A1 55","hex":true,"newline":"None"}
{"cmd":"read_lines","count":50,"filter":"err"}
{"cmd":"read_lines","count":20,"follow":true,"interval":0.5,"max_lines":200}
{"cmd":"stop"}
{"cmd":"wave_snapshot","count":100}
{"cmd":"spectrum_snapshot","count":512}
{"cmd":"xy_snapshot","count":256}
{"cmd":"modbus_recent","count":10}
{"cmd":"stm32_status"}
{"cmd":"settings_list"}
{"cmd":"settings_get","key":"baud"}
{"cmd":"settings_set","key":"baud","value":921600}
{"cmd":"settings_set","key":"protocol","value":"JustFloat"}
{"cmd":"set_protocol","protocol":"JustFloat"}
{"cmd":"wave_clear"}
{"cmd":"wave_sim","on":true}
{"cmd":"record","on":true}
```

### 1.3 follow 连续输出模式（read_lines follow=true）

| 项 | 口径 |
|----|------|
| 进入 | `{"cmd":"read_lines","count":20,"follow":true,"interval":0.5,"max_lines":200}` |
| 首次响应 | 含最新 `count` 行快照（时间升序）+ `cursor` 游标 + 生效的 `interval`/`max_lines`/`filter` |
| 推送 | 仅在出现新行时推送 `{"ok":true,"follow":true,"lines":[…],"cursor":N}`（时间升序） |
| 节流间隔 | `interval` 默认 0.5s，范围 0.05~60s（可按请求覆盖） |
| 单次上限 | `max_lines` 默认 200，上限 2000；超出部分下一轮继续推 |
| 过滤 | 请求里的 `filter` 正则对快照与推送同样生效 |
| 退出 | 发 `{"cmd":"stop"}` 或一行纯文本 `stop`，或直接断开连接；重复 follow 请求=按新参数重启 |

### 1.4 settings_set 可写设置键（发出后 UI 前端同步跟随）

| key | 类型 | 约束与行为 |
|-----|------|-----------|
| `baud` | int | **已连接时拒绝**（提示先断开；连接期间 UI 本就锁定）；改后波特率下拉文本与设置记忆同步 |
| `port` | str | 同上拒绝已连接；值须匹配可用串口（先 `list_ports`）；改后串口下拉跟随 |
| `protocol` | str | FireWater/JustFloat/RawData（切换会清空波形缓冲） |
| `theme` | str | `dark`/`light`（深色/浅色）；改后全页即时换肤并写设置记忆 |
| `points` | int | 显示点数（绘图区横向点数，钳制 50..200000）；改后波形页「显示点数」下拉跟随 |
| `fill` | bool | 曲线填充开关；改后设置页复选框、波形页填充与设置键 `wave/fill_under` 三者一致 |
| `paused` | bool | true=暂停 false=恢复；改后波形页「暂停/继续」按钮与解析状态一致（RX 显示不受影响）。注意：paused 只冻结**波形页**显示，频谱管线继续由频谱页自身「冻结」按钮单独控制（响应里的 `frozen` 字段即指频谱冻结按钮状态） |

响应统一为 `{"ok":true,"key":"…","before":…,"after":…}`（含改前/改后值）。设置键有中英文别名（如 `波特率`→`baud`、`显示点数`→`points`、`曲线填充`→`fill`、`暂停`→`paused`）。全部走主窗统一 apply 入口，下拉/复选框等 UI 控件与内部状态一致。

### 1.5 数据命令返回结构要点

- `status` → `connected, port, baud, protocol, frame_total, frame_count, channels, rx_entries, recording, sim_on`
- `read_lines` → `lines[{t, dir, text}]`（时间升序）+ follow 时的 `cursor`
- `wave_snapshot` → `protocol, frame_total, frame_count, paused, window_frames, count, channels[{name, enabled, values[]}]`
- `spectrum_snapshot` → `empty, frozen, acquisition_seconds, peak_hold, average, waterfall_on, waterfall_rows, params{sample_rate_hz, fft_points, window, df_hz, rbw_enbw_hz, channel, channel_name, ref_db, div_db}, peak{freq_hz, db}, total_bins, count, freq_start_hz, freqs_hz[], db[], waterfall_latest[]`（dB 谱频率升序；`count>0` 截高频端；`waterfall_latest` 为瀑布最新帧每频点 `[R,G,B]` 0..255，与瀑布图渲染同源，同口径截取）
- `xy_snapshot` → `xy_on, window_count, windows[{window, x_channel, y_channel, x_name, y_name, span, divs, draw_mode, persist, x[], y[]}]`
- `modbus_recent` → `transactions[{t, req_hex, resp_hex, resp_bytes, ok}]`（时间升序；ok=false 表示无响应/超时）
- `stm32_status` → `available` + 页面状态（页未挂接/未连接时给明确 `note`，不报错断链）
- `settings_list` / `settings_get` → `settings{...}` / `{key, value}`（键与 1.4 一致，另含只读项 `connected/auto_y/filter/filter_param/rx_format/tx_format/newline/sim_on/recording`）

### 1.6 调用示例（Python / PowerShell）

```python
import json, socket

def rpc(request: dict, port: int = 9101) -> dict:
    with socket.create_connection(('127.0.0.1', port), timeout=3) as s:
        s.sendall((json.dumps(request, ensure_ascii=False) + '\n').encode('utf-8'))
        buf = b''
        while b'\n' not in buf:
            buf += s.recv(65536)
        return json.loads(buf.split(b'\n')[0])

print(rpc({'cmd': 'status'}))
print(rpc({'cmd': 'settings_set', 'key': 'baud', 'value': 921600}))
```

```powershell
function Invoke-QtControl {
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
Invoke-QtControl '{"cmd":"status"}'
Invoke-QtControl '{"cmd":"spectrum_snapshot","count":256}'
```

---

## 2. 无头捕获模式（AI 独占串口）

不开界面，抓取串口数据并输出结构化 JSONL，通过退出码判定结果。

### 基本用法

```powershell
python qt/headless_main.py --port COM6 --baud 1500000 --protocol FireWater `
    --duration 10 --out cap.jsonl --error-regex "panic|Oops|HardFault"
```

### 参数一览（python qt/headless_main.py --help）

| 参数 | 默认 | 说明 |
|------|------|------|
| `--port COM6` | 自动选第一个 | 串口名；回放模式不需要 |
| `--baud 1500000` | 115200 | 波特率 |
| `--protocol X` | FireWater | FireWater / JustFloat / RawData |
| `--duration 10` | 10 | 捕获时长（秒） |
| `--out path.jsonl` | logs\headless_时间.jsonl | JSONL 输出路径 |
| `--error-regex 正则` | — | 命中即退出码 2（忽略大小写） |
| `--trigger-file x.json` | — | 事件触发捕获配置（见下） |
| `--expect-file x.json` | — | 期望测试步骤（见下） |
| `--flash-command "命令"` | — | 捕获前执行的烧录/任意命令 |
| `--flash-timeout-ms 300000` | 300000 | 烧录命令超时 |
| `--reset-mode rts\|dtr\|command` | — | 捕获前复位方式 |
| `--reset-command "命令"` | — | ResetMode=command 时的命令 |
| `--record` | — | 同时把原始字节流录进 JSONL（DATA 行） |
| `--replay x.jsonl` | — | 离线回放（与 --port 互斥） |
| `--dtr` / `--rts` | off | 打开串口时的 DTR/RTS 初始电平 |
| `--log-dir logs` | logs | 日志目录 |

### 退出码

| 码 | 含义 |
|----|------|
| 0 | 正常（期望测试全过或未启用） |
| 2 | `--error-regex` 命中（参数错误也走 2，stderr 有 `参数错误`） |
| 3 | 期望测试有失败步骤 |
| 4 | 复位命令失败 |
| 5 | 烧录命令失败/超时 |

### JSONL 输出格式（每行一个 JSON 对象）

```jsonc
{"dir":"META","port":"COM6","baud":1500000,"protocol":"FireWater","ms":0,...}   // 会话开始
{"dir":"RX","t":"…","text":"boot line","ms":123,"ch":[1.5,2.5]}   // FireWater 行；解析成功带 ch 通道值；命中 --error-regex 带 "hit":true
{"dir":"RX","ch":[1.5,2.5],"ms":130}                              // JustFloat 每帧一行
{"dir":"RX","samples":[100,200,...],"count":n,"ms":140}           // RawData 每块一行
{"dir":"FAULT","text":"CFSR:0x...","decoded":"BFSR.PRECISERR 精确数据总线错误；...","ms":150}  // HardFault 自动解码
{"dir":"EVT","name":"kernel_panic","text":"命中行","file":"logs\\events\\...txt","ms":160}     // 触发事件（分段已落盘）
{"dir":"FLASH","text":"烧录命令输出行","ms":170}                  // --flash-command 的输出
{"dir":"FLASH_RESULT","exit_code":0,"timed_out":false,"ms":180}
{"dir":"RESET","mode":"rts","ms":190}
{"dir":"TX","text":"发送内容","ms":200}                           // 期望测试的发送
{"dir":"TEST","name":"步骤名","send":"...","expect":"...","pass":true,"elapsed_ms":50,"matched":"..."}
{"dir":"TEST_SUMMARY","total":3,"fail":0,"all_pass":true}
{"dir":"DATA","b64":"base64...","n":123,"ms":210}                 // --record 时的原始字节
{"dir":"SUMMARY","rx_bytes":n,"lines":n,"frames":n,"channels":n,"error_hits":n,"test_total":n,"test_fail":n,"trigger_hits":n,"out":"...","ms":220}
```

AI 建议读取方式（PowerShell）：`Get-Content cap.jsonl | ForEach-Object { $_ | ConvertFrom-Json }`，按 `dir` 字段分流处理；`SUMMARY` 行必读。

### 事件触发捕获配置（--trigger-file）

```json
[
  { "name": "kernel_panic", "pattern": "Kernel panic", "before_lines": 50, "after_lines": 100 },
  { "name": "assert", "pattern": "Assertion failed", "once": true }
]
```
命中时把前 N 行 + 后 M 行写入 `logs\events\<name>_<时间>.txt`，并在 JSONL 输出 `EVT` 行指向该文件。`once:true` 只捕获第一次。

### 期望测试步骤（--expect-file）

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

### 回放（--replay）

GUI 录制（或 `--record`）产生的 JSONL（`DATA` 行含 base64 字节流）可通过 `python qt/headless_main.py --replay session.jsonl` 离线重放——走完整的协议解析/触发/输出管线，可换 `--protocol`/`--error-regex`/`--trigger-file` 反复分析，不占板子。回放模式下 `--flash-command`/`--reset-mode`/`--expect-file` 不可用。

---

## 3. AI 典型工作流

1. **刷机/烧录验证闭环**：`--flash-command ... --error-regex "panic|HardFault" --expect-file expect.json` → 看退出码 + SUMMARY 行。
2. **偶发故障抓现场**：常开 GUI + 控制通道，AI 用 `read_lines`（可 `follow` 持续盯屏 + `filter` 正则）取上下文；`modbus_recent` 复盘最近事务。
3. **波形/频谱分析**：`settings_set` 切协议/波特率（UI 同步跟随）→ `wave_snapshot`/`spectrum_snapshot` 取数值 → 离线算频率/RMS；或 `record` 录制后 `--replay` 反复解析。频谱数据的导出走频谱页 GUI 按钮（「导出PNG」=带参数标注的拼图、「导出CSV」=最新一帧或最近 N 秒；AI 控制通道暂无对应命令，数据面用 `spectrum_snapshot` 即可离线重算）。
4. **人机协作**：人操作 GUI，AI 通过控制通道读 `status`/`read_lines`/`spectrum_snapshot` 辅助判断，用 `settings_set`/`wave_sim`/`record` 驱动界面（改后控件与内部状态一致，`before/after` 可核对）。

注意：AI 执行本工具命令前请确认串口号与波特率；无头模式的 `--flash-command`/`--reset-mode command` 会执行任意命令行，调用方需自行保证命令安全。
