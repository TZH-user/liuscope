# 交接说明 — LiuScope（柳镜）v1.2.0

**日期：** 2026-10-27
**版本：** master @ v1.2.0（软件已由 serialscope 改名 **LiuScope**，中文别名「柳镜」，作者 折柳）

---

## 当前状态

| 指标 | 状态 |
|---|---|
| master 分支 | ✅ 仅 Qt 主线（PS 存档整体移至 `ps-legacy` 分支，封版 v1.0.0） |
| 仓库 | `github.com/TZH-user/liuscope`（旧 serialscope 链接自动重定向） |
| selftest | ✅ 33 组全绿（含 v1.2.0 新增 9 组） |
| release | ✅ v1.2.0 已发布，挂 `liuscope.exe`（~99 MB） |
| CI | ✅ GitHub Actions：push/PR 跑 `python qt/selftest.py`；`v*` tag 追加打包 exe |
| qt-frontend-spike 分支 | 已删除（全部合入） |

## v1.2.0 改动清单

- **终端页**：连接按钮状态化（断开/连接在手动/热插拔/连接丢失/程序断开全路径同步）；TX 行聊天式右对齐（保持 QPlainTextEdit + maximumBlockCount 内存修复）；悬停互转核对补齐（HEX 行显 ASCII，文本行显 HEX）
- **波形页**：「窗口」→「显示点数」；曲线填充迁至设置页；CH 复选框高对比边框；右键菜单全中文（恢复自动缩放/导出PNG/导出CSV/显示网格）；导出 PNG+CSV 菜单；XY 参数补齐（每窗 X/Y 源通道、量程固定±N/自适应、窗口数 1/2/4、分格、点/线、余晖档位）；悬停十字读数（首行 x，逐曲线「名: y」）
- **频谱页**：谱图与瀑布 x 轴联动对齐（同频点垂直对齐）；导出 PNG（标注采样率/FFT/窗/RBW/峰值保持/时间范围）与 CSV（最新一帧 / 最近 N 秒，N 钳制到实际采集时长）；清空功能；采集时长追踪
- **设置页（新增）**：主题深色/浅色即时切换并记忆；曲线填充迁移；STM32CubeProgrammer CLI 路径
- **关于页（新增）**：品牌 LiuScope / 柳镜、版本、作者 折柳、GPL-3.0、仓库链接
- **AI 控制通道规范化**：help 全命令表；read_lines count/follow（最新 N 行 / 连续输出）；spectrum_snapshot / xy_snapshot / modbus_recent / stm32_status / settings_list；settings_set（波特率/协议/主题/显示点数/曲线填充…）控制后 UI 同步；AI_INTERFACE.md 全量重写
- **STM32 页（新增）**：基于 STM32CubeProgrammer CLI——ST-LINK 探测、目标连接与芯片信息、硬/软复位、hex/bin 烧录（带校验）、全片擦除、读保护解除、OB 读取，全异步不卡 UI
- **改名**：serialscope → LiuScope（窗口标题/关于页/README/update_check URL/AI 文档）；README 截图全部换成 Qt 版

## 目录结构

```
liuscope/
  qt/                      ★ Qt 主线（v1.2.0）
    core/                  纯逻辑（settings / control_channel / stm32_cli / dsp / …）
    ui/                    分层窗体（main_window / wave_window / spectrum_panel / settings_page / stm32_panel / theme）
    selftest.py            离屏自测（33 组）
    headless_main.py       无头管线
  docs/screenshots/        Qt 版截图（README 引用）
  .github/workflows/ci.yml CI（selftest + tag 打包）
  AI_INTERFACE.md          AI 控制通道接口（TCP 9101 JSON 行协议）
  HANDOFF.md               本文件
```

## 构建与自检

```powershell
pip install PySide6 pyqtgraph numpy pyserial
python qt/main.py                      # 源码运行
python qt/selftest.py                  # 离屏自测（33 组）
python -m PyInstaller --noconfirm --onefile --windowed --paths qt --name liuscope qt/main.py
# 产物 dist/liuscope.exe（~99 MB）
```

## 已知限制 / 待办

- **真机回归待跑**：`F:\软件仓库_新\开发软件\串口调试助手实验区\h743xih6\`（qt_stress / qt_soak / qt_interact，需 COM9）
- AI 控制通道真 socket 端到端实测待做（selftest 已覆盖命令分发与 UI 同步）
- CubeProgrammer CLI `-l` 输出的 Description 字段在中文控制台显示 `?`（CLI 自身对管道输出码页所致，非解码 bug；`decode_output` 已有 UTF-8→GBK 兜底）
- 频谱「最近 N 秒」导出受历史容量上限（6000 帧 / 2000 万单元格，32k 点约 1 分钟跨度）
- 旧 AI 消费方注意：响应 `tool` 字段已由 `serialscope` 变为 `LiuScope`
- 浅色主题为 v1.2.0 新增，个别第三方控件配色可能在极端场景下对比度欠佳，发现问题报 issue

## 台架测试区

位置：`F:\软件仓库_新\开发软件\串口调试助手实验区\h743xih6\`（README_TESTING.md / qt_stress.py / qt_soak.py / qt_interact.py / shots/ / logs/）
