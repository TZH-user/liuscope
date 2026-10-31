# -*- coding: utf-8 -*-
"""证据图（第四轮）：鼠标注入跟手（光标打到画面两角）+ 跑表开关/透明度实况。

前置：板端 server_rk 已跑（raw uinput + overlay 参数化版）。复现:
    python _verify/linux_mouse_evidence.py [板IP]
产出: evidence/mouse_inject_a/b.png  注入 (80,80)/(940,520) 后的视频区裁帧（板端光标应分别出现）
      evidence/stopwatch_off.png    页上关跑表（自动重起流）后的画面（左上角无表）
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
page.chk_mouse.setChecked(True)      # 开鼠标控制（页面自己的开关）
page.btn_start.click()
print('开始接收 + 鼠标控制已开…')

deadline = time.time() + 45
while time.time() < deadline:
    app.processEvents()
    if page.decoder is not None and page.decoder.frame_count >= 30:
        break
    time.sleep(0.05)
app.processEvents()
time.sleep(0.5)
app.processEvents()


def grab_video(tag):
    app.processEvents()
    time.sleep(0.3)                  # 等最后注入帧上屏
    app.processEvents()
    pix = page.gl_widget.grab()
    pix.save(f'_verify/evidence/{tag}.png')
    print(f'SAVED {tag}.png 状态行: {page.lbl_status.text()}')


# 注入①：绝对移动到 (80,80)（走页面完整链路：_send_inject → 控制通道 → 板端 uinput）
page._send_inject([{'t': 'mouse_abs', 'x': 80, 'y': 80}])
grab_video('mouse_inject_a')

# 注入②：移到 (940,520)（右下区）——两帧光标位置不同 = 跟手证据
page._send_inject([{'t': 'mouse_abs', 'x': 940, 'y': 520}])
grab_video('mouse_inject_b')

# 跑表关闭（页上开关 → 自动重起流 → 板端不再烙表）
page.chk_watch.setChecked(False)
deadline = time.time() + 45
while time.time() < deadline:
    app.processEvents()
    if page.decoder is not None and page.decoder.frame_count >= 15:
        break
    time.sleep(0.05)
print('关表后状态行:', page.lbl_status.text())
grab_video('stopwatch_off')

# 收尾：关鼠标控制 + 停止
page.chk_mouse.setChecked(False)
page.btn_start.click()
t0 = time.time()
while time.time() - t0 < 8:
    app.processEvents()
    if page.source is None and page.control is None:
        break
    time.sleep(0.05)
win.close()
print('停止回收', 'OK' if page.source is None else 'FAIL')
