# LiuScope（柳镜）AI 控制通道 · 端到端验收考试报告

- 考卷：`_verify/ai_exam.md`（2026-10-26 v2，19 题 100 分）
- 做题者：外部 AI 测试员（与出题者隔离，材料仅为考卷 + 软件本体 + 随附文档）
- 测试时间：2026-10-27 04:36 – 05:55（本机时间）
- 被测软件：LiuScope v1.2.0（`python qt/main.py` 启动，AI 通道 tcp://127.0.0.1:9101）
- 台架：COM9（CH340 ↔ STM32H743 固件 V4，115200 8N1）+ ST-LINK V2
- 取证手段：AI 通道用自写 Python TCP 行协议脚本（`_verify/qrpc.py`）；GUI 用 ctypes/SendInput 人机协作（computer-use 技能在本子代理环境不可用，返回 "Computer Use is not available in subagent"，故按考卷允许的黑盒替代方式操作）；截图统一 PIL.ImageGrab。

---

## 一、总分与逐题得分表

| 题号 | 标题 | 得分 | 满分 | 一句话结论 |
|---|---|---|---|---|
| Q1 | 存活探测与行协议基础 | 4 | 4 | ping 单行合法 JSON；同连接三连发按序一一对应 |
| Q2 | help 自描述与盲上手 | 8 | 8 | 19 条命令与文档一一对应且三要素齐全；3 条盲构造请求全部首试 ok:true |
| Q3 | read_lines 快照 | 5 | 5 | 字段/升序/过滤/panic 命中全部达标（附带发现 filter 在取最新 N 条之后生效） |
| Q4 | follow 推送与 stop | 7 | 7 | 快照+游标+参数回显正确；推送仅新行、游标严格递增；JSON/纯文本 stop 均可退出 |
| Q5 | wave/xy_snapshot 可消费性 | 4 | 4 | 三数字（通道 3 / XY 窗 2 / 每窗 256 点）仅凭 JSON 即可读出 |
| Q6 | 频谱主频复算（真机） | 6 | 6 | argmax 与 peak 逐点一致；df 自洽；50/120/300 Hz 三峰全中（49.80/120.12/299.80） |
| Q7 | 主题换肤 GUI 跟随 | 4 | 4 | light 全页生效（设置页/波形页截图），get 一致，dark 恢复 |
| Q8 | 波特率两分支 | 4 | 4 | 未连接可改且下拉跟随 921600；已连接拒绝且报错文案清晰 |
| Q9 | 协议切换 + set_protocol | 4 | 4 | UI 下拉跟随；暂停冻结下切换后缓冲 0 帧/空通道；两命令等价 |
| Q10 | settings 三方一致 + 中文别名 | 4 | 4 | 16 键 = 7 可写 + 9 只读恰合；points/fill/paused/baud 四项 GUI=AI=记忆一致；中文别名等效 |
| Q11 | 真机 send 往返 | 4 | 4 | TX 记录 + INFO 信息块 + ECHO-PING 逐字符回显 + 计数增长 |
| Q12 | MODE 5 + Modbus 闭环 | 5 | 6 | GUI 三笔事务与 modbus_recent 复盘全对；AI 自主帧链路通，但考卷指定帧固件不应答（④ 失分，定性为固件侧） |
| Q13 | stm32_status 只读 | 2.5 | 3 | 不断链、探测 SN 与 GUI 一致；但响应无文档声称的 note 字段（① 扣 0.5） |
| Q14 | wave_sim 生命周期 | 6 | 6 | on→有数据→off→冻结→clear 归零→re-on 复用全部通过（首次运行被固件 MODE 6 残流污染，排除后重测） |
| Q15 | 异常路径 | 5 | 6 | 报错可读、通道健壮；但 points=10 被原样接受，无钳制无报错，与文档「钳制 50..200000」不符（⑤ 失分） |
| Q16 | 文档一致性全量核对 | 7 | 7 | 命令集/参数默认值/status 字段/settings_list 全一致；tool=LiuScope 与 HANDOFF 相符；22 条示例全为合法 JSON，2 条失败均为环境前提 |
| Q17 | 波形导出 CSV/PNG | 6 | 6 | CSV 6067 行与 frame_count 精确对账；PNG 1124×420/77 灰阶有效 |
| Q18 | 频谱导出 PNG/CSV | 7 | 7 | PNG 标注与 params 全一致；最新一帧=total_bins；N=5 与超长 N 帧率自洽且按 6000 帧上限钳制；export_csv/export_png 证实不存在于 AI 通道 |
| Q19 | GUI 观测项 + 缺口盘点 | 5 | 5 | 7 项 GUI 调整全部有前后截图与明确视觉差异；缺口盘点 15+ 项；给出 top-3 建议 |
| **合计** | | **97.5** | **100** | |

