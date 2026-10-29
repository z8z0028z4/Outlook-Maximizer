"""Verify native popup ordering with offscreen synthetic windows and no app activation."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".local" / "popup-native-verification.json")
    output = parser.parse_args().output
    output.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        result = {"status": "unsupported", "requires": "native Windows desktop"}
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
        return 2

    # Real HWNDs are needed. All fixture windows stay well outside visible screens.
    os.environ["QT_QPA_PLATFORM"] = "windows"
    import win32api
    import win32con
    import win32gui
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QSystemTrayIcon
    from outlook_maximizer.models import Analysis
    from outlook_maximizer.settings import Settings
    from outlook_maximizer.ui import CompanionApp
    from outlook_maximizer.window_focus import ForegroundWindow, is_window_above, raise_without_activation

    qt_app = QApplication.instance() or QApplication([])
    instance = win32api.GetModuleHandle(None)
    window_class = win32gui.WNDCLASS()
    window_class.hInstance = instance
    window_class.lpszClassName = f"OutlookMaximizerPopupVerification-{os.getpid()}"
    window_class.lpfnWndProc = win32gui.DefWindowProc
    atom = win32gui.RegisterClass(window_class)
    fixtures = []
    app = None
    result = {"status": "error", "synthetic_only": True, "actual_native_windows": True}
    phase = "fixture_setup"
    foreground = win32gui.GetForegroundWindow()

    def require(value, message):
        if not value:
            raise AssertionError(message)

    def unchanged_focus():
        require(win32gui.GetForegroundWindow() == foreground, "The desktop foreground changed during verification")

    try:
        for _ in range(2):
            hwnd = win32gui.CreateWindowEx(
                win32con.WS_EX_TOOLWINDOW, atom, "Synthetic popup fixture", win32con.WS_POPUP,
                -30000, -30000, 30, 30, 0, 0, instance, None,
            )
            fixtures.append(hwnd)
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
        source, other = fixtures
        source_context = ForegroundWindow(source, "outlook")
        other_context = ForegroundWindow(other, "other")
        settings = Settings(always_on_top=False, auto_summarize=False, auto_reply=False)
        with tempfile.TemporaryDirectory(prefix="popup-native-", dir=output.parent) as runtime, \
                patch("outlook_maximizer.ui.foreground_window", return_value=other_context) as context, \
                patch.object(Settings, "load", return_value=settings), \
                patch.object(CompanionApp, "_place_initial"), \
                patch.object(CompanionApp, "_position_reply"):
            app = CompanionApp(qt_app, Path(runtime), demo=True, tray=False, animations=False)
            app._foreground_timer.stop()
            app.queue_timer.stop()
            app.running = True
            app.summary_panel.move(-30000, -30000)
            app.reply_panel.move(-30000, -29600)

            def handles():
                return [int(panel.winId()) for panel in (app.summary_panel, app.reply_panel)]

            def above(reference):
                return all(is_window_above(hwnd, reference) for hwnd in handles())

            def native_visible(expected):
                require(all(bool(win32gui.IsWindowVisible(hwnd)) == expected for hwnd in handles()),
                        "Native panel visibility did not match the explicit display choice")

            def tick(value):
                context.return_value = value
                app._check_foreground()
                qt_app.processEvents()
                unchanged_focus()

            with patch.object(app, "_submit_job") as submissions:
                phase = "unpinned_outlook_selection"
                native_visible(False)
                require(raise_without_activation(source), "Could not stack the synthetic source")
                tick(source_context)
                native_visible(True)
                require(above(source), "Unpinned panels did not rise above the synthetic Outlook window")
                require(all(not win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOPMOST
                            for hwnd in handles()), "Unpinned panels acquired global topmost status")
                require(all(win32gui.GetWindowRect(hwnd)[2] < 0 for hwnd in handles()),
                        "A fixture moved onto a visible screen")
                app._receive_mail(replace(app.state.current, entry_id="synthetic-selected",
                                         body="Synthetic selected email; no external request."))
                require(above(source), "Selection did not present the unpinned panels")
                result["unpinned_outlook_selection"] = True

                phase = "other_application_covers_normal_panels"
                require(raise_without_activation(other), "Could not stack the synthetic other application")
                tick(other_context)
                require(all(is_window_above(other, hwnd) for hwnd in handles()),
                        "Normal panels stayed above the synthetic other application")
                analysis = Analysis(("Synthetic cached summary",), (), "", ())
                require(app.state.accept(app.state.generation, analysis), "Could not cache synthetic analysis")
                app._render_analysis(analysis)
                app._present()
                require(all(is_window_above(other, hwnd) for hwnd in handles()),
                        "Background content raised unpinned panels above another application")
                result["unpinned_yields_to_other_application"] = True

                phase = "return_to_outlook_keeps_results"
                generation = app.state.generation
                require(raise_without_activation(source), "Could not restack the synthetic source")
                tick(source_context)
                require(above(source), "Returning to Outlook did not restore panel order")
                require(app.state.analysis is analysis and app.state.generation == generation,
                        "Returning to Outlook discarded the cached result")
                result["return_preserves_results"] = True

                phase = "same_outlook_window_restack"
                require(raise_without_activation(source), "Could not cover panels with the same synthetic source")
                require(not above(source), "The same-source restack fixture did not cover the panels")
                tick(source_context)
                require(above(source), "The unchanged Outlook foreground context left panels covered")
                result["same_source_restack_recovers"] = True

                phase = "panel_activation_then_outlook_return"
                generation = app.state.generation
                tick(ForegroundWindow(handles()[0], "companion"))
                require(raise_without_activation(source), "Could not cover panels after companion activation")
                tick(source_context)
                require(above(source), "Returning from an active panel to Outlook left the panels covered")
                require(app.state.analysis is analysis and app.state.generation == generation,
                        "Returning from a panel discarded cached analysis")
                result["panel_activation_then_outlook_return"] = True

                phase = "native_hidden_panel_after_outlook_return"
                win32gui.ShowWindow(handles()[0], win32con.SW_HIDE)
                require(app.summary_panel.isVisible(), "Qt fixture did not retain its visible state")
                require(not win32gui.IsWindowVisible(handles()[0]), "Native hide fixture did not take effect")
                tick(source_context)
                native_visible(True)
                require(above(source), "Native-hidden panel did not recover on return to Outlook")
                result["native_hidden_panel_recovers"] = True

                phase = "companion_then_covered_tray_double_click"
                tick(ForegroundWindow(handles()[0], "companion"))
                require(raise_without_activation(source), "Could not cover panels after companion activation")
                context.return_value = ForegroundWindow(source, "shell")
                reason = QSystemTrayIcon.ActivationReason
                for event in (reason.Trigger, reason.DoubleClick, reason.Trigger):
                    app.tray.activated.emit(event)
                QTest.qWait(QApplication.doubleClickInterval() + 30)
                require(app.panels_visible, "Double-click hid panels covered after companion activation")
                native_visible(True)
                require(above(source), "Double-click did not recover companion-covered panels")
                unchanged_focus()
                result["companion_covered_double_click_recovers"] = True
                tick(source_context)

                phase = "outlook_restore_between_tray_clicks"
                require(raise_without_activation(source), "Could not cover panels before the first click")
                context.return_value = ForegroundWindow(source, "shell")
                app.tray.activated.emit(reason.Trigger)
                tick(source_context)
                require(above(source), "The fixture did not restore panels between clicks")
                context.return_value = ForegroundWindow(source, "shell")
                app.tray.activated.emit(reason.DoubleClick)
                app.tray.activated.emit(reason.Trigger)
                QTest.qWait(QApplication.doubleClickInterval() + 30)
                require(app.panels_visible, "The second click hid panels restored between clicks")
                native_visible(True)
                unchanged_focus()
                result["restore_between_clicks_stays_visible"] = True
                tick(source_context)

                phase = "covered_and_exposed_tray_double_click"
                require(raise_without_activation(source), "Could not cover the tray fixture")
                context.return_value = ForegroundWindow(source, "shell")
                reason = QSystemTrayIcon.ActivationReason
                for expected in (True, False):
                    for event in (reason.Trigger, reason.DoubleClick, reason.Trigger):
                        app.tray.activated.emit(event)
                    QTest.qWait(QApplication.doubleClickInterval() + 30)
                    require(app.panels_visible == expected, "Tray double-click chose the wrong display state")
                    native_visible(expected)
                    if expected:
                        require(above(source), "Double-click did not recover the covered panels")
                    unchanged_focus()
                result["double_click_recovers_then_hides"] = True

                phase = "pinned_native_topmost"
                app._pin_windows(True)
                native_visible(False)
                app.show_panels()
                native_visible(True)
                require(all(win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOPMOST
                            for hwnd in handles()), "Pinned panels lack native topmost status")
                require(raise_without_activation(other), "Could not raise the synthetic other window")
                tick(other_context)
                require(above(other), "Pinned panels did not stay above another normal application")
                result["pinned_native_topmost"] = True

                phase = "pinned_native_hidden_panel_recovery"
                win32gui.ShowWindow(handles()[0], win32con.SW_HIDE)
                require(app.summary_panel.isVisible() and not win32gui.IsWindowVisible(handles()[0]),
                        "Pinned native-hide fixture did not take effect")
                tick(other_context)
                native_visible(True)
                require(above(other), "Pinned panel did not recover while another app stayed foreground")
                result["pinned_native_hidden_panel_recovers"] = True

                phase = "explicit_hide_overrides_pin_and_auto"
                app.hide_panels()
                app._apply_settings(replace(app.settings, auto_summarize=True, auto_reply=True,
                                            codex_effort="high"))
                tick(source_context)
                app._receive_mail(replace(app.state.current, entry_id="synthetic-hidden",
                                         body="Synthetic hidden selection; automatic work must stay stopped."))
                app._run_auto()
                native_visible(False)
                require(not app.panels_visible and not app.debounce_timer.isActive(),
                        "Explicit hidden state did not suppress presentation and debounce")
                submissions.assert_not_called()
                result["hidden_overrides_pin_and_auto"] = True
                result["ai_requests"] = 0
                result["calendar_writes"] = 0
                unchanged_focus()

            app.close()
            app = None
            qt_app.processEvents()
            unchanged_focus()
        result.update(status="ok")
    except Exception as exc:
        result.update(status="error", failed_check=phase, error_type=type(exc).__name__, reason=str(exc))
    finally:
        if app is not None:
            app.close()
            qt_app.processEvents()
        for hwnd in reversed(fixtures):
            if win32gui.IsWindow(hwnd):
                win32gui.DestroyWindow(hwnd)
        win32gui.UnregisterClass(atom, instance)
    if result["status"] == "ok":
        if win32gui.GetForegroundWindow() == foreground:
            result["desktop_foreground_unchanged"] = True
        else:
            result.update(status="error", failed_check="fixture_cleanup",
                          reason="The desktop foreground changed during verification")
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
