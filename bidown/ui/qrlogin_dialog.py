"""扫码登录对话框。"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QDialog, QLabel, QMessageBox, QPushButton, QVBoxLayout

from ..bilibili import save_cookies_to_netscape_file
from ..config import BASE_DIR
from ..errors import format_error
from ..workers.qrlogin import QrLoginWorker


class QrLoginDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.worker = None
        self.cookie_file_path = ""
        self.setWindowTitle("扫码登录 B站")
        self.resize(360, 460)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title = QLabel("扫码登录")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #FB7299;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        hint = QLabel("用 B站手机客户端扫描下方二维码")
        hint.setStyleSheet("color: #6b7280;")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)

        self.qr_label = QLabel("正在生成二维码...")
        self.qr_label.setFixedSize(240, 240)
        self.qr_label.setAlignment(Qt.AlignCenter)
        self.qr_label.setStyleSheet("background: #ffffff; border: 2px dashed #FB7299; border-radius: 12px; color: #6b7280;")
        layout.addWidget(self.qr_label, alignment=Qt.AlignCenter)

        self.status_label = QLabel("准备中...")
        self.status_label.setStyleSheet("font-size: 14px; color: #1a1a2e;")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.refresh_btn = QPushButton("重新生成二维码")
        self.refresh_btn.clicked.connect(self.start)
        layout.addWidget(self.refresh_btn)

    def start(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(2000)
        self.qr_label.setText("正在生成二维码...")
        self.status_label.setText("准备中...")
        self.worker = QrLoginWorker(self.settings, self)
        self.worker.qrcode_ready.connect(self.render_qrcode)
        self.worker.status_changed.connect(self.status_label.setText)
        self.worker.login_success.connect(self.on_login_success)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()

    def render_qrcode(self, content):
        try:
            import qrcode
            from PyQt5.QtGui import QImage, QPainter, QColor
            qr = qrcode.QRCode(border=2)
            qr.add_data(content)
            qr.make(fit=True)
            matrix = qr.get_matrix()
            size = len(matrix)
            cell = 6
            img = QImage(size * cell, size * cell, QImage.Format_RGB32)
            img.fill(QColor("#ffffff"))
            painter = QPainter(img)
            painter.setBrush(QColor("#1a1a2e"))
            painter.setPen(Qt.NoPen)
            for y, row in enumerate(matrix):
                for x, v in enumerate(row):
                    if v:
                        painter.drawRect(x * cell, y * cell, cell, cell)
            painter.end()
            pixmap = QPixmap.fromImage(img).scaled(240, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.qr_label.setPixmap(pixmap)
        except Exception as exc:
            self.qr_label.setText(f"二维码渲染失败:\n{format_error(exc)}")

    def on_login_success(self, cookies):
        cookie_path = BASE_DIR / "cookies.txt"
        try:
            save_cookies_to_netscape_file(cookies, cookie_path)
            self.cookie_file_path = str(cookie_path)
            self.status_label.setText("登录成功！Cookie 已保存。")
            QMessageBox.information(self, "扫码登录", f"登录成功！\nCookie 已保存到:\n{cookie_path}")
            self.accept()
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", f"Cookie 保存失败: {format_error(exc)}")

    def on_failed(self, msg):
        self.status_label.setText(f"失败: {msg}")

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(2000)
        event.accept()
