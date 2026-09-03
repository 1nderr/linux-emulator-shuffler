import sys
import os
import subprocess
import random
import time
import psutil
from typing import Dict, List, Set, Tuple, Optional, Any
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QPushButton, QSpinBox, QLabel,
    QHeaderView, QAbstractItemView, QMessageBox
)
from PySide6.QtCore import QTimer, Signal, QObject
from PySide6.QtGui import QColor, QFont, QCloseEvent
from pynput import keyboard


class HotkeyEmitter(QObject):
    remove_triggered: Signal = Signal()


class LinuxGameShuffler(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Linux Emulator Shuffler (Wayland & X11)")
        self.resize(750, 520)

        self.selected_pids: List[int] = []
        self.current_pid: Optional[int] = None
        self.is_running: bool = False
        self.backend: str = "unknown"
        self.last_remove_time: float = 0.0  # Debounce timestamp

        # Keywords for emulator identification
        self.emulator_keywords: Set[str] = {
            "rmg", "rosalie", "mupen", "mupen64", "mupen64plus", "mupen64plus-qt", 
            "mupen64plus-gui", "mupen64plus-fz", "rmupen", "ares", "com.github.rosalie241.rmg",
            "dolphin-emu", "dolphin", "cemu", "yuzu", "suyu", "ryujinx", "sudachi",
            "duckstation", "pcsx2", "pcsx2-qt", "rpcs3", "ppsspp", "mednafen", "epsxe",
            "mgba", "vbam", "visualboyadvance", "melonds", "desmume", "citra", "azahar",
            "retroarch", "bizhawk", "pico8", "blastem", "flycast"
        }

        # Binaries to ignore UNLESS they explicitly match an emulator target
        self.wrapper_binaries: Set[str] = {
            "bash", "sh", "zsh", "systemd", "env", "python", "python3"
        }

        self.shuffle_timer: QTimer = QTimer(self)
        self.shuffle_timer.setSingleShot(True)
        self.shuffle_timer.timeout.connect(self.execute_shuffle)

        self.hotkey_emitter: HotkeyEmitter = HotkeyEmitter()
        self.hotkey_emitter.remove_triggered.connect(self.remove_current_game)
        self.hotkey_listener: Optional[keyboard.GlobalHotKeys] = None
        self.init_hotkeys()

        self.detect_backend()
        self.init_ui()
        self.refresh_process_list()

    def detect_backend(self) -> None:
        xdg_type: str = os.environ.get("XDG_SESSION_TYPE", "").lower()
        desktop: str = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()

        if "wayland" in xdg_type:
            if "kde" in desktop:
                self.backend = "kde_wayland"
            elif "gnome" in desktop:
                self.backend = "gnome_wayland"
            else:
                self.backend = "generic_wayland"
        else:
            self.backend = "x11"

    def init_ui(self) -> None:
        central_widget: QWidget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout: QVBoxLayout = QVBoxLayout(central_widget)

        control_layout: QHBoxLayout = QHBoxLayout()
        
        self.min_label: QLabel = QLabel("Min Sec:")
        self.min_spin: QSpinBox = QSpinBox()
        self.min_spin.setRange(1, 3600)
        self.min_spin.setValue(10)

        self.max_label: QLabel = QLabel("Max Sec:")
        self.max_spin: QSpinBox = QSpinBox()
        self.max_spin.setRange(1, 3600)
        self.max_spin.setValue(30)

        self.refresh_btn: QPushButton = QPushButton("Refresh Emulator List")
        self.refresh_btn.clicked.connect(self.refresh_process_list)

        control_layout.addWidget(self.min_label)
        control_layout.addWidget(self.min_spin)
        control_layout.addWidget(self.max_label)
        control_layout.addWidget(self.max_spin)
        control_layout.addStretch()
        control_layout.addWidget(self.refresh_btn)

        self.table: QTableWidget = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["PID", "Emulator Name", "Execution Path / Command"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)

        action_layout: QHBoxLayout = QHBoxLayout()
        self.start_btn: QPushButton = QPushButton("Start Shuffler")
        self.start_btn.clicked.connect(self.toggle_shuffler)
        self.status_label: QLabel = QLabel(f"Backend: {self.backend.upper()} | Status: Stopped")
        self.status_label.setStyleSheet("font-weight: bold; color: gray;")

        action_layout.addWidget(self.start_btn)
        action_layout.addWidget(self.status_label)
        action_layout.addStretch()

        help_label: QLabel = QLabel("Hotkey: [Delete] Remove Active Emulator & Shuffle Immediately")
        help_label.setStyleSheet("color: #666; font-size: 11px;")

        main_layout.addLayout(control_layout)
        main_layout.addWidget(self.table)
        main_layout.addLayout(action_layout)
        main_layout.addWidget(help_label)

    def is_emulator_process(self, proc_name: str, exec_base: str, cmd_str: str, exe_path: str) -> bool:
        target_str: str = f"{proc_name} {exec_base} {cmd_str} {exe_path}".lower()
        for kw in self.emulator_keywords:
            if kw in target_str:
                return True
        return False

    def get_window_pids(self) -> Set[int]:
        """Query display server to get PIDs owning an active graphical window."""
        window_pids: Set[int] = set()

        try:
            output: str = subprocess.check_output(["wmctrl", "-lp"], stderr=subprocess.DEVNULL).decode("utf-8")
            for line in output.splitlines():
                parts: List[str] = line.split()
                if len(parts) >= 3:
                    w_pid: int = int(parts[2])
                    if w_pid > 0:
                        window_pids.add(w_pid)
        except Exception:
            pass

        return window_pids

    def get_emulators(self) -> Dict[int, Tuple[str, str]]:
        processes: Dict[int, Tuple[str, str]] = {}
        window_pids: Set[int] = self.get_window_pids()

        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'uids', 'exe']):
            try:
                if proc.info['uids'].real != os.getuid():
                    continue

                pid: int = proc.info['pid']
                name: str = proc.info['name'] or ""
                cmdline: List[str] = proc.info['cmdline'] or []
                exe: str = proc.info['exe'] or ""

                cmd_str: str = " ".join(cmdline)
                exec_base: str = os.path.basename(cmdline[0]).lower() if cmdline else ""
                proc_name_lower: str = name.lower()

                # 1. Skip generic shells unless they contain an emulator target
                if (exec_base in self.wrapper_binaries or proc_name_lower in self.wrapper_binaries) and not self.is_emulator_process(name, exec_base, cmd_str, exe):
                    continue

                # 2. Check if process matches emulator signatures
                if self.is_emulator_process(name, exec_base, cmd_str, exe):
                    # Special check for RMG / Flatpak / AppImage processes where window PID may be 0
                    is_rmg = "rmg" in proc_name_lower or "rmg" in exec_base or "rosalie" in cmd_str.lower()
                    
                    if window_pids and pid not in window_pids and not is_rmg:
                        continue

                    display_name: str = name if name else exec_base
                    display_cmd: str = cmd_str[:60] if cmd_str else exe
                    processes[pid] = (display_name, display_cmd)

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        return processes

    def refresh_process_list(self) -> None:
        if self.is_running:
            return

        self.table.setRowCount(0)
        procs: Dict[int, Tuple[str, str]] = self.get_emulators()

        row: int = 0
        for pid, (name, title) in procs.items():
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(pid)))
            self.table.setItem(row, 1, QTableWidgetItem(name))
            self.table.setItem(row, 2, QTableWidgetItem(title))
            row += 1

    def update_table_highlights(self) -> None:
        green_bg: QColor = QColor("#d4edda")
        green_fg: QColor = QColor("#155724")
        
        is_dark: bool = self.palette().window().color().lightness() < 128
        default_fg: QColor = QColor("#ffffff") if is_dark else QColor("#000000")

        bold_font: QFont = QFont()
        bold_font.setBold(True)

        normal_font: QFont = QFont()
        normal_font.setBold(False)

        for row in range(self.table.rowCount()):
            pid_item: Optional[QTableWidgetItem] = self.table.item(row, 0)
            if not pid_item:
                continue

            pid: int = int(pid_item.text())
            in_rotation: bool = pid in self.selected_pids

            for col in range(self.table.columnCount()):
                item: Optional[QTableWidgetItem] = self.table.item(row, col)
                if item:
                    if in_rotation:
                        item.setBackground(green_bg)
                        item.setForeground(green_fg)
                        item.setFont(bold_font)
                    else:
                        item.setBackground(QColor(0, 0, 0, 0))
                        item.setForeground(default_fg)
                        item.setFont(normal_font)

    def focus_and_minimize_others(self, pid: int) -> None:
        try:
            # Check if current target process is RMG
            proc = psutil.Process(pid)
            proc_info = f"{proc.name()} {' '.join(proc.cmdline())}".lower()
            is_rmg = "rmg" in proc_info or "rosalie" in proc_info

            if self.backend == "x11":
                output: str = subprocess.check_output(["wmctrl", "-lp"]).decode("utf-8")
                for line in output.splitlines():
                    parts: List[str] = line.split()
                    if len(parts) >= 3:
                        win_id: str = parts[0]
                        win_pid: int = int(parts[2])
                        
                        # Match either by exact PID or window title fallback for RMG
                        if win_pid == pid or (is_rmg and ("rmg" in line.lower() or "mupen64" in line.lower())):
                            subprocess.run(["wmctrl", "-i", "-a", win_id])
                        elif win_pid in self.selected_pids:
                            subprocess.run(["wmctrl", "-i", "-r", win_id, "-b", "add,hidden"])

            elif self.backend == "kde_wayland":
                other_pids: List[int] = [p for p in self.selected_pids if p != pid]
                
                kwin_script: str = f"""
                var targetPid = {pid};
                var otherPids = {other_pids};
                var isRmg = {str(is_rmg).lower()};
                var windows = workspace.windowList();

                for (var i = 0; i < windows.length; i++) {{
                    var w = windows[i];
                    var caption = (w.caption || "").toLowerCase();
                    var resClass = (w.resourceClass || "").toLowerCase();
                    
                    var isTarget = (w.pid == targetPid) || (isRmg && (caption.indexOf("rmg") !== -1 || resClass.indexOf("rmg") !== -1));

                    if (isTarget) {{
                        w.minimized = false;
                        workspace.activeWindow = w;
                    }} else if (otherPids.indexOf(w.pid) !== -1) {{
                        w.minimized = true;
                    }}
                }}
                """
                
                script_path: str = "/tmp/shuffler_focus_emulator.js"
                with open(script_path, "w") as f:
                    f.write(kwin_script)

                subprocess.run([
                    "dbus-send", "--session", "--dest=org.kde.KWin",
                    "--type=method_call", "/Scripting",
                    "org.kde.kwin.Scripting.loadScript",
                    f"string:{script_path}"
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                subprocess.run([
                    "dbus-send", "--session", "--dest=org.kde.KWin",
                    "--type=method_call", "/Scripting",
                    "org.kde.kwin.Scripting.start"
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        except Exception:
            pass

    def toggle_shuffler(self) -> None:
        if not self.is_running:
            selected_rows: List[Any] = self.table.selectionModel().selectedRows()
            if not selected_rows:
                QMessageBox.warning(self, "Warning", "Please select at least one emulator process to shuffle.")
                return

            if self.min_spin.value() > self.max_spin.value():
                QMessageBox.warning(self, "Warning", "Min seconds cannot be greater than Max seconds.")
                return

            self.selected_pids = [int(self.table.item(row.row(), 0).text()) for row in selected_rows]

            self.is_running = True
            self.start_btn.setText("Stop Shuffler")
            self.refresh_btn.setEnabled(False)
            self.status_label.setText(f"Backend: {self.backend.upper()} | Running ({len(self.selected_pids)} active)")
            self.status_label.setStyleSheet("font-weight: bold; color: green;")
            
            self.table.clearSelection()
            self.update_table_highlights()
            self.execute_shuffle()
        else:
            self.stop_shuffler()

    def stop_shuffler(self) -> None:
        self.shuffle_timer.stop()
        self.is_running = False

        self.selected_pids.clear()
        self.current_pid = None
        self.start_btn.setText("Start Shuffler")
        self.refresh_btn.setEnabled(True)
        self.status_label.setText(f"Backend: {self.backend.upper()} | Stopped")
        self.status_label.setStyleSheet("font-weight: bold; color: gray;")

        self.update_table_highlights()

    def execute_shuffle(self) -> None:
        if not self.is_running or not self.selected_pids:
            return

        candidates: List[int] = [p for p in self.selected_pids if p != self.current_pid]
        if not candidates:
            candidates = self.selected_pids

        self.current_pid = random.choice(candidates)
        self.focus_and_minimize_others(self.current_pid)

        delay: int = random.randint(self.min_spin.value(), self.max_spin.value()) * 1000
        self.shuffle_timer.start(delay)

    def remove_current_game(self) -> None:
        current_time: float = time.time()
        
        if current_time - self.last_remove_time < 0.8:
            return

        self.last_remove_time = current_time

        if not self.is_running or self.current_pid is None:
            return

        if self.current_pid in self.selected_pids:
            self.selected_pids.remove(self.current_pid)
            self.current_pid = None
            
            self.shuffle_timer.stop()
            self.update_table_highlights()

            if len(self.selected_pids) == 0:
                self.stop_shuffler()
                QMessageBox.information(self, "Shuffler Stopped", "All emulator targets removed from pool.")
            else:
                self.status_label.setText(f"Backend: {self.backend.upper()} | Running ({len(self.selected_pids)} active)")
                self.execute_shuffle()

    def init_hotkeys(self) -> None:
        def on_remove() -> None:
            self.hotkey_emitter.remove_triggered.emit()

        self.hotkey_listener = keyboard.GlobalHotKeys({
            '<delete>': on_remove
        })
        self.hotkey_listener.start()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.is_running:
            self.stop_shuffler()
        if self.hotkey_listener:
            self.hotkey_listener.stop()
        event.accept()


if __name__ == "__main__":
    app: QApplication = QApplication(sys.argv)
    window: LinuxGameShuffler = LinuxGameShuffler()
    window.show()
    sys.exit(app.exec())
