# 交接说明 — serialscope V6（Qt 转正）

**日期：** 2026-10-26
**版本：** master @ 920f3bf

---

## 当前状态

Qt 支线已合并到 master，版本 `1.1.0`。master 工作树干净，与 origin 同步。

| 指标 | 状态 |
|---|---|
| master 分支 | ✅ 干净，已推送 |
| qt-frontend-spike 分支 | 已全部合入，可删除 |
| dist/serialscope-qt.exe | ⚠️ **旧构建**（2026-10-25 15:25，不含内存修复），需重建 |
| selftest | ✅ 24/24 全绿 |
| 压力/真机/交互测试 | ✅ 54/54 全绿 |

---

## 本次合并修复的 3 个真 bug

1. **日志控件内存泄漏**（~1.2KB/行 只增不减）
   - 根因：QTextEdit 的 QTextDocument 在 append+裁剪循环下碎片不归还
   - 修复：换 QPlainTextEdit + maximumBlockCount，实测 2 万彩色块封顶 52MB
   - 提交：`a3aad3f`

2. **QToolTip 路径错误**
   - 根因：PySide6 中 QToolTip 在 QtWidgets 而非 QtGui，事件过滤器异常连锁崩溃
   - 修复：`QtGui.QToolTip` → `QtWidgets.QToolTip`
   - 提交：`3b862ec`

3. **Modbus 累积器闲时无界增长**
   - 根因：`_modbus_rx_acc` 每字节都 extend，但只在事务开始/结束清空
   - 修复：加 `_modbus_active` 门控，仅事务在途时收集
   - 提交：`a3aad3f`

---

## 合并后下一步（按优先级）

### 立即（发版前必做）

```powershell
# 1. 重建 exe（旧版不含内存修复）
python -m PyInstaller --noconfirm --onefile --windowed --paths qt --name serialscope-qt qt/main.py
# 产物：dist/serialscope-qt.exe（约 101 MB）

# 2. 真机冒烟（双击运行，确认无崩溃）
```

### 等你拍板

```powershell
# 3. 正式发布
gh release create v1.1.0 \
  --title "serialscope v1.1.0 — Qt 主线转正" \
  --notes "..." \
  dist/serialscope-qt.exe
```

### 后续优化（非阻塞）

- qt 更新检查过滤 `-qt` 标签（当前可能误提示升级到 PS 版）
- PS 版回归真机测试（`run_stability_tests.ps1`）
- ST-Link V3 code 43（低优先级，V2 已可用）

---

## 目录结构

```
serialscope/
  qt/                     ★ Qt 主线（v1.1.0）
    core/                 纯逻辑（rx_buffer / wave_state / settings）
    ui/                   分层窗体（main_window / wave_window / spectrum_panel）
    selftest.py           离屏自测（24 组）
    headless_main.py      无头管线
    README_QT.md          里程碑 M1~M5 与对齐口径
  serial_terminal_v6.ps1  PowerShell 存档版（封版 v1.0.0）
  dist/                   待重建：旧版不含内存修复
  HANDOFF.md              本文件
  README.md               双实现说明（Qt 主线 + PS 存档）
```

---

## 台架测试区

位置：`F:\软件仓库_新\开发软件\串口调试助手实验区\h743xih6\`

- **README_TESTING.md**：测试脚本使用手册
- **qt_stress.py**：合成洪泛压测
- **qt_soak.py**：真机浸泡（需 COM9）
- **qt_interact.py**：前台交互测试（需 COM9）
- **shots/**：14 张截图证据（交互 10 + 压测 4）
- **logs/**：最近测试日志

---

## 合并日志（本次关键提交）

```
920f3bf 添加 Qt 版启动器脚本
4aff0e4 合并 Qt 支线：经压力/真机/交互 54 项全绿验收，Qt 实现转正为主线 v1.1.0
43f0e17 转正：Qt 支线版本号 1.1.0、README 双实现文档化
3b862ec 修复 QToolTip 引用错误
a3aad3f 内存双修复：日志控件换 QPlainTextEdit + Modbus 累积器门控
```
