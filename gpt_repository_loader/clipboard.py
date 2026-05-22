"""Cross-platform clipboard integration (issues #25 and #29).

The clipboard module is intentionally permissive: every backend is probed and
the first that works is used. Supported backends are:

* :mod:`pyperclip` when installed (the recommended path - works on Windows,
  macOS and Linux when one of ``xclip``, ``xsel`` or ``wl-copy`` is available).
* ``pbcopy`` on macOS.
* ``xclip`` and ``xsel`` on Linux.
* ``wl-copy`` on Wayland.
* ``clip.exe`` on Windows.

When none of these can be reached :func:`copy_to_clipboard` raises
:class:`ClipboardUnavailableError`. Callers are expected to either catch the
exception (the CLI does, and prints a helpful message) or to gate the
clipboard call on :func:`clipboard_available`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Callable, Iterable, Optional, Tuple


class ClipboardUnavailableError(RuntimeError):
    """Raised when no working clipboard backend is found."""


def _try_pyperclip() -> Optional[Callable[[str], None]]:
    try:
        import pyperclip  # type: ignore
    except Exception:
        return None

    def _copy(text: str) -> None:
        pyperclip.copy(text)

    try:
        # Probe by copying an empty string. pyperclip raises if no backend is
        # available on the host system.
        pyperclip.copy("")
    except Exception:
        return None
    return _copy


def _try_subprocess(cmd: Iterable[str]) -> Optional[Callable[[str], None]]:
    cmd = list(cmd)
    if not shutil.which(cmd[0]):
        return None

    def _copy(text: str) -> None:
        proc = subprocess.run(
            cmd,
            input=text,
            text=True,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if proc.returncode != 0:
            raise ClipboardUnavailableError(
                f"{cmd[0]} exited with status {proc.returncode}: {proc.stderr.strip()}"
            )

    return _copy


def _discover_backend() -> Tuple[str, Callable[[str], None]]:
    pyperclip_copy = _try_pyperclip()
    if pyperclip_copy is not None:
        return "pyperclip", pyperclip_copy
    if sys.platform == "darwin":
        copy = _try_subprocess(["pbcopy"])
        if copy is not None:
            return "pbcopy", copy
    if sys.platform.startswith("linux"):
        # Prefer wl-copy on Wayland sessions.
        if os.environ.get("WAYLAND_DISPLAY"):
            copy = _try_subprocess(["wl-copy"])
            if copy is not None:
                return "wl-copy", copy
        copy = _try_subprocess(["xclip", "-selection", "clipboard"])
        if copy is not None:
            return "xclip", copy
        copy = _try_subprocess(["xsel", "--clipboard", "--input"])
        if copy is not None:
            return "xsel", copy
    if sys.platform == "win32":
        copy = _try_subprocess(["clip.exe"])
        if copy is not None:
            return "clip.exe", copy
    raise ClipboardUnavailableError(
        "No working clipboard backend found. Install pyperclip or one of "
        "pbcopy/xclip/xsel/wl-copy/clip.exe to enable --clipboard."
    )


def clipboard_available() -> bool:
    try:
        _discover_backend()
        return True
    except ClipboardUnavailableError:
        return False


def copy_to_clipboard(text: str) -> str:
    """Copy ``text`` to the system clipboard. Returns the backend name used."""
    backend, copy = _discover_backend()
    copy(text)
    return backend
