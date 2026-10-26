# LiuScope v2.0 设计：Linux 设备互联（鲁班猫投屏 + 交互 + 串口网络化）

状态：设计稿 v1（2026-10-27，依据 `docs/research/调研_调试工具与低延迟投屏.md` 的选型结论）。
定位一句话：**软件版开发板 KVM**——网线连接 RK356x（野火 LubanCat 等）开发板，低延迟回传屏幕、键鼠交互、串口网络化，与 LiuScope 的串口调试主业和 AI 控制通道合为一体。

---

## 0. 目标与验收锚点

| 锚点 | 指标 | 依据 |
|---|---|---|
| 投屏端到端延迟 | **≤80ms**（千兆有线，1080p，软件法+慢动作相机复核） | scrcpy 官方 35~70ms、IP-KVM 品类 <100ms |
| 投屏帧率 | 30fps 起步，60fps 为 M4 目标 | scrcpy 30~60fps |
| 输入回路 | PC→板 uinput ≤15ms，体感即时（与视频回路解耦） | Weylus 同款解耦 |
| 串口网络化 | `rfc2217://` 直连 ser2net/NPort，功能与本地串口无差 | pyserial 官方 URL handler |
| 安全 | 控制通道必须鉴权（配对码 + 会话密钥），禁止裸 TCP 暴露注入权限 | RustDesk/Synergy 惯例 |

## 1. 总体架构（研究结论：架构 A，scrcpy 式）

```
┌─ LubanCat (RK356x, Linux) ────────────────┐      ┌─ PC (LiuScope) ─────────────────┐
│ liuscope-server 守护进程(Python)           │      │ qt/ 新增「设备屏」页              │
│  ├─ 视频: ffmpeg-rkmpp 子进程              │ UDP  │  ├─ PyAV 解码 → QOpenGLWidget    │
│  │   kmsgrab → h264_rkmpp(无B帧) → udp://  │─────▶│  │   渲染（OpenGL 直通，禁 QImage）│
│  ├─ 控制: TCP JSON 行协议(鉴权)            │ TCP  │  ├─ 输入事件序列化上行            │
│  │   键鼠下行/心跳/参数/串口透传            │◀────▶│  ├─ 串口页支持 rfc2217:// 地址    │
│  ├─ 输入: python-uinput 注入              │      │  └─ AI 通道扩展(见 §5)           │
│  └─ 串口: 板端 /dev/ttyS*/USB 透传         │      └─────────────────────────────────┘
│ M1 替代物: ser2net(RFC2217) 即可，不必自研  │
└───────────────────────────────────────────┘
```

关键取舍（均出自调研）：
- **不写 MPP C 代码**：视频段用 nyanmisaka FFmpeg-Rockchip 子进程（`-c:v h264_rkmpp -bf 0 -g 120`），C API 文档少是主要风险，子进程法绕开；将来要极致延迟再下潜。
- **不走 RTSP/WebRTC**：RTSP jitterbuffer 引入 0~50ms+、Windows 端要分发 ~100MB GStreamer；WebRTC 复杂度只在跨网段时值得。UDP 裸 H.264 流（仿 scrcpy）+ 应用层 NACK 重索关键帧兜底丢包。
- **捕获栈待定项**：LubanCat 镜像的图形栈（X11 / Weston / 无桌面）决定捕获路线——kmsgrab 需 `cap_sys_admin`；开工第一步是在真板上确认（见 §7 风险）。
- **渲染必须 OpenGL 直通**：PyAV 解码帧直接上 QOpenGLWidget 纹理，禁止经 QImage 拷贝；渲染慢丢帧只显最新（scrcpy 法）。

## 2. 板端 liuscope-server（新目录 `server-rk/`，Python）

单一入口 `python -m server_rk.main`（板端跑 Python 3.10+ 野火镜像均有）：

- **视频进程管理**：起停 ffmpeg-rkmpp 子进程；参数（分辨率/帧率/码率）经控制通道由 PC 下发，M4 做自适应。
- **控制服务**：TCP `:9201`，JSON 行协议（与 LiuScope AI 通道同风格，复用心智）；命令：`hello/pair`（配对码换会话密钥）、`video_start/video_stop/video_params`、`input`（键鼠事件批）、`serial_open/serial_write/serial_read`、`status`、`ping`。
- **输入注入**：python-uinput 建虚拟键鼠；PC 端事件批（≤10ms 一批）经控制通道下行。
- **串口透传**：M1 直接推荐用户跑 ser2net（LiuScope 文档给配置样例）；server 内置透传是 M2+ 的收拢项（免装 ser2net、与鉴权统一）。
- **权限落地**：systemd 最小权限服务 + udev 规则（`/dev/uinput` 给 input 组、kmsgrab setcap），不鼓励全程 root。
- 产物形态：`server-rk/` 纯 Python 包 + `install.sh`（systemd/udev 安装脚本），板端无需交叉编译。

