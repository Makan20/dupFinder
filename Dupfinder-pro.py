"""
Duplicate File Finder Pro - Modern PySide6 Implementation
Features:
- Dynamic Theme Switcher: Dark Mode & Light Mode (toggle instantly)
- High-Performance QThread worker with SHA-256 duplicate hashing
- Metric KPI Cards (Scanned files, Duplicate count, Wasted space, Selected space)
- Smart Batch Selection: Keep Oldest, Keep Newest, Clear Selection
- Safe deletion via send2trash (fallback to os.remove)
- TXT Report exporter
- File explorer reveal and clipboard path copy
"""

import sys
import os
import hashlib
from datetime import datetime
from collections import defaultdict

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QColor, QAction
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QLineEdit, QFileDialog,
    QProgressBar, QTreeWidget, QTreeWidgetItem, QHeaderView,
    QMessageBox, QFrame, QMenu
)

try:
    from send2trash import send2trash
    HAS_SEND2TRASH = True
except ImportError:
    HAS_SEND2TRASH = False


def human_size(num_bytes: int) -> str:
    if num_bytes is None:
        return "-"
    step = 1024.0
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if num_bytes < step:
            return f"{num_bytes:.1f} {unit}" if unit != 'B' else f"{int(num_bytes)} {unit}"
        num_bytes /= step
    return f"{num_bytes:.1f} PB"


