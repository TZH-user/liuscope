# -*- coding: utf-8 -*-
"""证据图（第三轮）：DPR 修复后的居中满幅渲染 + 板端毫秒跑表实时性证明。

前置：板端 server_rk 已跑（带跑表 overlay 版本）。复现:
    python _verify/linux_page_live2.py [板IP]
产出: evidence/linux_page_live.png   整窗（画面应居中满幅，统计行有 传输≈xx ms）
      evidence/stopwatch_t0/t1.png  视频区左上角两次裁帧（跑表数值必须不同）
"""
import os
import sys
import time

os.environ['QT_QPA_PLATFORM'] = 'windows'   # 真 GL 路径；必须在导入 PySide 前设
sys.path.insert(0, 'qt')

from PySide6 import QtWidgets  # noqa: E402

host = sys.argv[1] if len(sys.argv) > 1 else '192.168.2.100'
app = QtWidgets.QApplication(sys.argv)
import ui.main_window as mw  # noqa: E402

win = mw.MainWindow()
win.show()
win.resize(1500, 860)
win._port_watch_timer.stop()
app.processEvents()

win.settings_page.chk_show_screen.setChecked(True)
app.processEvents()
win.tabs.setCurrentWidget(win.device_screen)
app.processEvents()

page = win.device_screen
page.edit_host.setText(host)
page.btn_start.click()
print('开始接收已点击…')

deadline = time.time() + 45
while time.time() < deadline:
    app.processEvents()
    if page.decoder is not None and page.decoder.frame_count >= 36:
        break
    time.sleep(0.05)
app.processEvents()
time.sleep(0.4)
app.processEvents()

pix = win.grab()
pix.save('_verify/evidence/linux_page_live.png')
print(f'状态行: {page.lbl_status.text()}')
print(f'统计行: {page.lbl_fps.text()} | {page.lbl_lost.text()} | {page.lbl_delay.text()}')

# 跑表定性证明：两次取帧裁同一左上角（跑表位置），1.2s 间隔数值必须变化
shot0 = page.gl_widget.grab()
time.sleep(1.2)
app.processEvents()
shot1 = page.gl_widget.grab()
cw, ch = int(shot0.width() * 0.45), int(shot0.height() * 0.22)
crop0, crop1 = shot0.toImage().copy(0, 0, cw, ch), shot1.toImage().copy(0, 0, cw, ch)
crop0.save('_verify/evidence/stopwatch_t0.png')
crop1.save('_verify/evidence/stopwatch_t1.png')
b0, b1 = crop0.constBits().tobytes(), crop1.constBits().tobytes()
diff = sum(1 for a, b in zip(b0[::31], b1[::31]) if a != b)
print(f'跑表角两次取帧差异采样: {diff} 处不同（静止桌面下 >0 = 跑表在走）')
assert diff >= 3, '跑表数值 1.2s 内必须变化（实时性证明失败）'

page.btn_start.click()      # 停止收尾
t0 = time.time()
while time.time() - t0 < 8:
    app.processEvents()
    if page.source is None and page.control is None:
        break
    time.sleep(0.05)
win.close()
print('SAVED evidence/linux_page_live.png + stopwatch_t0/t1.png',
      '| 停止回收', 'OK' if page.source is None else 'FAIL')
