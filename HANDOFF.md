# 交接说明 — LiuScope（柳镜）v1.2.1

**日期：** 2026-10-27
**版本：** master @ v1.2.1（未发布：exe 已重打，release/tag 待确认后发）
**用户请看：** [docs/用户手册_v1.2.md](docs/用户手册_v1.2.md) ｜ **版本历史：** [CHANGELOG.md](CHANGELOG.md)

---

## 当前状态

| 指标 | 状态 |
|---|---|
| master 分支 | ✅ 仅 Qt 主线（PS 存档整体在 `ps-legacy` 分支，封版 v1.0.0） |
| 仓库 | `github.com/TZH-user/liuscope`（旧 serialscope 链接自动重定向） |
| selftest | ✅ 34 组全绿（v1.2.1 新增 THEME CONTRAST 组 + points 钳制/stm32 note 断言） |
| 真机台架回归 | ✅ 压测 16/16（内存阈值按 v1.2.1 校准为 190MB，导出历史缓冲 ~80MB 为设计内有界）+ 交互 29/29 + 浸泡 9/9 |
| AI 通道 E2E | ✅ 19 题 100 分真机真 socket 实测 **97.5/100**（出题/做题双代理隔离，报告 `_verify/ai_exam_report.md`） |
| 对比度审计 | ✅ 394 项 0 违规（两主题 × 7 页签，`_verify/theme_contrast_audit.py`） |
| release | v1.2.0 已发布；**v1.2.1 已就绪未发布**（等确认后打 tag，CI 自动打包） |
| CI | ✅ selftest + tag 打包 exe（含图标）+ `SHA256SUMS.txt` |

## v1.2.1 改动清单

- **应用图标**：「垂柳 × 示波水线」（`qt/assets/liuscope_icon.svg` 源 + 多尺寸 ico；窗口/任务栏/exe/README 全接入；`make_icon.py` 可再生）
- **浅色主题修复**（用户报告"灰底白字"）：#tool 小按钮统一蓝底白字（#0E639C，对比 6.4）；禁用按钮弱化蓝灰底白字；Modbus 闪烁/频谱警告/波形读数首行等硬编码深色值全部随主题
- **E2E 修复**：暗色下单选钮不渲染（QRadioButton QSS，导出口径对话框受益）；`settings_set points` 越界钳制 50..200000；`stm32_status` 补 `note` 字段；「恢复自动缩放」还原被历史滑块拖窄的横向宽度
- **文档**：AI_INTERFACE §3 导出措辞更正 + paused 语义澄清；用户手册补快速上手/配置卸载/故障排查 FAQ
- **工程**：CI 产出 SHA256SUMS.txt；CHANGELOG.md 建立；实验区台架内存阈值校准 + 固件已知问题记录

## 目录结构

```
liuscope/
  qt/                      ★ Qt 主线（v1.2.1）
    core/                  纯逻辑（settings / control_channel / stm32_cli / dsp / …）
    ui/                    分层窗体（main_window / wave_window / spectrum_panel / settings_page / stm32_panel / theme）
    assets/                图标（svg 源 + ico + make_icon.py）
    selftest.py            离屏自测（34 组）
    headless_main.py       无头管线
  docs/
    screenshots/           Qt 版截图（深浅两套）
    research/              调研：测试交接最佳实践 / 低延迟投屏选型
    设计_Linux设备互联_v2.md 下一版设计（鲁班猫投屏+交互+串口网络化）
    用户手册_v1.2.md        用户视角手册
  _verify/                 验收资产（对比度审计/浅色截图脚本/AI 考卷与报告）
  .github/workflows/ci.yml CI（selftest + tag 打包）
  AI_INTERFACE.md          AI 控制通道接口（TCP 9101 JSON 行协议）
  CHANGELOG.md             版本变更日志（SemVer + Keep a Changelog）
  HANDOFF.md               本文件
```

## 构建与自检

```powershell
pip install PySide6 pyqtgraph numpy pyserial
python qt/main.py                      # 源码运行
python qt/selftest.py                  # 离屏自测（34 组）
python _verify/theme_contrast_audit.py # 对比度审计（394 项）
python -m PyInstaller --noconfirm --onefile --windowed --paths qt --name liuscope `
  --icon qt/assets/liuscope.ico --add-data "qt/assets;assets" qt/main.py
# 产物 dist/liuscope.exe（~99 MB，图标已嵌入）
```

## 已知限制 / 待办

- **固件侧**（实验区固件，未修，已记入实验区 README_TESTING.md）：B-01 MODE 5 对 addr0+count1 读帧不应答；B-02 MODE 6 满速流不响应文本 `MENU`（只能 `KEY 1` 退出）
- **记录级**（低优先）：波形页暂停语义=只冻结波形页显示，频谱管线继续（已在 AI_INTERFACE §1.4 写明）；Q19 滑块拖拽中出现一次不可复现的导出弹窗（未落地）
- **v1.3 backlog**（来自交接调研清单）：pytest-qt 断言型 UI 回归套件；CI 对 exe 跑 offscreen 冒烟（`--selftest-exit`）；prerelease beta 渠道流程；Release notes 三段式模板
- CubeProgrammer CLI `-l` 输出 Description 字段在中文控制台显示 `?`（CLI 自身码页所致，非解码 bug）
- 频谱「最近 N 秒」导出受历史容量上限（6000 帧 / 2000 万单元格，32k 点约 1 分钟跨度）
- 下一版方向：Linux/鲁班猫设备互联（投屏+交互+串口网络化），设计与调研见 `docs/`，开工分支 `v2-linux-link`

## 台架测试区

位置：`F:\软件仓库_新\开发软件\串口调试助手实验区\h743xih6\`（README_TESTING.md / qt_stress.py / qt_soak.py / qt_interact.py / shots/ / logs/）
2026-10-27 回归基线：压测 16/16（RSS 阈值 190MB，含 v1.2 导出历史 ~80MB 设计内缓冲）、交互 29/29（连上后主动发 `MENU` 再断言横幅，不依赖板子开机状态）、浸泡 9/9（ADC 1001 行/s、FFT 三峰 50/120/300Hz 命中）。
