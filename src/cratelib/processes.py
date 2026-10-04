"""Whether a DJ program is running, so its library isn't changed under it."""

from __future__ import annotations

import os

import psutil


def running(*names: str) -> bool:
    """Whether a process whose name starts with one of ``names`` is running.

    Names are compared without case or a ``.exe`` suffix, so programs running under
    Wine count too.
    """
    wanted = tuple(n.lower() for n in names)
    for proc in psutil.process_iter(["name"]):
        name = os.path.splitext(proc.info["name"] or "")[0].lower()
        if name.startswith(wanted):
            return True
    return False


def mixxx_running() -> bool:
    return running("mixxx")


def serato_running() -> bool:
    return running("serato dj")
