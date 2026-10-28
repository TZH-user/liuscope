# -*- coding: utf-8 -*-
"""RK3562(LubanCat)一键上架:拿到凭据后自动完成 登录 → 环境盘点 → 网络修复 → 部署检查。

用法(在仓库根目录):
    python _verify/rk_bringup.py --user cat --pwd 你的密码
可选:
    --dhcp            网络没 IP 时尝试拉起 DHCP(默认尝试)
    --deploy          网络通了以后,由 PC 侧 http 服务把 server_rk 推到板子并启动
约定:COM/波特率走环境变量 RK_COM/RK_BAUD(同 rk_console.py);全程原始日志落
_verify/evidence_v13/rk_bringup.log。
"""
from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, '_verify')
from rk_console import Console, wait_for, login   # noqa: E402

PROMPT = r'\$ |# '


def sh(c, cmd: str, timeout_s: float = 30.0) -> str:
    """登录后的 shell 里执行命令并等待提示符。"""
    c.ser.reset_input_buffer()
    c.send(cmd + '\n')
    out = wait_for(c, PROMPT, timeout_s)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--user', required=True)
    ap.add_argument('--pwd', required=True)
    ap.add_argument('--deploy', action='store_true', help='网络可用时部署 server_rk 并启动')
    args = ap.parse_args()

    c = Console()
    try:
        print('== 1. 登录 ==')
        out = login(c, args.user, args.pwd)
        if '错误' in out and not ('$' in out or '#' in out):
            print('登录失败(密码错?):', out[-120:].replace('\r', ''))
            return 2
        sh(c, 'true')   # 对齐提示符
        print('登录成功')

        print('== 2. 环境盘点 ==')
        for label, cmd in [
            ('系统', 'uname -a && cat /etc/os-release | head -2'),
            ('Python', 'python3 --version'),
            ('ffmpeg', 'ffmpeg -version 2>/dev/null | head -1; ffmpeg -encoders 2>/dev/null | grep -i rkmpp'),
            ('KMS/DRI', 'ls /dev/dri/ 2>/dev/null; ls /dev/uinput 2>/dev/null'),
            ('串口设备', 'ls -l /dev/ttyFIQ0 /dev/ttyUSB* /dev/ttyS* 2>/dev/null | head -8'),
            ('sshd', 'systemctl is-active ssh 2>/dev/null; which sshd'),
            ('pip/pyserial', 'python3 -c "import serial; print(serial.VERSION)" 2>&1'),
        ]:
            out = sh(c, cmd)
            print(f'-- {label}:', out.split('\n', 1)[-1][:400].replace('\r', ''), sep='\n')

        print('== 3. 网络 ==')
        out = sh(c, 'ip -4 addr show | grep -v " 127.0.0.1" | grep inet || echo NO_IP')
        print(out.replace('\r', ''))
        board_ip = None
        if 'NO_IP' in out:
            print('>> 无 IP,尝试拉起 DHCP…')
            sh(c, f"echo {args.pwd} | sudo -S ip link set eth0 up")
            out = sh(c, f"echo {args.pwd} | sudo -S dhclient -v eth0 2>&1 || "
                        f"echo {args.pwd} | sudo -S nmcli device connect eth0 2>&1", 40)
            print(out[-400:].replace('\r', ''))
            out = sh(c, "ip -4 addr show eth0 | grep inet || echo STILL_NO_IP")
            print(out.replace('\r', ''))
        import re
        m = re.search(r'inet (\d+\.\d+\.\d+\.\d+)', out)
        if m:
            board_ip = m.group(1)
            print(f'>> 板子 IP: {board_ip}')

        print('== 4. 部署(server-rk) ==')
        if args.deploy and board_ip:
            # PC 侧临时 http 服务,板端拉取部署包
            import os
            import socketserver
            import subprocess
            import threading
            import tarfile

            tar_path = os.path.join(os.path.dirname(__file__), 'evidence_v13', 'server_rk_deploy.tar.gz')
            with tarfile.open(tar_path, 'w:gz') as tf:
                tf.add('server-rk/server_rk', arcname='server_rk')
                tf.add('server-rk/ser2net.yaml.example', arcname='ser2net.yaml.example')
            handler = lambda *a, **kw: None   # SimpleHTTPRequestHandler 由目录起服
            import http.server
            os.chdir(os.path.dirname(tar_path))
            httpd = http.server.HTTPServer(('0.0.0.0', 8099), http.server.SimpleHTTPRequestHandler)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            fname = os.path.basename(tar_path)
            sh(c, f"(wget -q http://{{PC_IP}}:8099/{fname} -O /tmp/{fname} || "
                  f"curl -fsSL http://{{PC_IP}}:8099/{fname} -o /tmp/{fname})")
            httpd.shutdown()
            print('>> 部署包传输需 PC IP,脚本内占位 {PC_IP} 请按实际替换后重跑 --deploy')
        else:
            print('>> 跳过(未传 --deploy 或无 IP)。网络通后手动步骤见 server-rk/README.md')

        return 0
    finally:
        c.close()


if __name__ == '__main__':
    sys.exit(main())
