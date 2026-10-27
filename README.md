# <img src="qt/assets/liuscope_256.png" width="42" align="top" alt="LiuScope 图标：垂柳与示波水线"/> LiuScope（柳镜）· RK3562 串口收发工具 V6

![Windows](https://img.shields.io/badge/Windows-10%20%2F%2011-blue)
![Qt](https://img.shields.io/badge/%E4%B8%BB%E7%BA%BF-Qt%20(PySide6)%20v1.2.0-41CD52)
![安装](https://img.shields.io/badge/%E5%85%8D%E5%AE%89%E8%A3%85-%E6%89%93%E5%8C%85exe-F48771)
![License](https://img.shields.io/badge/License-GPL--3.0-blue)

**一个串口调试终端**：收发日志、HEX⇄文本互转、三种协议实时波形（FireWater / JustFloat / RawData）、频谱仪 + 瀑布图、XY 相位图、Modbus RTU 主站调试台、STM32 烧录调试页、日志查找与时间戳定位、文件发送、命令历史、AI 控制通道（TCP JSON 行协议，供其他 AI/脚本遥控本工具）。面向 RK3562 / STM32 等嵌入式联调。

> v1.2.0 起软件更名 **LiuScope**（中文别名「柳镜」，原名 serialscope）。作者：折柳。

| 终端页（收发日志 + 右对齐发送行） | 波形页（FireWater 三通道） |
|---|---|
| ![终端页](docs/screenshots/main_dark.png) | ![波形页](docs/screenshots/wave_firewater.png) |

| 频谱页（FFT + 瀑布，频率轴对齐） | Modbus 页（事务 + 实时监视） |
|---|---|
| ![频谱页](docs/screenshots/spectrum_dark.png) | ![Modbus 页](docs/screenshots/modbus_watch.png) |

| STM32 烧录页 | 设置页（主题 / 曲线填充 / CLI 路径） |
|---|---|
| ![STM32 页](docs/screenshots/stm32_dark.png) | ![设置页](docs/screenshots/settings_dark.png) |

| 关于页 |
|---|
| ![关于页](docs/screenshots/about_dark.png) |

<details>
<summary><b>浅色主题</b>（设置页一键切换，全页跟随）</summary>

| 终端页（浅色） | 设置页（浅色） |
|---|---|
| ![终端页浅色](docs/screenshots/main_light.png) | ![设置页浅色](docs/screenshots/settings_light.png) |

</details>

---

## ✨ 功能一览

### 收发与日志（终端页）
- ASCII / HEX 双格式收发，换行符可选 CR / LF / CRLF
- **网络串口（v2 分支）**：端口框可手输 pyserial URL 直连——`rfc2217://板子IP:7000`（板端 ser2net/NPort，波特率远程协商）、`socket://`（TCP 裸透传）、`loop://`（本机回环自测）；协议见 [docs/设计_Linux设备互联_v2.md](docs/设计_Linux设备互联_v2.md)
- **无换行也不丢**：板卡发送不带换行符时，残行静默约 0.5 秒自动落显（不用等 8KB 强制线）
- **聊天式发送右对齐**：TX 行靠右显示、RX/SYS 靠左，收发对话一眼分明（窗口缩放自动重排）
- **悬停互转**：鼠标停在任意日志行 0.4 秒，HEX 行自动解码为 文本(UTF-8) / 十进制 / float32(小端)，文本行给出 UTF-8 HEX（悬停条目头行等效于悬停其正文）
- **波形悬停读数**：波形绘图区悬停出十字线与读数框——首行 x 坐标，随后每条可见曲线一行「曲线名: y值」（同曲线配色，y 取真实采样点）
- STM32 HardFault 寄存器串自动解码；RX/TX 字节统计、积压常显、自动保存到 `logs\`、一键导出

### 查找与定位（Ctrl+F）
- **输入即搜**：打字过程 150ms 防抖实时匹配，全部命中弱色高亮 + 当前命中强色高亮，命中行自动滚到视口中间
- Enter / Shift+Enter（或 F3 / Shift+F3）向后 / 向前循环跳转，到头自动回绕，不分大小写，计数显示「第 m/N 处」
- **时间戳定位**：直接输入 `20:57` / `20:57:38.5` / `2026-10-24 20:57:38`，跳到第一条 ≥ 该时刻的日志条目（只给时刻时日期取日志首条）

### 发送增强
- **⬆ / ⬇ 回翻发送历史**（cmd 风格）：上限 200 条，⬇ 走到底恢复正在编辑的草稿；多行编辑时方向键仍是移动光标
- **文件发送**：任意文件按 4KiB 分块流式发送（20ms 间隔），大文件不卡界面，发送中再点一次即停止
- **导出 PNG/CSV 选择**：波形页「导出」菜单按钮——导出 PNG（当前时域绘图区单图，含坐标轴与网格）或导出 CSV（全量缓冲，通道名表头，Excel 直开）

### 波形示波（波形页）
- **FireWater / JustFloat / RawData** 三协议实时曲线：显示点数可调（工具行「显示点数」）、自动/手动 Y 轴、滤波（滑动平均 / 零相位低通）、通道芯片改名持久化、历史滑块条（拖宽度/拖位置/滚轮缩放，主线同款语义）
- **频谱页**：FFT dB 谱（汉宁窗 ENBW 口径）+ 峰值保持 + 指数平均 + 手动 RBW + Marker + 主频标注；**频谱与瀑布 x 轴 (0, fs/2) 双向联动**（同一频点两图垂直对齐，缩放/平移跟随）；导出 PNG（谱图+瀑布拼图，顶部参数标注：采样率/FFT 长度/窗函数/RBW/峰值保持/平均/数据时间范围/主频）；导出 CSV（「最新一帧瞬时频点」或「最近 N 秒」，N 超累计采集时长自动钳制并提示）；「清空」一键复位轨迹/瀑布/导出缓冲
- **XY 相位图**（IQ 星座/正交性）：1/2/4 正方格窗口、每窗 X/Y 源通道与量程独立可调、余晖衰减/常驻、分格 4/5/8/10/16、线/点两种画法、量程切换余晖拉伸迁移、进入即回铺最近窗口、悬停坐标读出、格右键中文菜单（恢复自动缩放/导出/显示网格/清除余晖）
- 无设备时「模拟数据」一键验证整条链路（走真实解码管线）

### Modbus RTU（Modbus 页）
- **事务页（静态手动）**：功能码 01/02/03/04/05/06/0F/10；CRC16 自动生成与响应校验；异常码（01~0B）中文说明；寄存器解码 HEX / 十进制 / 有符号 / float32(ABCD / CDAB)；地址与数值支持十进制和 `0x` 十六进制；每次事务回写主日志（Ctrl+F 可检索），AI 通道 `modbus_recent` 可查最近事务
- **实时监视（动态 Watch）**：把读地址加入 Watch 表；轮询引擎把同 (从站, 功能码) 的项**合并成一次读请求**（近邻间隙 ≤4 合并，单次上限 125 寄存器 / 2000 线圈），响应按地址偏移切回各行；数值变化闪色（涨绿 / 跌红 / 其余琥珀）；**双击行回填事务区**改参数或下发写入

### STM32 烧录页（STM32CubeProgrammer CLI）
- **状态区**：CLI 路径四级解析（设置键 → C:\ST → PATH → 常见位置自动扫描）、ST-LINK 探测（多探针可选）、连接模式（UR / HOTPLUG / NORMAL）
- **操作**：连接目标（芯片型号/Flash/内核/电压信息区）、软复位 / 硬复位、hex 烧录（带 `-v` 校验）与 bin 烧录（基址可改）、全片擦除、读保护解除（危险操作二次确认）、OB（Option Bytes）读取；底部 CLI 原始输出日志
- 无 CLI / 无 ST-LINK / 路径失效均给中文提示并禁用按钮，不崩；关窗自动终止在跑的子进程

### 设置与关于
- **设置页**：主题深色 / 浅色一键切换（即时生效 + 写设置记忆，重启保持，绘图区/曲线/芯片/悬停读数整套换肤）；「曲线填充」开关（曲线下方同色浅填充，主线同款 alpha）；STM32CubeProgrammer CLI 路径选择与记忆（留空 = 自动探测）
- **关于页**：品牌 LiuScope（柳镜）、版本、作者 折柳、许可证 GPL-3.0、仓库地址

### AI 控制通道（默认开启，详见 [AI_INTERFACE.md](AI_INTERFACE.md)）
- TCP `127.0.0.1:9101`，一行一个 JSON 请求、逐行回 JSON 响应，与 GUI 共存（人机协作）
- **help 命令表**：结构化返回全部 19 条命令（命令名/参数/说明/示例），与文档同源
- **读数据**：`wave_snapshot` / `spectrum_snapshot`（频点+dB+主频+参数+瀑布最近帧）/ `xy_snapshot` / `modbus_recent` / `stm32_status` / `read_lines`（最新 N 行快照；`follow=true` 连续推送新行：interval 节流 0.05~60s、max_lines 上限 2000、正则过滤，`stop`/断开退出）
- **控制**：`send` / `set_protocol` / `wave_clear` / `wave_sim` / `record` / `settings_set`（波特率/串口/协议/主题/显示点数/曲线填充/暂停 7 个可写键，改后 UI 控件同步跟随，返回改前/改后值；波特率与串口已连接时拒绝并提示先断开）
- 无头模式：`python qt/headless_main.py`（AI 独占串口，捕获/回放/触发器/期望测试/复位烧录，退出码 0/2/3/4/5）

## 🚀 快速开始

**运行**：安装 Python 3.10+ 后——

```powershell
python -m pip install PySide6 pyserial pyqtgraph numpy
python qt/main.py          # 想不占控制台可用 pythonw qt\main.py
```

**打包单文件 exe**（打包步骤产出，约 100MB 自带全部依赖，双击即用；`--windowed` = 启动不弹黑窗）：

```powershell
python -m PyInstaller --noconfirm --onefile --windowed --paths qt --name LiuScope qt/main.py
```

RK3562 推荐先试 `1500000 / 8 / None / One / None`；板子按协议发数据即可看到曲线，波形页「格式说明」有三种协议的帧格式与单片机示例代码。

**离屏自测**（无需显示器/串口，33 组断言）：

```powershell
python qt/selftest.py
```

## ⌨️ 快捷键

| 按键 | 作用 |
|---|---|
| `Ctrl+F` | 呼出 / 关闭查找栏 |
| `Enter` / `Shift+Enter` | 查找：下一个 / 上一个 |
| `F3` / `Shift+F3` | 查找：循环跳转 |
| `Esc` | 关闭查找栏 |
| `⬆` / `⬇` | 发送区：回翻发送历史 / 恢复草稿 |

## 🧪 自检与测试

```powershell
# 离屏自测（33 组：协议/终端/热插拔/查找/录制/控制通道/headless/协议/波形/频谱/Modbus/
# 更新/主题/STM32 假 CLI/AI 通道扩展/七页签集成……）
python qt/selftest.py
```

## 📁 目录结构

```
LiuScope/
  qt/                      ★ Qt 主线（v1.2.0）：core（纯逻辑）/ ui（分层窗体）/
                           selftest.py（离屏自测 33 组）/ headless_main.py（无头管线）
  docs/                    screenshots/ 截图
  AI_INTERFACE.md          AI 控制通道 + 无头模式接入指南（命令表与 help 同源）
  qt/README_QT.md          Qt 主线里程碑（M1~M5 与 v1.2.0 收口）
  LICENSE                  GPL-3.0
```

> **PowerShell 存档版（v1.0.0 封版）已整体移至 [ps-legacy 分支](https://github.com/TZH-user/liuscope/tree/ps-legacy)**，不再随主线维护；零安装 PowerShell 实现与其插件框架请切到该分支查看。

## ⚠️ 已知限制

- **频谱「最近 N 秒」CSV 导出**受导出历史容量上限约束（6000 帧 / 2000 万单元格，参数变化会作废历史重计）；超出时按实际采集时长导出并在界面提示
- **AI 控制通道默认端口 9101**：被其他实例占用时本实例不开启（界面有提示），先结束占用进程再重启
- **已落显的历史日志行**保留切换主题当时的配色，仅新日志行随当前主题着色（逐行上色的既有机制）
- 打包 exe 约 100MB（PyInstaller 自带 Python 运行时，换体积可改用 `--onedir`）

## 📄 更多文档

- [CHANGELOG.md](CHANGELOG.md) — 版本变更日志（SemVer + Keep a Changelog）
- [docs/用户手册_v1.2.md](docs/用户手册_v1.2.md) — v1.2 新功能手册（STM32 烧录页 / AI 通道速用 / 频谱导出口径 / 主题与设置）
- [docs/设计_Linux设备互联_v2.md](docs/设计_Linux设备互联_v2.md) — 下一版：鲁班猫投屏 + 交互 + 串口网络化（含调研与里程碑）
- [qt/README_QT.md](qt/README_QT.md) — Qt 主线里程碑（M1~M5、v1.2.0 收口）与实现口径
- [AI_INTERFACE.md](AI_INTERFACE.md) — AI 控制通道（TCP 9101 JSON 行协议）+ 无头模式接入指南

## 📃 许可证

以 [GPL-3.0](LICENSE) 发布：可自由使用、修改与再分发，衍生作品须同样以 GPL-3.0 开源。作者：折柳。
