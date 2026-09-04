"""The shuffler window: process table, timing controls, and the global hotkey."""

from __future__ import annotations

import atexit
import logging
import random
import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from pynput import keyboard

from .detection import EmulatorDetector
from .suspension import ProcessSuspender
from .windows import WindowManager, detect_backend

log = logging.getLogger(__name__)

WINDOW_TITLE = "Linux Emulator Shuffler"
WINDOW_SIZE = (750, 520)

DEFAULT_MIN_SECONDS = 2
DEFAULT_MAX_SECONDS = 20
INTERVAL_RANGE = (1, 3600)

# Give the compositor time to minimize a window before its process stops
# responding, so it is not mistaken for a hung application.
SUSPEND_DELAY_MS = 250

# Key repeat would otherwise empty the whole pool on a single held keypress.
REMOVE_DEBOUNCE_SECONDS = 0.8

COLUMNS = ("PID", "Emulator Name", "Execution Path / Command")
PID_COLUMN = 0

HIGHLIGHT_BACKGROUND = "#d4edda"
HIGHLIGHT_FOREGROUND = "#155724"


class HotkeyEmitter(QObject):
    """Bridges pynput's listener thread onto the Qt event loop."""

    remove_triggered = Signal()


class ShufflerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(*WINDOW_SIZE)

        self.selected_pids: list[int] = []
        self.current_pid: int | None = None
        self.is_running = False
        self.last_remove_time = 0.0

        self.backend = detect_backend()
        self.detector = EmulatorDetector()
        self.window_manager = WindowManager(self.backend)
        self.suspender = ProcessSuspender()
        atexit.register(self.suspender.resume_all)

        self.shuffle_timer = QTimer(self)
        self.shuffle_timer.setSingleShot(True)
        self.shuffle_timer.timeout.connect(self.execute_shuffle)

        self.hotkey_emitter = HotkeyEmitter()
        self.hotkey_emitter.remove_triggered.connect(self.remove_current_game)
        self.hotkey_listener: keyboard.GlobalHotKeys | None = None
        self.init_hotkeys()

        self.init_ui()
        self.refresh_process_list()

    # ------------------------------------------------------------------ UI setup

    def init_ui(self) -> None:
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        control_layout = QHBoxLayout()
        self.min_spin = self._interval_spin(DEFAULT_MIN_SECONDS)
        self.max_spin = self._interval_spin(DEFAULT_MAX_SECONDS)

        self.refresh_btn = QPushButton("Refresh Emulator List")
        self.refresh_btn.clicked.connect(self.refresh_process_list)

        control_layout.addWidget(QLabel("Min Sec:"))
        control_layout.addWidget(self.min_spin)
        control_layout.addWidget(QLabel("Max Sec:"))
        control_layout.addWidget(self.max_spin)
        control_layout.addStretch()
        control_layout.addWidget(self.refresh_btn)

        self.table = QTableWidget()
        self.table.setColumnCount(len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)

        action_layout = QHBoxLayout()
        self.start_btn = QPushButton("Start Shuffler")
        self.start_btn.clicked.connect(self.toggle_shuffler)
        self.status_label = QLabel()
        action_layout.addWidget(self.start_btn)
        action_layout.addWidget(self.status_label)
        action_layout.addStretch()

        help_label = QLabel("Hotkey: [Delete] Remove Active Emulator & Shuffle Immediately")
        help_label.setStyleSheet("color: #666; font-size: 11px;")

        main_layout.addLayout(control_layout)
        main_layout.addWidget(self.table)
        main_layout.addLayout(action_layout)
        main_layout.addWidget(help_label)

        self.set_status_stopped()
        if not self.window_manager.can_focus:
            log.warning(
                "no window-focusing support for backend %r; the rotation will run "
                "but windows will not be raised", self.backend
            )

    def _interval_spin(self, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(*INTERVAL_RANGE)
        spin.setValue(value)
        return spin

    # ----------------------------------------------------------------- status

    def _set_status(self, text: str, color: str) -> None:
        self.status_label.setText(f"Backend: {self.backend.upper()} | {text}")
        self.status_label.setStyleSheet(f"font-weight: bold; color: {color};")

    def set_status_stopped(self) -> None:
        self._set_status("Stopped", "gray")

    def set_status_running(self) -> None:
        self._set_status(f"Running ({len(self.selected_pids)} active)", "green")

    # ------------------------------------------------------------ process table

    def refresh_process_list(self) -> None:
        if self.is_running:
            return

        self.table.setRowCount(0)
        for row, (pid, (name, command)) in enumerate(self.detector.find().items()):
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(pid)))
            self.table.setItem(row, 1, QTableWidgetItem(name))
            self.table.setItem(row, 2, QTableWidgetItem(command))

    def update_table_highlights(self) -> None:
        highlight_bg = QColor(HIGHLIGHT_BACKGROUND)
        highlight_fg = QColor(HIGHLIGHT_FOREGROUND)

        is_dark = self.palette().window().color().lightness() < 128
        default_fg = QColor("#ffffff") if is_dark else QColor("#000000")

        bold_font = QFont()
        bold_font.setBold(True)
        normal_font = QFont()
        normal_font.setBold(False)

        for row in range(self.table.rowCount()):
            pid_item = self.table.item(row, PID_COLUMN)
            if not pid_item:
                continue

            in_rotation = int(pid_item.text()) in self.selected_pids

            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if not item:
                    continue
                if in_rotation:
                    item.setBackground(highlight_bg)
                    item.setForeground(highlight_fg)
                    item.setFont(bold_font)
                else:
                    item.setBackground(QColor(0, 0, 0, 0))
                    item.setForeground(default_fg)
                    item.setFont(normal_font)

    # -------------------------------------------------------------- rotation

    def toggle_shuffler(self) -> None:
        if self.is_running:
            self.stop_shuffler()
            return

        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(
                self, "Warning", "Please select at least one emulator process to shuffle."
            )
            return

        if self.min_spin.value() > self.max_spin.value():
            QMessageBox.warning(
                self, "Warning", "Min seconds cannot be greater than Max seconds."
            )
            return

        self.selected_pids = [
            int(self.table.item(row.row(), PID_COLUMN).text()) for row in selected_rows
        ]
        self.is_running = True
        self.start_btn.setText("Stop Shuffler")
        self.refresh_btn.setEnabled(False)
        self.set_status_running()

        self.table.clearSelection()
        self.update_table_highlights()
        self.execute_shuffle()

    def stop_shuffler(self) -> None:
        self.shuffle_timer.stop()
        self.is_running = False

        self.suspender.resume_all()
        self.selected_pids.clear()
        self.current_pid = None

        self.start_btn.setText("Start Shuffler")
        self.refresh_btn.setEnabled(True)
        self.set_status_stopped()
        self.update_table_highlights()

    def execute_shuffle(self) -> None:
        if not self.is_running or not self.selected_pids:
            return

        candidates = [pid for pid in self.selected_pids if pid != self.current_pid]
        if not candidates:
            candidates = self.selected_pids

        self.current_pid = random.choice(candidates)
        self.focus_target(self.current_pid)

        delay_seconds = random.randint(self.min_spin.value(), self.max_spin.value())
        self.shuffle_timer.start(delay_seconds * 1000)

    def focus_target(self, pid: int) -> None:
        """Bring one emulator to the front and freeze the rest."""
        # Thaw before focusing, so the compositor never pings a frozen window.
        self.suspender.resume_tree(pid)
        self.window_manager.focus(pid, [p for p in self.selected_pids if p != pid])

        # Deferred so the minimize lands before the process stops responding.
        QTimer.singleShot(SUSPEND_DELAY_MS, lambda: self.suspend_background(pid))

    def suspend_background(self, pid: int) -> None:
        # The rotation may have moved on while this callback was pending.
        if not self.is_running or pid != self.current_pid:
            return
        self.suspender.suspend_all_except(self.selected_pids, pid)

    # ---------------------------------------------------------------- hotkey

    def remove_current_game(self) -> None:
        now = time.time()
        if now - self.last_remove_time < REMOVE_DEBOUNCE_SECONDS:
            return
        self.last_remove_time = now

        if not self.is_running or self.current_pid is None:
            return
        if self.current_pid not in self.selected_pids:
            return

        removed_pid = self.current_pid
        self.selected_pids.remove(removed_pid)
        self.current_pid = None
        self.suspender.resume_tree(removed_pid)

        self.shuffle_timer.stop()
        self.update_table_highlights()

        if not self.selected_pids:
            self.stop_shuffler()
            QMessageBox.information(
                self, "Shuffler Stopped", "All emulator targets removed from pool."
            )
        else:
            self.set_status_running()
            self.execute_shuffle()

    def init_hotkeys(self) -> None:
        def on_remove() -> None:
            self.hotkey_emitter.remove_triggered.emit()

        try:
            self.hotkey_listener = keyboard.GlobalHotKeys({"<delete>": on_remove})
            self.hotkey_listener.start()
        except Exception as exc:  # pynput raises backend-specific errors
            log.warning("global hotkey unavailable: %s", exc)
            self.hotkey_listener = None

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.is_running:
            self.stop_shuffler()
        self.suspender.resume_all()
        self.window_manager.cleanup()
        if self.hotkey_listener:
            self.hotkey_listener.stop()
        event.accept()
