"""Finding the emulator processes that are candidates for the rotation."""

from __future__ import annotations

import logging
import os
import subprocess

import psutil

log = logging.getLogger(__name__)

# Names that identify an emulator. Matched as substrings of a process's identity,
# so "cemu" also covers "Cemu_relwithdebinfo" and "org.ppsspp.PPSSPP".
EMULATOR_KEYWORDS: frozenset[str] = frozenset({
    "rmg", "rosalie", "mupen", "mupen64", "mupen64plus", "mupen64plus-qt",
    "mupen64plus-gui", "mupen64plus-fz", "rmupen", "ares", "com.github.rosalie241.rmg",
    "dolphin-emu", "dolphin", "cemu", "yuzu", "suyu", "ryujinx", "sudachi",
    "duckstation", "pcsx2", "pcsx2-qt", "rpcs3", "ppsspp", "mednafen", "epsxe",
    "mgba", "vbam", "visualboyadvance", "melonds", "desmume", "citra", "azahar",
    "retroarch", "bizhawk", "pico8", "blastem", "flycast",
})

# A process linking one of these is a real display client rather than a wrapper.
DISPLAY_LIBRARIES: tuple[str, ...] = ("libwayland-client", "libX11.so", "libxcb.so")

# Desktop applications whose binary name collides with a keyword. Matched exactly,
# so the Dolphin emulator (dolphin-emu, Dolphin_Emulator-*.AppImage) is still found
# while KDE's file manager is not.
EXCLUDED_BINARIES: frozenset[str] = frozenset({"dolphin"})

# Binaries named after themselves rather than after what they run. They are ignored
# unless their arguments name an emulator, and they are the only processes whose
# command line is worth scanning for one.
WRAPPER_BINARIES: frozenset[str] = frozenset({
    "bash", "sh", "zsh", "systemd", "env", "python", "python3",
    "mono", "java", "wine", "wine64", "dotnet",
})

# How much of the command line to show in the table.
COMMAND_DISPLAY_LENGTH = 60


def is_rmg(name: str, exec_base: str, cmd_str: str) -> bool:
    """Rosalie's Mupen GUI, which needs special handling throughout.

    It spawns an isolated process tree and reports PID 0 for its window under
    Xwayland, so it cannot be matched on window ownership like other emulators.
    """
    return "rmg" in name.lower() or "rmg" in exec_base.lower() or "rosalie" in cmd_str.lower()


class EmulatorDetector:
    """Scans running processes for emulators that own a graphical window."""

    def __init__(
        self,
        keywords: frozenset[str] = EMULATOR_KEYWORDS,
        display_libraries: tuple[str, ...] = DISPLAY_LIBRARIES,
        excluded_binaries: frozenset[str] = EXCLUDED_BINARIES,
        wrapper_binaries: frozenset[str] = WRAPPER_BINARIES,
    ) -> None:
        self.keywords = keywords
        self.display_libraries = display_libraries
        self.excluded_binaries = excluded_binaries
        self.wrapper_binaries = wrapper_binaries
        self._missing_wmctrl_logged = False

    def is_emulator(self, name: str, exec_base: str, cmd_str: str, exe_path: str) -> bool:
        """Match on what a process *is*, not on every path it happens to mention.

        Scanning the whole command line treats any process that merely names an
        emulator in an argument as an emulator: a KIO worker carries the requesting
        application's name in its socket argument, and a ROM path carries the
        console's name.
        """
        if name.lower() in self.excluded_binaries or exec_base in self.excluded_binaries:
            return False

        identity = f"{name} {exec_base} {os.path.basename(exe_path)}".lower()
        if any(keyword in identity for keyword in self.keywords):
            return True

        # An interpreter is named after itself, so the emulator it was asked to run
        # can only be identified from its arguments (mono BizHawk/EmuHawk.exe).
        if exec_base in self.wrapper_binaries or name.lower() in self.wrapper_binaries:
            command = cmd_str.lower()
            return any(keyword in command for keyword in self.keywords)

        return False

    def window_pids(self) -> set[int]:
        """PIDs the display server reports as owning a window.

        Only X11 and Xwayland windows appear here; native Wayland clients do not,
        which is why links_display_library() exists.
        """
        try:
            output = subprocess.check_output(
                ["wmctrl", "-lp"], stderr=subprocess.DEVNULL, text=True
            )
        except FileNotFoundError:
            if not self._missing_wmctrl_logged:
                log.warning("wmctrl not found; falling back to display-library detection")
                self._missing_wmctrl_logged = True
            return set()
        except (subprocess.CalledProcessError, OSError) as exc:
            log.debug("wmctrl failed: %s", exc)
            return set()

        pids: set[int] = set()
        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            try:
                pid = int(parts[2])
            except ValueError:
                continue
            if pid > 0:
                pids.add(pid)
        return pids

    def links_display_library(self, pid: int) -> bool:
        """Does this PID map a display-client library?

        wmctrl reports only X11/Xwayland windows, so on Wayland it cannot see
        native clients such as Flatpak Cemu. A mapped display library identifies
        real GUI processes on either display server, and rejects the wrappers
        (bwrap, Cemu-wrapper) that surround a Flatpak emulator.
        """
        try:
            with open(f"/proc/{pid}/maps") as handle:
                mapped = handle.read()
        except OSError:
            return True  # Unreadable: show the process rather than hide it

        return any(library in mapped for library in self.display_libraries)

    def collapse_helpers(
        self, candidates: dict[int, tuple[str, str]]
    ) -> dict[int, tuple[str, str]]:
        """Drop candidates that descend from another candidate.

        Multi-process emulators would otherwise list one row per helper. The
        top-most process is also the correct shuffle target, since suspending its
        tree freezes the helpers along with it.
        """
        kept: dict[int, tuple[str, str]] = {}
        for pid, info in candidates.items():
            try:
                ancestors = {parent.pid for parent in psutil.Process(pid).parents()}
            except psutil.Error:
                ancestors = set()

            if not ancestors & candidates.keys():
                kept[pid] = info
        return kept

    def find(self) -> dict[int, tuple[str, str]]:
        """Map every detected emulator PID to its display name and command."""
        candidates: dict[int, tuple[str, str]] = {}
        window_pids = self.window_pids()
        own_uid = os.getuid()

        for proc in psutil.process_iter(["pid", "name", "cmdline", "uids", "exe"]):
            try:
                if proc.info["uids"].real != own_uid:
                    continue

                pid = proc.info["pid"]
                name = proc.info["name"] or ""
                cmdline = proc.info["cmdline"] or []
                exe = proc.info["exe"] or ""

                cmd_str = " ".join(cmdline)
                exec_base = os.path.basename(cmdline[0]).lower() if cmdline else ""

                if not self.is_emulator(name, exec_base, cmd_str, exe):
                    continue

                # Keep a process if the display server reports a window for it, or
                # if it links a display library. The second test is what finds the
                # native Wayland clients wmctrl cannot enumerate. RMG reports no
                # usable window PID at all, so it bypasses both.
                if (
                    pid not in window_pids
                    and not is_rmg(name, exec_base, cmd_str)
                    and not self.links_display_library(pid)
                ):
                    continue

                candidates[pid] = (
                    name or exec_base,
                    cmd_str[:COMMAND_DISPLAY_LENGTH] if cmd_str else exe,
                )

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        return self.collapse_helpers(candidates)
