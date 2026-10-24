from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtCore import QSettings, QThread, Qt, QStandardPaths, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .export import export_csv
from .local_games import LocalGameScanResult, scan_local_game_names
from .models import FileLink, ScanConfig
from .scope import ScopeError, normalize_target
from .storage import ScanStore
from .worker import ScanWorker


MAX_GAME_KEYWORDS_PER_IMPORT = 8
DEFAULT_TEMPLATES = (
    "site:{site} {keyword} filetype:{ext}",
)
DEFAULT_EXTENSIONS = ("rar", "exfat", "fpkg", "pkg")


class LocalGameScanWorker(QThread):
    progress = Signal(int, int)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, root: str, parent=None):
        super().__init__(parent)
        self.root = root
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            result = scan_local_game_names(self.root, self._cancel.is_set, lambda entries, names: self.progress.emit(entries, names))
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.completed.emit(result)


class LocalGameReviewDialog(QDialog):
    def __init__(self, result: LocalGameScanResult, language: str, parent=None):
        super().__init__(parent)
        self.result = result
        self.language = language
        t = TEXT[language]
        self.setWindowTitle(t["local_review_title"])
        self.setMinimumSize(560, 440)
        layout = QVBoxLayout(self)
        summary = t["local_review_summary"].format(
            entries=result.entries_seen, root=result.root, names=len(result.candidates), errors=result.errors,
            filesystem=result.filesystem or "unknown",
        )
        if result.limited:
            summary += "\n" + t["local_review_limit"]
        if result.cancelled:
            summary += "\n" + t["local_review_cancel"]
        self.summary_label = QLabel(summary)
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)
        self.list_widget = QListWidget()
        initially_checked = len(result.candidates) <= MAX_GAME_KEYWORDS_PER_IMPORT
        for candidate in result.candidates:
            item = QListWidgetItem(candidate.name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if initially_checked else Qt.CheckState.Unchecked)
            item.setToolTip(f"{candidate.evidence}\n{candidate.relative_path}")
            item.setData(Qt.ItemDataRole.UserRole, candidate.name)
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget, 1)
        buttons = QDialogButtonBox(self)
        self.add_button = buttons.addButton(t["local_add_selected"], QDialogButtonBox.ButtonRole.AcceptRole)
        cancel_button = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.add_button.clicked.connect(self._accept_selected)
        cancel_button.clicked.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft if language == "fa" else Qt.LayoutDirection.LeftToRight)

    def selected_names(self) -> list[str]:
        return [
            str(self.list_widget.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self.list_widget.count())
            if self.list_widget.item(index).checkState() == Qt.CheckState.Checked
        ]

    def _accept_selected(self) -> None:
        count = len(self.selected_names())
        t = TEXT[self.language]
        if not count:
            QMessageBox.information(self, t["local_review_title"], t["local_select_none"])
            return
        if count > MAX_GAME_KEYWORDS_PER_IMPORT:
            QMessageBox.information(self, t["local_review_title"], t["local_select_limit"].format(limit=MAX_GAME_KEYWORDS_PER_IMPORT))
            return
        self.accept()


