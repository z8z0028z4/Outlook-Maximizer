"""Entry point for the Windows Outlook companion."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


def _single_instance(data_dir: Path):
    if os.name != "nt":
        return None, False
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    kernel.CreateMutexW.restype = ctypes.c_void_p
    key = hashlib.sha256(str(data_dir.resolve()).casefold().encode("utf-8")).hexdigest()[:24]
    handle = kernel.CreateMutexW(None, False, f"Local\\OutlookMaximizer-{key}")
    if not handle:
        raise OSError("Unable to create the application lock.")
    return handle, ctypes.get_last_error() == 183


def _startup_error_message(language: str, error_type: str, *, frozen: bool) -> str:
    if frozen:
        return (f"Startup failed ({error_type}). Reopen the app; if the problem persists, contact your IT support."
                if language == "en" else f"啟動失敗（{error_type}）。請重新開啟程式；若問題持續，請聯絡資訊支援人員。")
    return (f"Startup failed ({error_type}). Check that Python 3.12 is installed and use start.cmd to view startup status."
            if language == "en" else f"啟動失敗（{error_type}）。請確認使用 Python 3.12，並以 start.cmd 查看啟動狀態。")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Classic Outlook AI companion with Google Calendar API")
    parser.add_argument("--demo", action="store_true", help="show synthetic email without Outlook, AI or Calendar calls")
    parser.add_argument("--smoke-test", action="store_true", help="render the demo UI and exit automatically")
    parser.add_argument("--diagnose-outlook", action="store_true", help="report connection/counts only, without email content")
    parser.add_argument("--data-dir", type=Path, help="local application settings directory")
    args = parser.parse_args(argv)
    if args.diagnose_outlook:
        from outlook_maximizer.outlook_monitor import diagnose_outlook
        result = diagnose_outlook()
        print(json.dumps(result, ensure_ascii=True))
        return 0 if result.get("status") == "attached_existing_instance" else 1
    from PyQt6.QtCore import QTimer
    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import QApplication, QMessageBox
    from outlook_maximizer.settings import Settings, default_data_dir
    from outlook_maximizer.i18n import tr
    from outlook_maximizer.ui import CompanionApp
    data_dir = (args.data_dir or default_data_dir()).resolve()
    language = Settings.load(data_dir).ui_language
    title = tr("Outlook 摘要助手", language)
    qt_app = QApplication.instance() or QApplication([sys.argv[0]])
    qt_app.setApplicationName(title)
    qt_app.setFont(QFont("Microsoft JhengHei UI", 10))
    handle = None
    failures = []
    app = None
    original_excepthook = sys.excepthook
    try:
        if not args.smoke_test:
            handle, duplicate = _single_instance(data_dir)
            if duplicate:
                QMessageBox.information(None, title, tr("工具已經開啟，請使用系統匣的摘要助手圖示。", language))
                return 0
        def callback_error(kind, value, traceback):
            failures.append(kind.__name__)
            if not args.smoke_test:
                message = (f"A UI error occurred ({kind.__name__}). Quit from the system tray and reopen the app."
                           if language == "en" else f"介面發生錯誤（{kind.__name__}），請從系統匣結束並重新開啟工具。")
                QMessageBox.critical(None, title, message)
            if args.smoke_test:
                qt_app.quit()
        sys.excepthook = callback_error
        app = CompanionApp(qt_app, data_dir, demo=args.demo or args.smoke_test, tray=not args.smoke_test)
        qt_app.setWindowIcon(app.tray.icon())
        if args.smoke_test:
            QTimer.singleShot(1000, app.close)
        qt_app.exec()
        if args.smoke_test:
            print(json.dumps({"status": "ok" if not failures else "error", "demo_rendered": True, "callback_errors": failures}))
        return 1 if failures else 0
    except Exception as exc:
        if args.smoke_test:
            print(json.dumps({"status": "error", "error_type": type(exc).__name__}))
        else:
            message = _startup_error_message(language, type(exc).__name__, frozen=bool(getattr(sys, "frozen", False)))
            QMessageBox.critical(None, tr("無法啟動", language), message)
        return 1
    finally:
        if app and not app.closed:
            app.close()
        sys.excepthook = original_excepthook
        if handle:
            import ctypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
            kernel.CloseHandle(handle)


if __name__ == "__main__":
    raise SystemExit(main())
