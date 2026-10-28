# server-rk — LiuScope v2 板端服务（M2 骨架）

对应设计稿：[docs/设计_Linux设备互联_v2.md](../docs/设计_Linux设备互联_v2.md)。
目标板：野火 LubanCat 等 RK356x Linux 开发板（Python 3.10+，仅需 stdlib + pyserial）。

## 目录结构（M2 骨架）

    server-rk/
    ├── server_rk/           Python 包（python -m server_rk 启动）
    │   ├── __init__.py      包版本与模块职责索引
    │   ├── __main__.py      入口：--port(视频 UDP, 默认 9202) --control(TCP, 默认 9201) --serial --verbose
    │   ├── protocol.py      JSON 行协议编解码 + hello/challenge/pair 配对状态机（设计稿 §4）
    │   ├── control.py       TCP 9201 控制服务：每连接一线程 + 13 条命令分发表（依赖可注入）
    │   ├── video.py         FFmpegVideoSource（ffmpeg 子进程起停 + build_args）+ packetize 打包
    │   ├── input_inject.py  uinput 键鼠注入：Qt 键码 → Linux input 事件码映射，不可用即降级
    │   └── serial_bridge.py 板端串口透传：serial_for_url（/dev/tty*、rfc2217://、loop:// 通吃）
    ├── test_server_rk.py    本机直跑测试（无需真板/ffmpeg/uinput）
    └── ser2net.yaml.example M1 的 ser2net 配置样例

### 模块一句话

| 模块 | 职责 |
|---|---|
| protocol | 行协议编解码（一行请求一行响应，错误统一 `{"ok":false,"error":…}`）、配对状态机（challenge 绑定 + 配对码明文比对，M3 换 PSK 派生）、未配对门禁 |
| control | TCP 9201 服务、命令分发表（ping/status/hello/pair/video_*/input/serial_*/close）、`Deps` 依赖注入容器（video/injector/serial 均可换 fake 单测） |
| video | `FFmpegVideoSource` 只管命令行构造与子进程生命周期（kmsgrab→nv12→h264_rkmpp 无 B 帧→udp，§5.3）；`packetize` 按 `[seq u32][ts_ms u32][flags u8]` 切包，PC 端 H264UdpSource 须对称实现 |
| input_inject | Qt.Key_* → Linux input 事件码纯数据映射表 + uinput 批注入（key/mouse_move/mouse_button/wheel）；无 uinput 记日志不抛异常 |
| serial_bridge | 控制通道 serial_open/write/read → `serial_for_url` 单口占用透传，重开即换口/换波特率 |

### 本机测试（仓库根目录运行，Windows 即可）

    python server-rk/test_server_rk.py
    # 末行输出 SERVER-RK TESTS OK

覆盖：配对全流程（含错误配对码拒绝、未配对拒绝业务命令）、命令分发表（fake 依赖
直驱）+ TCP 127.0.0.1 全链路（随机端口）、build_args 快照断言、packetize 对称性
（模拟 PC 端拆包还原原流）、loop:// 串口读写闭环、Qt 键码映射、无 uinput 优雅降级。

### 视频拆包约定（PC 端 H264UdpSource 实现须一致）

UDP 包 = `[seq u32 大端][ts_ms u32 大端][flags u8][payload ≤1200]`；
flags：`0x01` 关键帧、`0x02` 分片首包、`0x04` 分片末包（单包 NAL 首末同置）；
seq 每 UDP 包 +1（u32 回绕）；PC 端按「首包起 → 末包止」拼接 payload 还原 NAL，
seq 跳变 → 控制通道 `resync{from_seq}` → 板端强制 IDR（M3 实现）。

### 板端运行（真板验证项见下方清单）

    cd server-rk && sudo python3 -m server_rk --verbose
    # 配对码：放 server_rk/pairing_code.txt 或设环境变量 SERVER_RK_PAIRING_CODE（缺省 246810）

### 真板上才验证的清单（骨架期遗留）

