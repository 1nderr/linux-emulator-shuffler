"""Backend-specific window focusing: wmctrl on X11, KWin scripting on KDE Wayland."""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import tempfile

import psutil

from .detection import is_rmg

log = logging.getLogger(__name__)

X11 = "x11"
KDE_WAYLAND = "kde_wayland"
GNOME_WAYLAND = "gnome_wayland"
GENERIC_WAYLAND = "generic_wayland"

# Backends that can actually focus a window. The others are detected so the UI can
# report them, but no focusing method is available for them.
SUPPORTED_BACKENDS = frozenset({X11, KDE_WAYLAND})

_KWIN_SCRIPT = """
var targetPid = {pid};
var otherPids = {other_pids};
var isRmg = {is_rmg};
var hideOthers = {hide_others};
var windows = workspace.windowList();

for (var i = 0; i < windows.length; i++) {{
    var w = windows[i];
    var caption = (w.caption || "").toLowerCase();
    var resClass = (w.resourceClass || "").toLowerCase();

    var titleIsRmg = caption.indexOf("rmg") !== -1 || resClass.indexOf("rmg") !== -1;
    var isTarget = (w.pid == targetPid) || (isRmg && titleIsRmg);

    if (isTarget) {{
        // Only unminimize if it actually is minimized: assigning to the property
        // unconditionally triggers KWin's unminimize animation every switch.
        if (w.minimized) {{
            w.minimized = false;
        }}
        if (typeof workspace.raiseWindow === "function") {{
            workspace.raiseWindow(w);
        }}
        workspace.activeWindow = w;
    }} else if (hideOthers && otherPids.indexOf(w.pid) !== -1) {{
        w.minimized = true;
    }}
}}
"""


_KWIN_RESTORE_SCRIPT = """
var pids = {pids};
var windows = workspace.windowList();

for (var i = 0; i < windows.length; i++) {{
    var w = windows[i];
    if (w.minimized && pids.indexOf(w.pid) !== -1) {{
        w.minimized = false;
    }}
}}
"""


def detect_backend() -> str:
    """Identify the display server and desktop from the session environment."""
    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()

    if "wayland" not in session_type:
        return X11
    if "kde" in desktop:
        return KDE_WAYLAND
    if "gnome" in desktop:
        return GNOME_WAYLAND
    return GENERIC_WAYLAND


class WindowManager:
    """Raises the active emulator's window and hides the rest."""

    def __init__(self, backend: str, hide_inactive: bool = False) -> None:
        self.backend = backend
        # Raising the target covers the others without a state change, so nothing
        # animates. Minimizing is only needed when the windows are not the same
        # size and the ones behind would otherwise show around the edges.
        self.hide_inactive = hide_inactive
        # A private, per-process path: the KWin script is rewritten on every
        # shuffle, and a predictable shared /tmp name would be writable by others.
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        self._script_path = os.path.join(runtime_dir, f"emulator-shuffler-{os.getpid()}.js")

    @property
    def can_focus(self) -> bool:
        return self.backend in SUPPORTED_BACKENDS

    def focus(self, pid: int, other_pids: list[int]) -> None:
        """Raise `pid`'s window and hide the windows of `other_pids`."""
        if self.backend == X11:
            self._focus_x11(pid, other_pids)
        elif self.backend == KDE_WAYLAND:
            self._focus_kwin(pid, other_pids)

    def restore(self, pids: list[int]) -> None:
        """Unminimize the given windows.

        Needed when leaving minimize mode: without it the windows minimized under
        the old mode would stay hidden until each next became the active target.
        """
        if not pids:
            return

        if self.backend == X11:
            self._restore_x11(pids)
        elif self.backend == KDE_WAYLAND:
            self._run_kwin_script(_KWIN_RESTORE_SCRIPT.format(pids=list(pids)))

    def _restore_x11(self, pids: list[int]) -> None:
        try:
            output = subprocess.check_output(
                ["wmctrl", "-lp"], stderr=subprocess.DEVNULL, text=True
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            log.warning("cannot list windows with wmctrl: %s", exc)
            return

        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            try:
                window_pid = int(parts[2])
            except ValueError:
                continue
            if window_pid in pids:
                self._run_wmctrl(["-i", "-r", parts[0], "-b", "remove,hidden"])

    def cleanup(self) -> None:
        """Remove the KWin script file written for this process."""
        with contextlib.suppress(OSError):
            os.unlink(self._script_path)

    def _target_is_rmg(self, pid: int) -> bool:
        try:
            proc = psutil.Process(pid)
        except psutil.Error:
            return False

        try:
            # Focusing matches "rmg" anywhere in the name or command line, which is
            # deliberately broader than detection's check: the window-title fallback
            # below must fire however RMG was launched.
            identity = f"{proc.name()} {' '.join(proc.cmdline())}"
        except psutil.Error:
            return False

        return is_rmg(identity, identity, identity)

    def _focus_x11(self, pid: int, other_pids: list[int]) -> None:
        try:
            output = subprocess.check_output(
                ["wmctrl", "-lp"], stderr=subprocess.DEVNULL, text=True
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            log.warning("cannot list windows with wmctrl: %s", exc)
            return

        target_is_rmg = self._target_is_rmg(pid)

        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue

            window_id = parts[0]
            try:
                window_pid = int(parts[2])
            except ValueError:
                continue

            # RMG reports no usable window PID, so fall back to its window title.
            title_matches_rmg = target_is_rmg and (
                "rmg" in line.lower() or "mupen64" in line.lower()
            )

            if window_pid == pid or title_matches_rmg:
                # Clear any minimized state left by a previous run before raising.
                self._run_wmctrl(["-i", "-r", window_id, "-b", "remove,hidden"])
                self._run_wmctrl(["-i", "-a", window_id])
            elif self.hide_inactive and window_pid in other_pids:
                self._run_wmctrl(["-i", "-r", window_id, "-b", "add,hidden"])

    def _run_wmctrl(self, args: list[str]) -> None:
        try:
            subprocess.run(
                ["wmctrl", *args],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError as exc:
            log.warning("wmctrl %s failed: %s", " ".join(args), exc)

    def _focus_kwin(self, pid: int, other_pids: list[int]) -> None:
        self._run_kwin_script(_KWIN_SCRIPT.format(
            pid=pid,
            other_pids=list(other_pids),
            is_rmg=str(self._target_is_rmg(pid)).lower(),
            hide_others=str(self.hide_inactive).lower(),
        ))

    def _run_kwin_script(self, script: str) -> None:
        try:
            with open(self._script_path, "w") as handle:
                handle.write(script)
        except OSError as exc:
            log.warning("cannot write KWin script to %s: %s", self._script_path, exc)
            return

        self._kwin_call("loadScript", f"string:{self._script_path}")
        self._kwin_call("start")

    def _kwin_call(self, method: str, *args: str) -> None:
        command = [
            "dbus-send", "--session", "--dest=org.kde.KWin",
            "--type=method_call", "/Scripting",
            f"org.kde.kwin.Scripting.{method}",
            *args,
        ]
        try:
            subprocess.run(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
            )
        except OSError as exc:
            log.warning("KWin %s call failed: %s", method, exc)
