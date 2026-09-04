"""Freezing emulators that are not the active rotation target.

Some emulators have no "pause when unfocused" setting -- Cemu is the usual
example -- so minimizing their window is not enough to stop the game advancing.
Sending SIGSTOP does stop it, on any emulator, without needing its cooperation.

Latency matters here. Every millisecond between the shuffle firing and the
outgoing game freezing is a frame that plays with nobody watching, which in a
fast game is the difference between coming back on the track and coming back in
a wall. The switch therefore freezes the outgoing game first and shares a single
scan of /proc across the whole operation.
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Container, Iterable

import psutil

log = logging.getLogger(__name__)

ChildMap = dict[int, list[int]]


def _process_or_none(pid: int) -> psutil.Process | None:
    """psutil.Process(pid), or None if it has already exited."""
    try:
        return psutil.Process(pid)
    except psutil.Error:
        return None


class ProcessSuspender:
    """Suspends and resumes emulator process trees, tracking what it froze.

    Every frozen PID is remembered so it can be thawed again on any exit path. A
    process left suspended is indistinguishable from a hung one, so resume_all()
    must run whenever the rotation stops.
    """

    def __init__(self) -> None:
        self.suspended_pids: set[int] = set()
        self.active_pid: int | None = None

    def children_map(self) -> ChildMap:
        """Parent PID -> child PIDs, from a single scan of /proc.

        psutil's children(recursive=True) rescans every process on each call, and
        building a psutil.Process per entry costs about three times what reading
        the stat files directly does. Both are on the path between the shuffle
        firing and the outgoing game freezing, so both are worth avoiding.
        """
        children: ChildMap = {}

        for entry in os.scandir("/proc"):
            if not entry.name.isdigit():
                continue

            try:
                with open(f"/proc/{entry.name}/stat", "rb") as stat:
                    fields = stat.read()
            except OSError:
                continue  # Exited between the scan and the read.

            # comm (field 2) is parenthesised and may itself contain spaces or a
            # ")", so ppid is the second field after the *final* ")".
            try:
                parent = int(fields[fields.rindex(b")") + 2:].split(b" ", 2)[1])
            except (ValueError, IndexError):
                continue

            children.setdefault(parent, []).append(int(entry.name))

        return children

    def process_tree(self, pid: int, children: ChildMap | None = None) -> list[psutil.Process]:
        """Return the process and its descendants, parent first.

        The selected PID is often a Flatpak wrapper (bwrap -> Cemu-wrapper -> cemu),
        so the emulator itself is a descendant rather than the PID being held.
        """
        if children is None:
            children = self.children_map()

        pids = [pid]
        pending = [pid]
        while pending:
            for child in children.get(pending.pop(), ()):
                pids.append(child)
                pending.append(child)

        return [proc for proc in map(_process_or_none, pids) if proc is not None]

    def suspend_tree(
        self,
        pid: int,
        keep: Container[int] = frozenset(),
        children: ChildMap | None = None,
    ) -> None:
        """Freeze one process tree, skipping any PID in `keep`."""
        # Parent first, so a wrapper cannot spawn children that are missed.
        for proc in self.process_tree(pid, children):
            if proc.pid in keep:
                continue
            try:
                proc.suspend()
            except psutil.Error as exc:
                log.debug("could not suspend %s: %s", proc.pid, exc)
                continue
            self.suspended_pids.add(proc.pid)

    def resume_tree(self, pid: int, children: ChildMap | None = None) -> None:
        """Thaw one target, children first so the parent sees them running."""
        for proc in reversed(self.process_tree(pid, children)):
            with contextlib.suppress(psutil.Error):
                proc.resume()
            self.suspended_pids.discard(proc.pid)

    def switch_to(self, pids: Iterable[int], active_pid: int) -> None:
        """Freeze every game in `pids` except `active_pid`, and thaw that one.

        The game that was on screen is frozen before anything else happens: it is
        the one the player has just stopped watching, so it is the one that must
        not keep playing.
        """
        pool = list(pids)
        children = self.children_map()
        # Never freeze a PID that also belongs to the active target's tree.
        keep = {proc.pid for proc in self.process_tree(active_pid, children)}

        outgoing = self.active_pid
        order = [outgoing] if outgoing is not None and outgoing in pool else []
        order += [pid for pid in pool if pid not in order]

        for pid in order:
            if pid != active_pid:
                self.suspend_tree(pid, keep, children)

        self.resume_tree(active_pid, children)
        self.active_pid = active_pid

    def resume_all(self) -> None:
        """Thaw everything that was frozen. Safe to call when nothing is frozen."""
        for pid in list(self.suspended_pids):
            with contextlib.suppress(psutil.Error):
                psutil.Process(pid).resume()
            self.suspended_pids.discard(pid)
        self.active_pid = None
