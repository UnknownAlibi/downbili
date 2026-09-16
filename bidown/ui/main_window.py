"""主窗口：标题栏、侧边栏、下载/历史/设置三页与全部交互逻辑。"""

import csv
import json
import random
import time
from pathlib import Path

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QIcon, QKeySequence, QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QShortcut, QSizeGrip, QSpinBox, QStackedWidget, QSystemTrayIcon, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget
)

from ..config import (
    AUDIO_QUALITY_LABELS,
    CODEC_LABELS,
    DEFAULT_DOWNLOAD_DIR,
    DEFAULT_SETTINGS,
    QUALITY_LABELS,
    RESOURCE_DIR,
    has_ffmpeg,
)
from ..errors import format_error
from ..history import (
    append_history_record,
    load_history,
    remove_history_record,
    save_history,
)
from ..logs import write_runtime_log
from ..mascot import generate_mascot_image
from ..settings import load_settings, save_settings
from ..shell import open_file_default, open_path_in_explorer
from ..urls import normalize_input
from ..utils import (
    format_bytes,
    format_count,
    format_duration,
    format_fps,
    format_upload_date,
    split_inputs,
)
from ..workers.cookie import CookieCheckWorker
from ..workers.download import DownloadWorker
from ..workers.preview import PreviewWorker
from ..workers.thumbnail import ThumbnailWorker
from .effects import (
    DroppablePlainTextEdit,
    NeonGlowCard,
    SakuraOverlay,
    SoundPlayer,
)
from .qrlogin_dialog import QrLoginDialog


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings, load_err = load_settings()
        if load_err:
            print(f"设置加载失败，使用默认设置: {load_err}")
        self.worker = None
        self.preview_worker = None
        self.cookie_check_worker = None
        self.preview_request_id = 0
        self._force_quit = False
        self.preview_pending = False
        self.preview_formats = []
        self.history_records = load_history()
        self._history_dirty = False
        self._thumb_workers = []
        self._mascot_state = None
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self.start_preview)
        self.base_window_title = "Bilibili 视频下载器"
        self.setWindowTitle(self.base_window_title)
        self.resize(1280, 860)
        self.setMinimumSize(900, 600)
        self._drag_pos = None
        self._is_maximized = False
        self.setWindowFlags(Qt.FramelessWindowHint)
        app_icon = self._app_icon_path()
        if app_icon:
            self.setWindowIcon(QIcon(str(app_icon)))
        self.sound_player = SoundPlayer(RESOURCE_DIR / "sounds")
        self.sakura_overlay = None
        self._init_mascot_image()
        self.init_ui()
        self.apply_settings_to_ui()
        self.refresh_history()
        self.init_tray_icon()
        self._setup_shortcuts()
        self.statusBar().showMessage("就绪")
        self.sound_player.play("click")

    def _app_icon_path(self):
        """返回应用图标路径，优先使用 icon.png，其次 icon.ico。"""
        for name in ("icon.png", "icon.ico"):
            path = RESOURCE_DIR / name
            if path.exists():
                return path
        return None

    def _init_mascot_image(self):
        """确认侧边栏看板娘图片：优先使用 icon.png，缺失时生成 mascot.png。"""
        icon_path = self._app_icon_path()
        if icon_path and icon_path.suffix.lower() == ".png":
            self.mascot_image_path = icon_path
        else:
            self.mascot_image_path = RESOURCE_DIR / "mascot.png"
            if not self.mascot_image_path.exists():
                try:
                    generate_mascot_image(self.mascot_image_path, size=200)
                except Exception as exc:
                    print("生成看板娘图片失败:", exc)

    def init_tray_icon(self):
        self.tray_icon = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        app_icon = self._app_icon_path()
        icon = QIcon(str(app_icon)) if app_icon else QIcon()
        if icon.isNull():
            return
        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip("Bilibili 视频下载器")
        # 右键菜单
        menu = QMenu(self)
        act_show = menu.addAction("显示主窗口")
        act_show.triggered.connect(self._tray_show_window)
        act_pause = menu.addAction("暂停 / 继续")
        act_pause.triggered.connect(self.toggle_pause_queue)
        menu.addSeparator()
        act_quit = menu.addAction("退出")
        act_quit.triggered.connect(self._tray_quit)
        self.tray_icon.setContextMenu(menu)
        # 单击/双击还原
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._tray_show_window()

    def _tray_show_window(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _tray_quit(self):
        self._force_quit = True
        self.close()

    def _setup_shortcuts(self):
        """注册全局键盘快捷键。"""
        # Ctrl+Enter: 开始下载
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.start_downloads)
        # Ctrl+D: 清空输入
        QShortcut(QKeySequence("Ctrl+D"), self, activated=self.clear_input)
        # Ctrl+Shift+V: 粘贴并解析
        QShortcut(QKeySequence("Ctrl+Shift+V"), self, activated=lambda: self.start_preview(force=True))
        # Space: 暂停/继续（焦点在输入框里时不拦截，否则打不出空格）
        QShortcut(QKeySequence("Space"), self, activated=self._shortcut_toggle_pause)
        # Esc: 取消下载（同理，在输入框里按 Esc 不应触发取消）
        QShortcut(QKeySequence("Escape"), self, activated=self._shortcut_cancel)
        # F5: 刷新历史
        QShortcut(QKeySequence("F5"), self, activated=self.refresh_history)
        # Ctrl+1/2/3: 切换页面
        QShortcut(QKeySequence("Ctrl+1"), self, activated=lambda: self.switch_page(0))
        QShortcut(QKeySequence("Ctrl+2"), self, activated=lambda: self.switch_page(1))
        QShortcut(QKeySequence("Ctrl+3"), self, activated=lambda: self.switch_page(2))
        # Ctrl+W: 最小化到托盘
        QShortcut(QKeySequence("Ctrl+W"), self, activated=self._minimize_to_tray)

    @staticmethod
    def _set_roomy_rows(table):
        """把表格行高设足。

        样式表里 QTableWidget::item 的 padding 不计入默认行高，
        默认 31px 会让 14px 文字上下各裁掉一点，这里按字体高度重新给出行高。
        """
        table.verticalHeader().setDefaultSectionSize(table.fontMetrics().height() + 18)

    def _input_has_focus(self):
        """当前焦点是否落在文本/数值输入控件上。"""
        return isinstance(
            QApplication.focusWidget(), (QLineEdit, QPlainTextEdit, QComboBox, QSpinBox)
        )

    def _shortcut_toggle_pause(self):
        if self._input_has_focus():
            return
        self.toggle_pause_queue()

    def _shortcut_cancel(self):
        if self._input_has_focus():
            return
        self.cancel_downloads()

    def _minimize_to_tray(self):
        if self.tray_icon:
            self.hide()
            self.tray_icon.showMessage(
                "Bilibili 视频下载器",
                "已最小化到托盘，单击图标恢复",
                QSystemTrayIcon.Information, 2000,
            )
        else:
            self.showMinimized()

    def create_card(self, title, glow=True, glow_color="#FB7299"):
        """创建一个带标题的圆角卡片容器，glow=True 时带霓虹呼吸边框。"""
        if glow and self.settings.get("fx_neon", True):
            card = NeonGlowCard(title=title, glow_color=glow_color)
            return card, card.layout
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(10)
        if title:
            title_label = QLabel(title)
            title_label.setObjectName("card-title")
            layout.addWidget(title_label)
        return card, layout

    def switch_page(self, index):
        """切换右侧页面，同步侧边栏按钮状态。"""
        self.content_stack.setCurrentIndex(index)
        self.nav_download_btn.setChecked(index == 0)
        self.nav_history_btn.setChecked(index == 1)
        self.nav_settings_btn.setChecked(index == 2)
        if index == 1 and self._history_dirty:
            self.refresh_history()

    def init_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ===== 自定义标题栏 =====
        title_bar = self._build_title_bar()
        root_layout.addWidget(title_bar)

        # ===== 主内容区 =====
        self.body_widget = QWidget()
        body_layout = QHBoxLayout(self.body_widget)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        # 侧边栏
        sidebar = self._build_sidebar()
        body_layout.addWidget(sidebar)

        # 右侧内容区（含缩放抓手）
        content_container = QWidget()
        content_layout = QVBoxLayout(content_container)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        self.content_stack = QStackedWidget()
        self.content_stack.setObjectName("contentStack")
        content_layout.addWidget(self.content_stack, 1)
        grip_layout = QHBoxLayout()
        grip_layout.setContentsMargins(0, 0, 0, 0)
        grip_layout.addStretch()
        grip_layout.addWidget(QSizeGrip(content_container))
        content_layout.addLayout(grip_layout)
        body_layout.addWidget(content_container, 1)

        root_layout.addWidget(self.body_widget, 1)

        # 樱花飘落层
        self.sakura_overlay = SakuraOverlay(self.body_widget, count=40)
        self.sakura_overlay.setGeometry(self.body_widget.rect())
        self.sakura_overlay.show()

        self._build_download_page()
        self._build_history_page()
        self._build_settings_page()
        self._apply_theme()

    def _build_title_bar(self):
        """构建现代极简标题栏：柔和背景、文字图标、圆角控制按钮。"""
        bar = QFrame()
        bar.setObjectName("titleBar")
        bar.setFixedHeight(42)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 0, 12, 0)
        layout.setSpacing(10)

        icon_label = QLabel()
        icon_label.setObjectName("titleIcon")
        icon_label.setFixedSize(28, 28)
        icon_label.setAlignment(Qt.AlignCenter)
        app_icon = self._app_icon_path()
        if app_icon:
            icon_label.setPixmap(
                QPixmap(str(app_icon)).scaled(
                    22, 22, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )

        self.title_label = QLabel(self.base_window_title)
        self.title_label.setObjectName("titleLabel")
        layout.addWidget(icon_label)
        layout.addWidget(self.title_label)
        layout.addStretch()

        self.min_btn = QPushButton("−")
        self.min_btn.setObjectName("windowCtrl")
        self.min_btn.setFixedSize(28, 28)
        self.min_btn.setToolTip("最小化")
        self.min_btn.clicked.connect(self.showMinimized)

        self.max_btn = QPushButton("□")
        self.max_btn.setObjectName("windowCtrl")
        self.max_btn.setFixedSize(28, 28)
        self.max_btn.setToolTip("最大化/还原")
        self.max_btn.clicked.connect(self.toggle_maximize)

        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("windowCtrlClose")
        self.close_btn.setFixedSize(28, 28)
        self.close_btn.setToolTip("关闭")
        self.close_btn.clicked.connect(self.close)

        layout.addWidget(self.min_btn)
        layout.addWidget(self.max_btn)
        layout.addWidget(self.close_btn)

        # 标题栏拖拽
        bar.mousePressEvent = self._title_bar_mouse_press
        bar.mouseMoveEvent = self._title_bar_mouse_move
        bar.mouseReleaseEvent = self._title_bar_mouse_release
        bar.mouseDoubleClickEvent = lambda event: self.toggle_maximize()

        return bar

    def _build_sidebar(self):
        """构建现代玻璃拟态侧边栏，含几何极简看板娘。"""
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        # 210 时「BiliDown」logo 需要 234px，会被裁掉；240 配合 20px 字号刚好放下
        sidebar.setFixedWidth(240)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(18, 24, 18, 24)
        sidebar_layout.setSpacing(10)

        # 顶部简洁装饰线
        decor = QFrame()
        decor.setObjectName("sidebarDecor")
        decor.setFixedHeight(3)
        sidebar_layout.addWidget(decor)
        sidebar_layout.addSpacing(8)

        logo = QLabel("BiliDown")
        logo.setObjectName("logo")
        subtitle = QLabel("Bilibili 视频下载器")
        subtitle.setObjectName("logo-subtitle")
        sidebar_layout.addWidget(logo)
        sidebar_layout.addWidget(subtitle)
        sidebar_layout.addSpacing(24)

        self.nav_download_btn = QPushButton(" 下 载")
        self.nav_download_btn.setObjectName("navBtn")
        self.nav_download_btn.setCheckable(True)
        self.nav_download_btn.setChecked(True)
        self.nav_download_btn.clicked.connect(lambda: self.switch_page(0))
        sidebar_layout.addWidget(self.nav_download_btn)

        self.nav_history_btn = QPushButton(" 历 史")
        self.nav_history_btn.setObjectName("navBtn")
        self.nav_history_btn.setCheckable(True)
        self.nav_history_btn.clicked.connect(lambda: self.switch_page(1))
        sidebar_layout.addWidget(self.nav_history_btn)

        self.nav_settings_btn = QPushButton(" 设 置")
        self.nav_settings_btn.setObjectName("navBtn")
        self.nav_settings_btn.setCheckable(True)
        self.nav_settings_btn.clicked.connect(lambda: self.switch_page(2))
        sidebar_layout.addWidget(self.nav_settings_btn)

        sidebar_layout.addStretch()

        # 看板娘 + 对话气泡
        mascot_box = QFrame()
        mascot_box.setObjectName("mascotBox")
        mascot_layout = QVBoxLayout(mascot_box)
        mascot_layout.setContentsMargins(10, 10, 10, 10)
        mascot_layout.setSpacing(6)

        self.mascot_bubble = QLabel("粘贴链接，开始下载")
        self.mascot_bubble.setObjectName("mascotBubble")
        self.mascot_bubble.setWordWrap(True)
        self.mascot_bubble.setAlignment(Qt.AlignCenter)
        mascot_layout.addWidget(self.mascot_bubble)

        mascot = QLabel()
        mascot.setObjectName("mascot")
        mascot.setAlignment(Qt.AlignCenter)
        if self.mascot_image_path.exists():
            pixmap = QPixmap(str(self.mascot_image_path)).scaled(
                150, 190, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            mascot.setPixmap(pixmap)
        else:
            mascot.setText("Assistant")
        mascot_layout.addWidget(mascot)

        sidebar_layout.addWidget(mascot_box)
        sidebar_layout.addSpacing(10)

        tip = QLabel("简洁、干净、好用")
        tip.setObjectName("sidebar-tip")
        tip.setWordWrap(True)
        sidebar_layout.addWidget(tip)

        return sidebar

    def _title_bar_mouse_press(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def _title_bar_mouse_move(self, event):
        if self._drag_pos is not None and event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos)
            event.accept()

    def _title_bar_mouse_release(self, event):
        self._drag_pos = None
        event.accept()

    def toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def resizeEvent(self, event):
        if self.sakura_overlay and self.body_widget:
            self.sakura_overlay.setGeometry(self.body_widget.rect())
        super().resizeEvent(event)

    # ---------- 下载页 ----------

    def _build_download_page(self):
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        # 窗口收窄到内容放不下时，宁可出现横向滚动条，也不要把右侧控件悄悄裁掉
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)

        header = QLabel("下载任务")
        header.setObjectName("page-title")
        sub = QLabel("粘贴链接，选择格式，一键下载")
        sub.setObjectName("page-subtitle")
        layout.addWidget(header)
        layout.addWidget(sub)

        # 输入卡片
        input_card, input_layout = self.create_card("链接输入")
        self.input_edit = DroppablePlainTextEdit()
        self.input_edit.setPlaceholderText(
            "每行一个链接或 BV 号，也可以用空格/逗号分隔\n"
            "支持从浏览器直接拖拽链接到此处\n"
            "示例：https://www.bilibili.com/video/BV13x41117TL"
        )
        self.input_edit.setMinimumHeight(80)
        self.input_edit.textChanged.connect(self.schedule_preview)
        input_layout.addWidget(self.input_edit)

        input_actions = QHBoxLayout()
        self.preview_btn = QPushButton("解析一下")
        self.preview_btn.clicked.connect(lambda: self.start_preview(force=True))
        self.use_format_btn = QPushButton("使用这个格式")
        self.use_format_btn.setEnabled(False)
        self.use_format_btn.clicked.connect(self.use_selected_format)
        self.add_pages_btn = QPushButton("添加选中的分P")
        self.add_pages_btn.setEnabled(False)
        self.add_pages_btn.clicked.connect(self.add_selected_pages_to_input)
        self.select_all_pages_btn = QPushButton("全选分P")
        self.select_all_pages_btn.setEnabled(False)
        self.select_all_pages_btn.clicked.connect(lambda: self.pages_table.selectAll())
        self.clear_input_btn = QPushButton("清空输入")
        self.clear_input_btn.setObjectName("secondaryBtn")
        self.clear_input_btn.clicked.connect(self.clear_input)
        self.import_links_btn = QPushButton("导入链接")
        self.import_links_btn.setObjectName("secondaryBtn")
        self.import_links_btn.clicked.connect(self.import_links_from_file)
        input_actions.addWidget(self.preview_btn)
        input_actions.addWidget(self.use_format_btn)
        input_actions.addWidget(self.add_pages_btn)
        input_actions.addWidget(self.select_all_pages_btn)
        input_actions.addStretch()
        input_actions.addWidget(self.import_links_btn)
        input_actions.addWidget(self.clear_input_btn)
        input_layout.addLayout(input_actions)

        # 当前下载格式：选完格式后必须能在下载页直接看到，而不是只在设置页里
        format_row = QHBoxLayout()
        format_row.setSpacing(10)
        format_hint = QLabel("下载格式")
        format_hint.setStyleSheet("color: #64748b; font-weight: 600;")
        self.selected_format_label = QLabel()
        self.selected_format_label.setWordWrap(True)
        self.clear_format_btn = QPushButton("清除选择")
        self.clear_format_btn.setObjectName("secondaryBtn")
        self.clear_format_btn.clicked.connect(self.clear_selected_format)
        format_row.addWidget(format_hint)
        format_row.addWidget(self.selected_format_label, 1)
        format_row.addWidget(self.clear_format_btn)
        input_layout.addLayout(format_row)
        layout.addWidget(input_card)

        # 预览卡片
        preview_card, preview_layout = self.create_card("视频预览")
        preview_top = QHBoxLayout()
        self.cover_label = QLabel("暂无封面")
        self.cover_label.setObjectName("cover")
        self.cover_label.setFixedSize(220, 124)
        self.cover_label.setAlignment(Qt.AlignCenter)
        preview_top.addWidget(self.cover_label)

        meta_grid = QGridLayout()
        meta_grid.setColumnStretch(1, 1)
        meta_grid.setColumnStretch(3, 1)
        meta_grid.setHorizontalSpacing(12)
        meta_grid.setVerticalSpacing(6)
        self.preview_title_label = QLabel("粘贴链接后自动解析")
        self.preview_title_label.setWordWrap(True)
        self.preview_title_label.setStyleSheet("font-weight: 600; color: #1a1a2e;")
        self.preview_uploader_label = QLabel("-")
        self.preview_duration_label = QLabel("-")
        self.preview_stats_label = QLabel("-")
        self.preview_pages_label = QLabel("-")
        self.preview_source_label = QLabel("-")
        self.preview_url_label = QLabel("-")
        self.preview_url_label.setWordWrap(True)
        self.preview_note_label = QLabel("-")
        self.preview_note_label.setWordWrap(True)
        meta_grid.addWidget(QLabel("标题"), 0, 0)
        meta_grid.addWidget(self.preview_title_label, 0, 1, 1, 3)
        meta_grid.addWidget(QLabel("UP/作者"), 1, 0)
        meta_grid.addWidget(self.preview_uploader_label, 1, 1)
        meta_grid.addWidget(QLabel("时长"), 1, 2)
        meta_grid.addWidget(self.preview_duration_label, 1, 3)
        meta_grid.addWidget(QLabel("数据"), 2, 0)
        meta_grid.addWidget(self.preview_stats_label, 2, 1)
        meta_grid.addWidget(QLabel("分P"), 2, 2)
        meta_grid.addWidget(self.preview_pages_label, 2, 3)
        meta_grid.addWidget(QLabel("来源"), 3, 0)
        meta_grid.addWidget(self.preview_source_label, 3, 1)
        meta_grid.addWidget(QLabel("链接"), 3, 2)
        meta_grid.addWidget(self.preview_url_label, 4, 0, 1, 4)
        meta_grid.addWidget(self.preview_note_label, 5, 0, 1, 4)
        preview_top.addLayout(meta_grid, 1)
        preview_layout.addLayout(preview_top)

        self.formats_table = QTableWidget(0, 8)
        self.formats_table.setHorizontalHeaderLabels(
            ["选择", "格式ID", "类型", "分辨率", "FPS", "编码", "大小", "说明"]
        )
        self.formats_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.formats_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.formats_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.formats_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.formats_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.formats_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.formats_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.formats_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        self.formats_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.formats_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.formats_table.itemSelectionChanged.connect(self.on_format_selection_changed)
        self.formats_table.setToolTip("单击选中一行，双击（或点「使用这个格式」）确认使用该格式")
        self.formats_table.doubleClicked.connect(self.on_format_double_clicked)
        self.formats_table.setMinimumHeight(120)
        self._set_roomy_rows(self.formats_table)
        stream_row = QHBoxLayout()
        self.show_streams_check = QCheckBox("显示全部原始流（高级）")
        self.show_streams_check.setToolTip(
            "B站 1080P 及以上是 DASH 流：画面和声音在服务器上就是分开的两条流，\n"
            "没有任何一条流自带声音。默认列表按分辨率合并显示，\n"
            "选中任意一行都会自动配上最佳音频（需要 ffmpeg 合并）。\n"
            "勾选后可看到并单独选择每一条原始流。"
        )
        self.show_streams_check.stateChanged.connect(self.on_show_streams_changed)
        stream_row.addWidget(self.show_streams_check)
        stream_hint = QLabel("列表按分辨率合并，选中哪一行都会自动带上声音")
        stream_hint.setStyleSheet("color: #64748b; font-size: 12px;")
        stream_row.addWidget(stream_hint)
        stream_row.addStretch()
        preview_layout.addLayout(stream_row)
        preview_layout.addWidget(self.formats_table)

        pages_label = QLabel("分 P 列表（多 P 视频可勾选要下载的分 P）")
        pages_label.setStyleSheet("color: #6b7280; margin-top: 4px;")
        preview_layout.addWidget(pages_label)
        self.pages_table = QTableWidget(0, 4)
        self.pages_table.setHorizontalHeaderLabels(["分P", "标题", "时长", "链接"])
        self.pages_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.pages_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.pages_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.pages_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.pages_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.pages_table.setSelectionMode(QAbstractItemView.MultiSelection)
        self.pages_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.pages_table.setMinimumHeight(80)
        self._set_roomy_rows(self.pages_table)
        self.pages_table.setVisible(False)
        pages_label.setVisible(False)
        self.pages_label = pages_label
        preview_layout.addWidget(self.pages_table)
        layout.addWidget(preview_card)

        # 队列卡片
        queue_card, queue_layout = self.create_card("⏬ 下载队列")
        action_row = QHBoxLayout()
        self.start_btn = QPushButton("开始下载吧")
        if (RESOURCE_DIR / "download.png").exists():
            self.start_btn.setIcon(QIcon(str(RESOURCE_DIR / "download.png")))
        self.start_btn.clicked.connect(self.start_downloads)
        self.pause_btn = QPushButton("暂停一下")
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self.toggle_pause_queue)
        self.cancel_btn = QPushButton("取消下载")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_downloads)
        action_row.addWidget(self.start_btn)
        action_row.addWidget(self.pause_btn)
        action_row.addWidget(self.cancel_btn)
        action_row.addStretch()
        queue_layout.addLayout(action_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        queue_layout.addWidget(self.progress_bar)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["任务", "状态", "进度", "输出"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_task_context_menu)
        self.table.doubleClicked.connect(self.on_task_double_clicked)
        self.table.setMinimumHeight(120)
        self._set_roomy_rows(self.table)
        queue_layout.addWidget(self.table)

        log_header = QHBoxLayout()
        log_label = QLabel("运行日志")
        log_label.setStyleSheet("font-weight: 600; color: #1a1a2e;")
        self.clear_log_btn = QPushButton("清空日志")
        self.clear_log_btn.setObjectName("secondaryBtn")
        self.clear_log_btn.clicked.connect(self.log_edit_clear)
        log_header.addWidget(log_label)
        log_header.addStretch()
        log_header.addWidget(self.clear_log_btn)
        queue_layout.addLayout(log_header)

        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMinimumHeight(60)
        self.log_edit.setMaximumHeight(140)
        queue_layout.addWidget(self.log_edit)
        layout.addWidget(queue_card)

        layout.addStretch()
        scroll.setWidget(container)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(scroll)
        self.content_stack.addWidget(page)

    # ---------- 历史页 ----------

    def _build_history_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)

        header = QLabel("下载历史")
        header.setObjectName("page-title")
        sub = QLabel("查看、重新下载或管理已完成任务")
        sub.setObjectName("page-subtitle")
        layout.addWidget(header)
        layout.addWidget(sub)

        # 分两行排布：窗口变窄时按钮不会被压到文字显示不全
        action_row = QHBoxLayout()
        action_row2 = QHBoxLayout()
        self.history_refresh_btn = QPushButton("刷新")
        self.history_refresh_btn.setObjectName("secondaryBtn")
        self.history_refresh_btn.clicked.connect(self.refresh_history)
        self.history_redownload_btn = QPushButton("重新下载")
        self.history_redownload_btn.clicked.connect(self.redownload_history)
        self.history_open_file_btn = QPushButton("打开文件")
        self.history_open_file_btn.setObjectName("secondaryBtn")
        self.history_open_file_btn.clicked.connect(self.open_history_file)
        self.history_open_dir_btn = QPushButton("打开目录")
        self.history_open_dir_btn.setObjectName("secondaryBtn")
        self.history_open_dir_btn.clicked.connect(self.open_history_dir)
        self.history_delete_btn = QPushButton("删除记录")
        self.history_delete_btn.setObjectName("secondaryBtn")
        self.history_delete_btn.clicked.connect(self.delete_history)
        self.history_clear_btn = QPushButton("清空全部")
        self.history_clear_btn.setObjectName("secondaryBtn")
        self.history_clear_btn.clicked.connect(self.clear_all_history)
        self.history_export_csv_btn = QPushButton("导出 CSV")
        self.history_export_csv_btn.setObjectName("secondaryBtn")
        self.history_export_csv_btn.clicked.connect(lambda: self.export_history("csv"))
        self.history_export_json_btn = QPushButton("导出 JSON")
        self.history_export_json_btn.setObjectName("secondaryBtn")
        self.history_export_json_btn.clicked.connect(lambda: self.export_history("json"))
        action_row.addWidget(self.history_refresh_btn)
        action_row.addWidget(self.history_redownload_btn)
        action_row.addWidget(self.history_open_file_btn)
        action_row.addWidget(self.history_open_dir_btn)
        action_row.addStretch()
        action_row2.addWidget(self.history_delete_btn)
        action_row2.addWidget(self.history_clear_btn)
        action_row2.addWidget(self.history_export_csv_btn)
        action_row2.addWidget(self.history_export_json_btn)
        action_row2.addStretch()
        layout.addLayout(action_row)
        layout.addLayout(action_row2)

        self.history_table = QTableWidget(0, 7)
        self.history_table.setHorizontalHeaderLabels(
            ["标题", "状态", "大小", "时长", "分辨率", "完成时间", "路径"]
        )
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setSortingEnabled(True)
        self.history_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.history_table.customContextMenuRequested.connect(self.show_history_context_menu)
        self.history_table.doubleClicked.connect(self.on_history_double_clicked)
        self._set_roomy_rows(self.history_table)
        layout.addWidget(self.history_table, 1)
        self.content_stack.addWidget(page)

    # ---------- 设置页 ----------

    def _build_settings_page(self):
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)

        header = QLabel("偏好设置")
        header.setObjectName("page-title")
        sub = QLabel("按自己的习惯配置下载方式")
        sub.setObjectName("page-subtitle")
        layout.addWidget(header)
        layout.addWidget(sub)

        # Cookie 卡片
        cookie_card, cookie_layout = self.create_card("Cookie 设置")
        cookie_mode_row = QHBoxLayout()
        cookie_mode_row.addWidget(QLabel("Cookie 来源"))
        self.cookie_mode_combo = QComboBox()
        self.cookie_mode_combo.addItem("不使用 Cookie（公开视频自动兜底）", "none")
        self.cookie_mode_combo.addItem("cookies.txt 文件", "file")
        self.cookie_mode_combo.addItem("读取 Chrome Cookie", "chrome")
        self.cookie_mode_combo.addItem("读取 Edge Cookie", "edge")
        self.cookie_mode_combo.addItem("读取 Firefox Cookie", "firefox")
        self.cookie_mode_combo.currentIndexChanged.connect(self.update_cookie_controls)
        cookie_mode_row.addWidget(self.cookie_mode_combo, 1)
        self.qr_login_btn = QPushButton("扫码登录")
        self.qr_login_btn.setStyleSheet("background: #16a34a;")
        self.qr_login_btn.clicked.connect(self.start_qr_login)
        self.check_cookie_btn = QPushButton("检测一下")
        self.check_cookie_btn.clicked.connect(self.check_cookie_status)
        cookie_mode_row.addWidget(self.qr_login_btn)
        cookie_mode_row.addWidget(self.check_cookie_btn)
        cookie_layout.addLayout(cookie_mode_row)

        cookie_file_row = QHBoxLayout()
        cookie_file_row.addWidget(QLabel("Cookie 文件"))
        self.cookie_file_edit = QLineEdit()
        self.cookie_file_edit.setPlaceholderText("选择从已登录 B站 浏览器导出的 cookies.txt")
        self.cookie_file_btn = QPushButton("浏览")
        self.cookie_file_btn.setObjectName("secondaryBtn")
        self.cookie_file_btn.clicked.connect(self.choose_cookie_file)
        cookie_file_row.addWidget(self.cookie_file_edit, 1)
        cookie_file_row.addWidget(self.cookie_file_btn)
        cookie_layout.addLayout(cookie_file_row)
        layout.addWidget(cookie_card)

        # 下载设置卡片
        dl_card, dl_layout = self.create_card("下载设置")
        # 注意：必须用 QGridLayout() 再 addLayout 到卡片已有布局上。
        # 直接 QGridLayout(dl_card) 会因为卡片已有布局而安装失败，
        # 里面的控件会没有父控件、完全不显示。
        dl_grid = QGridLayout()
        dl_layout.addLayout(dl_grid)
        dl_grid.setColumnStretch(1, 1)
        dl_grid.setColumnStretch(3, 1)
        dl_grid.setHorizontalSpacing(12)
        dl_grid.setVerticalSpacing(10)

        self.quality_combo = QComboBox()
        for value, label in QUALITY_LABELS.items():
            self.quality_combo.addItem(label, value)
        dl_grid.addWidget(QLabel("清晰度"), 0, 0)
        dl_grid.addWidget(self.quality_combo, 0, 1)

        self.codec_combo = QComboBox()
        for value, label in CODEC_LABELS.items():
            self.codec_combo.addItem(label, value)
        dl_grid.addWidget(QLabel("编码偏好"), 0, 2)
        dl_grid.addWidget(self.codec_combo, 0, 3)

        self.audio_quality_combo = QComboBox()
        for value, label in AUDIO_QUALITY_LABELS.items():
            self.audio_quality_combo.addItem(label, value)
        dl_grid.addWidget(QLabel("音频质量"), 1, 0)
        dl_grid.addWidget(self.audio_quality_combo, 1, 1)

        self.thread_combo = QComboBox()
        for value in range(1, 9):
            self.thread_combo.addItem(str(value), value)
        dl_grid.addWidget(QLabel("分片并发"), 1, 2)
        dl_grid.addWidget(self.thread_combo, 1, 3)

        self.concurrent_spin = QSpinBox()
        self.concurrent_spin.setRange(1, 8)
        self.concurrent_spin.setToolTip("同时下载多个视频的任务数（1=顺序下载，建议 2~4）")
        dl_grid.addWidget(QLabel("任务并发"), 4, 0)
        dl_grid.addWidget(self.concurrent_spin, 4, 1)

        self.custom_format_edit = QLineEdit()
        self.custom_format_edit.setReadOnly(True)
        self.custom_format_edit.setPlaceholderText("在预览格式表选中一行后点击\"使用这个格式\"")
        dl_grid.addWidget(QLabel("选中格式"), 2, 0)
        dl_grid.addWidget(self.custom_format_edit, 2, 1, 1, 3)

        dl_grid.addWidget(QLabel("保存位置"), 3, 0)
        dir_row = QHBoxLayout()
        self.dir_edit = QLineEdit()
        self.dir_edit.setReadOnly(True)
        self.dir_btn = QPushButton("浏览")
        self.dir_btn.setObjectName("secondaryBtn")
        if (RESOURCE_DIR / "folder.png").exists():
            self.dir_btn.setIcon(QIcon(str(RESOURCE_DIR / "folder.png")))
        self.dir_btn.clicked.connect(self.choose_download_dir)
        self.open_dir_btn = QPushButton("打开")
        self.open_dir_btn.setObjectName("secondaryBtn")
        if (RESOURCE_DIR / "folder.png").exists():
            self.open_dir_btn.setIcon(QIcon(str(RESOURCE_DIR / "folder.png")))
        self.open_dir_btn.clicked.connect(self.open_download_dir)
        dir_row.addWidget(self.dir_edit, 1)
        dir_row.addWidget(self.dir_btn)
        dir_row.addWidget(self.open_dir_btn)
        dl_grid.addLayout(dir_row, 3, 1, 1, 3)
        layout.addWidget(dl_card)

        # 网络与文件名卡片
        net_card, net_layout = self.create_card("网络与文件名")
        net_grid = QGridLayout()
        net_layout.addLayout(net_grid)
        net_grid.setColumnStretch(1, 1)
        net_grid.setHorizontalSpacing(12)
        net_grid.setVerticalSpacing(10)
        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("可选，例如 http://127.0.0.1:7890 或 socks5://127.0.0.1:1080")
        net_grid.addWidget(QLabel("代理"), 0, 0)
        net_grid.addWidget(self.proxy_edit, 0, 1, 1, 3)
        self.template_edit = QLineEdit()
        self.template_edit.setPlaceholderText("%(title).180B [%(id)s].%(ext)s")
        net_grid.addWidget(QLabel("文件名"), 1, 0)
        net_grid.addWidget(self.template_edit, 1, 1, 1, 3)
        layout.addWidget(net_card)

        # 附加内容卡片
        extra_card, extra_layout = self.create_card("附加内容")
        extra_row = QHBoxLayout()
        self.thumbnail_check = QCheckBox("下载封面")
        self.subtitle_check = QCheckBox("下载字幕")
        self.danmaku_check = QCheckBox("下载弹幕(B站)")
        extra_row.addWidget(self.thumbnail_check)
        extra_row.addWidget(self.subtitle_check)
        extra_row.addWidget(self.danmaku_check)
        extra_row.addStretch()
        extra_layout.addLayout(extra_row)
        layout.addWidget(extra_card)

        # 视觉特效开关卡片
        fx_card, fx_layout = self.create_card("视觉特效", glow=True, glow_color="#a78bfa")
        fx_row = QHBoxLayout()
        self.sakura_check = QCheckBox("粒子效果")
        self.neon_check = QCheckBox("卡片发光")
        self.sound_check = QCheckBox("音效反馈")
        self.sakura_check.stateChanged.connect(self.toggle_sakura_overlay)
        self.neon_check.stateChanged.connect(self.apply_fx_settings)
        self.sound_check.stateChanged.connect(self.apply_fx_settings)
        fx_row.addWidget(self.sakura_check)
        fx_row.addWidget(self.neon_check)
        fx_row.addWidget(self.sound_check)
        fx_row.addStretch()
        fx_layout.addLayout(fx_row)
        layout.addWidget(fx_card)

        # 恢复默认设置按钮
        reset_layout = QHBoxLayout()
        reset_layout.addStretch()
        self.export_settings_btn = QPushButton("导出设置")
        self.export_settings_btn.setObjectName("secondaryBtn")
        self.export_settings_btn.clicked.connect(self.export_settings)
        reset_layout.addWidget(self.export_settings_btn)
        self.import_settings_btn = QPushButton("导入设置")
        self.import_settings_btn.setObjectName("secondaryBtn")
        self.import_settings_btn.clicked.connect(self.import_settings)
        reset_layout.addWidget(self.import_settings_btn)
        self.reset_settings_btn = QPushButton("恢复默认设置")
        self.reset_settings_btn.setObjectName("secondaryBtn")
        self.reset_settings_btn.clicked.connect(self.reset_settings)
        reset_layout.addWidget(self.reset_settings_btn)
        layout.addLayout(reset_layout)

        layout.addStretch()
        scroll.setWidget(container)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(scroll)
        self.content_stack.addWidget(page)

    # ---------- 主题样式 ----------

    def _apply_theme(self):
        self.setStyleSheet("""
            QWidget {
                font-size: 14px;
                font-family: "Microsoft YaHei", "Segoe UI", "Inter", sans-serif;
                color: #334155;
            }
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #f8fafc, stop:0.5 #f1f5f9, stop:1 #f5f3ff);
            }
            #sidebar {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1e293b, stop:1 #334155);
                border-right: 1px solid rgba(255,255,255,0.08);
            }
            #sidebarDecor {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #fb923c, stop:0.5 #f472b6, stop:1 #a78bfa);
                border-radius: 2px;
            }
            #logo {
                font-size: 20px;
                font-weight: 800;
                color: #f8fafc;
                padding-left: 4px;
                letter-spacing: -0.5px;
            }
            #logo-subtitle {
                font-size: 12px;
                color: rgba(248,250,252,0.65);
                padding-left: 4px;
                letter-spacing: 1px;
            }
            #navBtn {
                background: transparent;
                color: rgba(248,250,252,0.85);
                border: none;
                border-radius: 14px;
                padding: 12px 18px;
                text-align: left;
                font-size: 15px;
                font-weight: 500;
                margin: 4px 0;
            }
            #navBtn:checked {
                background: rgba(255,255,255,0.12);
                color: #ffffff;
                font-weight: 600;
                border: 1px solid rgba(255,255,255,0.15);
            }
            #navBtn:hover:!checked {
                background: rgba(255,255,255,0.08);
            }
            #sidebar-tip {
                font-size: 12px;
                color: rgba(248,250,252,0.55);
                padding: 10px 8px;
                background: rgba(255,255,255,0.06);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 12px;
            }
            #titleBar {
                background: rgba(255,255,255,0.85);
                border-bottom: 1px solid rgba(148,163,184,0.2);
            }
            #titleIcon {
                background: transparent;
                border-radius: 8px;
            }
            #titleLabel {
                color: #334155;
                font-size: 14px;
                font-weight: 600;
                background: transparent;
            }
            #windowCtrl {
                background: transparent;
                color: #64748b;
                border: none;
                border-radius: 8px;
                font-size: 16px;
                font-weight: 500;
                padding: 0;
            }
            #windowCtrl:hover {
                background: #e2e8f0;
                color: #334155;
            }
            #windowCtrlClose {
                background: transparent;
                color: #64748b;
                border: none;
                border-radius: 8px;
                font-size: 18px;
                font-weight: 500;
                padding: 0;
            }
            #windowCtrlClose:hover {
                background: #fecaca;
                color: #dc2626;
            }
            #mascotBox {
                background: rgba(255,255,255,0.08);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 20px;
            }
            #mascot {
                font-size: 12px;
                font-weight: 600;
                color: rgba(248,250,252,0.7);
                background: transparent;
                border-radius: 14px;
                padding: 6px 4px;
                min-height: 60px;
            }
            #mascotBubble {
                font-size: 12px;
                font-weight: 500;
                color: #1e293b;
                background: rgba(255,255,255,0.92);
                border: none;
                border-radius: 12px;
                padding: 8px 10px;
            }
            #page-title {
                font-size: 24px;
                font-weight: 700;
                color: #1e293b;
                padding-bottom: 4px;
            }
            #page-subtitle {
                color: #64748b;
                margin-bottom: 6px;
                font-size: 13px;
                font-weight: 500;
            }
            #card {
                background: rgba(255,255,255,0.82);
                border: 1px solid rgba(148,163,184,0.18);
                border-radius: 20px;
            }
            #card-title {
                font-size: 15px;
                font-weight: 700;
                color: #334155;
                padding-bottom: 8px;
                border-bottom: 1px solid rgba(148,163,184,0.18);
            }
            #cover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #fff7ed, stop:0.5 #fdf4ff, stop:1 #f0f9ff);
                border: 1px solid rgba(148,163,184,0.2);
                border-radius: 16px;
                color: #64748b;
                font-weight: 500;
            }
            QPlainTextEdit, QLineEdit, QComboBox {
                background: rgba(255,255,255,0.7);
                border: 1px solid rgba(148,163,184,0.25);
                border-radius: 12px;
                padding: 8px 12px;
                selection-background-color: #fb923c;
                selection-color: white;
            }
            QPlainTextEdit:focus, QLineEdit:focus, QComboBox:focus {
                border: 1px solid #fb923c;
                background: rgba(255,255,255,0.95);
            }
            QComboBox::drop-down {
                border: none;
                width: 26px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid #94a3b8;
                width: 0px;
                height: 0px;
            }
            QPushButton {
                background: #334155;
                color: white;
                border: none;
                border-radius: 12px;
                padding: 9px 16px;
                font-weight: 600;
            }
            QPushButton:hover:!disabled {
                background: #475569;
            }
            QPushButton:pressed:!disabled {
                background: #1e293b;
            }
            QPushButton:disabled {
                background: #e2e8f0;
                color: #94a3b8;
            }
            #secondaryBtn {
                background: rgba(255,255,255,0.7);
                color: #334155;
                border: 1px solid rgba(148,163,184,0.25);
            }
            #secondaryBtn:hover:!disabled {
                background: rgba(255,255,255,0.95);
                border: 1px solid rgba(148,163,184,0.4);
            }
            QProgressBar {
                border: 1px solid rgba(148,163,184,0.2);
                border-radius: 12px;
                height: 22px;
                background: rgba(255,255,255,0.6);
                text-align: center;
                color: #334155;
                font-weight: 600;
                font-size: 12px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #fb923c, stop:1 #a78bfa);
                border-radius: 10px;
            }
            QTableWidget {
                background: rgba(255,255,255,0.7);
                border: 1px solid rgba(148,163,184,0.18);
                border-radius: 16px;
                gridline-color: rgba(148,163,184,0.12);
                selection-background-color: rgba(251,146,60,0.2);
                selection-color: #1e293b;
                alternate-background-color: rgba(248,250,252,0.5);
            }
            QTableWidget::item {
                padding: 8px;
            }
            QHeaderView::section {
                background: rgba(241,245,249,0.8);
                color: #475569;
                border: none;
                padding: 10px 8px;
                font-weight: 700;
            }
            QCheckBox {
                spacing: 8px;
                font-weight: 500;
            }
            QCheckBox::indicator {
                width: 20px;
                height: 20px;
                border-radius: 6px;
                border: 1px solid rgba(148,163,184,0.4);
                background: rgba(255,255,255,0.7);
            }
            QCheckBox::indicator:checked {
                background: #fb923c;
                border: 1px solid #fb923c;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 10px;
                border: none;
                margin: 2px;
            }
            QScrollBar::handle:vertical {
                background: rgba(148,163,184,0.35);
                border-radius: 5px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(148,163,184,0.55);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                background: none;
                border: none;
            }
            QScrollBar:horizontal {
                background: transparent;
                height: 10px;
                border: none;
                margin: 2px;
            }
            QScrollBar::handle:horizontal {
                background: rgba(148,163,184,0.35);
                border-radius: 5px;
                min-width: 30px;
            }
            QScrollBar::handle:horizontal:hover {
                background: rgba(148,163,184,0.55);
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                background: none;
                border: none;
            }
            QStatusBar {
                background: rgba(255,255,255,0.7);
                color: #475569;
                border-top: 1px solid rgba(148,163,184,0.15);
                padding: 5px 14px;
                font-size: 12px;
                font-weight: 500;
            }
            QStatusBar::item {
                border: none;
            }
            QLabel {
                color: #334155;
                background: transparent;
            }
            QMenu {
                background: rgba(255,255,255,0.95);
                border: 1px solid rgba(148,163,184,0.2);
                border-radius: 12px;
                padding: 6px;
            }
            QMenu::item {
                padding: 7px 26px;
                border-radius: 8px;
                font-weight: 500;
            }
            QMenu::item:selected {
                background: rgba(251,146,60,0.15);
                color: #1e293b;
            }
            QMenu::separator {
                height: 1px;
                background: rgba(148,163,184,0.18);
                margin: 5px 10px;
            }
            QToolTip {
                background: #1e293b;
                color: #f8fafc;
                border: none;
                border-radius: 8px;
                padding: 6px 12px;
                font-weight: 500;
            }
        """)

    # ---------- 设置读写 ----------

    def apply_settings_to_ui(self):
        self.dir_edit.setText(self.settings["download_dir"])
        self.proxy_edit.setText(self.settings.get("proxy", ""))
        self.cookie_file_edit.setText(self.settings.get("cookie_file", ""))
        self.template_edit.setText(self.settings.get("filename_template", DEFAULT_SETTINGS["filename_template"]))
        self.custom_format_edit.setText(self.settings.get("custom_format", ""))
        self.set_combo_value(self.quality_combo, self.settings.get("quality", "best"))
        self.set_combo_value(self.cookie_mode_combo, self.settings.get("cookie_mode", "none"))
        self.set_combo_value(self.thread_combo, int(self.settings.get("fragment_threads", 4)))
        self.concurrent_spin.setValue(int(self.settings.get("concurrent_downloads", 1) or 1))
        self.set_combo_value(self.codec_combo, self.settings.get("codec_preference", "auto"))
        self.set_combo_value(self.audio_quality_combo, self.settings.get("audio_quality", "auto"))
        self.thumbnail_check.setChecked(bool(self.settings.get("download_thumbnail", False)))
        self.subtitle_check.setChecked(bool(self.settings.get("download_subtitle", False)))
        self.danmaku_check.setChecked(bool(self.settings.get("download_danmaku", False)))
        self.sakura_check.setChecked(bool(self.settings.get("fx_sakura", True)))
        self.neon_check.setChecked(bool(self.settings.get("fx_neon", True)))
        self.sound_check.setChecked(bool(self.settings.get("fx_sound", True)))
        self.apply_fx_settings()
        self.update_cookie_controls()
        self.refresh_selected_format_label()

    def set_combo_value(self, combo, value):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def collect_settings(self):
        settings = dict(self.settings)
        settings.update({
            "download_dir": self.dir_edit.text().strip() or str(DEFAULT_DOWNLOAD_DIR),
            "quality": self.quality_combo.currentData(),
            "custom_format": self.custom_format_edit.text().strip(),
            "cookie_mode": self.cookie_mode_combo.currentData(),
            "cookie_file": self.cookie_file_edit.text().strip(),
            "proxy": self.proxy_edit.text().strip(),
            "filename_template": self.template_edit.text().strip() or DEFAULT_SETTINGS["filename_template"],
            "fragment_threads": self.thread_combo.currentData(),
            "concurrent_downloads": self.concurrent_spin.value(),
            "codec_preference": self.codec_combo.currentData(),
            "audio_quality": self.audio_quality_combo.currentData(),
            "download_thumbnail": self.thumbnail_check.isChecked(),
            "download_subtitle": self.subtitle_check.isChecked(),
            "download_danmaku": self.danmaku_check.isChecked(),
            "fx_sakura": self.sakura_check.isChecked(),
            "fx_neon": self.neon_check.isChecked(),
            "fx_sound": self.sound_check.isChecked(),
        })
        return settings

    def reset_settings(self):
        """恢复默认设置并更新 UI。"""
        ret = QMessageBox.question(
            self, "恢复默认设置",
            "确定要恢复默认设置吗？\n当前自定义的下载目录、Cookie 等配置将被重置。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        self.settings = dict(DEFAULT_SETTINGS)
        ok, err = save_settings(self.settings)
        if not ok:
            QMessageBox.warning(self, "保存失败", f"恢复默认设置后保存失败：\n{err}")
            return
        self.apply_settings_to_ui()
        self.statusBar().showMessage("已恢复默认设置", 5000)

    def toggle_sakura_overlay(self, state):
        """开关樱花飘落层。"""
        if self.sakura_overlay is None:
            return
        self.sakura_overlay.setVisible(state == Qt.Checked)

    def apply_fx_settings(self):
        """应用二次元特效开关到运行态。"""
        if self.sound_player:
            self.sound_player.enabled = self.sound_check.isChecked()
        neon_enabled = self.neon_check.isChecked()
        for card in self.findChildren(NeonGlowCard):
            card.set_glow_enabled(neon_enabled)

    # ---------- 文件/Cookie 选择 ----------

    def choose_download_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "选择下载文件夹", self.dir_edit.text())
        if folder:
            self.dir_edit.setText(folder)

    def choose_cookie_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 cookies.txt", "",
            "Cookie Files (cookies.txt *.txt);;All Files (*.*)",
        )
        if path:
            self.cookie_file_edit.setText(path)
            self.set_combo_value(self.cookie_mode_combo, "file")

    def update_cookie_controls(self):
        use_file = self.cookie_mode_combo.currentData() == "file"
        self.cookie_file_edit.setEnabled(use_file)
        self.cookie_file_btn.setEnabled(use_file)

    def open_download_dir(self):
        open_path_in_explorer(self.dir_edit.text() or str(DEFAULT_DOWNLOAD_DIR))

    # ---------- Cookie 检测 ----------

    def check_cookie_status(self):
        if self.cookie_check_worker and self.cookie_check_worker.isRunning():
            return
        self.settings = self.collect_settings()
        self.check_cookie_btn.setEnabled(False)
        self.statusBar().showMessage("正在检测 Cookie...")
        self.cookie_check_worker = CookieCheckWorker(self.settings, self)
        self.cookie_check_worker.result_ready.connect(self.on_cookie_check_result)
        self.cookie_check_worker.failed.connect(self.on_cookie_check_failed)
        self.cookie_check_worker.finished.connect(self.on_cookie_check_finished)
        self.cookie_check_worker.start()

    def on_cookie_check_result(self, result):
        if result.get("reason"):
            QMessageBox.warning(self, "Cookie 检测", result["reason"])
            return
        if not result.get("logged_in"):
            QMessageBox.information(
                self, "Cookie 检测",
                "Cookie 未登录或已失效。\n请重新导出 cookies.txt 或关闭浏览器后重试。",
            )
            return
        vip_text = ""
        if result.get("vip_status"):
            vip_type = result.get("vip_type")
            if vip_type == 2:
                vip_text = "（年度大会员）"
            elif vip_type == 1:
                vip_text = "（月度大会员）"
        msg = (
            f"Cookie 可用\n\n"
            f"用户名: {result.get('username') or '-'}\n"
            f"UID: {result.get('mid') or '-'}\n"
            f"大会员: {'是' + vip_text if result.get('vip_status') else '否'}\n"
            f"SESSDATA: {'已获取' if result.get('sessdata_found') else '未获取'}"
        )
        QMessageBox.information(self, "Cookie 检测", msg)

    def on_cookie_check_failed(self, msg):
        QMessageBox.warning(self, "Cookie 检测失败", msg)

    def on_cookie_check_finished(self):
        self.check_cookie_btn.setEnabled(True)
        self.statusBar().showMessage("Cookie 检测完成", 5000)

    def start_qr_login(self):
        """小白一键配置：扫码登录 B站，自动保存 cookies.txt。"""
        self.settings = self.collect_settings()
        dialog = QrLoginDialog(self.settings, self)
        dialog.start()
        if dialog.exec_() == QDialog.Accepted and dialog.cookie_file_path:
            self.cookie_file_edit.setText(dialog.cookie_file_path)
            self.set_combo_value(self.cookie_mode_combo, "file")
            self.update_cookie_controls()
            self.settings = self.collect_settings()
            ok, err = save_settings(self.settings)
            if not ok:
                self.statusBar().showMessage(f"设置保存失败: {err}", 5000)
            self.append_log(f"扫码登录成功，Cookie 已保存: {dialog.cookie_file_path}")
            self.statusBar().showMessage("扫码登录成功", 5000)
            self.check_cookie_status()

    # ---------- 预览 ----------

    def schedule_preview(self):
        if self.worker and self.worker.isRunning():
            return
        self.preview_pending = True
        self.preview_timer.start(800)

    def start_preview(self, force=False):
        if self.worker and self.worker.isRunning():
            return
        if force:
            # 手动点击解析时取消待触发的自动预览，避免解析两次
            self.preview_timer.stop()
            self.preview_pending = False
        if self.sound_player:
            self.sound_player.play("click")
        text = self.input_edit.toPlainText()
        urls = split_inputs(text)
        if not urls:
            self.clear_preview()
            return
        url = urls[0]
        if not force and not self.preview_pending:
            return
        self.preview_pending = False
        self.preview_request_id += 1
        rid = self.preview_request_id
        self.preview_btn.setEnabled(False)
        self.preview_title_label.setText("解析中...")
        self.preview_note_label.setText("如果是批量链接，这里预览第一个。")
        self.formats_table.setRowCount(0)
        # 清掉上一个视频的格式信息，避免「补音频流」用到旧链接的音频
        self.preview_formats = []
        self.use_format_btn.setEnabled(False)
        self.preview_worker = PreviewWorker(rid, url, self.collect_settings(), self)
        self.preview_worker.info_ready.connect(self.on_preview_ready)
        self.preview_worker.failed.connect(self.on_preview_failed)
        self.preview_worker.finished.connect(self.on_preview_finished)
        self.preview_worker.start()

    def on_preview_ready(self, request_id, info):
        if request_id != self.preview_request_id:
            return
        if self.sound_player:
            self.sound_player.play("success")
        self.preview_formats = info.get("formats") or []
        self.preview_title_label.setText(info.get("title") or "-")
        self.preview_uploader_label.setText(info.get("uploader") or "-")
        self.preview_duration_label.setText(format_duration(info.get("duration")))
        stats = f"播放 {format_count(info.get('view_count'))}  点赞 {format_count(info.get('like_count'))}"
        upload_date = format_upload_date(info.get("upload_date"))
        if upload_date != "-":
            stats += f"  发布 {upload_date}"
        self.preview_stats_label.setText(stats)
        pages = info.get("pages") or []
        self.preview_pages_label.setText(f"{len(pages)} P" if pages else "单 P")
        self.preview_source_label.setText(info.get("source") or "-")
        self.preview_url_label.setText(info.get("url") or "-")
        self.preview_note_label.setText(info.get("note") or "-")
        thumb_url = info.get("thumbnail") or ""
        if thumb_url:
            self._load_thumbnail(thumb_url)
        else:
            self.cover_label.setText("暂无封面")
            self.cover_label.setPixmap(QPixmap())
        self.fill_formats_table(self.preview_formats)
        if pages:
            self.fill_pages_table(pages)
        else:
            self.pages_table.setRowCount(0)
            self.pages_table.setVisible(False)
            self.pages_label.setVisible(False)
            self.add_pages_btn.setEnabled(False)
            self.select_all_pages_btn.setEnabled(False)

    def on_preview_failed(self, request_id, msg):
        if request_id != self.preview_request_id:
            return
        if self.sound_player:
            self.sound_player.play("fail")
        self.preview_title_label.setText("解析失败")
        self.preview_note_label.setText(msg)
        QMessageBox.warning(self, "解析失败", f"无法解析该链接：\n\n{msg}\n\n请检查：\n1. 链接是否正确\n2. 是否需要配置 Cookie\n3. 网络是否连接正常")

    def on_preview_finished(self):
        self.preview_btn.setEnabled(True)

    def _load_thumbnail(self, url):
        """异步加载封面，避免主线程网络请求阻塞界面。"""
        proxy = (self.settings.get("proxy") or "").strip()
        self.cover_label.setPixmap(QPixmap())
        self.cover_label.setText("封面加载中...")
        rid = self.preview_request_id
        # 保留所有 worker 引用，防止线程运行中被 GC 回收导致崩溃
        self._thumb_workers = [w for w in self._thumb_workers if w.isRunning()]
        worker = ThumbnailWorker(url, proxy, self)
        worker.loaded.connect(lambda data, r=rid: self._on_thumbnail_loaded(r, data))
        worker.failed.connect(lambda msg, r=rid: self._on_thumbnail_failed(r, msg))
        self._thumb_workers.append(worker)
        worker.start()

    def _on_thumbnail_loaded(self, request_id, data):
        if request_id != self.preview_request_id:
            return
        pixmap = QPixmap()
        pixmap.loadFromData(data)
        if pixmap.isNull():
            self.cover_label.setText("封面加载失败")
            return
        self.cover_label.setPixmap(
            pixmap.scaled(220, 124, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def _on_thumbnail_failed(self, request_id, msg):
        if request_id != self.preview_request_id:
            return
        self.cover_label.setPixmap(QPixmap())
        self.cover_label.setText("封面加载失败")

    def fill_formats_table(self, formats):
        """渲染格式表：默认按分辨率合并，勾选后显示全部原始流。"""
        self.formats_table.setRowCount(0)
        if not formats:
            return
        if self._show_raw_streams():
            self._fill_raw_stream_table(formats)
        else:
            self._fill_grouped_format_table(formats)
        self.refresh_selected_format_label()

    def _show_raw_streams(self):
        check = getattr(self, "show_streams_check", None)
        return bool(check and check.isChecked())

    def on_show_streams_changed(self, state):
        """切换「按分辨率合并 / 全部原始流」视图后重画表格。"""
        if self.preview_formats:
            self.fill_formats_table(self.preview_formats)
        else:
            self.formats_table.setRowCount(0)
            self.refresh_selected_format_label()

    def _pick_video_for_height(self, candidates):
        """同一分辨率下按「编码偏好」挑一条视频流；auto 时优先 H.264（兼容性最好）。"""
        preference = (self.settings.get("codec_preference") or "auto").lower()
        orders = {
            "h264": ("avc", "hvc", "hev", "av0"),
            "hevc": ("hvc", "hev", "avc", "av0"),
            "av1": ("av0", "hvc", "hev", "avc"),
        }
        for key in orders.get(preference, ("avc", "hvc", "hev", "av0")):
            matched = [f for f in candidates if key in str(f.get("vcodec") or "").lower()]
            if matched:
                return max(matched, key=lambda item: item.get("tbr") or 0)
        return candidates[0] if candidates else None

    def _fill_grouped_format_table(self, formats):
        """按分辨率合并渲染：每行 = 该分辨率的视频 + 最佳音频，选中即自动合并成带声音的成品。

        B站 1080P 起走 DASH，服务端音视频本来就是两条独立流
        （实测某个视频 15 条流 = 12 条纯视频 + 3 条纯音频 + 0 条自带声音），
        所以「一个分辨率一行」才是普通用户真正需要看到的列表。
        """
        video_only, audio_only, muxed = [], [], []
        for f in formats:
            vcodec = f.get("vcodec") or ""
            acodec = f.get("acodec") or ""
            is_video = bool(vcodec and vcodec != "none")
            is_audio = bool(acodec and acodec != "none")
            if is_video and is_audio:
                muxed.append(f)
            elif is_video:
                video_only.append(f)
            elif is_audio:
                audio_only.append(f)

        best_audio = max(audio_only, key=lambda item: item.get("tbr") or 0, default=None)
        audio_id = str(best_audio.get("format_id") or "") if best_audio else ""
        ffmpeg_ready = has_ffmpeg()

        def resolution_of(f):
            if f.get("width") and f.get("height"):
                return f"{f['width']}x{f['height']}"
            if f.get("height"):
                return f"{f['height']}p"
            return ""

        def add_row(f, ftype, format_id, note, recommend="", size=None):
            row = self.formats_table.rowCount()
            self.formats_table.insertRow(row)
            mark_item = QTableWidgetItem(recommend)
            mark_item.setData(Qt.UserRole, recommend)
            self.formats_table.setItem(row, 0, mark_item)
            self.formats_table.setItem(row, 1, QTableWidgetItem(format_id))
            self.formats_table.setItem(row, 2, QTableWidgetItem(ftype))
            self.formats_table.setItem(row, 3, QTableWidgetItem(resolution_of(f)))
            self.formats_table.setItem(row, 4, QTableWidgetItem(format_fps(f.get("fps"))))
            self.formats_table.setItem(row, 5, QTableWidgetItem(f.get("vcodec") or "-"))
            self.formats_table.setItem(
                row, 6, QTableWidgetItem(format_bytes(size if size is not None else f.get("filesize")))
            )
            self.formats_table.setItem(row, 7, QTableWidgetItem(note))

        if not video_only:
            # 站点直接提供音视频一体的成品（例如 YouTube 的单文件格式）
            ordered = sorted(muxed, key=lambda item: item.get("height") or 0, reverse=True)
            for index, f in enumerate(ordered):
                add_row(f, "音视频", str(f.get("format_id", "")), "可直接下载",
                        recommend="推荐" if index == 0 else "")
            if best_audio:
                add_row(best_audio, "音频", audio_id, "仅音频，没有画面")
            return

        groups = {}
        for f in video_only:
            groups.setdefault(f.get("height") or 0, []).append(f)
        for index, height in enumerate(sorted(groups, reverse=True)):
            video = self._pick_video_for_height(groups[height])
            if video is None:
                continue
            video_id = str(video.get("format_id", ""))
            if audio_id and ffmpeg_ready:
                size = (video.get("filesize") or 0) + (best_audio.get("filesize") or 0)
                add_row(video, "视频+音频", f"{video_id}+{audio_id}",
                        "视频+最佳音频，下载时自动合并",
                        recommend="推荐" if index == 0 else "", size=size)
            else:
                note = "纯视频流，没有声音" if ffmpeg_ready else "纯视频流，缺 ffmpeg 无法合并音频"
                add_row(video, "视频", video_id, note)
        if best_audio:
            add_row(best_audio, "音频", audio_id, "仅音频，没有画面")

    def _fill_raw_stream_table(self, formats):
        # 找出最高分辨率的视频流和最高码率的音频流，用于推荐标记
        best_video = None
        best_audio = None
        for f in formats:
            vcodec = f.get("vcodec") or ""
            acodec = f.get("acodec") or ""
            if vcodec and vcodec != "none":
                if best_video is None or (f.get("height") or 0) > (best_video.get("height") or 0):
                    best_video = f
            if acodec and acodec != "none" and (not vcodec or vcodec == "none"):
                if best_audio is None or (f.get("tbr") or 0) > (best_audio.get("tbr") or 0):
                    best_audio = f

        has_cookie = self.settings.get("cookie_mode") != "none"
        ffmpeg_ready = has_ffmpeg()

        for f in formats:
            row = self.formats_table.rowCount()
            self.formats_table.insertRow(row)
            vcodec = f.get("vcodec") or ""
            acodec = f.get("acodec") or ""
            is_video = bool(vcodec and vcodec != "none")
            is_audio = bool(acodec and acodec != "none")
            if is_video and is_audio:
                ftype = "音视频"
            elif is_video:
                ftype = "视频"
            elif is_audio:
                ftype = "音频"
            else:
                ftype = "?"
            resolution = ""
            if f.get("width") and f.get("height"):
                resolution = f"{f['width']}x{f['height']}"
            elif f.get("height"):
                resolution = f"{f['height']}p"

            # 标记：纯视频流/纯音频流绝不标「推荐」，否则用户会选中没有声音的格式
            recommend = ""
            if is_video and not is_audio:
                recommend = "视频流"
            elif is_audio and not is_video:
                recommend = "音频流"

            # 可用性说明
            notes = []
            if f.get("note"):
                notes.append(f["note"])
            # DASH 视频+音频需要 ffmpeg 合并
            if is_video and not is_audio:
                notes.append("需合并音频")
                if not ffmpeg_ready:
                    notes.append("缺ffmpeg")
            # 高清格式可能需要 Cookie
            if is_video and (f.get("height") or 0) >= 1080 and not has_cookie:
                notes.append("需Cookie")
            note_text = " | ".join(notes) if notes else ("可直接下载" if (is_video and is_audio) else "")

            mark_item = QTableWidgetItem(recommend)
            # 原始标记存进 UserRole，方便在其上叠加「已选」而不丢「推荐」
            mark_item.setData(Qt.UserRole, recommend)
            self.formats_table.setItem(row, 0, mark_item)
            self.formats_table.setItem(row, 1, QTableWidgetItem(str(f.get("format_id", ""))))
            self.formats_table.setItem(row, 2, QTableWidgetItem(ftype))
            self.formats_table.setItem(row, 3, QTableWidgetItem(resolution))
            self.formats_table.setItem(row, 4, QTableWidgetItem(format_fps(f.get("fps"))))
            self.formats_table.setItem(row, 5, QTableWidgetItem(vcodec or "-"))
            self.formats_table.setItem(row, 6, QTableWidgetItem(format_bytes(f.get("filesize"))))
            self.formats_table.setItem(row, 7, QTableWidgetItem(note_text))

        # 增加 DASH 组合推荐行
        if best_video and best_audio and ffmpeg_ready:
            row = self.formats_table.rowCount()
            self.formats_table.insertRow(row)
            combined_size = (best_video.get("filesize") or 0) + (best_audio.get("filesize") or 0)
            vres = ""
            if best_video.get("width") and best_video.get("height"):
                vres = f"{best_video['width']}x{best_video['height']}"
            elif best_video.get("height"):
                vres = f"{best_video['height']}p"
            combo_item = QTableWidgetItem("推荐")
            combo_item.setData(Qt.UserRole, "推荐")
            self.formats_table.setItem(row, 0, combo_item)
            self.formats_table.setItem(row, 1, QTableWidgetItem(f"{best_video.get('format_id','')}+{best_audio.get('format_id','')}"))
            self.formats_table.setItem(row, 2, QTableWidgetItem("DASH组合"))
            self.formats_table.setItem(row, 3, QTableWidgetItem(vres))
            self.formats_table.setItem(row, 4, QTableWidgetItem(format_fps(best_video.get("fps"))))
            self.formats_table.setItem(row, 5, QTableWidgetItem(best_video.get("vcodec") or "-"))
            self.formats_table.setItem(row, 6, QTableWidgetItem(format_bytes(combined_size)))
            self.formats_table.setItem(row, 7, QTableWidgetItem("视频+音频合并 | 需ffmpeg"))

        # 没有可拆分的 DASH 流时（例如已经有音视频一体的格式），退回推荐最佳单文件
        if not (best_video and best_audio and ffmpeg_ready):
            row = self._best_muxed_row()
            if row is not None:
                item = self.formats_table.item(row, 0)
                if item is not None:
                    item.setData(Qt.UserRole, "推荐")
                    item.setText("推荐")

    def fill_pages_table(self, pages):
        self.pages_table.setRowCount(0)
        for p in pages:
            row = self.pages_table.rowCount()
            self.pages_table.insertRow(row)
            self.pages_table.setItem(row, 0, QTableWidgetItem(str(p.get("page", 1))))
            self.pages_table.setItem(row, 1, QTableWidgetItem(p.get("title") or "-"))
            self.pages_table.setItem(row, 2, QTableWidgetItem(format_duration(p.get("duration"))))
            self.pages_table.setItem(row, 3, QTableWidgetItem(p.get("url") or "-"))
        self.pages_table.setVisible(True)
        self.pages_label.setVisible(True)
        self.add_pages_btn.setEnabled(True)
        self.select_all_pages_btn.setEnabled(True)

    def clear_preview(self):
        self.preview_title_label.setText("粘贴链接后自动解析")
        self.preview_uploader_label.setText("-")
        self.preview_duration_label.setText("-")
        self.preview_stats_label.setText("-")
        self.preview_pages_label.setText("-")
        self.preview_source_label.setText("-")
        self.preview_url_label.setText("-")
        self.preview_note_label.setText("-")
        self.cover_label.setText("暂无封面")
        self.cover_label.setPixmap(QPixmap())
        self.formats_table.setRowCount(0)
        self.pages_table.setRowCount(0)
        self.pages_table.setVisible(False)
        self.pages_label.setVisible(False)
        self.use_format_btn.setEnabled(False)
        self.add_pages_btn.setEnabled(False)
        self.select_all_pages_btn.setEnabled(False)
        self.refresh_selected_format_label()

    def on_format_selection_changed(self):
        selected = self.formats_table.selectionModel().selectedRows()
        self.use_format_btn.setEnabled(bool(selected))
        if selected:
            row = selected[0].row()
            item = self.formats_table.item(row, 1)
            self.statusBar().showMessage(
                f"已选中格式 {item.text() if item else ''}，双击或点「使用这个格式」确认", 4000
            )

    def on_format_double_clicked(self, index):
        """双击格式行 = 使用这个格式。"""
        self.formats_table.selectRow(index.row())
        self.use_selected_format()

    def _best_muxed_row(self):
        """返回分辨率最高的「音视频一体」格式所在行（没有则 None）。"""
        best_row, best_height = None, -1
        for row in range(self.formats_table.rowCount()):
            type_item = self.formats_table.item(row, 2)
            if not type_item or type_item.text() != "音视频":
                continue
            res_item = self.formats_table.item(row, 3)
            height = 0
            if res_item and "x" in res_item.text():
                try:
                    height = int(res_item.text().split("x")[1])
                except ValueError:
                    height = 0
            if height >= best_height:
                best_row, best_height = row, height
        return best_row

    def _best_audio_format_id(self):
        """从预览到的格式里挑码率最高的纯音频流。"""
        best_id, best_tbr = "", -1
        for f in self.preview_formats:
            vcodec = f.get("vcodec") or ""
            acodec = f.get("acodec") or ""
            if acodec and acodec != "none" and (not vcodec or vcodec == "none"):
                tbr = f.get("tbr") or 0
                if tbr > best_tbr:
                    best_id, best_tbr = str(f.get("format_id") or ""), tbr
        return best_id

    def _resolve_format_choice(self, row, format_id):
        """把表格里选中的格式转换成实际使用的格式字符串，并给出提示。

        纯视频流必须自动补音频流：B站 DASH 的视频和音频是分开的，
        只下视频流会得到没有声音的视频（历史记录里的 format_id=30080 就是这样来的）。
        """
        type_item = self.formats_table.item(row, 2)
        ftype = type_item.text() if type_item else ""
        if ftype == "视频":
            audio_id = self._best_audio_format_id()
            if audio_id and has_ffmpeg():
                return f"{format_id}+{audio_id}", f"（纯视频流，已自动补上音频流 {audio_id}）"
            if not has_ffmpeg():
                return format_id, "（纯视频流，未检测到 ffmpeg 无法合并音频，下载结果没有声音）"
            return format_id, "（纯视频流，没有可用的音频流，下载结果可能没有声音）"
        if ftype == "音频":
            return format_id, "（仅音频，没有画面）"
        if ftype == "DASH组合":
            return format_id, "（视频+音频组合，需要 ffmpeg 合并）"
        return format_id, ""

    def _ensure_audio_track(self, format_id):
        """下载前的兜底：设置里残留在纯视频流时自动补上音频流，避免下出无声视频。"""
        if not format_id or "+" in format_id:
            return format_id
        for f in self.preview_formats:
            if str(f.get("format_id") or "") != format_id:
                continue
            vcodec = f.get("vcodec") or ""
            acodec = f.get("acodec") or ""
            if vcodec and vcodec != "none" and (not acodec or acodec == "none"):
                audio_id = self._best_audio_format_id()
                if audio_id and has_ffmpeg():
                    return f"{format_id}+{audio_id}"
            return format_id
        return format_id

    def _describe_format(self, format_id):
        """给「正在使用的格式」加一句可读说明，用于日志与悬停提示。"""
        if not format_id:
            return ""
        if "+" in format_id:
            return "视频+音频组合"
        for f in self.preview_formats:
            if str(f.get("format_id") or "") != format_id:
                continue
            vcodec = f.get("vcodec") or ""
            acodec = f.get("acodec") or ""
            is_video = bool(vcodec and vcodec != "none")
            is_audio = bool(acodec and acodec != "none")
            if is_video and not is_audio:
                return "纯视频流，下载后没有声音"
            if is_audio and not is_video:
                return "纯音频流，没有画面"
        return ""

    def _format_row_for(self, format_id):
        """查找格式对应的行；组合格式（a+b）先匹配组合行，再退而匹配其中的视频流行。"""
        candidates = [format_id]
        if "+" in format_id:
            candidates.append(format_id.split("+")[0].strip())
        for candidate in candidates:
            for row in range(self.formats_table.rowCount()):
                item = self.formats_table.item(row, 1)
                if item and item.text() == candidate:
                    return row
        return None

    def refresh_format_selection_marks(self):
        """在格式表第 0 列叠加「✔ 已选」，保留行原有的标记。"""
        selected = (self.custom_format_edit.text() or "").strip()
        targets = {selected} if selected else set()
        if "+" in selected:
            targets.update(part.strip() for part in selected.split("+"))
        matched = False
        for row in range(self.formats_table.rowCount()):
            item = self.formats_table.item(row, 0)
            if item is None:
                continue
            base = item.data(Qt.UserRole)
            if base is None:
                base = item.text()
                item.setData(Qt.UserRole, base)
            format_item = self.formats_table.item(row, 1)
            format_text = format_item.text() if format_item else ""
            if format_text and format_text in targets:
                item.setText(f"✔ 已选 {base}".strip())
                matched = True
            else:
                item.setText(base)
        return matched

    def _set_format_chip(self, text, state="idle"):
        """更新下载页「下载格式」提示条的文案与配色。"""
        styles = {
            "idle": "color:#64748b; background:rgba(148,163,184,0.14);",
            "active": "color:#0f766e; background:rgba(45,212,191,0.18); font-weight:600;",
            "warn": "color:#b45309; background:rgba(251,146,60,0.22); font-weight:600;",
        }
        self.selected_format_label.setText(text)
        self.selected_format_label.setStyleSheet(
            styles.get(state, styles["idle"]) + "border-radius:10px; padding:6px 12px;"
        )

    def refresh_selected_format_label(self):
        """刷新「当前使用格式」提示，并同步格式表里的已选标记。

        三种状态：未选择（跟随清晰度）/ 已选且在当前格式表中找到 / 已选但表中没有匹配项。
        """
        if not hasattr(self, "selected_format_label") or not hasattr(self, "custom_format_edit"):
            return
        selected = (self.custom_format_edit.text() or "").strip()
        if not selected:
            self.refresh_format_selection_marks()
            self._set_format_chip("未选择格式 · 按「清晰度」设置自动选择", "idle")
            return
        if self.refresh_format_selection_marks():
            row = self._format_row_for(selected)
            details = []
            for col in (2, 3, 5, 6):
                item = self.formats_table.item(row, col) if row is not None else None
                if item and item.text() and item.text() != "-":
                    details.append(item.text())
            suffix = f"（{' · '.join(details)}）" if details else ""
            self._set_format_chip(f"已选格式：{selected}{suffix}", "active")
        else:
            self._set_format_chip(
                f"已选格式：{selected} · 当前格式表没有匹配项，下载时可能不适用", "warn"
            )

    def use_selected_format(self):
        selected = self.formats_table.selectionModel().selectedRows()
        if not selected:
            return
        row = selected[0].row()
        format_item = self.formats_table.item(row, 1)
        if format_item is None:
            return
        format_id = format_item.text()
        chosen, hint = self._resolve_format_choice(row, format_id)
        self.custom_format_edit.setText(chosen)
        self.refresh_selected_format_label()
        if self.sound_player:
            self.sound_player.play("click")
        self.append_log(f"已选择格式: {chosen}{hint}")
        self.statusBar().showMessage(
            f"已选择格式 {chosen}{hint}，点「开始下载吧」即按此格式下载", 6000
        )

    def clear_selected_format(self):
        """清除已选格式，回到「按清晰度自动选择」。"""
        if not (self.custom_format_edit.text() or "").strip():
            self.statusBar().showMessage("当前没有选择格式", 3000)
            return
        self.custom_format_edit.clear()
        self.refresh_selected_format_label()
        self.append_log("已清除格式选择，改为按「清晰度」设置自动选择")
        self.statusBar().showMessage("已清除格式选择", 3000)

    def add_selected_pages_to_input(self):
        selected_rows = sorted({idx.row() for idx in self.pages_table.selectionModel().selectedRows()})
        if not selected_rows:
            QMessageBox.information(self, "提示", "请先在分 P 列表中选择要下载的分 P。")
            return
        urls = []
        for r in selected_rows:
            url_item = self.pages_table.item(r, 3)
            if url_item and url_item.text().strip():
                urls.append(url_item.text().strip())
        if not urls:
            return
        existing = self.input_edit.toPlainText().strip()
        if existing:
            new_text = existing + "\n" + "\n".join(urls)
        else:
            new_text = "\n".join(urls)
        self.input_edit.setPlainText(new_text)
        self.statusBar().showMessage(f"已添加 {len(urls)} 个分 P", 3000)

    def clear_input(self):
        self.input_edit.clear()
        self.clear_preview()
        self.custom_format_edit.clear()
        self.statusBar().showMessage("已清空输入", 2000)

    def import_links_from_file(self):
        """从 .txt 文件导入链接列表。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "导入链接文件", "", "文本文件 (*.txt);;所有文件 (*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            urls = split_inputs(content)
            if not urls:
                QMessageBox.information(self, "提示", "文件中未找到有效链接。")
                return
            existing = self.input_edit.toPlainText().strip()
            if existing:
                self.input_edit.setPlainText(existing + "\n" + "\n".join(urls))
            else:
                self.input_edit.setPlainText("\n".join(urls))
            self.statusBar().showMessage(f"已导入 {len(urls)} 条链接", 3000)
            self.append_log(f"从文件导入 {len(urls)} 条链接: {path}")
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", f"读取文件失败：\n{format_error(exc)}")

    # ---------- 下载 ----------

    def check_cookie_settings(self):
        mode = self.settings.get("cookie_mode") or "none"
        if mode == "file":
            cookie_file = (self.settings.get("cookie_file") or "").strip()
            if not cookie_file:
                return "Cookie 模式为 cookies.txt，但未选择文件。"
            if not Path(cookie_file).exists():
                return f"Cookie 文件不存在: {cookie_file}"
        elif mode in ("chrome", "edge", "firefox"):
            pass
        return ""

    def start_downloads(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "提示", "当前已有下载任务在进行。")
            return
        if self.sound_player:
            self.sound_player.play("start")
        text = self.input_edit.toPlainText()
        urls = split_inputs(text)
        if not urls:
            QMessageBox.information(self, "提示", "请输入要下载的链接。")
            return
        self.settings = self.collect_settings()
        ok, err = save_settings(self.settings)
        if not ok:
            self.statusBar().showMessage(f"设置保存失败: {err}", 5000)
        download_dir = self.settings.get("download_dir") or DEFAULT_DOWNLOAD_DIR
        try:
            Path(download_dir).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            QMessageBox.warning(self, "目录创建失败", f"无法创建下载目录：\n{download_dir}\n\n{e}")
            return
        cookie_err = self.check_cookie_settings()
        if cookie_err:
            ret = QMessageBox.question(
                self, "Cookie 提示",
                f"{cookie_err}\n\n是否仍然继续下载？（公开视频可走兜底接口）",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if ret != QMessageBox.Yes:
                return
        custom_format = (self.settings.get("custom_format") or "").strip()
        fixed_format = self._ensure_audio_track(custom_format)
        if fixed_format != custom_format:
            custom_format = fixed_format
            self.custom_format_edit.setText(fixed_format)
            self.settings["custom_format"] = fixed_format
            self.refresh_selected_format_label()
            self.append_log(f"所选格式是纯视频流，已自动补上音频流 → {fixed_format}")
        quality_label = QUALITY_LABELS.get(self.settings.get("quality"), self.settings.get("quality") or "")
        format_desc = custom_format or f"自动（{quality_label}）"
        format_note = self._describe_format(custom_format)
        format_full = f"{format_desc}（{format_note}）" if format_note else format_desc
        self.append_log(f"开始下载 {len(urls)} 个任务 · 格式: {format_full}")
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for i, url in enumerate(urls):
            url = normalize_input(url)
            self.table.insertRow(i)
            self.set_cell(i, 0, url)
            self.set_cell(i, 1, "等待")
            self.set_cell(i, 2, "0%")
            self.set_cell(i, 3, "")
            head_item = self.table.item(i, 0)
            if head_item:
                head_item.setToolTip(f"格式: {format_full}\n清晰度: {quality_label}\n链接: {url}")
        self.start_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        self.set_controls_enabled(False)
        self.progress_bar.setValue(0)
        self.statusBar().showMessage("下载中...")
        self.worker = DownloadWorker(urls, self.settings, self)
        self.worker.item_started.connect(self.on_item_started)
        self.worker.item_progress.connect(self.on_item_progress)
        self.worker.item_finished.connect(self.on_item_finished)
        self.worker.item_failed.connect(self.on_item_failed)
        self.worker.item_history.connect(self.on_item_history)
        self.worker.log.connect(self.append_log)
        self.worker.all_done.connect(self.on_all_done)
        self.worker.paused_changed.connect(self.on_queue_paused_changed)
        self.worker.start()

    def set_controls_enabled(self, enabled):
        self.input_edit.setEnabled(enabled)
        self.preview_btn.setEnabled(enabled)
        self.use_format_btn.setEnabled(enabled and bool(self.formats_table.selectionModel().selectedRows()))
        self.clear_format_btn.setEnabled(enabled)
        self.add_pages_btn.setEnabled(enabled and self.pages_table.isVisible())
        self.select_all_pages_btn.setEnabled(enabled and self.pages_table.isVisible())
        self.dir_btn.setEnabled(enabled)
        self.cookie_mode_combo.setEnabled(enabled)
        self.cookie_file_btn.setEnabled(enabled)
        self.quality_combo.setEnabled(enabled)
        self.codec_combo.setEnabled(enabled)
        self.audio_quality_combo.setEnabled(enabled)
        self.thread_combo.setEnabled(enabled)
        self.concurrent_spin.setEnabled(enabled)
        self.proxy_edit.setEnabled(enabled)
        self.template_edit.setEnabled(enabled)
        self.thumbnail_check.setEnabled(enabled)
        self.subtitle_check.setEnabled(enabled)
        self.danmaku_check.setEnabled(enabled)
        self.qr_login_btn.setEnabled(enabled)
        self.check_cookie_btn.setEnabled(enabled)

    def set_cell(self, row, col, text):
        item = self.table.item(row, col)
        if item:
            item.setText(text)
        else:
            self.table.setItem(row, col, QTableWidgetItem(text))

    def on_item_started(self, index, url):
        if index >= self.table.rowCount():
            return
        self.set_cell(index, 0, url)
        self.set_cell(index, 1, "下载中")
        self.set_cell(index, 2, "0%")
        self.statusBar().showMessage(f"下载中: {url}")
        self.update_window_title()

    QUEUE_FINISHED_STATES = ("完成", "完成（公开视频兜底）", "失败", "已取消")

    def _queue_counts(self):
        """返回 (参与统计的任务数, 已结束任务数)。

        「已从队列删除」的任务不再计入分母，否则进度条永远到不了 100%。
        """
        total = done = 0
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            state = item.text() if item else "等待"
            if state == "已删除":
                continue
            total += 1
            if state in self.QUEUE_FINISHED_STATES:
                done += 1
        return total, done

    def on_item_progress(self, index, percent, detail):
        if index >= self.table.rowCount():
            return
        self.set_cell(index, 2, detail)
        if percent >= 0:
            # 总进度 = 已结束任务数 + 当前任务进度，按参与统计的任务数摊平
            total, done = self._queue_counts()
            total = total or 1
            current = max(0.0, min(100.0, percent)) / 100.0
            overall = (done + current) / total * 100
            self.progress_bar.setValue(int(overall))
            self.update_window_title()

    def on_item_finished(self, index, output_detail, status_text):
        if index >= self.table.rowCount():
            return
        if self.sound_player and (status_text or "完成") == "完成":
            self.sound_player.play("success2")
        self.set_cell(index, 1, status_text or "完成")
        self.set_cell(index, 2, "100%")
        self.set_cell(index, 3, output_detail)
        self._update_progress_bar()
        self.update_window_title()

    def on_item_failed(self, index, error):
        if index >= self.table.rowCount():
            return
        if self.sound_player:
            self.sound_player.play("fail")
        self.set_cell(index, 1, "失败")
        self.set_cell(index, 2, error)
        self._update_progress_bar()
        self.update_window_title()

    def _update_progress_bar(self):
        total, done = self._queue_counts()
        overall = done / total * 100 if total else 0
        self.progress_bar.setValue(int(overall))

    def update_window_title(self):
        """根据当前下载状态更新窗口标题和标题栏标签。"""
        total, done = self._queue_counts()
        if not total:
            title = self.base_window_title
            self.setWindowTitle(title)
            if hasattr(self, "title_label"):
                self.title_label.setText(title)
            self._update_mascot_by_state("idle")
            return
        running = self.worker and self.worker.isRunning() and not self.worker.cancelled
        if running:
            if self.worker.paused:
                title = f"{self.base_window_title} - 已暂停 ({done}/{total})"
                self._update_mascot_by_state("paused")
            else:
                title = f"{self.base_window_title} - 下载中 ({done}/{total})"
                self._update_mascot_by_state("downloading")
            self.setWindowTitle(title)
            if hasattr(self, "title_label"):
                self.title_label.setText(title)
            return
        failed = sum(1 for r in range(self.table.rowCount()) if self.table.item(r, 1) and self.table.item(r, 1).text() == "失败")
        cancelled = sum(1 for r in range(self.table.rowCount()) if self.table.item(r, 1) and self.table.item(r, 1).text() == "已取消")
        if cancelled:
            title = f"{self.base_window_title} - 已取消"
            self._update_mascot_by_state("cancelled")
        elif failed:
            title = f"{self.base_window_title} - 完成（有失败）"
            self._update_mascot_by_state("failed")
        else:
            title = f"{self.base_window_title} - 全部完成"
            self._update_mascot_by_state("completed")
        self.setWindowTitle(title)
        if hasattr(self, "title_label"):
            self.title_label.setText(title)

    def _update_mascot_by_state(self, state):
        """根据状态切换看板娘气泡台词。

        只有状态真正变化时才换台词：进度回调每秒会调用多次，
        否则气泡文字会持续随机跳动。
        """
        if not hasattr(self, "mascot_bubble"):
            return
        if self._mascot_state == state:
            return
        self._mascot_state = state
        lines = {
            "idle": ["粘贴链接，开始下载", "支持 B站、yt-dlp 源", "准备就绪"],
            "downloading": ["下载进行中...", "稍等片刻", "正在处理"],
            "paused": ["已暂停", "随时可以继续", "休息一下"],
            "completed": ["全部完成", "可以去查看了", "下载结束"],
            "failed": ["有任务失败", "检查链接或 Cookie", "可尝试重试"],
            "cancelled": ["已取消", "欢迎再次使用", "操作已中止"],
        }
        self.mascot_bubble.setText(random.choice(lines.get(state, lines["idle"])))

    def on_item_history(self, record):
        append_history_record(record)
        self.history_records = load_history()
        # 批量下载时不要每个任务都重建整张历史表（否则是 O(n²) 的刷新开销），
        # 先标记为待刷新，等全部结束或切到历史页时再刷新一次。
        self._history_dirty = True
        if self.content_stack.currentIndex() == 1:
            self.refresh_history()

    def on_all_done(self, ok):
        if self.sound_player:
            self.sound_player.play("complete" if ok else "fail")
        if self._history_dirty:
            self.refresh_history()
        self.start_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.pause_btn.setText("暂停一下")
        self.set_controls_enabled(True)
        self.table.setSortingEnabled(True)
        self.update_window_title()
        # 打开目录：取第一个成功任务的目录
        output_dir = ""
        for r in range(self.table.rowCount()):
            detail = self.table.item(r, 3)
            if detail and detail.text():
                p = Path(detail.text())
                if p.exists():
                    output_dir = str(p.parent)
                    break
        if not output_dir:
            output_dir = self.settings.get("download_dir") or DEFAULT_DOWNLOAD_DIR
        if ok:
            self.progress_bar.setValue(100)
            self.statusBar().showMessage("全部任务完成")
            self.show_tray_message("下载完成", "全部任务已完成")
            reply = QMessageBox.question(
                self, "下载完成",
                f"全部任务已完成！\n下载目录：{output_dir}\n\n是否打开下载目录？",
                QMessageBox.Open | QMessageBox.No,
                QMessageBox.Open,
            )
            if reply == QMessageBox.Open:
                open_path_in_explorer(output_dir)
        else:
            failed_rows = [r for r in range(self.table.rowCount())
                           if self.table.item(r, 1) and self.table.item(r, 1).text() == "失败"]
            msg = "任务结束（有失败或取消）"
            if failed_rows:
                msg += f"\n失败任务数：{len(failed_rows)}"
            self.statusBar().showMessage(msg.replace("\n", " "))
            self.show_tray_message("下载结束", "有任务失败或被取消")

    def show_tray_message(self, title, message):
        if self.tray_icon:
            try:
                self.tray_icon.showMessage(title, message, QSystemTrayIcon.Information, 3000)
            except Exception:
                pass

    def cancel_downloads(self):
        if self.sound_player:
            self.sound_player.play("click")
        if self.worker and self.worker.isRunning():
            self.statusBar().showMessage("正在取消...")
            self.worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.update_window_title()

    def toggle_pause_queue(self):
        if self.sound_player:
            self.sound_player.play("click")
        if not self.worker:
            return
        if self.worker.paused:
            self.worker.resume()
            self.pause_btn.setText("暂停一下")
            self.statusBar().showMessage("下载中...")
        else:
            self.worker.pause()
            self.pause_btn.setText("继续下载")
            self.statusBar().showMessage("队列已暂停")
        self.update_window_title()

    def on_queue_paused_changed(self, paused):
        if paused:
            self.pause_btn.setText("继续下载")
        else:
            self.pause_btn.setText("暂停一下")
        self.update_window_title()

    def append_log(self, msg):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self.log_edit.appendPlainText(line)
        write_runtime_log(msg)

    def log_edit_clear(self):
        self.log_edit.clear()

    # ---------- 任务表右键 ----------

    def show_task_context_menu(self, pos):
        row = self.table.indexAt(pos).row()
        if row < 0:
            return
        menu = QMenu(self.table)
        act_open = menu.addAction("打开文件")
        act_dir = menu.addAction("打开目录")
        menu.addSeparator()
        act_copy_err = menu.addAction("复制错误信息")
        act_copy_cell = menu.addAction("复制单元格")
        act_copy_output = menu.addAction("复制输出路径")
        menu.addSeparator()
        act_retry = menu.addAction("重试")
        act_remove = menu.addAction("从队列删除（仅等待中）")
        action = menu.exec_(self.table.viewport().mapToGlobal(pos))
        if action == act_open:
            self.open_task_file(row)
        elif action == act_dir:
            self.open_task_dir(row)
        elif action == act_copy_err:
            self.copy_task_error(row)
        elif action == act_copy_cell:
            self.copy_cell(row, pos)
        elif action == act_copy_output:
            self.copy_task_output(row)
        elif action == act_retry:
            self.retry_task(row)
        elif action == act_remove:
            self.remove_waiting_task(row)

    def on_task_double_clicked(self, index):
        self.open_task_file(index.row())

    def task_output_path(self, row):
        if row < 0 or row >= self.table.rowCount():
            return ""
        item = self.table.item(row, 3)
        return item.text().strip() if item else ""

    def open_task_file(self, row):
        path = self.task_output_path(row)
        if not path:
            QMessageBox.information(self, "提示", "该任务没有输出文件路径。")
            return
        if not Path(path).exists():
            QMessageBox.information(self, "提示", "文件不存在，可能已被移动或删除。")
            return
        open_file_default(path)

    def open_task_dir(self, row):
        path = self.task_output_path(row)
        if not path:
            path = self.dir_edit.text() or str(DEFAULT_DOWNLOAD_DIR)
        open_path_in_explorer(path)

    def copy_task_error(self, row):
        if row < 0 or row >= self.table.rowCount():
            return
        item = self.table.item(row, 2)
        text = item.text() if item else ""
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage("已复制错误信息", 3000)

    def copy_cell(self, row, pos):
        item = self.table.itemAt(pos)
        if item:
            QApplication.clipboard().setText(item.text())
            self.statusBar().showMessage("已复制单元格内容", 3000)

    def copy_task_output(self, row):
        path = self.task_output_path(row)
        if path:
            QApplication.clipboard().setText(path)
            self.statusBar().showMessage("已复制输出路径", 3000)

    def retry_task(self, row):
        if row < 0 or row >= self.table.rowCount():
            return
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "提示", "当前还有下载任务在进行，请等待完成或取消后再重试。")
            return
        url_item = self.table.item(row, 0)
        if not url_item:
            return
        url = url_item.text().strip()
        if not url:
            return
        self.input_edit.setPlainText(url)
        self.switch_page(0)
        self.start_downloads()

    def remove_waiting_task(self, row):
        if row < 0 or row >= self.table.rowCount():
            return
        status_item = self.table.item(row, 1)
        status_text = status_item.text() if status_item else ""
        if status_text not in {"等待", "已暂停"}:
            QMessageBox.information(self, "提示", "只能删除等待中或已暂停的任务。")
            return
        if self.worker:
            self.worker.skip_index(row)
        self.set_cell(row, 1, "已删除")
        self.set_cell(row, 2, "-")
        self.statusBar().showMessage("已从队列删除", 3000)

    # ---------- 历史 ----------

    def refresh_history(self):
        self.history_records = load_history()
        # 填充期间必须关闭排序，否则 QTableWidget 会在插入过程中重排行序导致数据错位
        self.history_table.setSortingEnabled(False)
        self.history_table.setRowCount(0)
        for r in self.history_records:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            self.history_table.setItem(row, 0, QTableWidgetItem(r.get("title") or "-"))
            status = r.get("status") or "-"
            if status == "completed":
                status = "完成"
            elif status == "failed":
                status = "失败"
            elif status == "cancelled":
                status = "已取消"
            if r.get("warning"):
                status = f"{status} · {r['warning']}"
            self.history_table.setItem(row, 1, QTableWidgetItem(status))
            self.history_table.setItem(row, 2, QTableWidgetItem(format_bytes(r.get("file_size"))))
            self.history_table.setItem(row, 3, QTableWidgetItem(str(r.get("duration") or "-")))
            self.history_table.setItem(row, 4, QTableWidgetItem(r.get("resolution") or "-"))
            ts = r.get("finished_at")
            time_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "-"
            self.history_table.setItem(row, 5, QTableWidgetItem(time_str))
            path = r.get("output_path") or ""
            if path and not Path(path).exists():
                path = f"{path}  (文件已移动或删除)"
            self.history_table.setItem(row, 6, QTableWidgetItem(path))
        self.history_table.setSortingEnabled(True)
        self._history_dirty = False
        self.statusBar().showMessage(f"历史记录: {len(self.history_records)} 条")

    def history_status_text(self, status):
        mapping = {"completed": "完成", "failed": "失败", "cancelled": "已取消"}
        return mapping.get(status, status or "-")

    def current_history_record(self):
        row = self.history_table.currentRow()
        if row < 0 or row >= len(self.history_records):
            return None, -1
        return self.history_records[row], row

    def copy_history_link(self, row=None):
        if row is None:
            row = self.history_table.currentRow()
        if row < 0 or row >= len(self.history_records):
            return
        url = self.history_records[row].get("url") or ""
        if url:
            QApplication.clipboard().setText(url)
            self.statusBar().showMessage("已复制链接", 3000)

    def redownload_history(self):
        record, _ = self.current_history_record()
        if not record:
            QMessageBox.information(self, "提示", "请先在历史列表中选择一条记录。")
            return
        url = record.get("url") or ""
        if not url:
            QMessageBox.warning(self, "提示", "该历史记录没有可用的链接。")
            return
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "提示", "当前还有下载任务在进行，请等待完成或取消后再重试。")
            return
        self.input_edit.setPlainText(url)
        self.switch_page(0)
        self.start_downloads()

    def open_history_file(self):
        record, _ = self.current_history_record()
        if not record:
            QMessageBox.information(self, "提示", "请先在历史列表中选择一条记录。")
            return
        path = record.get("output_path") or ""
        if not path:
            QMessageBox.information(self, "提示", "该记录没有输出文件路径。")
            return
        if not Path(path).exists():
            QMessageBox.information(self, "提示", "文件不存在，可能已被移动或删除。")
            return
        open_file_default(path)

    def open_history_dir(self):
        record, _ = self.current_history_record()
        if not record:
            path = self.dir_edit.text() or str(DEFAULT_DOWNLOAD_DIR)
        else:
            path = record.get("output_path") or self.dir_edit.text() or str(DEFAULT_DOWNLOAD_DIR)
        open_path_in_explorer(path)

    def delete_history(self):
        _, row = self.current_history_record()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择要删除的记录。")
            return
        ret = QMessageBox.question(
            self, "删除记录",
            "确定删除这条历史记录吗？（不会删除本地文件）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret == QMessageBox.Yes:
            remove_history_record(row)
            self.refresh_history()

    def clear_all_history(self):
        ret = QMessageBox.question(
            self, "清空历史",
            "确定清空全部历史记录吗？（不会删除本地文件）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret == QMessageBox.Yes:
            save_history([])
            self.refresh_history()

    def export_history(self, fmt):
        """导出历史记录到 CSV 或 JSON 文件。"""
        records = self.history_records or load_history()
        if not records:
            QMessageBox.information(self, "导出", "当前没有历史记录可导出。")
            return
        if fmt == "csv":
            default_name = "bilibili_history.csv"
            filter_str = "CSV 文件 (*.csv);;所有文件 (*)"
        else:
            default_name = "bilibili_history.json"
            filter_str = "JSON 文件 (*.json);;所有文件 (*)"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出历史记录", default_name, filter_str
        )
        if not path:
            return
        try:
            if fmt == "csv":
                fields = ["title", "url", "status", "file_size", "duration",
                          "resolution", "output_path", "error", "created_at", "finished_at"]
                with open(path, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                    writer.writeheader()
                    for r in records:
                        writer.writerow(r)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(records, f, ensure_ascii=False, indent=2)
            self.statusBar().showMessage(f"已导出 {len(records)} 条记录到 {path}", 5000)
            self.append_log(f"导出历史记录 ({fmt}): {path} ({len(records)} 条)")
            QMessageBox.information(self, "导出成功", f"已导出 {len(records)} 条记录到\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", f"导出失败：\n{format_error(exc)}")

    def export_settings(self):
        """导出当前设置到 JSON 文件。"""
        path, _ = QFileDialog.getSaveFileName(
            self, "导出设置", "bilibili_settings.json", "JSON 文件 (*.json);;所有文件 (*)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=2)
            self.statusBar().showMessage(f"设置已导出到 {path}", 5000)
            self.append_log(f"导出设置: {path}")
            QMessageBox.information(self, "导出成功", f"设置已导出到\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", f"导出失败：\n{format_error(exc)}")

    def import_settings(self):
        """从 JSON 文件导入设置。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "导入设置", "", "JSON 文件 (*.json);;所有文件 (*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                QMessageBox.warning(self, "导入失败", "文件内容不是有效的设置 JSON。")
                return
            merged = dict(DEFAULT_SETTINGS)
            merged.update(data)
            ok, err = save_settings(merged)
            if not ok:
                QMessageBox.warning(self, "导入失败", f"保存设置失败：\n{err}")
                return
            self.settings = merged
            self.apply_settings_to_ui()
            self.statusBar().showMessage(f"设置已从 {path} 导入", 5000)
            self.append_log(f"导入设置: {path}")
            QMessageBox.information(self, "导入成功", "设置已导入并应用。")
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", f"导入失败：\n{format_error(exc)}")

    def show_history_context_menu(self, pos):
        index = self.history_table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        menu = QMenu(self.history_table)
        act_redownload = menu.addAction("重新下载")
        act_copy_link = menu.addAction("复制链接")
        act_open = menu.addAction("打开文件")
        act_dir = menu.addAction("打开目录")
        menu.addSeparator()
        act_delete = menu.addAction("删除记录")
        action = menu.exec_(self.history_table.viewport().mapToGlobal(pos))
        if action == act_redownload:
            self.history_table.selectRow(row)
            self.redownload_history()
        elif action == act_copy_link:
            self.history_table.selectRow(row)
            self.copy_history_link(row)
        elif action == act_open:
            self.history_table.selectRow(row)
            self.open_history_file()
        elif action == act_dir:
            self.history_table.selectRow(row)
            self.open_history_dir()
        elif action == act_delete:
            self.history_table.selectRow(row)
            self.delete_history()

    def on_history_double_clicked(self, index):
        self.history_table.selectRow(index.row())
        self.open_history_file()

    # ---------- 关闭 ----------

    def closeEvent(self, event):
        # 如果托盘可用且不是强制退出，最小化到托盘
        if self.tray_icon and not self._force_quit:
            event.ignore()
            self.hide()
            self.tray_icon.showMessage(
                "Bilibili 视频下载器",
                "已最小化到托盘，单击图标恢复窗口",
                QSystemTrayIcon.Information, 3000,
            )
            return
        if self.worker and self.worker.isRunning():
            ret = QMessageBox.question(
                self, "确认退出",
                "有下载任务正在进行，确定退出吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                event.ignore()
                return
            self.worker.cancel()
            self.worker.wait(3000)
        if self.preview_worker and self.preview_worker.isRunning():
            self.preview_worker.wait(2000)
        if self.cookie_check_worker and self.cookie_check_worker.isRunning():
            self.cookie_check_worker.wait(2000)
        for thumb_worker in self._thumb_workers:
            if thumb_worker.isRunning():
                thumb_worker.wait(1000)
        ok, err = save_settings(self.collect_settings())
        if not ok:
            self.statusBar().showMessage(f"设置保存失败: {err}", 5000)
        if self.tray_icon:
            self.tray_icon.hide()
        event.accept()