- kmsgrab 需要 `cap_sys_admin`（setcap 或 udev 规则），以及镜像图形栈（X11/Weston/无桌面）对捕获路线的影响（设计稿 §7.1）；
- `h264_rkmpp` 编码器名与 nyanmisaka ffmpeg-rkmpp 的可得性（野火镜像是否预编译，§7.2）；`-s` 缩放对 rkmpp 的支持度；
- `/dev/uinput` 权限组（udev 规则给 input 组）与 python-uinput 安装（§2 权限落地）；
- GOP `-g fps*2` 与码率默认值在 1080p30 实测的观感调优；`resync`→强制 IDR 的 M3 落地。

## M1（已完成）：串口网络化 —— 板端 ser2net + PC 端直连

M1 不需要本目录代码：板端装 **ser2net**（RFC2217），LiuScope 串口页直接手输
`rfc2217://<板子IP>:7000` 连接（v2 分支已支持，pyserial 原生协议，波特率由板端协商）。

板端安装与配置：

```bash
sudo apt install ser2net
sudo cp ser2net.yaml.example /etc/ser2net.yaml   # 按实际串口设备号/波特率修改
sudo systemctl restart ser2net
```

验收（对齐设计稿 M1）：LiuScope 串口页以 `rfc2217://` 连接后，收发行为与本地 COM 口一致；
`loop://` 回环全链已在 selftest `NET SERIAL URL` 组覆盖。M2 起串口透传由 server 内置
（`serial_bridge`，与配对鉴权统一），ser2net 方案保留并存。

## RK3562(鲁班猫)Linux 板上架记录(2026-10-28)

**板端状态:server-rk 已部署并验证**(部署走调试串口带外通道,详见 `_verify/rk_console.py` /
`_verify/rk_deploy_serial.py`;登录凭据不落库,问用户要)。

| 项 | 状态 |
|---|---|
| 系统 | Debian 12,Python 3.11.2,主机名 lubancat,用户 cat |
| 网口 | eth0 静态 192.168.137.2/24(上次 ICS 遗留);本次运行时追加 192.168.1.50/24;**物理层故障待换线**(对端只广播 10baseT,RX 全死) |
| 串口调试 | COM19(USB-TTL CH340)@ **1500000**;打开即松 DTR/RTS |
| server-rk | ~/server_rk/(0.2.0-m2),pyserial 3.5 装在 ~/.local;板上自检全绿 + TCP 9201 协议握手(hello 挑战)通过 |
| ffmpeg | 5.1.6(Debian 版)**无 h264_rkmpp 编码器**——M2 视频联调需先落 libx264 回退或装 nyanmisaka ffmpeg-rkmpp |
| uinput | /dev/uinput 存在但 root-only(uinput=False,注入降级,M3 处理) |
| KMS | /dev/dri card0/card1 + renderD128/129 ✓ |

**重要踩坑:RK FIQ 调试器**

1. 串口输入流中出现字面量 **`fiq`** 三连字符 → 内核 FIQ 调试器立即劫持控制台
   (打 "Welcome to irq debugger mode",bash 从此收不到输入,命令全部被吞)。
   处置:`console` 命令交还;传输编码必须保证线上永不出现该子串
   (rk_deploy_serial.py 用字母表置换 f→!、i→#、q→^,板端 `tr '!#^' 'f'i'q'` 还原——
   还原命令自身也要拆词!)。
2. 串口 BREAK 会触发 FIQ 调试器(SysRq 尝试的副产物);长时间空闲也可能进入 debug 模式。
3. 控制台交互一律 expect 式(等模式出现再发下一步),固定延时必错拍。

**网络修复后的一键验收**(网线换好/ICS 开启后):

```bash
# 板端
cd ~ && sudo python3 -m server_rk --verbose    # 配对码缺省 246810
# PC 端:LiuScope「Linux 支持」页填板子 IP(192.168.1.50 或 192.168.137.2)+ 配对码;
# 串口页 rfc2217://192.168.1.50:7000(需先装 ser2net,样例见 ser2net.yaml.example)
```
