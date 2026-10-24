# serialscope-qt（qt-frontend-spike 支线）

用 PySide6（Qt for Python）重写前端的实验支线。**稳定并通过合并评估前不会合入 master**；
master（PowerShell / WinForms）继续独立维护，两边版本号平行：

| 线 | 版本口径 | 说明 |
|---|---|---|
| master（主线） | `app_version` 1.1.0（开发中）→ 下个 Release v1.1.0 | 现有全部功能，真机回归后发布 |
| qt-frontend-spike（支线） | 0.1.0-qt 起，0.x = 实验期 | Qt 新前端，见下方里程碑 |

## 运行与自测

```powershell
python -m pip install PySide6-Essentials pyserial   # 一次性环境准备（已装可跳过）
python qt/main.py                # 运行
python qt/main.py --selftest     # 离屏自测（无需显示器/串口）
```

## 为什么试 Qt

主线的一切 UI 痛点都源自 WinForms 无样式系统：深色主题要逐控件自绘 + Win32 消息劫持。
Qt 的 QSS 是纯 CSS 写法，一套声明全局生效；波形可用 pyqtgraph（GPU 加速、百万点级）；
代价是分发要带 Python 运行时（PyInstaller 打包约 40-80MB，主线零安装优势会失去）。

## 里程碑（合并评估的验收线）

- **M1 骨架闭环（本次）**：模块化工程（core 纯逻辑 / ui 分层）、连接/断开、RX/TX 着色日志、
  行缓冲 + 空闲落显 + 积压常显（与主线同构语义）、离屏自测
- **M2 波形**：pyqtgraph 曲线 + FireWater/JustFloat/RawData 三协议解析（core/protocol.py，
  从主线协议语义移植）+ 频谱/XY 相位评估
- **M3 收发增强**：HEX 双格式、查找与时间戳定位、发送历史、文件分块发送
- **M4 外围**：Modbus RTU 插件位、检测更新（复用主线 Releases API）、
  PyInstaller 打包与体积实测
- **M5 合并评估**：与主线功能对齐清单逐项过 + 真机（RK3562/CH340）回归，
  由你拍板：合入 master（主线退役或双轨） / 继续观察 / 关闭支线

## 目录

```
qt/
  main.py               入口（--selftest 转自测）
  core/rx_buffer.py     行缓冲/空闲落显/积压（纯逻辑，假时钟可测）
  core/serial_worker.py QThread 串口读写（端口在工作线程打开）
  core/__init__.py      APP_VERSION = '0.1.0-qt'
  ui/main_window.py     主窗体
  ui/theme.py           深色 QSS + 日志行配色
  selftest.py           离屏自测（RxBuffer 单测 + 主窗体管线）
```
