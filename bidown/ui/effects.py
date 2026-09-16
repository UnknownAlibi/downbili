"""界面特效与自定义控件：粒子层、发光卡片、音效、可拖拽输入框。"""

import math
import random
import re
from pathlib import Path

from PyQt5.QtCore import QEasingCurve, QPropertyAnimation, QTimer, Qt, QUrl
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtMultimedia import QSoundEffect
from PyQt5.QtWidgets import QFrame, QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from ..urls import normalize_input
from ..utils import split_inputs


class SakuraOverlay(QWidget):
    """透明覆盖层，绘制柔和浮动的几何粒子（圆点/小圆环），现代极简。"""

    COLORS = [
        QColor(251, 146, 60),
        QColor(244, 114, 182),
        QColor(167, 139, 250),
        QColor(45, 212, 191),
        QColor(250, 204, 21),
    ]

    def __init__(self, parent=None, count=30):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.particles = []
        for _ in range(count):
            self.particles.append(self._reset_particle())
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_particles)
        self.timer.start(50)

    def _reset_particle(self, y=None):
        w = self.width() or 800
        return {
            "x": random.randint(0, max(w, 100)),
            "y": y if y is not None else random.randint(-300, 0),
            "speed": random.uniform(0.6, 2.2),
            "sway": random.uniform(0.2, 0.8),
            "phase": random.uniform(0, 6.28),
            "size": random.randint(4, 10),
            "ring": random.random() > 0.6,
            "color": random.choice(self.COLORS),
            "alpha": random.randint(40, 100),
        }

    def resizeEvent(self, event):
        for p in self.particles:
            if p["x"] > self.width():
                p["x"] = self.width() - 10
        super().resizeEvent(event)

    def showEvent(self, event):
        if not self.timer.isActive():
            self.timer.start(50)
        super().showEvent(event)

    def hideEvent(self, event):
        # 隐藏时停掉定时器，避免后台白白重绘消耗 CPU
        self.timer.stop()
        super().hideEvent(event)

    def _update_particles(self):
        for i, p in enumerate(self.particles):
            p["y"] += p["speed"]
            p["x"] += math.sin(p["phase"]) * p["sway"]
            p["phase"] += 0.03
            if p["y"] > self.height() + 30:
                self.particles[i] = self._reset_particle(y=-30)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for p in self.particles:
            c = QColor(p["color"])
            c.setAlpha(p["alpha"])
            painter.setPen(Qt.NoPen)
            painter.setBrush(c)
            r = p["size"] / 2
            painter.drawEllipse(int(p["x"] - r), int(p["y"] - r), int(p["size"]), int(p["size"]))
            if p["ring"]:
                c2 = QColor(c)
                c2.setAlpha(p["alpha"] // 2)
                pen = QPen(c2)
                pen.setWidth(1)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(int(p["x"] - r - 3), int(p["y"] - r - 3), int(p["size"] + 6), int(p["size"] + 6))
        painter.end()


class NeonGlowCard(QFrame):
    """柔和呼吸发光边框的卡片（玻璃拟态风格）。"""

    def __init__(self, title=None, glow_color="#FB7299"):
        super().__init__()
        self.setObjectName("card")
        self.glow_color = QColor(glow_color)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(18, 16, 18, 18)
        self.layout.setSpacing(10)
        if title:
            title_label = QLabel(title)
            title_label.setObjectName("card-title")
            self.layout.addWidget(title_label)
        self._glow_alpha = 60
        self._glow_enabled = True
        self.anim = QPropertyAnimation(self, b"glow_alpha")
        self.anim.setDuration(2500)
        self.anim.setStartValue(40)
        self.anim.setEndValue(90)
        self.anim.setEasingCurve(QEasingCurve.InOutSine)
        self.anim.finished.connect(self._reverse_glow)
        self.anim.start()

    def _reverse_glow(self):
        self.anim.setStartValue(self.anim.endValue())
        self.anim.setEndValue(40 if self.anim.endValue() > 65 else 90)
        self.anim.start()

    def get_glow_alpha(self):
        return self._glow_alpha

    def set_glow_alpha(self, value):
        self._glow_alpha = value
        self.update()

    glow_alpha = property(get_glow_alpha, set_glow_alpha)

    def set_glow_enabled(self, enabled):
        self._glow_enabled = enabled
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._glow_enabled:
            rect = self.rect().adjusted(2, 2, -2, -2)
            # 柔和外发光层
            for i in range(4, 0, -1):
                alpha = int(self._glow_alpha * (i / 4.0) * 0.35)
                pen = QPen(QColor(self.glow_color.red(), self.glow_color.green(), self.glow_color.blue(), alpha))
                pen.setWidth(i * 2)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(rect.adjusted(-i, -i, i, i), 22, 22)
        painter.end()
        super().paintEvent(event)


class SoundPlayer:
    """管理音效 WAV 文件并在交互时播放。"""

    SOUNDS = {
        "start": (880, 150),
        "success": (523, 120),
        "success2": (659, 120),
        "fail": (220, 250),
        "click": (1200, 60),
        "complete": (523, 150),
    }

    def __init__(self, resource_dir):
        self.resource_dir = Path(resource_dir)
        self.resource_dir.mkdir(parents=True, exist_ok=True)
        self._cache = {}
        self.enabled = True

    def _ensure_wav(self, name):
        path = self.resource_dir / f"{name}.wav"
        if not path.exists():
            freq, duration = self.SOUNDS.get(name, (440, 150))
            self._generate_beep(path, freq, duration)
        return path

    def _generate_beep(self, path, freq, duration_ms):
        import math, wave, struct
        sample_rate = 22050
        samples = int(sample_rate * duration_ms / 1000.0)
        with wave.open(str(path), "w") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            for i in range(samples):
                t = i / sample_rate
                envelope = max(0.0, 1.0 - i / samples)
                val = int(32767 * 0.3 * envelope * math.sin(2 * math.pi * freq * t))
                w.writeframes(struct.pack("<h", val))

    def play(self, name):
        if not self.enabled:
            return
        try:
            path = self._ensure_wav(name)
            if name not in self._cache:
                effect = QSoundEffect()
                effect.setSource(QUrl.fromLocalFile(str(path)))
                effect.setVolume(0.4)
                self._cache[name] = effect
            self._cache[name].play()
        except Exception:
            pass


class DroppablePlainTextEdit(QPlainTextEdit):
    """支持拖拽链接/BV 号到输入框的纯文本编辑器。"""

    def dragEnterEvent(self, event):
        mime = event.mimeData()
        if mime.hasText() or mime.hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        mime = event.mimeData()
        if mime.hasText() or mime.hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        mime = event.mimeData()
        texts = []
        if mime.hasUrls():
            for url in mime.urls():
                texts.append(url.toString())
        elif mime.hasText():
            texts.append(mime.text())
        else:
            event.ignore()
            return

        dropped = " ".join(texts)
        candidates = split_inputs(dropped)
        normalized = []
        for c in candidates:
            c = c.strip()
            if not c:
                continue
            # 浏览器拖拽有时会带标题和 URL 一起，取看起来像链接或 BV/av 号的部分
            if c.startswith("http://") or c.startswith("https://"):
                normalized.append(c)
            elif re.fullmatch(r"BV[0-9A-Za-z]{10,}", c) or re.fullmatch(r"[aA][vV]\d+", c):
                normalized.append(normalize_input(c))
            else:
                # 尝试从一行文本中提取 URL
                m = re.search(r"(https?://\S+)", c)
                if m:
                    normalized.append(m.group(1))

        if not normalized:
            event.ignore()
            return

        existing = self.toPlainText().strip()
        new_links = "\n".join(normalized)
        if existing:
            self.setPlainText(existing + "\n" + new_links)
        else:
            self.setPlainText(new_links)
        event.acceptProposedAction()
