"""内置看板娘绘制（缺少 icon.png 时使用）。"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter, QPen, QPixmap


def generate_mascot_image(path, size=200):
    """绘制几何极简风看板娘 PNG：柔和色块、干净线条、现代扁平感。"""
    h = int(size * 1.25)
    pixmap = QPixmap(size, h)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    def ellipse(x, y, w, t_h):
        painter.drawEllipse(int(x), int(y), int(w), int(t_h))

    def line(x1, y1, x2, y2, color, width=2):
        pen = QPen(QColor(color))
        pen.setWidth(width)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(int(x1), int(y1), int(x2), int(y2))

    try:
        cx, cy = size // 2, int(size * 0.58)
        face_r = int(size * 0.30)

        # 配色：低饱和珊瑚、奶油、柔灰
        hair = QColor(120, 113, 108)      # 暖灰褐
        skin = QColor(255, 237, 213)      # 奶油肤色
        blush = QColor(254, 202, 202, 160)
        accent = QColor(251, 146, 60)     # 珊瑚橙
        line_color = QColor(87, 83, 78)

        # 后发：大圆
        painter.setPen(Qt.NoPen)
        painter.setBrush(hair)
        ellipse(cx - face_r - 6, cy - face_r - 4, (face_r + 6) * 2, (face_r + 10) * 2)

        # 脸部
        painter.setBrush(skin)
        ellipse(cx - face_r, cy - face_r, face_r * 2, face_r * 2)

        # 头发刘海：两个弧线覆盖额头
        painter.setBrush(hair)
        ellipse(cx - face_r - 2, cy - face_r - 6, face_r + 8, face_r * 0.85)
        ellipse(cx - 6, cy - face_r - 6, face_r + 8, face_r * 0.85)

        # 耳朵
        painter.setBrush(skin)
        ellipse(cx - face_r - 6, cy + 2, 10, 14)
        ellipse(cx + face_r - 4, cy + 2, 10, 14)

        # 眼睛：大圆眼 + 竖线瞳孔（现代极简）
        eye_r = max(6, face_r // 5)
        lx, ly = cx - face_r // 2 - 2, cy - face_r // 8
        rx, ry = cx + face_r // 2 + 2, cy - face_r // 8
        painter.setBrush(line_color)
        ellipse(lx - eye_r, ly - eye_r, eye_r * 2, eye_r * 2)
        ellipse(rx - eye_r, ry - eye_r, eye_r * 2, eye_r * 2)
        painter.setBrush(QColor(255, 255, 255))
        ellipse(lx - eye_r // 2, ly - eye_r // 2, eye_r // 2, eye_r // 2)
        ellipse(rx - eye_r // 2, ry - eye_r // 2, eye_r // 2, eye_r // 2)

        # 腮红
        painter.setBrush(blush)
        ellipse(cx - face_r + 10, cy + face_r // 4, face_r // 3, face_r // 5)
        ellipse(cx + face_r - 10 - face_r // 3, cy + face_r // 4, face_r // 3, face_r // 5)

        # 嘴巴：简洁微笑线
        line(cx - 6, cy + face_r // 3, cx, cy + face_r // 3 + 4, line_color, 2)
        line(cx, cy + face_r // 3 + 4, cx + 6, cy + face_r // 3, line_color, 2)

        # 身体：简单梯形/圆角矩形
        body_w = int(face_r * 1.4)
        body_h = int(size * 0.28)
        bx, by = cx - body_w // 2, cy + face_r - 4
        painter.setBrush(QColor(245, 245, 244))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(bx, by, body_w, body_h, body_w // 2, body_w // 2)

        # 衣领装饰
        painter.setBrush(accent)
        ellipse(cx - 8, by + 8, 16, 10)
    finally:
        painter.end()
    pixmap.save(str(path), "PNG")
