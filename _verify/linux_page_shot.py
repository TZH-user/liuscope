# -*- coding: utf-8 -*-
"""证据图：Linux 支持页真机视频渲染（窗口平台真 GL，控制通道自动触发 + 收流解码上屏）。

前置：板端 server_rk 已跑；PC UDP 9202 空闲。复现:
    python _verify/linux_page_shot.py [板IP]
流程: 开主窗 → Linux 支持页填板 IP → 开始接收（自动 video_start）→
      等解码 ≥36 帧 → 整窗截图落盘 → 停止收尾。
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
win._port_watch_timer.stop()
app.processEvents()

# 页签可能被显隐设置（linux/show_screen）隐藏：证据需要该页可见，强制打开并切换
win.settings_page.chk_show_screen.setChecked(True)
app.processEvents()
win.tabs.setCurrentWidget(win.device_screen)
app.processEvents()

page = win.device_screen
page.edit_host.setText(host)
page.btn_start.click()
print('开始接收已点击（控制通道自动触发）…')

frames = -1
deadline = time.time() + 45
while time.time() < deadline:
    app.processEvents()
    if page.decoder is not None and page.decoder.frame_count >= 36:
        frames = page.decoder.frame_count
        break
    time.sleep(0.05)
app.processEvents()
time.sleep(0.3)             # 让最后一帧完成上屏
app.processEvents()

pix = win.grab()
pix.save('_verify/evidence/linux_page_live.png')
print(f'状态行: {page.lbl_status.text()}')
print(f'统计行: {page.lbl_fps.text()} | {page.lbl_lost.text()}')
print(f'解码帧数: {max(frames, page.decoder.frame_count if page.decoder else 0)}')

page.btn_start.click()      # 停止（video_stop + 撤 UDP + 解码线程回收）
t0 = time.time()
while time.time() - t0 < 8:
    app.processEvents()
    if page.source is None and page.control is None:
        break
    time.sleep(0.05)
win.close()
print('SAVED _verify/evidence/linux_page_live.png',
      '| 停止回收', 'OK' if page.source is None else 'FAIL')