def human_time(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "-"


def file_hash(path: str, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


class ScanWorker(QThread):
    progress_changed = Signal(int)
    status_changed = Signal(str)
    scan_completed = Signal(dict, int)
    error_occurred = Signal(str)

    def __init__(self, root_folder: str):
        super().__init__()
        self.root_folder = root_folder
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        try:
            self.status_changed.emit("در حال پیمایش فایل‌ها و بررسی سایز...")
            all_files = []

            for dirpath, _, filenames in os.walk(self.root_folder):
                if self.is_cancelled:
                    self.status_changed.emit("اسکن لغو شد.")
                    return

                for fn in filenames:
                    fullpath = os.path.join(dirpath, fn)
                    try:
                        st = os.stat(fullpath)
                        if st.st_size > 0:
                            all_files.append((fullpath, st.st_size, st.st_mtime))
                    except OSError:
                        continue

            total_files = len(all_files)
            if total_files == 0:
                self.scan_completed.emit({}, 0)
                return

            self.status_changed.emit(f"{total_files:,} فایل یافت شد. فیلتر فایل‌های هم‌اندازه...")

            size_groups = defaultdict(list)
            for fullpath, size, mtime in all_files:
                size_groups[size].append((fullpath, size, mtime))

            candidates = [group for group in size_groups.values() if len(group) > 1]
            total_candidates = sum(len(g) for g in candidates)

            if total_candidates == 0:
                self.progress_changed.emit(100)
                self.scan_completed.emit({}, total_files)
                return

            self.status_changed.emit(f"در حال محاسبه هش SHA-256 برای {total_candidates:,} فایل مشکوک...")

            hash_groups = defaultdict(list)
            processed = 0

            for group in candidates:
                for fullpath, size, mtime in group:
                    if self.is_cancelled:
                        self.status_changed.emit("اسکن متوقف گردید.")
                        return

                    try:
                        h = file_hash(fullpath)
                        hash_groups[h].append({
                            "path": fullpath,
                            "size": size,
                            "mtime": mtime
                        })
                    except (OSError, PermissionError):
                        pass

                    processed += 1
                    pct = int((processed / total_candidates) * 100)
                    self.progress_changed.emit(pct)

            duplicate_groups = {h: files for h, files in hash_groups.items() if len(files) > 1}
            for files in duplicate_groups.values():
                files.sort(key=lambda x: x["mtime"])

            self.progress_changed.emit(100)
            self.scan_completed.emit(duplicate_groups, total_files)

        except Exception as e:
            self.error_occurred.emit(str(e))


class MetricCard(QFrame):
    def __init__(self, title: str, initial_value: str = "0", accent_dark: str = "#38bdf8", accent_light: str = "#0284c7"):
        super().__init__()
        self.setObjectName("MetricCard")
        self.accent_dark = accent_dark
        self.accent_light = accent_light
        self.is_dark = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        self.lbl_title = QLabel(title)
        self.lbl_title.setObjectName("MetricTitle")
        self.lbl_title.setAlignment(Qt.AlignCenter)

        self.lbl_value = QLabel(initial_value)
        self.lbl_value.setObjectName("MetricValue")
        self.lbl_value.setAlignment(Qt.AlignCenter)

        layout.addWidget(self.lbl_title)
        layout.addWidget(self.lbl_value)
        self.apply_theme(True)

    def set_value(self, val: str):
        self.lbl_value.setText(val)

    def apply_theme(self, is_dark: bool):
        self.is_dark = is_dark
        accent = self.accent_dark if is_dark else self.accent_light
        bg = "#1e293b" if is_dark else "#ffffff"
        border = "#334155" if is_dark else "#e2e8f0"
        title_color = "#94a3b8" if is_dark else "#64748b"

        self.setStyleSheet(f"""
            QFrame#MetricCard {{
                background-color: {bg};
                border-radius: 12px;
                border: 1px solid {border};
            }}
            QLabel#MetricTitle {{
                color: {title_color};
                font-size: 12px;
                font-weight: 500;
            }}
            QLabel#MetricValue {{
                color: {accent};
                font-size: 20px;
                font-weight: bold;
            }}
        """)


class ModernDuplicateFinder(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Duplicate File Finder Pro")
        self.resize(1180, 780)
        self.duplicate_groups = {}
        self.worker = None
        self.is_dark_theme = True

        self.init_ui()
        self.apply_theme()

    def get_dark_stylesheet(self) -> str:
        return """
            QMainWindow {
                background-color: #0f172a;
            }
            QWidget {
                color: #f8fafc;
                font-family: 'Segoe UI', Tahoma, sans-serif;
                font-size: 13px;
            }
            QLineEdit {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 8px 12px;
                color: #f8fafc;
            }
            QLineEdit:focus {
                border: 1px solid #3b82f6;
            }
            QPushButton {
                background-color: #1e293b;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 8px 16px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #334155;
            }
            QPushButton#PrimaryBtn {
                background-color: #2563eb;
                border: none;
            }
            QPushButton#PrimaryBtn:hover {
                background-color: #1d4ed8;
            }
            QPushButton#DangerBtn {
                background-color: #dc2626;
                border: none;
            }
            QPushButton#DangerBtn:hover {
                background-color: #b91c1c;
            }
            QPushButton#DangerBtn:disabled {
                background-color: #451a1a;
                color: #7f1d1d;
            }
            QPushButton#ThemeToggleBtn {
                background-color: #1e293b;
                border: 1px solid #475569;
                color: #f1f5f9;
                padding: 8px 14px;
            }
            QPushButton#ThemeToggleBtn:hover {
                background-color: #334155;
            }
            QProgressBar {
                background-color: #1e293b;
                border-radius: 6px;
                text-align: center;
                color: #ffffff;
                font-size: 11px;
                height: 12px;
            }
            QProgressBar::chunk {
                background-color: #3b82f6;
                border-radius: 6px;
            }
            QTreeWidget {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 10px;
                outline: 0;
                padding: 4px;
            }
            QTreeWidget::item {
                padding: 6px 4px;
                border-bottom: 1px solid #243044;
            }
            QTreeWidget::item:hover {
                background-color: #27354a;
            }
            QTreeWidget::item:selected {
                background-color: #1d4ed8;
                color: #ffffff;
            }
            QHeaderView::section {
                background-color: #0f172a;
                color: #94a3b8;
                padding: 8px;
                border: none;
                font-weight: 600;
            }
            QMenu {
                background-color: #1e293b;
                border: 1px solid #334155;
                color: #f8fafc;
                padding: 4px;
            }
            QMenu::item:selected {
                background-color: #2563eb;
            }
        """

    def get_light_stylesheet(self) -> str:
        return """
            QMainWindow {
                background-color: #f8fafc;
            }
            QWidget {
                color: #0f172a;
                font-family: 'Segoe UI', Tahoma, sans-serif;
                font-size: 13px;
            }
            QLineEdit {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                padding: 8px 12px;
                color: #0f172a;
            }
            QLineEdit:focus {
                border: 1px solid #2563eb;
            }
            QPushButton {
                background-color: #ffffff;
                color: #0f172a;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                padding: 8px 16px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #f1f5f9;
            }
            QPushButton#PrimaryBtn {
                background-color: #2563eb;
                color: #ffffff;
                border: none;
            }
            QPushButton#PrimaryBtn:hover {
                background-color: #1d4ed8;
            }
            QPushButton#DangerBtn {
                background-color: #ef4444;
                color: #ffffff;
                border: none;
            }
            QPushButton#DangerBtn:hover {
                background-color: #dc2626;
            }
            QPushButton#DangerBtn:disabled {
                background-color: #fee2e2;
                color: #f87171;
            }
            QPushButton#ThemeToggleBtn {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                color: #0f172a;
                padding: 8px 14px;
            }
            QPushButton#ThemeToggleBtn:hover {
                background-color: #e2e8f0;
            }
            QProgressBar {
                background-color: #e2e8f0;
                border-radius: 6px;
                text-align: center;
                color: #0f172a;
                font-size: 11px;
                height: 12px;
            }
            QProgressBar::chunk {
                background-color: #2563eb;
                border-radius: 6px;
            }
            QTreeWidget {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 10px;
                outline: 0;
                padding: 4px;
            }
            QTreeWidget::item {
                padding: 6px 4px;
                border-bottom: 1px solid #f1f5f9;
            }
            QTreeWidget::item:hover {
                background-color: #f8fafc;
            }
            QTreeWidget::item:selected {
                background-color: #dbeafe;
                color: #1e3a8a;
            }
            QHeaderView::section {
                background-color: #f1f5f9;
                color: #475569;
                padding: 8px;
                border: none;
                font-weight: 600;
            }
            QMenu {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                color: #0f172a;
                padding: 4px;
            }
            QMenu::item:selected {
                background-color: #2563eb;
                color: #ffffff;
            }
        """

    def apply_theme(self):
        if self.is_dark_theme:
            self.setStyleSheet(self.get_dark_stylesheet())
            self.btn_theme.setText("☀️ تم روشن")
            self.lbl_status.setStyleSheet("color: #94a3b8; font-size: 12px;")
            self.lbl_actions_title.setStyleSheet("font-weight: bold; color: #cbd5e1;")
        else:
            self.setStyleSheet(self.get_light_stylesheet())
            self.btn_theme.setText("🌙 تم تاریک")
            self.lbl_status.setStyleSheet("color: #64748b; font-size: 12px;")
            self.lbl_actions_title.setStyleSheet("font-weight: bold; color: #334155;")

        for card in [self.card_scanned, self.card_dupes, self.card_wasted, self.card_selected]:
            card.apply_theme(self.is_dark_theme)

        # Update existing group items color
        group_color = QColor("#38bdf8") if self.is_dark_theme else QColor("#0284c7")
        for idx in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(idx).setForeground(0, group_color)

    def toggle_theme(self):
        self.is_dark_theme = not self.is_dark_theme
        self.apply_theme()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(16)

        # 1. Top Folder Selection Bar & Theme Switcher
        folder_layout = QHBoxLayout()
        folder_layout.setSpacing(10)

        self.txt_folder = QLineEdit()
        self.txt_folder.setPlaceholderText("مسیر پوشه مورد نظر را انتخاب کنید...")
        self.txt_folder.setReadOnly(True)

        self.btn_browse = QPushButton("📁 انتخاب پوشه")
        self.btn_browse.clicked.connect(self.browse_folder)

        self.btn_scan = QPushButton("▶ شروع اسکن")
        self.btn_scan.setObjectName("PrimaryBtn")
        self.btn_scan.clicked.connect(self.start_scan)

        self.btn_cancel = QPushButton("⏹ توقف")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_scan)

        self.btn_theme = QPushButton("☀️ تم روشن")
        self.btn_theme.setObjectName("ThemeToggleBtn")
        self.btn_theme.clicked.connect(self.toggle_theme)

        folder_layout.addWidget(self.txt_folder, stretch=1)
        folder_layout.addWidget(self.btn_browse)
        folder_layout.addWidget(self.btn_scan)
        folder_layout.addWidget(self.btn_cancel)
        folder_layout.addWidget(self.btn_theme)
        main_layout.addLayout(folder_layout)

        # 2. Metric KPI Cards
        cards_layout = QGridLayout()
        cards_layout.setSpacing(12)

        self.card_scanned = MetricCard("فایل‌های اسکن شده", "0", "#38bdf8", "#0284c7")
        self.card_dupes = MetricCard("فایل‌های تکراری", "0", "#fbbf24", "#d97706")
        self.card_wasted = MetricCard("فضای قابل آزادسازی", "0 B", "#f87171", "#dc2626")
        self.card_selected = MetricCard("انتخاب‌شده برای حذف", "0 (0 B)", "#4ade80", "#16a34a")

        cards_layout.addWidget(self.card_scanned, 0, 0)
        cards_layout.addWidget(self.card_dupes, 0, 1)
        cards_layout.addWidget(self.card_wasted, 0, 2)
        cards_layout.addWidget(self.card_selected, 0, 3)
        main_layout.addLayout(cards_layout)

        # 3. Status and Progress Bar
        status_box = QVBoxLayout()
        status_box.setSpacing(6)
        self.lbl_status = QLabel("آماده به کار")
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        status_box.addWidget(self.lbl_status)
        status_box.addWidget(self.progress_bar)
        main_layout.addLayout(status_box)

        # 4. Smart Selection Action Bar
        action_bar = QHBoxLayout()
        action_bar.setSpacing(8)

        self.lbl_actions_title = QLabel("انتخاب هوشمند:")

        self.btn_keep_oldest = QPushButton("⚡ نگه‌داشتن قدیمی‌ترین (فایل اصل)")
        self.btn_keep_oldest.clicked.connect(lambda: self.smart_select(keep="oldest"))

        self.btn_keep_newest = QPushButton("⚡ نگه‌داشتن جدیدترین")
        self.btn_keep_newest.clicked.connect(lambda: self.smart_select(keep="newest"))

        self.btn_clear_sel = QPushButton("✕ لغو انتخاب‌ها")
        self.btn_clear_sel.clicked.connect(self.clear_selection)

        action_bar.addWidget(self.lbl_actions_title)
        action_bar.addWidget(self.btn_keep_oldest)
        action_bar.addWidget(self.btn_keep_newest)
        action_bar.addWidget(self.btn_clear_sel)
        action_bar.addStretch()
        main_layout.addLayout(action_bar)

        # 5. Duplicate Files Tree
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["نام فایل / گروه", "حجم", "تاریخ تغییر", "مسیر کامل"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Interactive)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tree.setColumnWidth(0, 320)
        self.tree.itemChanged.connect(self.on_item_checked)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_context_menu)
        main_layout.addWidget(self.tree, stretch=1)

        # 6. Bottom Action Bar
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(12)

        self.btn_export = QPushButton("📋 خروجی گزارش (TXT)")
        self.btn_export.clicked.connect(self.export_report)

        self.btn_delete = QPushButton("🗑 انتقال فایل‌های انتخاب‌شده به سطل زباله")
        self.btn_delete.setObjectName("DangerBtn")
        self.btn_delete.setEnabled(False)
        self.btn_delete.clicked.connect(self.delete_selected)

        bottom_layout.addWidget(self.btn_export)
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.btn_delete)
        main_layout.addLayout(bottom_layout)

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "انتخاب پوشه برای بررسی فایل‌های تکراری")
        if folder:
            self.txt_folder.setText(os.path.normpath(folder))

    def start_scan(self):
        folder = self.txt_folder.text().strip()
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self, "خطا", "لطفاً ابتدا یک پوشه معتبر انتخاب کنید.")
            return

        self.tree.clear()
        self.duplicate_groups.clear()
        self.card_scanned.set_value("0")
        self.card_dupes.set_value("0")
        self.card_wasted.set_value("0 B")
        self.card_selected.set_value("0 (0 B)")
        self.progress_bar.setValue(0)

        self.btn_scan.setEnabled(False)
        self.btn_browse.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_delete.setEnabled(False)

        self.worker = ScanWorker(folder)
        self.worker.progress_changed.connect(self.progress_bar.setValue)
        self.worker.status_changed.connect(self.lbl_status.setText)
        self.worker.scan_completed.connect(self.on_scan_completed)
        self.worker.error_occurred.connect(self.on_scan_error)
        self.worker.start()

    def cancel_scan(self):
        if self.worker:
            self.worker.cancel()
            self.btn_cancel.setEnabled(False)

    def on_scan_error(self, err: str):
        self.btn_scan.setEnabled(True)
        self.btn_browse.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        QMessageBox.critical(self, "خطا در اسکن", f"مشکلی در حین اسکن رخ داد:\n{err}")

    def on_scan_completed(self, duplicate_groups: dict, total_scanned: int):
        self.btn_scan.setEnabled(True)
        self.btn_browse.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.duplicate_groups = duplicate_groups

        self.card_scanned.set_value(f"{total_scanned:,}")

        if not duplicate_groups:
            self.lbl_status.setText("اسکن کامل شد. هیچ فایل تکراری یافت نشد.")
            self.card_dupes.set_value("0")
            self.card_wasted.set_value("0 B")
            return

        total_dupe_files = 0
        total_wasted_bytes = 0
        group_idx = 1
        group_color = QColor("#38bdf8") if self.is_dark_theme else QColor("#0284c7")

        self.tree.blockSignals(True)
        for h, files in sorted(duplicate_groups.items(), key=lambda kv: -kv[1][0]["size"]):
            file_size = files[0]["size"]
            wasted = file_size * (len(files) - 1)
            total_wasted_bytes += wasted
            total_dupe_files += len(files)

            group_title = f"گروه {group_idx}: {len(files)} فایل مشابه (هدررفت: {human_size(wasted)})"
            group_item = QTreeWidgetItem([group_title, human_size(file_size), "", f"هش: {h[:12]}..."])
            group_item.setForeground(0, group_color)
            group_item.setFont(0, QFont("Segoe UI", 10, QFont.Bold))

            for f_info in files:
                fname = os.path.basename(f_info["path"])
                child_item = QTreeWidgetItem([
                    fname,
                    human_size(f_info["size"]),
                    human_time(f_info["mtime"]),
                    f_info["path"]
                ])
                child_item.setCheckState(0, Qt.Unchecked)
                child_item.setData(0, Qt.UserRole, f_info)
                group_item.addChild(child_item)

            self.tree.addTopLevelItem(group_item)
            group_item.setExpanded(True)
            group_idx += 1

        self.tree.blockSignals(False)

        self.card_dupes.set_value(f"{total_dupe_files:,}")
        self.card_wasted.set_value(human_size(total_wasted_bytes))
        self.lbl_status.setText(f"اسکن کامل شد. {len(duplicate_groups)} گروه تکراری پیدا شد.")

    def on_item_checked(self, item: QTreeWidgetItem, column: int):
        if column != 0 or item.parent() is None:
            return
        self.update_selected_summary()

    def update_selected_summary(self):
        count = 0
        total_bytes = 0

        for g_idx in range(self.tree.topLevelItemCount()):
            group_item = self.tree.topLevelItem(g_idx)
            for c_idx in range(group_item.childCount()):
                child = group_item.child(c_idx)
                if child.checkState(0) == Qt.Checked:
                    f_info = child.data(0, Qt.UserRole)
                    if f_info:
                        count += 1
                        total_bytes += f_info["size"]

        self.card_selected.set_value(f"{count:,} ({human_size(total_bytes)})")
        self.btn_delete.setEnabled(count > 0)

    def smart_select(self, keep: str = "oldest"):
        self.tree.blockSignals(True)
        for g_idx in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(g_idx)
            children = [group.child(i) for i in range(group.childCount())]
            if not children:
                continue

            children.sort(key=lambda item: item.data(0, Qt.UserRole)["mtime"])

            for idx, child in enumerate(children):
                if keep == "oldest":
                    child.setCheckState(0, Qt.Checked if idx > 0 else Qt.Unchecked)
                elif keep == "newest":
                    child.setCheckState(0, Qt.Checked if idx < len(children) - 1 else Qt.Unchecked)

        self.tree.blockSignals(False)
        self.update_selected_summary()

    def clear_selection(self):
        self.tree.blockSignals(True)
        for g_idx in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(g_idx)
            for c_idx in range(group.childCount()):
                group.child(c_idx).setCheckState(0, Qt.Unchecked)
        self.tree.blockSignals(False)
        self.update_selected_summary()

    def show_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item or item.parent() is None:
            return

        f_info = item.data(0, Qt.UserRole)
        if not f_info:
            return

        menu = QMenu(self)
        action_open_dir = QAction("📂 باز کردن پوشه فایل", self)
        action_copy_path = QAction("📋 کپی آدرس کامل", self)

        action_open_dir.triggered.connect(lambda: self.reveal_in_explorer(f_info["path"]))
        action_copy_path.triggered.connect(lambda: QApplication.clipboard().setText(f_info["path"]))

        menu.addAction(action_open_dir)
        menu.addAction(action_copy_path)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def reveal_in_explorer(self, file_path: str):
        folder = os.path.dirname(file_path)
        if sys.platform.startswith("win"):
            os.startfile(folder)
        elif sys.platform == "darwin":
            os.system(f'open "{folder}"')
        else:
            os.system(f'xdg-open "{folder}"')

    def export_report(self):
        if not self.duplicate_groups:
            QMessageBox.information(self, "گزارش", "هیچ موردی برای ذخیره گزارش وجود ندارد.")
            return

        now = datetime.now()
        default_name = f"Report-{now.strftime('%Y%m%d-%H%M%S')}.txt"
        save_path, _ = QFileDialog.getSaveFileName(self, "ذخیره گزارش فایل‌های تکراری", default_name, "Text files (*.txt)")

        if not save_path:
            return

        try:
            lines = [
                "Duplicate Files Report - PySide6 Pro",
                f"مسیر اسکن‌شده: {self.txt_folder.text()}",
                f"تاریخ گزارش: {now.strftime('%Y-%m-%d %H:%M')}",
                f"تعداد گروه‌های تکراری: {len(self.duplicate_groups)}",
                "=" * 60,
                ""
            ]

            total_wasted = 0
            for idx, (h, files) in enumerate(self.duplicate_groups.items(), 1):
                f_size = files[0]["size"]
                wasted = f_size * (len(files) - 1)
                total_wasted += wasted

                lines.append(f"[گروه {idx}] - {len(files)} فایل - حجم هر فایل: {human_size(f_size)} - هدررفت: {human_size(wasted)}")
                lines.append(f"هش SHA-256: {h}")
                for f in files:
                    lines.append(f"  • {f['path']} ({human_time(f['mtime'])})")
                lines.append("-" * 60)

            lines.append(f"\nکل فضای آزادشدنی: {human_size(total_wasted)}")

            with open(save_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))

            QMessageBox.information(self, "موفقیت", f"گزارش با موفقیت ذخیره شد:\n{save_path}")
        except Exception as e:
            QMessageBox.critical(self, "خطا", f"خطا در ایجاد گزارش:\n{e}")

    def delete_selected(self):
        items_to_delete = []
        total_size = 0

        for g_idx in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(g_idx)
            for c_idx in range(group.childCount()):
                child = group.child(c_idx)
                if child.checkState(0) == Qt.Checked:
                    f_info = child.data(0, Qt.UserRole)
                    if f_info:
                        items_to_delete.append((child, f_info))
                        total_size += f_info["size"]

        if not items_to_delete:
            return

        mode_msg = "انتقال به سطل زباله (قابل بازیابی)" if HAS_SEND2TRASH else "حذف دائمی (غیرقابل بازیابی!)"
        reply = QMessageBox.question(
            self,
            "تایید حذف",
            f"آیا از حذف {len(items_to_delete)} فایل به حجم مجموع {human_size(total_size)} اطمینان دارید؟\nنحوه حذف: {mode_msg}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        deleted_count = 0
        errors = []

        for item, f_info in items_to_delete:
            path = f_info["path"]
            try:
                if HAS_SEND2TRASH:
                    send2trash(os.path.normpath(path))
                else:
                    os.remove(path)

                parent_group = item.parent()
                parent_group.removeChild(item)
                deleted_count += 1

                if parent_group.childCount() <= 1:
                    index = self.tree.indexOfTopLevelItem(parent_group)
                    self.tree.takeTopLevelItem(index)

            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {str(e)}")

        self.update_selected_summary()

        res_msg = f"{deleted_count} فایل با موفقیت پاکسازی شد."
        if errors:
            res_msg += f"\nخطا در حذف {len(errors)} فایل رخ داد."
            QMessageBox.warning(self, "اتمام عملیات با خطا", res_msg)
        else:
            QMessageBox.information(self, "اتمام عملیات", res_msg)


def main():
    app = QApplication(sys.argv)
    window = ModernDuplicateFinder()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