## 3. PC 端（qt/ 扩展）

1. **串口页**：端口下拉支持任意 pyserial URL（`rfc2217://192.168.x.x:7000`），下拉列出本地口 + 允许手输 URL；RFC2217 协商的波特率由板端执行，UI 标注来源。改动集中在 SerialWorker 建口处（`serial_for_url` 统一入口），预计极小。
2. **设备屏页（新）**：地址+配对码 → 连接 → 视频窗（QOpenGLWidget）+ 键鼠捕获（焦点内劫持，Esc 释放）+ 状态栏（帧率/码率/延迟 overlay，M3：编码/网络/解码分段显示，抄 Moonlight overlay 思路）。
3. **AI 通道扩展**（见 §5）。

## 4. 协议细节（v2 草案）

- **控制通道**：TCP 9201；首包 `hello{v,nonce}` → `pair{code}` → `session{key}`（PSK 派生，控制面后续可选 libsodium secretbox；M1 可先明文+配对码，M2 前必须加密）。
- **视频面**：UDP 9202；包 = `[seq u32][ts_ms u32][flags u8][payload NAL]`；PC 丢包检测（seq 跳变）→ 控制通道发 `resync{from_seq}` → 板端强制 IDR；静止画面 ffmpeg 无帧输出即零带宽（scrcpy 同款特性）。
- **键鼠批**：`{"cmd":"input","events":[{t:"key|mouse|wheel",...,ms}]}`，批间隔 ≤10ms。

## 5. AI 通道 v2（含本轮遗留的「画面调整」缺口）

v2 把「AI 可调观测项」补全（本轮 E2E Q19 盘点的缺口清单为输入）：
- 频谱/XY 观测项命令化：`settings_set` 扩键或新命令 `spectrum_adjust{ref_db,div_db,marker}`、`xy_adjust{persist,divs}`；
- 新命令 `screen_snapshot{quality}`（设备屏当前帧 JPEG base64）、`screen_click{x,y,btn}`、`screen_type{text}`——AI 能「看板子的屏幕、操作板子的界面」，与串口调试数据同通道供 AI 消费（这是相对竞品的差异化）；
- `device_status`（server 版本/视频参数/延迟统计）。

## 6. 里程碑（验收写死）

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **M1 串口网络化**（~1 周） | 串口页 rfc2217 + ser2net 文档样例；无新硬件 | 对 COM 口自环：本地口与 rfc2217 口行为一致；断线重连 |
| **M2 视频单向** | server-rk 视频段 + 设备屏页只读播放 | 1080p30 流畅，端到端 ≤80ms（软件法实测记录） |
| **M3 输入 + overlay** | uinput 注入 + 延迟分段 overlay | 键鼠操作体感即时；输入回路 ≤15ms；overlay 三段延迟能区分瓶颈 |
| **M4 体验收口** | 码率/帧率自适应、加密控制面、AI screen_* 命令 | 参数漂移场景不断流；AI 能看屏点按完成一次板端 GUI 操作 |

## 7. 风险与开工前置

1. **真板图形栈确认**（开工第一件事）：LubanCat 镜像是 X11/Weston/无桌面？kmsgrab 是否免 root 可用？——决定捕获参数与 udev 脚本。
2. **ffmpeg-rkmpp 可得性**：野火镜像若无预编译，用 otp/apt 的 rockchip ffmpeg 或自编译（一次踩坑写进安装脚本）。
3. **Windows 端 PyAV 打包**：PyInstaller 含 PyAV 体积 +~30MB，确认 license 合规（LGPL 动态链接）。
4. **PySide6 OpenGL 打包**：确认 PyInstaller 钩子带齐 QtOpenGL 模块；offscreen 自测对 QOpenGLWidget 的覆盖方式（离屏 GL 上下文）需验证。
5. 仓库策略：同仓库开 `v2-linux-link` 分支，`server-rk/` 新目录 + `qt/` 增量；master 仍只进稳定版（保持「云端主线=Qt 单机版」的治理不变，v2 成熟后合回）。
