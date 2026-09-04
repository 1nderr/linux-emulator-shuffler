"""Freezing emulators that are not the active rotation target.

Some emulators have no "pause when unfocused" setting -- Cemu is the usual
example -- so minimizing their window is not enough to stop the game advancing.
Sending SIGSTOP does stop it, on any emulator, without needing its cooperation.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Container, Iterable

import psutil

log = logging.getLogger(__name__)


class ProcessSuspender:
    """Suspends and resumes emulator process trees, tracking what it froze.

    Every frozen PID is remembered so it can be thawed again on any exit path. A
    process left suspended is indistinguishable from a hung one, so resume_all()
    must run whenever the rotation stops.
    """

    def __init__(self) -> None:
        self.suspended_pids: set[int] = set()

    def process_tree(self, pid: int) -> list[psutil.Process]:
        """Return the process and its descendants, parent first.

        The selected PID is often a Flatpak wrapper (bwrap -> Cemu-wrapper -> cemu),
        so the emulator itself is a descendant rather than the PID being held.
        """
        try:
            proc = psutil.Process(pid)
        except psutil.Error:
            return []

        try:
            return [proc, *proc.children(recursive=True)]
        except psutil.Error:
            return [proc]

    def suspend_tree(self, pid: int, keep: Container[int] = frozenset()) -> None:
        """Freeze one process tree, skipping any PID in `keep`."""
        # Parent first, so a wrapper cannot spawn children that are missed.
        for proc in self.process_tree(pid):
            if proc.pid in keep:
                continue
            try:
                proc.suspend()
            except psutil.Error as exc:
                log.debug("could not suspend %s: %s", proc.pid, exc)
                continue
            self.suspended_pids.add(proc.pid)

    def suspend_all_except(self, pids: Iterable[int], active_pid: int) -> None:
        """Freeze every tree in `pids` other than the active target's."""
        # Never freeze a PID that also belongs to the active target's tree.
        keep = {proc.pid for proc in self.process_tree(active_pid)}

        for pid in pids:
            if pid != active_pid:
                self.suspend_tree(pid, keep)

    def resume_tree(self, pid: int) -> None:
        """Thaw one target, children first so the parent sees them running."""
        for proc in reversed(self.process_tree(pid)):
            with contextlib.suppress(psutil.Error):
                proc.resume()
            self.suspended_pids.discard(proc.pid)

    def resume_all(self) -> None:
        """Thaw everything that was frozen. Safe to call when nothing is frozen."""
        for pid in list(self.suspended_pids):
            with contextlib.suppress(psutil.Error):
                psutil.Process(pid).resume()
            self.suspended_pids.discard(pid)
