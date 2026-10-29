"""Small, content-free Windows foreground and stacking queries, independent of COM."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any


_OUTLOOK_CLASS = "rctrl_renwnd32"
_SHELL_CLASSES = {"shell_traywnd", "shell_secondarytraywnd", "notifyiconoverflowwindow", "#32768"}
_GA_ROOTOWNER = 3
_GW_HWNDNEXT = 2
_SW_SHOWNOACTIVATE = 4
_RAISE_FLAGS = 0x0001 | 0x0002 | 0x0010 | 0x0200  # NOSIZE | NOMOVE | NOACTIVATE | NOOWNERZORDER
_MAX_WINDOWS = 4096


@dataclass(frozen=True)
class ForegroundWindow:
    hwnd: int = 0
    kind: str = "unknown"


def _win32() -> tuple[Any, Any, type[Exception]] | None:
    if os.name != "nt":
        return None
    try:
        import pywintypes
        import win32gui
        import win32process
    except ImportError:
        return None
    return win32gui, win32process, pywintypes.error


def _transient_class(name: str) -> bool:
    return name in _SHELL_CLASSES or (name.startswith("qt") and ("qwindowpopup" in name or "qwindowtooltip" in name))


def foreground_window() -> ForegroundWindow:
    """Identify the active surface without reading titles, contents or process paths."""
    native = _win32()
    if native is None:
        return ForegroundWindow()
    gui, process, native_error = native
    try:
        hwnd = gui.GetForegroundWindow()
        if not hwnd or not gui.IsWindow(hwnd):
            return ForegroundWindow()
        name = gui.GetClassName(hwnd).casefold()
        if _transient_class(name):
            return ForegroundWindow(hwnd, "shell")
        if name == _OUTLOOK_CLASS:
            return ForegroundWindow(hwnd, "outlook")
        owner = gui.GetAncestor(hwnd, _GA_ROOTOWNER)
        if owner and owner != hwnd and gui.GetClassName(owner).casefold() == _OUTLOOK_CLASS:
            return ForegroundWindow(hwnd, "outlook")
        _, process_id = process.GetWindowThreadProcessId(hwnd)
        return ForegroundWindow(hwnd, "companion" if process_id == os.getpid() else "other")
    except (native_error, OSError, TypeError, ValueError, OverflowError):
        return ForegroundWindow()


def raise_without_activation(hwnd: int) -> bool:
    """Show and raise a requested window without focusing or changing its topmost band."""
    native = _win32()
    if native is None:
        return False
    gui, _process, native_error = native
    try:
        if not hwnd or not gui.IsWindow(hwnd):
            return False
        # Qt's logical visibility can remain true after Windows hides the HWND.
        if not gui.IsWindowVisible(hwnd) or gui.IsIconic(hwnd):
            gui.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)
            if not gui.IsWindowVisible(hwnd) or gui.IsIconic(hwnd):
                return False
        result = gui.SetWindowPos(hwnd, 0, 0, 0, 0, 0, _RAISE_FLAGS)  # HWND_TOP
        # pywin32 returns None on success and raises on failure.
        return result is None or bool(result)
    except (native_error, OSError, TypeError, ValueError, OverflowError):
        return False


def is_native_window_visible(hwnd: int) -> bool | None:
    """Check native visibility; return None when the platform cannot be queried."""
    native = _win32()
    if native is None:
        return None
    gui, _process, native_error = native
    try:
        return bool(hwnd and gui.IsWindow(hwnd) and gui.IsWindowVisible(hwnd) and not gui.IsIconic(hwnd))
    except (native_error, OSError, TypeError, ValueError, OverflowError):
        return None


def is_window_above(hwnd: int, reference: int) -> bool:
    """Conservatively compare two visible desktop windows, never their contents."""
    native = _win32()
    if native is None:
        return False
    gui, _process, native_error = native
    try:
        if not all(window and gui.IsWindow(window) and gui.IsWindowVisible(window)
                   and not gui.IsIconic(window) for window in (hwnd, reference)):
            return False
        if hwnd == reference:
            return True
        current, first = gui.GetTopWindow(0), 0
        seen: set[int] = set()
        for _ in range(_MAX_WINDOWS):
            if not current or current in seen:
                return False
            seen.add(current)
            if current in (hwnd, reference):
                if first:
                    return first == hwnd
                first = current
            current = gui.GetWindow(current, _GW_HWNDNEXT)
        return False
    except (native_error, OSError, TypeError, ValueError, OverflowError):
        return False