TEXT = {
    "en": {
        "title": "DorkLink Extractor",
        "subtitle": "Scoped public-page discovery and file-link verification",
        "target": "Target website",
        "target_hint": "example.org or https://example.org",
        "keywords": "Keywords",
        "keywords_hint": "One keyword per line",
        "local_scan_button": "Find game names in a local folder…",
        "local_scan_title": "Scan a local folder",
        "local_scan_prompt": "Choose one folder on a Windows-visible drive, including exFAT. The scan is read-only and does not unpack files or follow links.",
        "local_scanning": "Scanning the selected folder…",
        "local_progress": "Scanned {entries:,} entries; found {names} candidate names.",
        "local_review_title": "Review detected game names",
        "local_review_summary": "Inspected {entries:,} entries on {filesystem} under {root}. Found {names} candidates and {errors} filesystem issues. Names are inferred from folders, filenames, or Steam manifests; they do not prove ownership.",
        "local_review_limit": "A safety limit was reached; this list may be partial.",
        "local_review_cancel": "The scan was cancelled; review this partial list.",
        "local_add_selected": "Add selected as keywords",
        "local_select_none": "Select at least one game name.",
        "local_select_limit": "Select at most {limit} names at a time to keep search runs manageable.",
        "local_scan_empty": "No likely game names were found in the selected folder.",
        "local_added": "Added {count} game name(s) to Keywords.",
        "templates": "Search templates",
        "templates_hint": "One per line. Variables: {keyword}, {site}, {target}, {ext}. {ext} runs a separate search for every selected format.",
        "extensions": "File extensions",
        "extensions_hint": ".rar, .exfat, .fpkg, .pkg",
        "settings": "Scan settings",
        "max_pages": "Maximum pages",
        "concurrency": "Concurrency",
        "delay": "Delay between requests (seconds)",
        "playwright": "Render pages with JavaScript when needed (optional)",
        "consent": "I am authorized to scan this website and its public pages.",
        "start": "Start scan",
        "stop": "Stop",
        "export": "Export CSV",
        "results": "Discovered file URLs",
        "status": "Ready",
        "restored_partial": "Showing {files} links saved from scan #{scan}; that scan stopped before completion.",
        "restored_complete": "Showing {files} links saved from scan #{scan}.",
        "scan_empty_done": "No matching file URLs were saved. Check the spelling, search templates, public/indexed pages, and selected formats.",
        "url": "File URL",
        "source": "Source page",
        "format": "Format",
        "status_col": "HTTP status",
        "reachable": "Reachable",
        "yes": "Yes",
        "no": "No",
        "size": "Bytes",
        "not_checked": "Not checked",
        "external_unverified": "External download links listed but not checked: {count} (outside target domain).",
        "log": "Activity",
        "scan_running": "Scan running…",
        "stop_requested": "Stop requested. Finishing the current request…",
        "scan_done": "Scan #{scan} finished: {files} file links, {reachable} reachable, {pages} pages, {errors} errors.",
        "confirm_target": "Enter a target website.",
        "confirm_keyword": "Enter at least one keyword.",
        "confirm_consent": "Confirm that you are authorized to scan this website.",
        "export_empty": "There are no results to export yet.",
        "export_ok": "CSV exported successfully.",
        "saved": "Saved",
        "language": "Language",
    },
    "fa": {
        "title": "استخراج‌گر لینک دُرک",
        "subtitle": "کشف صفحات عمومی و بررسی لینک فایل‌ها در محدودهٔ مشخص",
        "target": "وب‌سایت هدف",
        "target_hint": "example.org یا https://example.org",
        "keywords": "کلیدواژه‌ها",
        "keywords_hint": "هر کلیدواژه در یک خط",
        "local_scan_button": "پیدا کردن نام بازی‌ها در یک پوشه محلی…",
        "local_scan_title": "اسکن یک پوشه محلی",
        "local_scan_prompt": "یک پوشه را در یک درایو قابل‌مشاهده در ویندوز، از جمله exFAT، انتخاب کنید. اسکن فقط‌خواندنی است و فایل‌ها را باز یا از حالت فشرده خارج نمی‌کند.",
        "local_scanning": "پوشه انتخاب‌شده در حال اسکن است…",
        "local_progress": "{entries:,} مورد بررسی شد؛ {names} نام احتمالی پیدا شد.",
        "local_review_title": "بازبینی نام بازی‌های پیدا شده",
        "local_review_summary": "{entries:,} مورد در مسیر {root} روی فایل‌سیستم {filesystem} بررسی شد. {names} نام احتمالی و {errors} خطای دسترسی پیدا شد. نام‌ها از پوشه، نام فایل یا مانیفست Steam حدس زده شده‌اند و مالکیت را ثابت نمی‌کنند.",
        "local_review_limit": "حد مجاز اسکن رسید؛ فهرست ممکن است کامل نباشد.",
        "local_review_cancel": "اسکن لغو شد؛ این فهرست ناقص را بازبینی کنید.",
        "local_add_selected": "افزودن موارد انتخاب‌شده به کلیدواژه‌ها",
        "local_select_none": "حداقل یک نام بازی را انتخاب کنید.",
        "local_select_limit": "برای کوتاه ماندن جست‌وجو، هر بار حداکثر {limit} نام انتخاب کنید.",
        "local_scan_empty": "نام احتمالی بازی در پوشه انتخاب‌شده پیدا نشد.",
        "local_added": "{count} نام بازی به کلیدواژه‌ها افزوده شد.",
        "templates": "الگوهای جست‌وجو",
        "templates_hint": "هر الگو در یک خط. متغیرها: {keyword}، {site}، {target}، {ext}. استفاده از {ext} برای هر قالب انتخاب‌شده یک جست‌وجوی جدا اجرا می‌کند.",
        "extensions": "پسوند فایل‌ها",
        "extensions_hint": ".rar, .exfat, .fpkg, .pkg",
        "settings": "تنظیمات اسکن",
        "max_pages": "حداکثر صفحات",
        "concurrency": "هم‌زمانی",
        "delay": "فاصلهٔ بین درخواست‌ها (ثانیه)",
        "playwright": "در صورت نیاز صفحات جاوااسکریپتی را نمایش بده (اختیاری)",
        "consent": "اجازهٔ اسکن این وب‌سایت و صفحات عمومی آن را دارم.",
        "start": "شروع اسکن",
        "stop": "توقف",
        "export": "خروجی CSV",
        "results": "لینک‌های فایل پیدا شده",
        "status": "آماده",
        "restored_partial": "{files} لینک ذخیره‌شده از اسکن #{scan} نمایش داده می‌شود؛ آن اسکن پیش از پایان متوقف شده است.",
        "restored_complete": "{files} لینک ذخیره‌شده از اسکن #{scan} نمایش داده می‌شود.",
        "scan_empty_done": "لینک فایلی ذخیره نشد. املای کلیدواژه، الگوهای جست‌وجو، عمومی و نمایه‌شدن صفحات و قالب‌های انتخاب‌شده را بررسی کنید.",
        "url": "نشانی فایل",
        "source": "صفحهٔ مبدأ",
        "format": "قالب",
        "status_col": "وضعیت HTTP",
        "reachable": "در دسترس",
        "yes": "بله",
        "no": "خیر",
        "size": "بایت",
        "not_checked": "بررسی نشده",
        "external_unverified": "لینک‌های دانلود خارج از دامنه فهرست شدند اما بررسی نشدند: {count}.",
        "log": "فعالیت‌ها",
        "scan_running": "اسکن در حال اجراست…",
        "stop_requested": "درخواست توقف ثبت شد؛ درخواست فعلی در حال پایان است…",
        "scan_done": "اسکن #{scan} تمام شد: {files} لینک، {reachable} در دسترس، {pages} صفحه، {errors} خطا.",
        "confirm_target": "نشانی وب‌سایت هدف را وارد کنید.",
        "confirm_keyword": "حداقل یک کلیدواژه وارد کنید.",
        "confirm_consent": "تأیید کنید که اجازهٔ اسکن این وب‌سایت را دارید.",
        "export_empty": "هنوز نتیجه‌ای برای خروجی وجود ندارد.",
        "export_ok": "فایل CSV با موفقیت ذخیره شد.",
        "saved": "ذخیره شد",
        "language": "زبان",
    },
}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("DorkLink", "Extractor")
        self.language = self.settings.value("language", "en")
        self.worker: ScanWorker | None = None
        self.local_scan_worker: LocalGameScanWorker | None = None
        self.local_scan_progress: QProgressDialog | None = None
        self.rows: list[dict] = []
        self.scan_id: int | None = None
        self.data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = str(self.data_dir / "dorklink.sqlite3")
        self.setMinimumSize(1080, 720)
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #10151d; color: #e6edf5; font-size: 13px; }
            QGroupBox { border: 1px solid #2c3745; border-radius: 9px; margin-top: 12px; padding: 14px 10px 10px; font-weight: 600; color: #e9f2fc; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
            QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: #171f2a; border: 1px solid #344253; border-radius: 6px; padding: 7px; selection-background-color: #3478b8; }
            QTextEdit { line-height: 1.4; }
            QPushButton { background: #243346; border: 1px solid #3c526c; border-radius: 7px; padding: 9px 14px; font-weight: 600; }
            QPushButton:hover { background: #2d4259; }
            QPushButton#primary { background: #2474b9; border-color: #438ecb; }
            QPushButton#primary:hover { background: #2d87cf; }
            QPushButton:disabled { color: #778393; background: #1c2530; }
            QTableWidget { background: #131a23; alternate-background-color: #18212c; gridline-color: #2a3543; border: 1px solid #2c3745; border-radius: 7px; }
            QHeaderView::section { background: #1b2633; color: #b9c9db; padding: 7px; border: 0; border-right: 1px solid #2d3948; }
            QProgressBar { background: #1a2430; border: 1px solid #314153; border-radius: 5px; text-align: center; height: 13px; }
            QProgressBar::chunk { background: #3185c8; border-radius: 4px; }
            QCheckBox { spacing: 8px; }
            QLabel#subtitle { color: #98a8b9; }
        """)
        self._build_ui()
        self._restore_fields()
        self._set_language(self.language)
        self._restore_latest_results()

    def _build_ui(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 18, 20, 16)
        header = QGridLayout()
        self.title_label = QLabel()
        self.title_label.setFont(QFont("Segoe UI", 21, QFont.Weight.Bold))
        self.subtitle_label = QLabel()
        self.subtitle_label.setObjectName("subtitle")
        self.language_label = QLabel()
        self.language_combo = QComboBox()
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("فارسی", "fa")
        self.language_combo.currentIndexChanged.connect(lambda: self._set_language(self.language_combo.currentData()))
        header.addWidget(self.title_label, 0, 0)
        header.addWidget(self.language_label, 0, 1, Qt.AlignmentFlag.AlignRight)
        header.addWidget(self.language_combo, 0, 2)
        header.addWidget(self.subtitle_label, 1, 0, 1, 3)
        layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 8, 8, 0)
        form_box = QGroupBox()
        form = QFormLayout(form_box)
        self.form_labels = [QLabel() for _ in range(4)]
        self.target_edit = QLineEdit()
        self.keyword_edit = QTextEdit()
        self.keyword_edit.setMaximumHeight(82)
        self.local_games_button = QPushButton()
        self.local_games_button.clicked.connect(self.scan_local_game_names)
        self.template_edit = QTextEdit()
        self.template_edit.setMaximumHeight(102)
        self.extension_edit = QLineEdit()
        form.addRow(self.form_labels[0], self.target_edit)
        form.addRow(self.form_labels[1], self.keyword_edit)
        form.addRow("", self.local_games_button)
        form.addRow(self.form_labels[2], self.template_edit)
        form.addRow(self.form_labels[3], self.extension_edit)
        self.form_box = form_box
        left_layout.addWidget(form_box)

        settings_box = QGroupBox()
        settings_grid = QGridLayout(settings_box)
        self.settings_labels = [QLabel() for _ in range(3)]
        self.pages_spin = QSpinBox()
        self.pages_spin.setRange(1, 1000)
        self.pages_spin.setValue(50)
        self.concurrency_spin = QSpinBox()
        self.concurrency_spin.setRange(1, 10)
        self.concurrency_spin.setValue(3)
        self.delay_spin = QDoubleSpinBox()
        self.delay_spin.setRange(0.5, 30)
        self.delay_spin.setSingleStep(0.5)
        self.delay_spin.setValue(1.0)
        self.playwright_check = QCheckBox()
        settings_grid.addWidget(self.settings_labels[0], 0, 0)
        settings_grid.addWidget(self.pages_spin, 0, 1)
        settings_grid.addWidget(self.settings_labels[1], 1, 0)
        settings_grid.addWidget(self.concurrency_spin, 1, 1)
        settings_grid.addWidget(self.settings_labels[2], 2, 0)
        settings_grid.addWidget(self.delay_spin, 2, 1)
        settings_grid.addWidget(self.playwright_check, 3, 0, 1, 2)
        self.settings_box = settings_box
        left_layout.addWidget(settings_box)

        self.consent_check = QCheckBox()
        left_layout.addWidget(self.consent_check)
        buttons = QGridLayout()
        self.start_button = QPushButton()
        self.start_button.setObjectName("primary")
        self.stop_button = QPushButton()
        self.stop_button.setEnabled(False)
        self.export_button = QPushButton()
        self.start_button.clicked.connect(self.start_scan)
        self.stop_button.clicked.connect(self.stop_scan)
        self.export_button.clicked.connect(self.export_results)
        buttons.addWidget(self.start_button, 0, 0, 1, 2)
        buttons.addWidget(self.stop_button, 1, 0)
        buttons.addWidget(self.export_button, 1, 1)
        left_layout.addLayout(buttons)
        left_layout.addStretch(1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 8, 0, 0)
        self.results_box = QGroupBox()
        results_layout = QVBoxLayout(self.results_box)
        self.table = QTableWidget(0, 6)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in range(2, 6):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        results_layout.addWidget(self.table)
        right_layout.addWidget(self.results_box, 1)
        self.activity_box = QGroupBox()
        activity_layout = QVBoxLayout(self.activity_box)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.status_label = QLabel()
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumHeight(130)
        activity_layout.addWidget(self.status_label)
        activity_layout.addWidget(self.progress)
        activity_layout.addWidget(self.log_edit)
        right_layout.addWidget(self.activity_box)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([390, 690])
        layout.addWidget(splitter, 1)
        self.setCentralWidget(root)

    def _set_language(self, language: str):
        if language not in TEXT:
            language = "en"
        self.language = language
        self.settings.setValue("language", language)
        self.language_combo.blockSignals(True)
        self.language_combo.setCurrentIndex(0 if language == "en" else 1)
        self.language_combo.blockSignals(False)
        t = TEXT[language]
        self.setWindowTitle(t["title"])
        self.title_label.setText(t["title"])
        self.subtitle_label.setText(t["subtitle"])
        self.language_label.setText(t["language"])
        self.target_edit.setPlaceholderText(t["target_hint"])
        self.keyword_edit.setPlaceholderText(t["keywords_hint"])
        self.local_games_button.setText(t["local_scan_button"])
        self.local_games_button.setToolTip(t["local_scan_prompt"])
        self.template_edit.setPlaceholderText(t["templates_hint"])
        self.extension_edit.setPlaceholderText(t["extensions_hint"])
        self.form_box.setTitle(t["target"] + " / " + t["keywords"] + " / " + t["templates"])
        self.settings_box.setTitle(t["settings"])
        self.results_box.setTitle(t["results"])
        self.activity_box.setTitle(t["log"])
        for label, value in zip(self.form_labels, [t["target"], t["keywords"], t["templates"], t["extensions"]]):
            label.setText(value)
        for label, value in zip(self.settings_labels, [t["max_pages"], t["concurrency"], t["delay"]]):
            label.setText(value)
        self.playwright_check.setText(t["playwright"])
        self.consent_check.setText(t["consent"])
        self.start_button.setText(t["start"])
        self.stop_button.setText(t["stop"])
        self.export_button.setText(t["export"])
        self.table.setHorizontalHeaderLabels([t["url"], t["source"], t["format"], t["status_col"], t["reachable"], t["size"]])
        if not self.worker or not self.worker.isRunning():
            self.status_label.setText(t["status"])
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft if language == "fa" else Qt.LayoutDirection.LeftToRight)
        if self.rows:
            self._populate_results()

    def _restore_fields(self):
        self.target_edit.setText(self.settings.value("target", ""))
        self.keyword_edit.setPlainText(self.settings.value("keywords", ""))
        old_templates = (
            "site:{site} {keyword} filetype:{ext}",
            "site:{site} intitle:index.of {keyword}",
        )
        previous_broad_defaults = (
            "site:{site} {keyword}",
            'site:{site} "{keyword}" دانلود',
            'site:{site} "{keyword}" download',
        )
        saved_templates = self.settings.value("templates", "\n".join(DEFAULT_TEMPLATES))
        if isinstance(saved_templates, (list, tuple)):
            saved_templates = "\n".join(str(line) for line in saved_templates)
        template_lines = tuple(line.strip() for line in str(saved_templates).splitlines() if line.strip())
        normalized_templates = tuple(line.casefold() for line in template_lines)
        if not template_lines or normalized_templates in {
            tuple(line.casefold() for line in old_templates),
            tuple(line.casefold() for line in previous_broad_defaults),
        }:
            template_lines = DEFAULT_TEMPLATES
        self.template_edit.setPlainText("\n".join(template_lines))

        # The user requested a narrow game-package search; always restore this exact set.
        self.extension_edit.setText(", ".join(DEFAULT_EXTENSIONS))

    def _restore_latest_results(self):
        try:
            scan, rows = ScanStore(self.database_path).latest_scan()
        except Exception:
            return
        if not scan:
            return
        self.scan_id = int(scan["id"])
        self.rows = rows
        self._populate_results()
        key = "restored_partial" if not scan.get("finished_at") else "restored_complete"
        self.status_label.setText(TEXT[self.language][key].format(files=len(rows), scan=self.scan_id))

    def scan_local_game_names(self):
        if self.local_scan_worker and self.local_scan_worker.isRunning():
            return
        initial = self.settings.value(
            "local_game_scan_root",
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.HomeLocation),
        )
        root = QFileDialog.getExistingDirectory(self, TEXT[self.language]["local_scan_title"], initial)
        if not root:
            return
        self.settings.setValue("local_game_scan_root", root)
        self.local_games_button.setEnabled(False)
        self.local_scan_progress = QProgressDialog(
            TEXT[self.language]["local_scanning"],
            TEXT[self.language]["stop"],
            0,
            0,
            self,
        )
        self.local_scan_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.local_scan_progress.setMinimumDuration(0)
        self.local_scan_worker = LocalGameScanWorker(root, self)
        self.local_scan_progress.canceled.connect(self.local_scan_worker.cancel)
        self.local_scan_worker.progress.connect(self._update_local_scan_progress)
        self.local_scan_worker.completed.connect(self._review_local_game_names)
        self.local_scan_worker.failed.connect(self._local_game_scan_failed)
        self.local_scan_worker.finished.connect(lambda: self.local_games_button.setEnabled(True))
        self.local_scan_worker.start()
        self.local_scan_progress.show()

    def _update_local_scan_progress(self, entries: int, names: int) -> None:
        if self.local_scan_progress:
            self.local_scan_progress.setLabelText(TEXT[self.language]["local_progress"].format(entries=entries, names=names))

    def _close_local_scan_progress(self) -> None:
        if self.local_scan_progress:
            self.local_scan_progress.close()
            self.local_scan_progress = None

    def _local_game_scan_failed(self, detail: str) -> None:
        self._close_local_scan_progress()
        QMessageBox.warning(self, TEXT[self.language]["local_scan_title"], detail)

    def _review_local_game_names(self, result: LocalGameScanResult) -> None:
        self._close_local_scan_progress()
        if not result.candidates:
            QMessageBox.information(self, TEXT[self.language]["local_scan_title"], TEXT[self.language]["local_scan_empty"])
            return
        dialog = LocalGameReviewDialog(result, self.language, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = dialog.selected_names()
        existing_lines = [line.strip() for line in self.keyword_edit.toPlainText().splitlines() if line.strip()]
        existing = {line.casefold() for line in existing_lines}
        added = [name for name in selected if name.casefold() not in existing]
        self.keyword_edit.setPlainText("\n".join(existing_lines + added))
        if added:
            message = TEXT[self.language]["local_added"].format(count=len(added))
            self.status_label.setText(message)
            self._log(message)

    def start_scan(self):
        t = TEXT[self.language]
        target_value = self.target_edit.text().strip()
        keywords = tuple(line.strip() for line in self.keyword_edit.toPlainText().splitlines() if line.strip())
        if not target_value:
            self._error(t["confirm_target"])
            return
        if not keywords:
            self._error(t["confirm_keyword"])
            return
        if not self.consent_check.isChecked():
            self._error(t["confirm_consent"])
            return
        try:
            target = normalize_target(target_value)
        except ScopeError as exc:
            self._error(str(exc))
            return
        templates = tuple(line.strip() for line in self.template_edit.toPlainText().splitlines() if line.strip())
        extensions = tuple(dict.fromkeys(
            item.strip().lower().lstrip(".")
            for item in self.extension_edit.text().replace(";", ",").split(",") if item.strip()
        ))
        if not extensions:
            self._error(t["extensions_hint"])
            return
        config = ScanConfig(
            target_url=target,
            keywords=keywords,
            templates=templates,
            extensions=extensions,
            max_pages=self.pages_spin.value(),
            concurrency=self.concurrency_spin.value(),
            request_delay=self.delay_spin.value(),
            use_playwright=self.playwright_check.isChecked(),
        )
        self.settings.setValue("target", self.target_edit.text())
        self.settings.setValue("keywords", self.keyword_edit.toPlainText())
        self.settings.setValue("templates", self.template_edit.toPlainText())
        self.settings.setValue("extensions", self.extension_edit.text())
        self.rows = []
        self.table.setRowCount(0)
        self._last_pages = 0
        self.log_edit.clear()
        self.worker = ScanWorker(config, self.database_path)
        self.worker.progress.connect(self.on_progress)
        self.worker.completed.connect(self.on_completed)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.on_worker_finished)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.progress.show()
        self.status_label.setText(t["scan_running"])
        self.worker.start()

    def stop_scan(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.stop_button.setEnabled(False)
            self._log(TEXT[self.language]["stop_requested"])

    def on_progress(self, event: dict):
        message = event.get("message", "")
        self._last_pages = int(event.get("pages", 0))
        new_rows = event.get("new_rows", [])
        if new_rows:
            existing_urls = {row.get("url") for row in self.rows}
            added_rows = []
            for row in new_rows:
                url = row.get("url")
                if url not in existing_urls:
                    self.rows.append(row)
                    added_rows.append(row)
                    existing_urls.add(url)
            if added_rows:
                self._append_result_rows(added_rows)
        self.status_label.setText(message)
        self._log(message)

    def on_completed(self, scan_id: int, rows: list, errors: int):
        self.scan_id = scan_id
        self.rows = rows
        self._populate_results()
        reachable = sum(bool(row.get("reachable")) for row in rows)
        pages = int(getattr(self, "_last_pages", 0))
        self.status_label.setText(TEXT[self.language]["scan_done"].format(scan=scan_id, files=len(rows), reachable=reachable, pages=pages, errors=errors))
        if not rows:
            self.status_label.setText(self.status_label.text() + " " + TEXT[self.language]["scan_empty_done"])
        external = sum("External host; not checked" in str(row.get("error", "")) for row in rows)
        if external:
            self.status_label.setText(self.status_label.text() + " " + TEXT[self.language]["external_unverified"].format(count=external))
        self._log(self.status_label.text())

    def _populate_results(self):
        self.table.setRowCount(0)
        self._append_result_rows(self.rows)

    def _append_result_rows(self, rows: list[dict]) -> None:
        for row in rows:
            idx = self.table.rowCount()
            self.table.insertRow(idx)
            unchecked_external = "External host; not checked" in str(row.get("error", ""))
            status = row.get("http_status") or (TEXT[self.language]["not_checked"] if unchecked_external else "")
            reachable = TEXT[self.language]["not_checked"] if unchecked_external else (TEXT[self.language]["yes"] if row.get("reachable") else TEXT[self.language]["no"])
            values = [row.get("url", ""), row.get("source_page", ""), row.get("extension", ""), status, reachable, row.get("content_length") or ""]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col in {0, 1}:
                    item.setToolTip(str(value))
                self.table.setItem(idx, col, item)

    def on_failed(self, detail: str):
        brief = detail.splitlines()[0] if detail else "Scan failed."
        self._error(brief)
        self._log(detail)

    def on_worker_finished(self):
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.progress.hide()

    def export_results(self):
        if not self.rows:
            self._error(TEXT[self.language]["export_empty"])
            return
        path, _ = QFileDialog.getSaveFileName(self, TEXT[self.language]["export"], "dorklink-results.csv", "CSV files (*.csv)")
        if not path:
            return
        items = [FileLink(**{key: row.get(key) for key in FileLink.__dataclass_fields__}) for row in self.rows]
        try:
            export_csv(path, items)
        except OSError as exc:
            self._error(str(exc))
            return
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setWindowTitle(TEXT[self.language]["export"])
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(f"{TEXT[self.language]['export_ok']}\n{path}")
        dialog.exec()

    def _log(self, message: str):
        if message:
            self.log_edit.moveCursor(QTextCursor.MoveOperation.End)
            self.log_edit.insertPlainText(message + "\n")
            self.log_edit.moveCursor(QTextCursor.MoveOperation.End)

    def _error(self, message: str):
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle(TEXT[self.language]["title"])
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(message)
        dialog.exec()


def main():
    app = QApplication(sys.argv)
    app.setOrganizationName("DorkLink")
    app.setApplicationName("Extractor")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
