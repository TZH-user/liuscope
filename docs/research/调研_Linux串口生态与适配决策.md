# 调研报告：Linux 串口生态与 LiuScope 适配决策

- **调研日期**：2026-10-28
- **问题**：面向 Linux 适配，其他厂家串口工具做了什么？按需求度决定 LiuScope 跟进哪些。
- **可信度标注**：【官方】项目官方仓库/文档；【第三方】教程/社区对比；【经验】嵌入式社区公认坑点

## TL;DR（决策）

按需求度三档处理（对应 v1.3.0 已实现 / 排期 / 不做）：

| 档 | 结论 |
|---|---|
| **已做（v1.3.0）** | ① 打开失败按平台给处置提示（dialout 权限 / ModemManager·brltty 抢占 / Windows 占用）；② 端口下拉 VID/PID 芯片标注；③ 断线自动重连（tio 同款能力，适配热插拔重枚举）；④ 网络串口 URL 直通（rfc2217/socket，等价 Linux 下远程板卡接入） |
| **排期（随 v2 Linux 版）** | ⑤ `/dev/serial/by-id` 稳定命名引导（现在手输即用，缺的是 UI 提示）；⑥ udev 规则样例进文档；⑦ Linux 打包（AppImage/deb，随 v2 一起做） |
| **不做** | ⑧ udev 规则自动安装（动系统配置，越界）；⑨ 自带驱动（内核态，非串口工具职责） |

## 1. 竞品在 Linux 上做了什么

**tio**（【官方】https://github.com/tio/tio ，查证 2026-10-28）——嵌入式开发者向的 TTY 工具，特性清单对我们的参照价值最大：自动探测串口设备、**断线自动重连**（设备重插后自动接回）、modem line 切换（DTR/RTS）、hex 输入输出模式、行时间戳、日志到文件、配置 profiles、RX/TX 统计。
→ LiuScope 对照：自动重连/modem 线控/hex/日志/RX-TX 统计均已具备或 v1.3.0 补齐；profiles ≈ 我们的设置记忆；缺 tio 的 `--list` 彩色设备清单（我们有下拉+标注，等价）。

**picocom / minicom / screen**（【第三方】对比教程 https://cmaven.github.io/en/etc/linux-serial-terminal-programs ，查证 2026-10-28）——经典终端三件套，无 GUI、无波形/数据能力。共同前置：`sudo usermod -aG dialout $USER`（权限）。
→ 结论：GUI 数据向是 LiuScope 的差异位，无需对标终端仿真能力。

**PuTTY（Linux 版）/ CuteCom / Serial Studio**（【经验】跨平台串口 GUI 惯例）：CuteCom 在 Linux 发行版仓库常见但功能薄；Serial Studio 走「串口→仪表盘」路线并发行 deb/AppImage——**Linux GUI 发行形态**是我们 v2 版需要补的（排期 ⑦）。

## 2. Linux 串口两大高频坑（我们已内置提示）

1. **权限**：`Permission denied` → 加 dialout 组、重新插拔（【第三方】同上教程；几乎所有工具 README 都写这条）。
2. **端口被抢**：Ubuntu 桌面默认 **ModemManager** 会探测新插入的串口（打开口、发 AT 探针），**brltty** 会抢 CP210x——表现为打开失败/设备时有时无（【经验】嵌入式社区高频问题，Ask Ubuntu 多个高票问答）。处置：`sudo systemctl disable --now brltty ModemManager` 或加 udev 规则排除。
   → LiuScope v1.3.0 在打开失败时按错误文本映射这两条处置提示（qt/core/port_hints.py，win32/linux 各自口径），不自动改系统配置（不做档 ⑧）。

## 3. 稳定命名与热插拔

- 设备重插后 `/dev/ttyUSB0/1` 可能互换 → 惯用解法 `/dev/serial/by-id/…`（按 USB 序列号稳定）。LiuScope 端口下拉可手输任意路径（v2 M1 起的 URL 直通同样接受），pyserial 原生支持；v2 排期把 by-id 提示写进下拉 tooltip 与手册（⑤）。
- 热插拔监听：LiuScope 用 2s 轮询扫描（Windows/Linux 通用），配 v1.3.0 自动重连后，「烧录后 COM 重枚举」场景工具自动接回——对齐 tio 的自动重连体验。

## 4. 与 v2（Linux 版/设备互联）的关系

v2 的「Linux 支持」页（原设备屏）解决的是**板子→PC 的屏幕回传与交互**（server-rk + UDP H.264），与本报告的**PC 端串口工具 Linux 化**互补：前者是 LiuScope 连接 Linux 板卡的能力，后者是 LiuScope 本体在 Linux 桌面运行的能力（v2 M4+ 排期：PySide6 本就跨平台，工作量主要在打包 ⑦ 与 udev/by-id 引导 ⑤⑥）。
