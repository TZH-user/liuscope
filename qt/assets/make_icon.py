# -*- coding: utf-8 -*-
"""图标生成：liuscope_icon.svg → liuscope.ico（16~256 全尺寸阶梯）+ liuscope_256.png。

运行（仓库根目录）：python qt/assets/make_icon.py
依赖：PySide6（QtSvg 渲染）+ Pillow（多尺寸 ico 封装）。
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SVG = HERE / 'liuscope_icon.svg'
ICO = HERE / 'liuscope.ico'
PNG = HERE / 'liuscope_256.png'
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def render_png(px: int) -> 'QImage':
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(str(SVG))
    img = QImage(px, px, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    renderer.render(painter)
    painter.end()
    return img


def main() -> int:
    img256 = render_png(256)
    if not img256.save(str(PNG), 'PNG'):
        print('PNG 保存失败'); return 1

    from PIL import Image
    pil = Image.open(str(PNG))
    pil.save(str(ICO), format='ICO', sizes=SIZES)
    print(f'OK {ICO.name} ({ICO.stat().st_size} bytes) + {PNG.name}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
