# serialscope-qt（qt-frontend-spike 支线）

用 PySide6（Qt for Python）重写前端的实验支线。**稳定并通过合并评估前不会合入 master**；
master（PowerShell / WinForms）继续独立维护，两边版本号平行：

| 线 | 版本口径 | 说明 |
|---|---|---|
| master（主线） | `app_version` 1.1.0（开发中）→ 下个 Release v1.1.0 | 现有全部功能，真机回归后发布 |
| qt-frontend-spike（支线） | 0.4.0-qt，0.x = 实验期 | Qt 新前端，主线功能已全部复刻，待真机回归 |

## 运行与自测

```powershell
python -m pip install PySide6-Essentials pyserial pyqtgraph   # 一次性环境准备（已装可跳过）
python qt/main.py                # 运行（想不占控制台可用 pythonw qt\main.py）
python qt/main.py --selftest     # 离屏自测（无需显示器/串口，12 组断言）
```

打包（实测 101 MB 单文件 exe，全套自测通过；`--windowed` = 启动不弹 cmd 黑窗）：

```powershell
python -m PyInstaller --noconfirm --onefile --windowed --paths qt --name serialscope-qt qt/main.py
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
  CSV 导出 + 模拟数据；频谱/XY 相位/场景预设/录制尚未移植（见下方“未复刻清单”）
- **M3 收发增强（已完成，b989cc4）**：ASCII/HEX 双格式收发、换行模式（无/LF/CR/CRLF）、
  发送历史（⬆/⬇ 回翻）、日志查找条（高亮 + 第 i/N 处）、文件分块发送（4 KiB/20ms）
- **M4 外围（已完成，cc14aab）**：Modbus RTU 调试台（8 功能码手动事务 + Watch 合并轮询，
  core/modbus.py 逐字对齐主线插件）、检测更新（GitHub Releases API，线程池后台 + 24h 节流）、
  PyInstaller 打包与体积实测
- **M4.5 频谱仪（已完成，本次）**：FFT dB 谱（core/dsp.py 移植 C# WaveDsp：最长 2^k 段 +
  汉宁窗 + 单边归一化 + 20log10 底噪 -140）+ 峰值保持/指数平均 + 手动 RBW（fs/RBW 反推 FFT
  长度）+ 参考电平/dB每格标度 + 瀑布图（色带逐段对齐主线 ColorMap，最新在顶、行数 120 上限）
  + Marker（悬停读数）+ 主频标注（跳过 DC）
- **M5 合并评估**：真机（RK3562/CH340）回归 + 下方未复刻清单补齐或豁免，
  由你拍板：合入 master（主线退役或双轨） / 继续观察 / 关闭支线

## 未复刻清单（M5 前补齐或明确豁免）

- XY 相位图（IQ 李萨如/星座图，余晖累积）
- 波形场景预设（wave_presets.txt）与会话录制（JSONL 回放）
- 自动日志落盘 / 日志导出 / 控制通道（headless 指令面）
- 行悬停格式互转（HEX↔文本↔float32 tooltip）
- 主线 Demo 模式（-Demo 演示截图链路，属开发工具，可豁免）

## 目录

```
qt/
  main.py               入口（--selftest 转自测）
  core/rx_buffer.py     行缓冲/空闲落显/积压（纯逻辑，假时钟可测）
  core/serial_worker.py QThread 串口读写（端口在工作线程打开）
  core/hexutil.py       HEX 文本互转（0x 前缀/多分隔符，非法 token 报错）
  core/wave_state.py    三协议解码 + 视图模型 + 滤波缓存（纯逻辑，可测）
  core/dsp.py           滑动平均/零相位低通/FFT 幅度谱/瀑布色带（numpy，对齐主线 C# WaveDsp）
  core/modbus.py        CRC16/组帧/响应解析/Watch 合并轮询（纯逻辑，可测）
  core/update_check.py  版本比较 + GitHub Releases 检查（opener 可注入）
  core/settings.py      gui_settings_qt.ini 读写（QSettings IniFormat）
  core/__init__.py      APP_VERSION = '0.3.0-qt'
  ui/main_window.py     主窗体（终端/波形/Modbus 三页签 + 帮助菜单）
  ui/wave_window.py     pyqtgraph 波形页
  ui/spectrum_panel.py  频谱仪页（FFT dB 谱 + 瀑布 + Marker）
  ui/modbus_panel.py    Modbus 调试台页（transport 注入式收发）
  ui/find_bar.py        查找条
  ui/history_edit.py    发送历史输入框
  ui/theme.py           深色 QSS + 日志行配色
  selftest.py           离屏自测（12 组：RxBuffer/HEX/主窗体/协议/波形页/M3/Modbus×2/更新×2/频谱×2）
```