---

## 二、逐题要点与证据清单

证据文件均在 `F:\软件仓库_新\开发软件\串口调试助手-V6\_verify\evidence\`（下表省略目录前缀）。脚本在 `_verify\` 下（q01~q16、qrpc.py、guiclick.py、screenshot.py 等）。

### Q1 存活与行协议（4/4）
- `{"cmd":"ping"}` → `{"ok":true,"tool":"LiuScope","control":"on","docs":"AI_INTERFACE.md"}`，单行无粘连。
- 同连接一次性发 ping/help/status 三行 → 按序收到 3 条合法 JSON（命令特征互不串行）。
- 证据：`q01_ping_result.json`（含三行原文）。

### Q2 help 自描述与盲上手（8/8）
- help 返回结构化 `commands[]`（19 条，顺序与 §1.1 完全一致）+ `follow_semantics`。
- 19 条条目全部具备 参数说明（名/类型/默认值）/ 功能说明 / 可照抄 example；无参数命令 params 为空 dict（合理）。
- 盲构造（仅凭 help，未看文档）首试即 ok:true：
  - `{"cmd":"spectrum_snapshot","count":64}` ✓
  - `{"cmd":"settings_set","key":"points","value":800}` ✓（before 1000 → after 800）
  - `{"cmd":"read_lines","count":5,"filter":"."}` ✓
- 证据：`q02_help_raw.json`（help 全文）、`q02_blind_firsttry.json`。

### Q3 read_lines 快照（5/5，真机 MODE 4）
- `lines[{t,dir,text}]`，同时出现 RX/TX（另有 SYS 解码行），t 升序，20 行封顶。
- 2.8s 窗口 19 条 RX，`[HH:MM:SS.mmm]` 格式（约 200ms/条，模式横幅自称 5Hz 一致）。
- `filter="E/"` 每条命中；`filter="panic|CFSR"` 命中 `kernel panic!`、`CFSR: 0x…` 与自动解码行。
- 观察项：`count` 先截最新 N 条原始行、再做正则过滤（非「N 条匹配行」），消费方需知晓。
- 证据：`q03_read_lines_result.json`。

### Q4 follow 推送（7/7，独立连接）
- 首响应：5 行快照 + cursor=402 + `interval:0.3/max_lines:50/filter:''` 回显。
- 5s 收到 16 次推送 `{"ok":true,"follow":true,"lines":[…],"cursor":N}`：仅新行、时间升序、cursor 严格递增。
- `{"cmd":"stop"}` → `{"ok":true,"follow":false,"note":"已停止推送"}`，同连接 `status` 正常应答。
- 重复 follow interval=0.2 回显生效；纯文本 `stop\n` 同样退出（同样回 ack）。
- filter follow（`E/|FAULT`）快照与推送全部命中正则，0 违例。
- 证据：`q04_follow_result.json`。

### Q5 wave/xy_snapshot（4/4，wave_sim）
- `status.channels=3` == wave_snapshot channels 长度；name/enabled/values 齐全；两次调用间数据更新。
- xy 初始 `xy_on:false` 但 windows 已有数据（观察项：XY 缓冲在视图关闭时也在积累）；GUI 点「XY相位图」按钮（默认 2 窗）后 `xy_on:true`，每窗 `window/x_channel/y_channel/x_name/y_name/span/divs/draw_mode/persist` 齐全，x[]/y[] 等长 256。
- 三数字：通道数 **3**（status.channels）、XY 窗口数 **2**（window_count）、每窗点数 **256**（len(window.x)）。
- 观察项：XY 开关无 AI 命令且文档也未声称有（不计失分）；所需人工步骤 = 波形页工具行「XY相位图」按钮。
- 证据：`q5_wave_snapshots.json`、`q5_xy_after_enable.json`、`q5_xy_enabled.png`。

### Q6 频谱主频复算（6/6，真机 MODE 6）
- 关键字段齐全：`params{sample_rate_hz:1000, fft_points:2048, window:汉宁窗, df_hz:0.48828125, rbw_enbw_hz:0.732421875,…}`、`peak{freq_hz,db}`、`freqs_hz[]/db[]` 等长 1024、频率升序。
- 独立 argmax：`freqs[102]=49.8046875 Hz` 与 `peak.freq_hz` 同一频点（偏差 0）。
- `df_hz / (fs/fft_points) = 1.0000`。
- 去掉 <10Hz 后三峰：**49.80 / 120.12 / 299.80 Hz**，全部落在 50/120/300 ±10 Hz。
- 附加观察：`count=512` 时 `freq_start_hz` 0 → 250（截高频端）符合文档。
- 证据：`q06_spectrum_result.json`。

### Q7 主题换肤（4/4）
- `settings_set theme light` → `before:"dark", after:"light"`；`settings_get` → light；改回 dark 恢复。
- 截图：设置页与波形页在 light 下整页变浅（含绘图区/XY 窗），非个别控件。
- README 已知限制（终端页已落显历史行保留当时配色）：本轮 light 期间终端无新落显行，无法产生对照证据，如实记「未观察到（无复现条件）」。
- 证据：`q07_theme_set_light.json`、`q07_theme_get.json`、`q07_theme_set_dark.json`、`q07_settings_dark.png`、`q07_settings_light.png`、`q07_wave_dark.png`、`q07_wave_light.png`、`q07_terminal_after_dark.png`。

### Q8 波特率两分支（4/4）
- 未连接：921600 → `before 1500000 → after 921600`（启动记忆值为 1500000），下拉框文本变 921600（截图），`settings_get` 一致。
- 已连接：`{"ok":false,"error":"已连接：请先断开再修改波特率（连接期间 UI 已锁定）"}`——不看文档即可行动；下拉保持 115200（截图）。
- 全程未向固件发 BAUD（红线遵守）。
- 证据：`q08_step1_baud_disconnected.json`、`q08_step1_baud_dropdown_921600.png`、`q08_step2_connected_com9.png`、`q08_step3_baud_connected_reject.json`、`q08_step3_dropdown_unchanged.png`、`q08_restore115200.json`。

### Q9 协议切换（4/4）
- `settings_set protocol JustFloat` → before/after 正确；波形页协议下拉显示 JustFloat（截图裁剪）。
- 清空证据（加强版）：暂停冻结缓冲（565 帧/每通道 50 点）→ 切协议 → `wave_snapshot` = `frame_count:0, frame_total:0, pts:[]`。
- `{"cmd":"set_protocol","protocol":"FireWater"}` → ok:true，下拉跟随 FireWater（截图裁剪）；与 settings_set 等价。
- 证据：`q09_protocol_switch.json`、`q09_clear_evidence_paused.json`、`q09_ui_follow.json`、`q09_wave_justfloat_crop.png`、`q09_wave_firewater_back_crop.png`。

### Q10 settings 三方一致（4/4）
- `settings_list` 16 键 = §1.4 的 7 可写键 + §1.5 的 9 只读键，无多无缺。
- 交叉核对（AI 改 → GUI 截图 → get 复核）：points（800→1000→别名 1200，GUI 下拉 1200）、fill（勾→不勾，设置页复选框截图）、paused（按钮变「继续」、FPS 0 曲线冻结）、baud（Q8 的 921600 证据链）。
- 中文别名：`显示点数`→points、`暂停`→paused、`曲线填充`→fill 全部 ok:true 且 after 正确。
- 证据：`q10_settings_cross.json`、`q10_wave_points1200_paused.png`、`q10_settings_fill_off.png`、`q10_settings_fill_restored.png`。

### Q11 真机 send 往返（4/4）
- `send INFO` → TX 记录 + RX 信息块含 `H743 TEST FIXTURE`、`STM32H743XIH6`、`KEY1=PI7`。
- `filter="ECHO-PING"` → TX 行 `ECHO-PING-12345<CR><LF>` + RX 行 `ECHO-PING-12345`（逐字符一致）。
- status rx_entries 646 → 672 增长。
- 证据：`q11_send_roundtrip.json`。

### Q12 MODE 5 + Modbus（5/6）
- ① `=== MODE 5` 经 read_lines 确认 ✓。
- ② GUI 三笔事务全部成功（截图含发送/响应/寄存器解码行）：03@0×4 → `03 E8 03 E9 03 EA 03 EB`（1000~1003）；06 reg5=0x1234 → 规范回显；03@5×1 → `12 34`（4660）。
- ③ `modbus_recent` 三笔时间升序，req_hex/resp_hex/resp_bytes/ok 齐全，字节数与 hex 一致，三笔 ok:true ✓。
- ④ **0 分**：按考卷处方 `send(hex=true, newline="None")` 发 `01 03 00 00 00 01 84 0A`（CRC 自算，教科书值），**固件无任何应答**。定性实验：
  - 同法发送 `01 03 00 00 00 04`、`01 03 00 05 00 01`、`01 03 00 00 00 02`、`01 03 00 01 00 01` 均有应答；
  - GUI Modbus 引擎发同一帧 `01 03 00 00 00 01` 同样 600ms 超时（截图）→ **固件对「地址 0 + 数量 1」这一特定组合不应答**，非 LiuScope 通道问题；
  - AI 发帧链路本身已验证：异常帧 `01 41 …` → 应答 `01 C1 01 B0 50`（与预期 `01 C1 01`+CRC 完全一致）。
- 观察项：AI hex 原始帧不计入 modbus_recent（GUI 引擎事务才计入）；hex=true 的 text 需空格分隔（无空格报「HEX 输入无效」，报错清晰）；ASCII 收码下二进制帧显示为转义+「(空闲落显)」标注。
- 证据：`q12_mode5_confirm.json`、`q12_tx1_fc03_addr0_n4.png`、`q12_tx2_fc06_result.png`、`q12_tx3_fc03_reg5_result.png`、`q12_modbus_recent_final.json`、`q12_ai_frames_clean.json`、`q12_addr0count1_investigation.json`、`q12_gui_addr0count1_in_mode5.png`、`q12_export_cmd 无关（见 Q18）`。

### Q13 stm32_status（2.5/3）
- 页未打开时与打开/探测后查询均 `ok:true` 不断链（② ✓）。
- 探测后 `probe_count:1, last_probe_sn:E1007200D0D2139393740544` 与 GUI 状态区/CLI 日志 SN 完全一致（③ ✓，截图）。
- ① 扣 0.5：响应含 `available/cli_found/cli_path/connected/busy/busy_text` 等，但**没有**文档 §1.5 与考卷所述的说明性 `note` 字段（两种状态下均无）。
- 证据：`q13_stm32_status_page_not_attached.json`、`q13_stm32_status_after_probe.json`、`q13_stm32_page_initial.png`、`q13_stm32_after_probe.png`。

### Q14 wave_sim 生命周期（6/6，排除污染后重测）
- on → `sim_on:true`，波形页曲线滚动（截图，状态栏 帧 39 | FPS 16）。
- 数据流动：两次 snapshot frame_count 增长、数值有限；spectrum peak 有值。
- off → `sim_on:false` 且 frame_count 两次采样完全相等（冻结）。
- `wave_clear` → frame_count=0、values 空；re-on → 新数据（24 帧起）。
- **测试过程记录（重要）**：首次运行被污染——固件残留在 MODE 6（1kHz 单通道流）持续出帧，导致「off 后 frame_count 仍以 ~1000fps 增长」的假象。根因：MODE 6 高速流下固件**不响应文本命令 MENU**（两种换行均无效），但 `KEY 1`（模拟按键）能回菜单。排除后重测全部通过。
- 证据：`q14_sim_lifecycle.json`、`q14_sim_on_wavepage.png`、`q14_off_freeze_recheck.json`（污染证据）、`q14_terminal_check.json`、`q14_menu_recover.json`、`q14_key1_attempt.json`（恢复菜单证据）。

### Q15 异常路径（5/6，全程断开）
- 未连接 send → `{"ok":false,"error":"串口未连接。"}`；随后 ping ok:true（通道未死）✓ 2/2。
- 未知命令 → `未知命令: no_such_cmd_xyz（发送 {"cmd":"help"} 获取命令表）` ✓ 1/1。
- 非法 key → `设置键 'no_such_key' 不可写（可写: baud/port/protocol/theme/points/fill/paused；只读见 settings_list）` ✓ 1/1。
- 非法 value → `波特率必须是正整数: 'abc'`；`settings_get baud` 仍 115200（无半写）✓ 1/1。
- **points=10 → `{"ok":true,"key":"points","before":"1200","after":"10"}`**：既未钳制到 50 也未报错，与 §1.4「钳制 50..200000」**不符**（0/1；已按考卷要求把该观察带入 Q16 核对表）。GUI 下拉随之显示 10（截图）。
- 证据：`q15_error_paths.json`、`q15_points10_gui.png`、`q15_points_restore.json`、`q15_disconnected.png`。

### Q16 文档一致性（7/7）
核对表（核对项 / 文档声称 / 实测 / 结论）：

| # | 核对项 | 文档声称（出处） | 实测 | 结论 |
|---|---|---|---|---|
| 1 | help 命令集合 | §1.1 表 19 条 | help 19 条，名称与顺序逐一同 | **一致** |
| 2 | read_lines 参数 | `count=20, filter="", follow=false, interval=0.5, max_lines=200`（§1.1/§1.3） | help 同值；实测默认行为一致 | **一致** |
| 3 | send 参数 | `text, hex=false, newline="CRLF"`（§1.1） | help：hex "bool=false"、newline "默认 CRLF"；未连接报错符合 §1.1「未连接报错」 | **一致** |
| 4 | spectrum_snapshot 参数 | `count=0`（0=全部，>0 截高频端） | help/实测一致；count=512 时 freq_start_hz 右移 | **一致** |
| 5 | settings_set 参数 | `key, value`（§1.1） | help 一致 | **一致** |
| 6 | status 字段集 | §1.5：connected, port, baud, protocol, frame_total, frame_count, channels, rx_entries, recording, sim_on | 实测 10 字段全在；另有无害信封字段 `ok`/`tool` | **一致**（信封字段另记 tool 行） |
| 7 | tool 字段 | HANDOFF.md：「响应 tool 字段已由 serialscope 变为 LiuScope」；AI_INTERFACE.md 未记载该字段 | 每条响应 `tool:"LiuScope"` | 与 HANDOFF 相符；AI_INTERFACE **文档缺失**（按考卷口径：值非 serialscope，不构成严重不一致） |
| 8 | settings_list 键集合 | §1.4 七可写 + §1.5 九只读 | 16 键恰合 | **一致** |
| 9 | §1.2 示例逐条发送 | 22 行示例 JSON（考卷称 19 条，实为 22 行/19 命令，行数差异不影响口径） | 22/22 返回合法 JSON；仅 2 条 `send` 因「串口未连接」ok:false —— §1.1 明文「未连接报错」，属环境前提 | **一致**（0 处文档错误） |
| 10 | points 钳制 | §1.4：「钳制 50..200000」 | 10 被原样接受（Q15） | **不一致**（已在 Q15 计分，此处按考卷要求入表） |
| 11 | 频谱「程序化 export_csv/export_png」 | §3：「频谱页还提供程序化 export_csv/export_png（见频谱页面板）」 | 两命令均 `未知命令`；实际形态 = 频谱页 GUI 按钮 | **不一致**（措辞误导；在 Q18④ 计分并在此留证） |
| 12 | stm32_status 的 note 字段 | §1.5：「未连接时给明确 note，不报错断链」 | 不报错断链属实；但无 note 字段 | **部分不一致**（Q13 已计分） |

- 证据：`q02_help_raw.json`（命令表/参数）、`q16_status_fields.json`、`q10_settings_cross.json`（settings_list）、`q16_examples_all.json`（22 条示例原文+恢复步骤）、`q18_export_cmd_probe.json`、`q15_error_paths.json`。

### Q17 波形导出（6/6，wave_sim；数据源背景见 Q14 说明）
- 帧率先行测量：5s 内 frame_total 103183→108237 → **1010.8 fps**（含同口 MODE 6 残流，见问题清单 P-03；对账不受影响）。
- `wave_clear` 后积累 ~6.0s → `paused=true` 冻结 → `wave_snapshot`（默认 500）：frame_total=frame_count=**6067**，window_frames=800，CH0/CH1/CH2 各 500 点。
- GUI 导出 CSV → `frame,CH0,CH1,CH2` 表头，数据行 **6067 行** —— **与 frame_count（=frame_total，自 wave_clear 起的全量缓冲）精确相等（偏差 0 行）**；估计值 1010.8×6.0≈6065 的 +2 行差为 RPC 往返延时所致，口径成立。全部单元格可解析为数值。
- GUI 导出 PNG → 1124×420 RGBA，77 级灰度（>10），非空白。
- 导出动作反馈：Windows 保存对话框（标题「导出CSV」/「导出PNG」，默认名 spectrum.csv/wave.png，当前目录=软件根目录）+ 文件落地时间戳。
- 证据：`q17_prep.json`、`q17_wave_export.csv`、`q17_wave_export.png`、`q17_wave_tab.png`、`q17_export_menu.png`。

### Q18 频谱导出（7/7，真机 MODE 6，累计采集 ≥800s）
- 冻结快照（paused=true）：params{1000 Hz, 2048, 汉宁窗, df 0.48828125, rbw_enbw 0.732421875}、peak 49.8046875 Hz@53.05 dB、total_bins=1024。
- **PNG**（谱图+瀑布拼图）顶部标注逐项比对：采样率 1000 Hz ✓ | FFT 长度 2048 点 ✓ | 窗函数 汉宁窗 ✓ | Δf 0.488 Hz ✓ | RBW(ENBW) 0.732 Hz ✓ | 通道 CH0 ✓ | 峰值保持 关/平均 关/瀑布 开（与快照 peak_hold=false/average=false/waterfall_on=true 一致）✓ | 参考电平 0 dB、10 dB/格 ✓ | 主频 49.80 Hz @ 53.1 dB ✓ | 数据时间范围+累计采集 ✓。
- **最新一帧 CSV**：头行自证口径；1024 行（频率Hz,dB）== total_bins ✓。
- **最近 5.0 秒 CSV**：`共 41 帧`，41984 数据行=41×1024，时间跨度 4.92s ✓。
- **最近 872.0 秒 CSV（超长 N = acq 812+60）**：被钳制 —— 头行 `共 6000 帧`，实际跨度 660.6s（< 872 请求值，也 < 当时累计 835s）；钳制依据为 README 明示的 **6000 帧导出缓冲上限**（选择对话框同步显示「累计采集时长: 835.0 s | 导出缓冲: 6000 帧」= 界面提示，截图）；未另见导出后弹窗提示（记录）。
- 帧率自洽：N=5 → 41/5 = 8.2 fps；超长 N → 6000/660.6 = 9.08 fps；偏差 −9.7%（±10% 内，注：按导出跨度计；若按全部采集时长 835s 计为 7.19，偏差系 6000 帧截断所致，已说明）。理论帧率 1000/2048=0.488 fps vs 实测 9.08 fps → 倍率 ≈18.6，属帧步进/重叠口径（按考卷口径记录，不扣分）。
- **export_csv / export_png AI 命令**：实测均返回 `未知命令: …（发送 {"cmd":"help"} 获取命令表）` → §3「程序化导出」的实际形态 = **频谱页面板 GUI 按钮**，AI 通道无此命令（已在 Q16 表计为不一致）。
- 观察：paused=true 后 `spectrum_snapshot.db[]` 跨 2s 逐值相等（解析确已冻结），但 `frozen` 字段仍为 false，且 `acquisition_seconds` 继续增长（时间型计数器）——字段语义与直觉略有出入（记录，不影响判分项）。
- 证据：`q18_spectrum_frozen_snapshot.json`、`q18_spectrum_export.png`、`q18_csv_latest_v2.csv`、`q18_csv_last5s_v2.csv`、`q18_csv_n872_v2.csv`、`q18_acq_before_overlong.json`、`q18_n872_dialog.png`、`q18_csv_choice_dialog.png`、`q18_export_cmd_probe.json`、`q18_paused_effect_check.json`、`q18_statusbar_strip.png`。

### Q19 GUI 观测项与缺口盘点（5/5）
7 项调整（每项均有前后截图与视觉差异，前 4 项即满足满分线）：

| # | 页面 | 控件/操作 | 变化 | 视觉差异 |
|---|---|---|---|---|
| 1 | 频谱 | dB/格 10→5→10 | `q19_spec_baseline_db10.png` / `q19_spec_db5.png` | 纵轴刻度改 5dB/格（-10/-20/-30/-40），曲线纵向拉伸，瀑布色标同步改刻度 |
| 2 | 频谱 | 峰值保持 开→关 | `q19_spec_peakhold_on.png` | 出现黄绿色峰值保持迹线，与实时青色谱线分离 |
| 3 | 频谱 | 瀑布 开→关 | `q19_spec_waterfall_off.png` | 瀑布区域整块消失，谱图占满全高 |
| 4 | 波形/XY | 余晖 1s→5s→1s | `q19_xy_baseline_crop.png` / `q19_xy_persist5s_crop.png` | 轨迹残影显著变长变亮（C 形弧） |
| 5 | 波形/XY | 窗口数 2→1→2 | `q19_xy_wincount_1.png` | 双窗并排变单窗布局 |
| 6 | 波形 | 右键「显示网格」关→开 | `q19_grid_compare.png`（上下对比） | 网格线消失/恢复 |
| 7 | 波形 | 历史滑块拖窄（1200→49 帧）→右键「恢复自动缩放」 | `q19_slider_narrowed.png` / `q19_autoscale_restored2.png` | 视图进入「历史 [6489,6539]」极限放大；恢复后回到「跟随」 |

缺口盘点（GUI 可调但 AI 通道不可设；「在/不在」相对 settings_list 16 键 + 19 条命令）：

| # | 页面 | 控件 | 调整后值 | 在 settings_list？ |
|---|---|---|---|---|
| 1 | 波形 | 自动Y 开关 + Y min/max | 开 | 不在（`auto_y` 仅只读） |
| 2 | 波形 | 通道显隐 CH0/CH1/CH2 | 勾选 | 不在 |
| 3 | 波形 | 滤波类型 + 参数（16） | 关 | 不在 |
| 4 | 波形 | 历史滑块（宽度/位置） | 拖窄→恢复 | 不在 |
| 5 | 波形 | 绘图区右键（网格/导出/恢复缩放） | 网格关 | 不在（导出仅 GUI） |
| 6 | 频谱 | 参考电平 | 0 dB | 不在 |
| 7 | 频谱 | dB/格 | 5 | 不在 |
| 8 | 频谱 | RBW（自动/手动） | 自动 | 不在 |
| 9 | 频谱 | 峰值保持 / 指数平均 | 关/关 | 不在 |
| 10 | 频谱 | 瀑布 / 最新在底 / 对数色标 / 冻结 | 开/开/关/未冻结 | 不在 |
| 11 | 频谱 | 通道/点数/采样率（频谱侧） | CH0/2048/1000 | 不在 |
| 12 | XY | 窗口数 / 余晖 / 显示(线点) | 2 / 1s / 线 | 不在 |
| 13 | XY | 每窗 X/Y 源通道、量程 ±N/自适应、分格 | CH0×CH1 等 | 不在 |
| 14 | 终端 | RX/TX 格式、换行符 | ASCII/CRLF | 不在（`rx_format/tx_format/newline` 仅只读） |
| 15 | 终端 | 连接/断开、刷新串口 | 已连接/断开 | 不在（无对应 AI 命令，文档亦如此声明） |

Top-3 建议下一版加入 AI 命令（路线图取证）：
1. **频谱显示参数包**（参考电平/dB每格/RBW/峰值保持/平均/瀑布）：联调中 AI 需按信号幅度自适应调纵向标尺后截图取证，是「AI 看图」闭环价值最高的一组。
2. **XY 相位图参数**（窗口数/余晖/量程/分格/源通道）：IQ 正交性调试的 AI 闭环必需（当前 XY 数据可读但不可调）。
3. **波形 Y 轴（auto_y 可写化 + y_min/y_max）**：`auto_y` 已在 settings_list 中只读存在，补写路径改造成本最低、收益直接（配合读数截屏可做幅值复核）。

---

## 三、问题清单

### A. 疑似缺陷（按严重度）

| 编号 | 严重度 | 现象 | 复现步骤 | 证据 |
|---|---|---|---|---|
| B-01 | 高（固件侧，非 LiuScope） | 固件 V4 MODE 5 对 `01 03 00 00 00 01`（读保持寄存器 地址0×1）**不应答**；同引擎对 addr0×2/×4、addr1×1、addr5×1 均正常应答 | MODE 5 下 GUI Modbus 页 发送该帧 → 600ms 超时「无响应」；或 AI send 同帧 | `q12_gui_addr0count1_in_mode5.png`、`q12_addr0count1_investigation.json` |
| B-02 | 中 | MODE 6（1kHz 满速流）下固件不响应文本命令 `MENU`（LF/CRLF 均无效），只能靠 `KEY 1` 退出 —— 与「命令各模式通用」的台架约定不符；长期满速运行会持续灌满日志缓冲 | MODE 6 运行中 send "MENU" → 流不停 | `q14_terminal_check.json`、`q14_menu_recover.json`、`q14_key1_attempt.json` |
| B-03 | 中 | `settings_set points` 不按文档钳制 50..200000：value=10 被原样接受（GUI 下拉同步显示 10），value 上限未测（10 以下已足证与文档不符） | 断开状态下 `{"cmd":"settings_set","key":"points","value":10}` | `q15_error_paths.json`、`q15_points10_gui.png` |
| B-04 | 低 | STM32 页右键「恢复自动缩放」等右键菜单项正常，但**波形页**「恢复自动缩放」只恢复跟随+自动Y，不恢复历史滑块收窄后的横向窗口宽度；重新应用「显示点数」也不恢复，需再次拖滑块 | 滑块拖窄 → 右键恢复自动缩放 → 跨度仍 ~47 帧 | `q19_autoscale_restored2.png`、`q19_points_reapplied.png` |
| B-05 | 低 | 暗色主题下部分控件近乎不可见：频谱 CSV 选择对话框中**未选中的单选钮不渲染圆圈**（所选状态只能靠排除法判断）；波形页 XY「窗1/窗2 量程 分格」行只见文字标签、未见可交互下拉 | 打开频谱导出 CSV 对话框观察；波形页 XY 区域观察 | `q18_n5_dialog_state.png`、`q19_win1_wide_crop.png`、`q19_xyctrl_fullrow.png` |
| B-06 | 低（记录级） | `spectrum_snapshot.frozen` 在 paused=true（解析确已冻结，db 逐值不变）时仍返回 false；`acquisition_seconds` 为时间型计数器，暂停期间照常增长 | paused=true 后相隔 2s 两次快照比对 | `q18_paused_effect_check.json` |
| B-07 | 低（记录级） | Q19 滑块拖拽试验中一次意外弹出「导出PNG」保存对话框（默认名 wave.png），未能稳定复现，已取消、无文件落地；不排除测试脚本双击杂散触发 | 无法稳定复现 | `q19_zoom3_dragged.png` |

### B. 文档不一致 / 文档缺失

| 编号 | 严重度 | 事项 | 证据 |
|---|---|---|---|
| D-01 | 中 | AI_INTERFACE.md §1.4 称 points「钳制 50..200000」，实测不钳制（=B-03 的文档面） | `q15_error_paths.json` |
| D-02 | 中 | AI_INTERFACE.md §3 称频谱页「提供程序化 export_csv/export_png」，§1.1 命令表亦无 —— 实测两命令不存在，实为 GUI 按钮；「程序化」措辞误导 | `q18_export_cmd_probe.json` |
| D-03 | 低 | AI_INTERFACE.md 未记载响应信封 `tool` 字段（HANDOFF.md 已声明更名 LiuScope，值相符）→ 记「文档缺失」 | 任意响应原文，如 `q01_ping_result.json` |
| D-04 | 低 | §1.5 称 stm32_status「未连接时给明确 note」——实测无 note 字段（不断链部分属实） | `q13_stm32_status_page_not_attached.json` |
| D-05 | 信息 | AI_INTERFACE.md §1.2 实为 22 行示例（19 命令 + 3 条同命令变体），考卷按「19 条」计数 | `q16_examples_all.json` |

### C. 体验/口径建议（不构成缺陷）

| 编号 | 建议 |
|---|---|
| S-01 | `read_lines` 的 `count` 语义（先取最新 N 条原始行、后过滤）建议在 help 参数说明中写明，避免消费方误以为是「N 条命中行」 |
| S-02 | `spectrum_snapshot.frozen` 建议与 paused 联动或在文档说明其真实语义；`acquisition_seconds` 暂停期间继续累计宜注明 |
| S-03 | 导出超长 N 被 6000 帧上限截断时，建议导出完成后在状态栏/弹窗再给一次明确的「已按上限钳制至 Xs/6000 帧」提示（选择对话框内的「导出缓冲: 6000 帧」较隐晦） |
| S-04 | 深色主题下未选中单选钮/部分下拉可见性差，建议提高对比度（HANDOFF 已有浅色主题同类提醒） |
| S-05 | 「恢复自动缩放」建议连同横向窗口宽度一并复位到「显示点数」口径（B-04） |
| S-06 | MODE 6 满速流时建议软件侧对接收洪流给出背压提示（本轮 1kHz 流曾持续灌入 ~150 万行日志缓冲） |

### D. 测试过程说明（诚实记录）
- computer-use 技能在本子代理环境不可用（"Computer Use is not available in subagent"），GUI 操作按考卷「人机协作」口径以 ctypes/SendInput 黑盒完成，未读任何 qt/ 源码。
- Q17 的帧率测量与 CSV 数据中混有固件 MODE 6 残流（Q14 首轮污染的同一根因，当时未察觉）；Q17 的判分口径（CSV 行数 == frame_count）不受数据来源影响，实测精确相等。发现后已用 KEY 1 收回菜单并重测 Q14。
- Q12 的两笔测试期误发事务（从站 41/4，固件静默）留在 modbus_recent 历史中，均 ok:false，可作为「错误从站静默不应答」的额外佐证。
- 收尾状态：theme=dark、baud=115200、protocol=FireWater、points=1000、fill=true、paused=false、sim_off、record 停止、串口断开；固件经 KEY 1 处于菜单态（MODE 6 下 MENU 无效故未再连串口补发）；软件进程已关闭，9101 无监听（仅 TIME_WAIT 残留）、COM9 无占用。
