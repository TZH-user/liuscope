# server-rk — LiuScope v2 板端服务（M1 阶段）

对应设计稿：[docs/设计_Linux设备互联_v2.md](../docs/设计_Linux设备互联_v2.md)。
目标板：野火 LubanCat 等 RK356x Linux 开发板（Debian/Buildroot 均可）。

## M1（当前）：串口网络化 —— 板端 ser2net + PC 端直连

M1 不需要本目录代码：板端装 **ser2net**（RFC2217），LiuScope 串口页直接手输
`rfc2217://<板子IP>:7000` 连接（v2 分支已支持，pyserial 原生协议，波特率由板端协商）。

板端安装与配置：

```bash
sudo apt install ser2net
sudo cp ser2net.yaml.example /etc/ser2net.yaml   # 按实际串口设备号/波特率修改
sudo systemctl restart ser2net
```

验收（对齐设计稿 M1）：LiuScope 串口页以 `rfc2217://` 连接后，收发行为与本地 COM 口一致；
`loop://` 回环全链已在 selftest `NET SERIAL URL` 组覆盖。

## M2+（占位）：投屏 + 输入注入守护进程

后续在此目录实现 `python -m server_rk.main`：
KMS 捕获 → FFmpeg-Rockchip(`h264_rkmpp`,无 B 帧) → UDP 裸流；TCP 9201 JSON 行控制通道
（配对码鉴权）；python-uinput 键鼠注入。编码段可先用 ffmpeg-rkmpp 子进程验证，见设计稿 §5.3。
