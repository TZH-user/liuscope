# LiuScope（柳镜）AI 控制通道 · 端到端验收考题

- 考题版本：2026-10-26（出题官编制；v2 增补第八部分「导出与画面取证」3 题，原题分值微调，总分不变）
- 总分：100 分，共 19 题
- 做题者所需材料：本文件 + 软件本身 + 软件根目录随附文档（`AI_INTERFACE.md` / `README.md` / `HANDOFF.md`）。禁止为作答而阅读软件源码。
- 题目可乱序作答，但每题的「前置条件」需自行满足；做完硬件题后请把固件用 `MENU` 命令收回菜单、把软件侧波特率恢复 115200，避免影响其他题。

---

## 【环境与启动说明】（做题者直接照此操作）

### 软件与 AI 控制通道

- 软件根目录：`F:\软件仓库_新\开发软件\串口调试助手-V6`（含中文与空格风险字符，shell 命令务必加引号）。
- 启动：在该目录下执行 `python qt/main.py`，弹出真实 GUI 窗口（窗口标题为 LiuScope）。
- GUI 启动即默认监听 AI 控制通道 `tcp://127.0.0.1:9101`。协议：TCP 连接后每发一行 JSON 请求（`\n` 结尾），回一行 JSON 响应（UTF-8）；一条连接可连续发多条请求。错误响应统一为 `{"ok":false,"error":"…"}`。
- 若 9101 连不上：可能是被其他实例占用（界面会有提示），先结束占用进程再重启软件。
- `AI_INTERFACE.md` §1.6 附有现成的 Python `rpc()` 函数与 PowerShell 示例，可直接抄用；也允许自写任意临时脚本（建议放在本目录 `_verify\` 下）。

### GUI 操作与截图取证手段

- **截图**：统一用 PIL 的 `PIL.ImageGrab` 全屏/窗口截屏（本机**未安装 pyautogui**，不要在脚本中依赖它）；截图保存到 `_verify\evidence\`，文件名注明题号。
- **GUI 点击/键盘**：可用本机 **computer-use 技能**——先阅读 `C:\Users\18152\.zcode\cli\plugins\cache\zcode-plugins-official\computer-use\0.6.5\skills\computer-use\SKILL.md`，按其说明通过 `mcp__node_repl__js` 执行（每次调用先做该技能要求的初始化引导，再用 get_app_state 观察并点击目标控件）；也可以直接人工操作 GUI——「人机协作」本就是本工具的设计场景。
- 涉及「GUI 前端跟随」的题目，截图必须能看清对应控件的变化前后（必要时先把相关页面切到前台）。
- 注意：文件保存对话框等模态窗口打开期间，AI 通道请求会被排队而不丢失（`AI_INTERFACE.md` §1 已声明），导出类题目可放心保持连接。

### 台架硬件（真机题专用）

- `COM9` = USB-SERIAL CH340，接 STM32H743 实验板，链路固定 **115200 8N1**。做题前确认 COM9 未被其他程序占用（可用 `list_ports` 查看存在性）。
- 固件 V4 共 6 种模式，串口命令（以**文本发送、换行结尾**）：`MODE 0..6` / `KEY 1..4` / `MENU` / `INFO` / `BAUD n` / `BAUD?`，各模式通用。固件模式语义：

| MODE | 名称 | 语义 |
|---|---|---|
| 0 | 菜单等待 | 设备空闲态，显示模式菜单 |
| 1 | 透传回环 | 逐字节回显；空闲约 1s 心跳 `BOOT_OK USART1 115200 8N1` |
| 2 | FireWater 波形 | 10Hz × 4 通道，每行 4 个逗号分隔整数（-1500..1500） |
| 3 | JustFloat 波形 | 50Hz × 8 通道（8×float32 + 帧尾 00 00 80 7F） |
| 4 | 日志/故障流 | 约 200ms 一条，行内含 `[HH:MM:SS.mmm]` 时间戳与 `E/`、`W/` 等级别前缀；约每 5s 出现一次 `kernel panic!` 与 `CFSR: 0x…`（HardFault 序列） |
| 5 | Modbus RTU 从站 | 按需应答（约定见下） |
| 6 | ADC 样本流 | 约 1kHz × 1 通道，每行一个整数值 |

- **MODE 5 约定**：从站地址 1；保持寄存器 0..3 出厂值 1000~1003（即 HEX `03 E8 03 E9 03 EA 03 EB`，仅在本轮未写过时成立）；支持功能码 03/06/16；非法功能码（如 0x41）返回异常响应 `01 C1 01`；错误 CRC 静默不应答。
- **切 MODE 5 的操作**：用 AI 通道 `send` 发文本 `MODE 5`，随后用 `read_lines` 确认出现 `=== MODE 5`；若 2 秒内未确认成功，先发 `MENU` 再发 `MODE 5` 重试。测完发 `MENU` 回菜单。
- **警示**：绝不要用 send 向固件发 `BAUD n`——固件会真的切换波特率导致链路漂移；若误发，立即发 `BAUD 115200` 恢复。
- **连接方式**：AI 通道命令表中没有串口连接/断开命令（用 `help` 可自行验证）。串口的连接/断开、Modbus 事务的发起、XY 相位图的开关等 GUI 操作，请直接操作软件窗口完成——「人机协作」本就是该工具的设计场景。

### 模拟数据源

- 软件内置模拟数据源：`wave_sim` 命令开关。不接硬件也能产生波形/频谱/XY 数据，适合先行完成数据读取类题目。

### 证据与判分规范

- 每题需提交：关键 JSON 请求/响应原文（可截取）+ 涉及 UI 的截图。拿不出证据的验收项记 0 分。
- 「文档声称」类验收项若与实测不符，如实记录「文档声称 / 实测 / 结论」，不要为了凑过而改口径——发现不一致本身就是验收价值。

---

## 第一部分 存活与自描述（12 分）

### Q1 存活探测与行协议基础（4 分）

- **目的**：验证通道存活、JSON 行协议的正确性与多请求复用。
- **前置条件**：GUI 已启动，9101 可连。
- **步骤**：
  1. 建立 TCP 连接，发送 `{"cmd":"ping"}`，接收响应。
  2. 在**同一条连接**上一次性连续发送 `{"cmd":"ping"}`、`{"cmd":"help"}`、`{"cmd":"status"}` 三行，随后连续读取三条响应。
- **验收标准**：
  - ①（2 分）`ping` 响应为单行合法 JSON 且 `ok:true`（响应应为一次请求一行，不粘连后续内容）。
  - ②（2 分）同连接三连发能按序收到三条一一对应的合法 JSON 响应（顺序不串、不丢、不混行）。

### Q2 help 自描述性与「盲上手」能力（8 分）

- **目的**：验收「一个只看 help 输出的 AI 能否独立上手」——help 是否结构化到足以替代文档。
- **前置条件**：GUI 已启动。**本题作答期间不得翻阅 `AI_INTERFACE.md` §1.1/§1.2**（其余题目不受限）。
- **步骤**：
  1. 发 `{"cmd":"help"}`，检查返回的命令表结构与覆盖面。
  2. **盲上手测试**：仅凭 help 输出中的参数说明与示例，为以下三个命令各构造一条请求并首试（构造前不允许看文档示例）：
     - `spectrum_snapshot`（带 `count` 参数）；
     - `settings_set`（把 `points` 改为一个新值）；
     - `read_lines`（带 `filter` 正则与 `count`，**不要**带 follow）。
- **验收标准**：
  - ①（2 分）help 返回结构化命令表，命令名与 `AI_INTERFACE.md` §1.1 表格的 19 条命令一一对应（列出差异，如有）。
  - ②（4 分）每条命令的条目同时具备三要素：参数说明（参数名，及默认值或类型）、功能说明、可照抄的示例。缺任一要素的命令，每条扣 0.5 分，扣完为止。
  - ③（2 分）三个盲构造请求**首试即 `ok:true`**（每个成功得 0.5 分，三个全部首试成功再得 0.5 分；若因 help 信息不足而试错，记录试错次数与 help 中缺失的信息）。若误入 follow 推送，用 `stop` 退出后再继续。
  - 附：提交 help 原始输出全文作为证据。

---

## 第二部分 数据读取（22 分）

### Q3 read_lines 快照：count / filter / 时间升序 / 字段（5 分）

- **目的**：验收 `read_lines` 快照语义与正则过滤的可消费性（真机数据）。
- **前置条件**：GUI 已连接 COM9@115200（协议不限）。
- **步骤**：
  1. 用 AI 通道 `send` 发文本 `MODE 4`（LF 换行），等待约 3 秒。
  2. `read_lines`（`count=20`），检查返回结构与内容。
  3. `read_lines`（`count=10`，`filter="E/"`），检查过滤效果。
  4. 继续等待至累计 ≥6 秒后，`read_lines`（`count=20`，`filter="panic|CFSR"`）。
  5. 测完发 `MENU` 收尾。
- **验收标准**：
  - ①（2 分）步骤 2 返回 `lines` 数组，每条含 `t`/`dir`/`text` 三字段；`dir` 中同时出现 RX（固件日志）与 TX（刚发的 MODE 4）；`t` 按时间升序（非降）；行数 ≤20。
  - ②（1.5 分）RX 行 `text` 符合 MODE 4 语义：含 `[HH:MM:SS.mmm]` 时间戳格式，且 3 秒窗口内 RX 行数 ≥10（对应约 200ms/条）。
  - ③（1.5 分）步骤 3 返回的每条 `text` 均匹配正则 `E/`；步骤 4 能命中 `kernel panic` 或 `CFSR` 行（MODE 4 约每 5s 一次 HardFault）。

### Q4 follow 连续推送与 stop 退出（7 分）

- **目的**：验收 `read_lines follow=true` 的推送语义（cursor、节流、仅新行、退出方式）。
- **前置条件**：GUI 已连接 COM9@115200，固件处于 MODE 4（或 MODE 2 波形流）持续出数据。建议用**独立连接**做本题。
- **步骤**：
  1. 发 `{"cmd":"read_lines","count":5,"follow":true,"interval":0.3,"max_lines":50}`，观察首响应；随后在同一连接上持续接收约 5 秒。
  2. 发 `{"cmd":"stop"}`，再在同一连接发 `{"cmd":"status"}`。
  3. 重新发起 follow（改 `interval=0.2`），确认能收到推送后，发一行**纯文本** `stop`（非 JSON），继续接收约 2 秒验证推送确实停止。
  4. 再发起一次带 `filter` 正则的 follow，验证过滤在推送中同样生效。
- **验收标准**：
  - ①（2 分）首响应含 ≤5 行快照 + `cursor` 游标 + 生效参数回显（`interval=0.3`、`max_lines=50`、`filter`）。
  - ②（2 分）持续期收到 ≥2 次推送，格式为 `{"ok":true,"follow":true,"lines":[…],"cursor":N}`；推送仅含新行（无重复行）、时间升序、`cursor` 单调递增。
  - ③（1 分）`stop` 后 follow 终止，且同一连接发 `status` 得到正常应答（通道恢复常规问答）。
  - ④（1 分）重复 follow 按新参数重启（interval=0.2 生效并回显）；纯文本 `stop` 同样能退出推送。
  - ⑤（1 分）带 `filter` 的 follow：快照与推送中的行全部匹配正则。

### Q5 wave_snapshot / xy_snapshot 字段可消费性（4 分）

- **目的**：验收波形与 XY 快照「不查源码就能消费」——仅凭返回 JSON 即可读出通道数、窗口数、点数。
- **前置条件**：`wave_sim` 开启（`{"cmd":"wave_sim","on":true}`），无需硬件。
- **步骤**：
  1. `status` 记录 `channels`。
  2. `wave_snapshot`（`count=200`）；隔 1 秒再取一次。
  3. `xy_snapshot`（`count=256`）；若返回 `xy_on:false` 或 `windows` 为空，先在 GUI 上开启 XY 相位图（1 个窗口即可），记录所需的人工步骤，再重取。
- **验收标准**：
  - ①（1.5 分）`wave_snapshot`：`channels` 数组长度 == `status.channels`；每通道含 `name`/`enabled`/`values`；`values` 长度在 (0, 200] 区间且全为有限数值；两次调用间数据有更新（非同一份静态数组）。
  - ②（1.5 分）`xy_snapshot`：开启后 `windows` 非空，每窗字段齐全（`window`/`x_channel`/`y_channel`/`x_name`/`y_name`/`span`/`divs`/`draw_mode`/`persist`），`x[]` 与 `y[]` 等长且非空。
  - ③（1 分）不查任何源码，仅凭返回 JSON 在报告中写出三个数字：当前通道数、XY 窗口数、每窗数据点数，并说明取自哪个字段。
  - 附注：AI 通道没有 XY 开关命令且文档也未声称有，「开启 XY 需人工 GUI 操作」仅记录为观察项，不计失分。

### Q6 spectrum_snapshot 主频复算与参数自洽（6 分）

- **目的**：验收频谱快照的可消费性：能从 `freqs_hz[]/db[]` 独立复算主频，参数自洽，并在真机信号源上对出已知三峰。
- **前置条件**：
  - 首选（真机）：GUI 已连接 COM9@115200；`settings_set` 把协议切为 FireWater；`send` 发 `MODE 6`（ADC 1kHz 单通道流）。
  - 兜底（无真机）：`wave_sim` 开启（只能拿「自洽」部分分）。
- **步骤**：
  1. `spectrum_snapshot`（`count=0`，即全部频点），保存完整 JSON。
  2. 用脚本对 `freqs_hz[]` 与 `db[]` 独立求幅度最大点，与 `peak` 字段比对；用 `params` 里的 `sample_rate_hz`/`fft_points` 与 `df_hz` 互验。
  3. 真机路径：掐掉 DC 附近频段（如 <10Hz）后找出三个最显著峰，与台架已知值 50 / 120 / 300 Hz 比对。
  4. 再取一次 `spectrum_snapshot`（`count=512`），观察 `freq_start_hz` 与数组长度变化（count>0 截高频端）。
- **验收标准**：
  - ①（1.5 分）返回含文档所列关键字段：`params{sample_rate_hz, fft_points, window, df_hz, …}`、`peak{freq_hz, db}`、`freqs_hz[]`、`db[]`；两数组等长且频率升序。
  - ②（1.5 分）独立复算的 argmax 主频与 `peak.freq_hz` 一致（偏差 ≤1 个频点间隔）。
  - ③（1 分）参数自洽：`df_hz ≈ sample_rate_hz / fft_points`（偏差 ≤5%）。
  - ④（2 分，仅真机路径可得）三峰落在 50 / 120 / 300 Hz ±10 Hz 内（三峰齐得满分，命中两峰得 1 分，一峰及以下不得分）。若峰整体偏移（如等比例缩放），如实记录实测频率并判本项失分——这通常意味着采样率口径有问题。
  - 附注：`acquisition_seconds` 随时间增长、`count=512` 时 `freq_start_hz` 右移（取高频端）作为附加观察记录，不占分。

---

## 第三部分 控制与 UI 同步（16 分）

### Q7 settings_set 主题换肤——GUI 全页跟随（4 分）

- **目的**：验收 `settings_set` 修改后 GUI 前端真的跟随（截图证据）。
- **前置条件**：GUI 已启动，初始主题为深色（若不是，先切回 dark 再开始）。
- **步骤**：
  1. 截一张当前 GUI 全窗截图（深色基线）。
  2. `{"cmd":"settings_set","key":"theme","value":"light"}`。
  3. 分别把**设置页**与**波形页**切到前台，各截一张；`settings_get` 查 `theme`。
  4. `settings_set` 把主题改回 `dark`，再截一张确认恢复。
- **验收标准**：
  - ①（1 分）响应 `ok:true` 且 `before`/`after` 正确（dark→light）。
  - ②（2 分）截图对比：设置页与波形页整体配色由深变浅（两页各 1 分；仅个别控件变色不算全页换肤）。
  - ③（1 分）`settings_get` 返回 `light` 与实际一致，且改回 dark 后恢复。
  - 附注：README 已知限制——终端页**已落显的历史日志行**保留切换当时配色，仅新行随新主题着色；此现象不算失败，但需在报告中注明观察到与否。

### Q8 settings_set 波特率：未连接改值跟随 / 已连接拒绝（4 分）

- **目的**：验收波特率键的两种分支：未连接可改且 UI 下拉跟随；已连接拒绝且报错清晰。
- **前置条件**：GUI 已启动且**未连接**串口。
- **步骤**：
  1. `{"cmd":"settings_set","key":"baud","value":921600}`；截终端页波特率下拉框；`settings_get` 复核。
  2. 先把软件侧波特率用 `settings_set` 改回 115200，再在 GUI 终端页连接 COM9（固件在 115200）。
  3. 已连接状态下 `{"cmd":"settings_set","key":"baud","value":9600}`；截下拉框确认未变。
- **验收标准**：
  - ①（1.5 分）未连接时 `ok:true` 且 `before`/`after` 正确；下拉框文本变为 921600（截图）；`settings_get baud` 一致。
  - ②（1.5 分）已连接时 `ok:false`，`error` 文本明确指向「已连接/先断开」类原因（能让人不看文档就明白该先做什么）。
  - ③（1 分）拒绝后下拉框保持 115200 不变（截图）。
  - 警示：全程不得用 send 向固件发 `BAUD` 命令。

### Q9 settings_set 协议切换：UI 跟随 + 波形缓冲清空 + set_protocol 等价（4 分）

- **目的**：验收协议切换的前端跟随、缓冲清空行为与旧命令 `set_protocol` 的向后兼容。
- **前置条件**：`wave_sim` 开启（保证有波形数据）。
- **步骤**：
  1. 先取一次 `wave_snapshot` 确认有数据。
  2. `{"cmd":"settings_set","key":"protocol","value":"JustFloat"}`；切到波形页截图（协议显示/下拉）；立即再取 `wave_snapshot`。
  3. `{"cmd":"set_protocol","protocol":"FireWater"}`；对比两种写法的响应与 UI 效果。
- **验收标准**：
  - ①（1.5 分）`settings_set` 后波形页协议显示跟随变为 JustFloat（截图）；响应 `before`/`after` 正确。
  - ②（1.5 分）切换后 `wave_snapshot` 显示缓冲已清空（`values` 为空 / 点数 0，frame 计数归零或按其字段语义重新累计），与文档「切换会清空波形缓冲」一致。
  - ③（1 分）`set_protocol` 与 `settings_set(protocol)` 等价：均 `ok:true`、`after` 一致、UI 同样跟随。

### Q10 settings_list/get 与 GUI 控件交叉一致性 + 中文别名（4 分）

- **目的**：验收设置读写与 GUI 显示三方一致（AI 值 = GUI 控件 = settings 记忆），以及中英文键别名。
- **前置条件**：GUI 已启动，未连接串口（便于改波特率类键）。
- **步骤**：
  1. `settings_list` 全量导出；对照 `AI_INTERFACE.md` §1.4/§1.5 列出可写键与只读项清单。
  2. 至少交叉核对 4 项「改后三处一致」：`points` ↔ 波形页「显示点数」下拉、`fill` ↔ 设置页「曲线填充」复选框、`paused` ↔ 波形页「暂停/继续」按钮文字、`baud` ↔ 终端页下拉。每项：`settings_set` 修改 → 截 GUI 控件 → `settings_get` 复核（`paused` 改 true 后可顺带观察波形页曲线冻结而终端页 RX 计数仍在涨）。
  3. 中文别名：`{"cmd":"settings_set","key":"显示点数","value":1000}`（或 波特率/曲线填充/暂停 任一）。
- **验收标准**：
  - ①（1 分）`settings_list` 覆盖 §1.4 全部 7 个可写键与 §1.5 所列只读项（connected/auto_y/filter/filter_param/rx_format/tx_format/newline/sim_on/recording）；列出多出/缺失项。
  - ②（2 分）4 项交叉核对每项 0.5 分：GUI 控件、`settings_get`、`settings_set.after` 三处一致；至少 1 项有清晰前后对比截图。
  - ③（1 分）中文别名键等效：`ok:true` 且 `after` 正确、GUI 同步。

---

## 第四部分 硬件链路（真 COM9，13 分）

### Q11 真机 send 往返：TX 记录与固件应答（4 分）

- **目的**：验收 `send` → 固件 → `read_lines` 的真机闭环与 TX 记录。
- **前置条件**：GUI 已连接 COM9@115200。
- **步骤**：
  1. `send` 文本 `INFO`（`newline="LF"`），随后 `read_lines`（`count=40`）。
  2. `send` 文本 `MODE 1`（进透传回环），再 `send` 文本 `ECHO-PING-12345`（带 `\r\n` 或用默认 CRLF），随后 `read_lines`（`filter="ECHO-PING"`）。
  3. 前后各取一次 `status`，对比计数。
  4. 测完发 `MENU` 回菜单。
- **验收标准**：
  - ①（1.5 分）`send` 返回 `ok:true`；`read_lines` 中存在 `dir` 为 TX 的发送记录，且存在固件信息块 RX 行（含 `H743 TEST FIXTURE`、`STM32H743XIH6`、`KEY1=PI7` 等字段，允许跨多行）。
  - ②（1.5 分）回环行命中：`filter="ECHO-PING"` 返回的 `text` 内容与所发一致（逐字符）。
  - ③（1 分）`status` 的接收条目/帧计数较连接初值增长。

### Q12 MODE 5 切换 + Modbus 事务与 modbus_recent 复盘（6 分）

- **目的**：验收「AI 切固件模式 + GUI 发起 Modbus 事务 + AI 用 `modbus_recent` 复盘」的人机协作闭环，以及 AI 自主发帧能力。
- **前置条件**：GUI 已连接 COM9@115200；GUI Modbus 页可用。
- **步骤**：
  1. `send` 文本 `MODE 5`，用 `read_lines` 确认出现 `=== MODE 5`（未成功则按环境说明的 MENU 重试法）。
  2. 在 GUI Modbus 页（从站地址 1）依次执行三笔事务：
     - 功能码 03：读保持寄存器，地址 0，数量 4；
     - 功能码 06：写单寄存器 reg5 = 0x1234；
     - 功能码 03：读 reg5，数量 1。
  3. `modbus_recent`（`count=10`），逐笔核对。
  4. **AI 自主事务**：自行计算 CRC16，用 `send`（`hex=true`，`newline="None"`）发 `01 03 00 00 00 01` + CRC，从 `read_lines` 观察响应（可先把 GUI 的 RX 显示切到 HEX 便于读帧）。
- **验收标准**：
  - ①（1 分）`read_lines` 能确认固件已进入 MODE 5（出现 `=== MODE 5`）。
  - ②（2 分）GUI 三笔事务执行成功（Modbus 页可见响应/寄存器值）。
  - ③（2 分）`modbus_recent` 中能找到这 3 笔：时间升序；每笔 `req_hex`/`resp_hex`/`resp_bytes`/`ok` 齐全且 `resp_bytes` 与 `resp_hex` 字节数一致；第一笔响应数据段含 `03 E8 03 E9 03 EA 03 EB`（=1000~1003，寄存器未被写过时；若台架已被写过，判据改为「06 写入值与 03 回读值一致」）；06 笔 `resp_hex == req_hex`（规范回显）；回读笔数据为 `12 34`；三笔 `ok:true`。
  - ④（1 分）AI 自主发的 03 读帧能观察到响应 `01 03 02 03 E8` + CRC。
  - 附注：`send` 发起的原始帧是否也计入 `modbus_recent` 属文档未定义行为——记录观察即可，不计分。异常码事务（如功能码 0x41 → `01 C1 01`）作为附加观察：`ok` 的判定口径与文档「ok=false 表示无响应/超时」是否自洽。

### Q13 stm32_status 只读状态查询（3 分）

- **目的**：验收 STM32 页状态的只读查询：不断链、提示明确、与 GUI 一致。
- **前置条件**：GUI 已启动（无需连接串口）。台架配 ST-LINK V2，可对照真机探测结果。
- **步骤**：
  1. `{"cmd":"stm32_status"}`（未连接、页未挂接等状态各试一次更有说服力）。
  2. 打开 GUI STM32 页，对照状态区显示截图。
- **验收标准**：
  - ①（1 分）响应 `ok:true`，含 `available` 与说明性 `note` 类字段。
  - ②（1 分）无论页未挂接/未连接/已探测，均不返回 `ok:false` 断链（文档口径：给明确提示，不报错断链）。
  - ③（1 分）与 GUI STM32 页状态区交叉一致（截图：页面上的 ST-LINK 探测结果/中文提示与 `available`、`note` 语义相符）。

---

## 第五部分 内置模拟源（6 分）

### Q14 wave_sim 模拟源生命周期：on → 有数据 → off → clear 归零（6 分）

- **目的**：验收内置模拟源全生命周期与 `wave_clear` 行为（不接硬件即可完整复现）。
- **前置条件**：GUI 已启动，无需硬件；建议初始协议 FireWater。
- **步骤**：
  1. `{"cmd":"wave_sim","on":true}`；取 `status`；等 2 秒截波形页（应有滚动曲线）。
  2. 间隔 ≥1 秒取两次 `wave_snapshot` 与一次 `spectrum_snapshot`。
  3. `{"cmd":"wave_sim","on":false}`；等 2 秒再取两次 `wave_snapshot` 对比。
  4. `{"cmd":"wave_clear"}`；立即取 `wave_snapshot`；随后重新 `wave_sim on` 验证链路可复用。
- **验收标准**：
  - ①（1.5 分）`wave_sim on` 后 `status.sim_on == true`；波形页截图出现曲线。
  - ②（1.5 分）数据真实流动：两次 `wave_snapshot` 间 `frame_count` 增长、`values` 非空且为有限数值；`spectrum_snapshot` 非空（`peak` 有值）。
  - ③（1.5 分）`wave_sim off` 后 `sim_on == false` 且 `frame_count` 停止增长（两次采样差为 0）。
  - ④（1.5 分）`wave_clear` 后 `wave_snapshot` 点数归零（`values` 空数组或 count==0）；重新 `wave_sim on` 后能继续产生新数据。

---

## 第六部分 异常路径（6 分）

### Q15 异常路径：未连接 send / 未知命令 / 非法参数 / points 钳制（6 分）

- **目的**：验收出错时的报错质量（可读、可定位、引导正确）与通道健壮性。
- **前置条件**：GUI 已启动且串口**断开**（Q15 全程保持断开）。
- **步骤**：
  1. 断开状态下 `{"cmd":"send","text":"hi"}`；随后 `{"cmd":"ping"}`。
  2. `{"cmd":"no_such_cmd_xyz"}`。
  3. `{"cmd":"settings_set","key":"no_such_key","value":1}`。
  4. `{"cmd":"settings_set","key":"baud","value":"abc"}`（类型错误）；随后 `settings_get` 查 `baud`。
  5. `{"cmd":"settings_set","key":"points","value":10}`；随后 `settings_get` 查 `points`（文档口径：钳制 50..200000）。
- **验收标准**：
  - ①（2 分）未连接 `send` 返回 `ok:false`，`error` 文本可读、能定位原因（未连接/未选串口类），无堆栈或乱码；随后 `ping` 仍 `ok:true`（通道未死）。
  - ②（1 分）未知命令 `ok:false` 且 `error` 中出现 help 引导（文档口径：未知命令报错并提示用 help）。
  - ③（1 分）非法 key `ok:false`，错误信息能让人识别是「键不存在」。
  - ④（1 分）非法 value `ok:false`，且无半写状态（`settings_get baud` 仍为改前值）。
  - ⑤（1 分）`points=10` 按文档钳制口径返回 `after==50`；若实现为报错而非钳制，记「与文档不符」，并把该观察带到 Q16。

---

## 第七部分 文档一致性（7 分）

### Q16 AI_INTERFACE.md / HANDOFF.md 声称 vs 实测全量核对（7 分）

- **目的**：验收交付文档与软件实际行为一致（命令名、参数名、字段清单、tool 口径、示例可运行）。
- **前置条件**：GUI 已启动；部分核对项需要未连接状态（如 send 示例）。
- **步骤**：逐项核对并产出一张核对表（列：核对项 / 文档声称 / 实测 / 结论一致|不一致|文档缺失）：
  1. help 命令集合 vs `AI_INTERFACE.md` §1.1 表格 19 条命令名。
  2. help 中 `read_lines`/`send`/`settings_set`/`spectrum_snapshot` 的参数名与默认值 vs §1.1/§1.3（如 `count=20`、`interval=0.5`、`max_lines=200`、`newline="CRLF"`）。
  3. `status` 实际字段集合 vs §1.5 所列（connected, port, baud, protocol, frame_total, frame_count, channels, rx_entries, recording, sim_on）；多出的字段要逐一列出。
  4. tool 口径：响应是否携带 `tool` 字段、值是什么。参照：随附 `HANDOFF.md` 声称响应 `tool` 字段已由 `serialscope` 改为 `LiuScope`，而 `AI_INTERFACE.md` 未记载该字段——若实测有 `tool` 但文档未记，记「文档缺失」；若值仍为 `serialscope`，记「严重不一致」。
  5. `settings_list` 实际键集合 vs §1.4 可写键 + §1.5 只读项。
  6. §1.2 的示例 JSON 逐条发送（19 条），每条都应得到**合法 JSON 响应**（不要求全 `ok:true`）：凡 `ok:false` 须判定是「环境前提不满足」（如已连接时改波特率、未连接时 send）还是「文档错误」；只有后者计为不一致。
- **验收标准**（按核对表给分）：
  - ①（1.5 分）命令集合一致（差异逐条列出，每处不一致扣 0.5）。
  - ②（1.5 分）四条命令参数名/默认值一致（每处不一致扣 0.5）。
  - ③（1 分）status 字段与文档口径的出入情况（完全一致得满分；多字段且无害每字段扣 0.25；缺字段每字段扣 0.5）。
  - ④（1.5 分）tool 字段结论明确且有证据（原始响应 JSON）；判定理由引用对应文档行。
  - ⑤（0.5 分）settings_list 键集合一致。
  - ⑥（1 分）19 条示例全部返回合法 JSON，且异常响应均可归因为环境前提（归因为文档错误的，每处扣 0.5，直至 0 分）。

---

## 第八部分 导出与画面取证（18 分）

### Q17 波形页导出：CSV 语义与数据口径、PNG 有效性（6 分）

- **目的**：验收 GUI 导出产物落地可用，且 CSV 内容与 AI 通道读到的缓冲口径对得上账（不查源码即可完成对账）。
- **前置条件**：`wave_sim` 开启（或真机 MODE 2/3 数据流）；GUI 波形页可见曲线；能操作 GUI（见「GUI 操作与截图取证手段」）。
- **步骤**：
  1. `wave_sim on`；先测一下数据率（间隔已知秒数取两次 `wave_snapshot`，用 `frame_count` 增长算出帧率）；再执行 `wave_clear` 从已知空缓冲起算，等若干秒积累已知帧数后，建议 `settings_set` 把 `paused` 置 true 冻结缓冲（使导出与读取口径对齐）。
  2. 取一次 `wave_snapshot`（不传 count，即默认 500），记录 `frame_total`/`frame_count`/`window_frames`/`count` 与各通道点数。
  3. 波形页「导出」菜单 → 导出 CSV，保存到 `_verify\evidence\`（若走文件对话框，记录实际选择的路径与对话框行为）。
  4. 同菜单 → 导出 PNG。
  5. 用 Python 解析产物：CSV 检查表头与数据行；PNG 用 PIL 打开，检查尺寸与内容非空白（如灰度级种类 >10、宽 ≥600px）。
  6. 核对完成后 `paused=false` 恢复。
  7. 健壮性观察（可选，不计分）：导出对话框点「取消」、或把保存路径指到不可写位置，记录软件表现。
- **验收标准**：
  - ①（2 分）CSV 落地存在（报告绝对路径）；表头含通道名（允许附加时间/序号列）且与 `wave_snapshot` 的 `channels[].name` 一致；数据行均为有限数值、数值列数与通道数吻合。
  - ②（2 分）CSV 数据行数与导出时刻的缓冲口径对账成立：明确说明映射到 `wave_snapshot` 的哪个字段（`frame_total`/`frame_count`/`window_frames`/`count`）及换算关系；实测行数不超过自 `wave_clear` 起积累的帧总数（已知帧率×秒数），且与所选口径偏差 ≤2 行、或对偏差给出令验收人信服的口径解释（如显示点数截断、缓冲容量上限）。行数为 0 或完全无法解释的，本项 0 分。
  - ③（1.5 分）PNG 落地存在、PIL 可打开、宽 ≥600px 且内容非空白（非全图单一颜色）。
  - ④（0.5 分）导出动作有 GUI 反馈证据（对话框路径回显 / 状态提示 / 文件时间戳），记录之。
  - 附注：README 对波形 CSV 的口径是「全量缓冲，通道名表头」；AI 通道 §1.1 命令表中没有波形导出命令——若实测发现隐藏导出命令，仅记录为观察项。

### Q18 频谱页导出：PNG 参数标注一致性与 CSV 双口径（7 分）

- **目的**：验收频谱导出的可消费性：PNG 顶部参数标注与 `spectrum_snapshot.params` 一致；CSV「最新一帧」与「最近 N 秒」两种口径行数均可验证；N 超累计采集时长时的钳制行为与文档一致。
- **前置条件**：首选真机：GUI 连 COM9@115200 + FireWater + `send "MODE 6"`（同 Q6）；兜底 `wave_sim`。数据源已持续运行 ≥30 秒（`acquisition_seconds` 有累计量）；能操作 GUI。
- **步骤**：
  1. 取 `spectrum_snapshot`（`count=0`），记录 `params{sample_rate_hz, fft_points, window, df_hz, rbw_enbw_hz}`、`peak.freq_hz`、`total_bins`、`len(freqs_hz)`、`acquisition_seconds`。建议先 `paused=true` 冻结解析使比对稳定（快照含 `frozen` 字段可佐证），核对完恢复。
  2. 频谱页「导出」→ 导出 PNG（README：谱图+瀑布拼图，顶部参数标注含 采样率/FFT 长度/窗函数/RBW/峰值保持/平均/数据时间范围/主频）。
  3. 读 PNG 顶部标注：把顶部标注区域裁剪保存为证据并读出文本（目视或 OCR 均可），逐项与快照参数比对。
  4. 导出 CSV → 选「最新一帧瞬时频点」口径。
  5. 导出 CSV → 选「最近 N 秒」口径两次：一次 N=5（需累计采集时长 ≥5s）；一次 N 取 `acquisition_seconds + 60`（故意超时长）。
  6. 解析两个 CSV：行/列结构与频点数、时长口径核对；留意界面是否出现钳制提示（README：N 超累计采集时长自动钳制并提示）。
- **验收标准**：
  - ①（2 分）PNG 落地、PIL 可打开；顶部标注文本与快照一致：采样率、FFT 长度、窗函数三项与 `params` 同名项一致；RBW 标注与 `rbw_enbw_hz` 一致（或给出与 `df_hz` 的换算说明）；主频标注与 `peak.freq_hz` 一致。每错/缺一项扣 0.5，扣完为止。
  - ②（1.5 分）「最新一帧」CSV 的频点数与 `count=0` 时的 `total_bins`（或 `len(freqs_hz)`）一致（按行计或按列计均可，转置口径需说明）；数值为可读的频率/dB 值。
  - ③（2 分）「最近 N 秒」口径自洽且钳制正确：N=5 的行数 ÷ 5 与 超长 N 的行数 ÷ `acquisition_seconds` 反推出的实测帧率一致（±10%）；超长 N 被钳制到实际采集时长（行数 ≈ acquisition×实测帧率，明显小于 N×理论帧率），界面有钳制提示（截图）；另给出理论帧率（`sample_rate_hz/fft_points`）与实测帧率对照，倍率 ≠1 时记录为步进/重叠口径说明（不扣分）。
  - ④（1 分）程序化导出口径核实：`AI_INTERFACE.md` §3 声称「频谱页还提供程序化 `export_csv/export_png`（见频谱页面板）」，而 §1.1 命令表中无此二命令——实测发送 `{"cmd":"export_csv"}` 与 `{"cmd":"export_png"}` 并记录响应，在报告中写明该声称的实际形态（GUI 按钮 / AI 命令 / 不存在）。若实测确认「文档声称的 AI 程序化导出不存在」，在 Q16 核对表追加一行（在那里计分），本题只要求核实过程与结论留证。
  - ⑤（0.5 分）导出产物路径与钳制提示均有截图/路径记录。

### Q19 可调观测项 GUI 交互验证与「AI 画面调整能力缺口」盘点（5 分）

- **目的**：与 Q7~Q10 区分——本题不测 `settings_set` 的跟随，而是验收 GUI 观测项本身可调且**视觉生效**；同时盘点「GUI 可调但 AI 通道无法设置」的观测项，为下一版 AI 能力扩展取证。
- **前置条件**：`wave_sim` 开启（或真机数据流）；GUI 可操作（computer-use 或人工）；Q7~Q10 改过的状态已复原。
- **步骤**（每项按「调整前截图 → 操作 → 调整后截图 → 一句话描述视觉差异」执行，控件名以页面实际为准）：
  1. 频谱页：调整「每格 dB」或参考电平至少一档，观察谱图纵向刻度/曲线高度变化；切换峰值保持或指数平均开关，观察谱线形态变化。
  2. 频谱页：瀑布图开/关（及「最新帧在底」类瀑布选项），观察瀑布区域出现/变化。
  3. XY 页：余晖档位调高一档以上（观察轨迹残影长度变化）；分格数切换（如 4→16 或 1→4，观察窗口布局变化）。
  4. 波形页：右键菜单「显示网格」开/关；先手动缩放再「恢复自动缩放」。
  5. 缺口盘点：对照 `settings_list` 实测键集合与 help 的 19 条命令，列出「GUI 可调但 AI 通道不可设」的观测项清单（已知线索：`settings_list` 中 `auto_y` 是只读项，而波形页 Y 轴可在 GUI 切自动/手动——即典型缺口；其余如每格 dB、参考电平、RBW、峰值保持、平均、Marker、瀑布选项、XY 余晖/分格/量程等，以页面实际控件为准）。
- **验收标准**：
  - ①（2.5 分）完成 ≥4 项 GUI 调整且每项有前后截图、视觉差异明确（每项约 0.6 分，四项即满分）；点了控件但拿不出视觉变化证据的项不计入。
  - ②（1.5 分）缺口盘点表 ≥6 项，每项含：所在页、控件名、调整后的值、是否属于 `settings_list` 键集合的判定（在/不在）。
  - ③（1 分）给出「最值得下一版加入 AI 命令的前 3 项」及理由（作为路线图取证，合理即得分）。
  - 附注：发现某 GUI 项调整后视觉无变化且无任何提示，记录为疑似缺陷单独上报（不扣本题分）；②③的盘点结论属能力缺口取证，不算软件缺陷。

---

## 评分汇总

| 题号 | 标题 | 分值 |
|---|---|---|
| Q1 | 存活探测与行协议基础 | 4 |
| Q2 | help 自描述性与「盲上手」能力 | 8 |
| Q3 | read_lines 快照：count / filter / 时间升序 / 字段 | 5 |
| Q4 | follow 连续推送与 stop 退出 | 7 |
| Q5 | wave_snapshot / xy_snapshot 字段可消费性 | 4 |
| Q6 | spectrum_snapshot 主频复算与参数自洽 | 6 |
| Q7 | settings_set 主题换肤——GUI 全页跟随 | 4 |
| Q8 | settings_set 波特率：未连接改值跟随 / 已连接拒绝 | 4 |
| Q9 | settings_set 协议切换：UI 跟随 + 缓冲清空 + set_protocol 等价 | 4 |
| Q10 | settings_list/get 与 GUI 控件交叉一致性 + 中文别名 | 4 |
| Q11 | 真机 send 往返：TX 记录与固件应答 | 4 |
| Q12 | MODE 5 切换 + Modbus 事务与 modbus_recent 复盘 | 6 |
| Q13 | stm32_status 只读状态查询 | 3 |
| Q14 | wave_sim 模拟源生命周期 | 6 |
| Q15 | 异常路径：未连接 send / 未知命令 / 非法参数 / points 钳制 | 6 |
| Q16 | 文档一致性全量核对 | 7 |
| Q17 | 波形页导出：CSV 语义与数据口径、PNG 有效性 | 6 |
| Q18 | 频谱页导出：PNG 参数标注一致性与 CSV 双口径 | 7 |
| Q19 | 可调观测项 GUI 交互验证与「AI 画面调整能力缺口」盘点 | 5 |
| **合计** | | **100** |
