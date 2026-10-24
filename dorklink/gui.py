from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QStandardPaths
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
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
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .export import export_csv
from .models import FileLink, ScanConfig
from .scope import ScopeError, normalize_target
from .worker import ScanWorker


TEXT = {
    "en": {
        "title": "DorkLink Extractor",
        "subtitle": "Scoped public-page discovery and file-link verification",
        "target": "Target website",
        "target_hint": "example.org or https://example.org",
        "keywords": "Keywords",
        "keywords_hint": "One keyword per line",
        "templates": "Search templates",
        "templates_hint": "One per line. Variables: {keyword}, {site}, {target}, {ext}",
        "extensions": "File extensions",
        "extensions_hint": ".pdf, .docx, .xlsx, .pptx, .csv, .txt, .zip",
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
        "url": "File URL",
        "source": "Source page",
        "format": "Format",
        "status_col": "HTTP status",
        "reachable": "Reachable",
        "yes": "Yes",
        "no": "No",
        "size": "Bytes",
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
        "templates": "الگوهای جست‌وجو",
        "templates_hint": "هر الگو در یک خط. متغیرها: {keyword}، {site}، {target}، {ext}",
        "extensions": "پسوند فایل‌ها",
        "extensions_hint": ".pdf, .docx, .xlsx, .pptx, .csv, .txt, .zip",
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
        "url": "نشانی فایل",
        "source": "صفحهٔ مبدأ",
        "format": "قالب",
        "status_col": "وضعیت HTTP",
        "reachable": "در دسترس",
        "yes": "بله",
        "no": "خیر",
        "size": "بایت",
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
        self.template_edit = QTextEdit()
        self.template_edit.setMaximumHeight(102)
        self.extension_edit = QLineEdit()
        form.addRow(self.form_labels[0], self.target_edit)
        form.addRow(self.form_labels[1], self.keyword_edit)
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
        self.template_edit.setPlainText(self.settings.value("templates", "site:{site} {keyword} filetype:{ext}\nsite:{site} intitle:index.of {keyword}"))
        self.extension_edit.setText(self.settings.value("extensions", ".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.csv,.txt,.zip"))

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
            item.strip().lower().lstrip(".") for item in self.extension_edit.text().replace(";", ",").split(",") if item.strip()
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
        self.status_label.setText(message)
        self._log(message)

    def on_completed(self, scan_id: int, rows: list, errors: int):
        self.scan_id = scan_id
        self.rows = rows
        self._populate_results()
        reachable = sum(bool(row.get("reachable")) for row in rows)
        pages = int(getattr(self, "_last_pages", 0))
        self.status_label.setText(TEXT[self.language]["scan_done"].format(scan=scan_id, files=len(rows), reachable=reachable, pages=pages, errors=errors))
        self._log(self.status_label.text())

    def _populate_results(self):
        self.table.setRowCount(0)
        for row in self.rows:
            idx = self.table.rowCount()
            self.table.insertRow(idx)
            values = [row.get("url", ""), row.get("source_page", ""), row.get("extension", ""), row.get("http_status") or "", TEXT[self.language]["yes"] if row.get("reachable") else TEXT[self.language]["no"], row.get("content_length") or ""]
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
