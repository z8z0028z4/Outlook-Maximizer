import os
import unittest
from unittest.mock import Mock, patch

from outlook_maximizer.window_focus import (
    ForegroundWindow, _win32, foreground_window, is_native_window_visible, is_window_above,
    raise_without_activation,
)


class NativeError(Exception):
    pass


class WindowFocusTests(unittest.TestCase):
    def native(self, *, window_class="ordinary", owner_class=None, process_id=None):
        gui = Mock()
        gui.GetForegroundWindow.return_value = 10
        gui.IsWindow.return_value = True
        gui.IsWindowVisible.return_value = True
        gui.IsIconic.return_value = False
        gui.GetAncestor.return_value = 20 if owner_class else 10
        gui.GetClassName.side_effect = lambda hwnd: owner_class if hwnd == 20 else window_class
        gui.SetWindowPos.return_value = None
        process = Mock()
        process.GetWindowThreadProcessId.return_value = (100, os.getpid() if process_id is None else process_id)
        return gui, process, NativeError

    def test_unavailable_native_support_is_import_safe(self):
        with patch("outlook_maximizer.window_focus.os.name", "posix"):
            self.assertIsNone(_win32())
        with patch("outlook_maximizer.window_focus._win32", return_value=None):
            self.assertEqual(foreground_window(), ForegroundWindow())
            self.assertFalse(raise_without_activation(10))
            self.assertIsNone(is_native_window_visible(10))
            self.assertFalse(is_window_above(10, 20))

    def test_outlook_class_is_recognized_before_own_process(self):
        native = self.native(window_class="RCTRL_RENWND32")
        with patch("outlook_maximizer.window_focus._win32", return_value=native):
            self.assertEqual(foreground_window(), ForegroundWindow(10, "outlook"))
        native[1].GetWindowThreadProcessId.assert_not_called()

    def test_outlook_root_owner_dialog_is_recognized(self):
        native = self.native(window_class="#32770", owner_class="rctrl_renwnd32")
        with patch("outlook_maximizer.window_focus._win32", return_value=native):
            self.assertEqual(foreground_window(), ForegroundWindow(10, "outlook"))
        native[0].GetAncestor.assert_called_once_with(10, 3)

    def test_tray_and_menu_windows_are_transient(self):
        for name in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
                     "#32768", "Qt6QWindowPopupSaveBits", "Qt661QWindowPopupDropShadowSaveBits",
                     "Qt6QWindowToolTipSaveBits"):
            with self.subTest(name=name), patch("outlook_maximizer.window_focus._win32", return_value=self.native(window_class=name)):
                self.assertEqual(foreground_window().kind, "shell")

    def test_regular_own_qt_window_is_companion_and_other_process_is_other(self):
        for process_id, expected in ((os.getpid(), "companion"), (os.getpid() + 1, "other")):
            with self.subTest(process_id=process_id), patch(
                "outlook_maximizer.window_focus._win32",
                return_value=self.native(window_class="Qt6QWindowToolSaveBits", process_id=process_id),
            ):
                self.assertEqual(foreground_window(), ForegroundWindow(10, expected))

    def test_invalid_or_racing_foreground_handle_is_unknown(self):
        for value in (0, 10):
            native = self.native()
            native[0].GetForegroundWindow.return_value = value
            native[0].GetClassName.side_effect = NativeError("private window detail")
            with self.subTest(value=value), patch("outlook_maximizer.window_focus._win32", return_value=native):
                self.assertEqual(foreground_window(), ForegroundWindow())

    def test_raise_preserves_size_position_owner_and_keyboard_focus(self):
        native = self.native()
        with patch("outlook_maximizer.window_focus._win32", return_value=native):
            self.assertTrue(raise_without_activation(10))
        native[0].SetWindowPos.assert_called_once_with(10, 0, 0, 0, 0, 0, 0x0213)
        native[0].SetForegroundWindow.assert_not_called()
        native[0].SetActiveWindow.assert_not_called()
        native[0].ShowWindow.assert_not_called()

    def test_raise_rejects_missing_window_and_native_failure(self):
        native = self.native()
        native[0].IsWindow.return_value = False
        with patch("outlook_maximizer.window_focus._win32", return_value=native):
            self.assertFalse(raise_without_activation(10))
        native[0].SetWindowPos.assert_not_called()
        native[0].IsWindow.return_value = True
        for result in (False, NativeError("private error")):
            native[0].SetWindowPos.side_effect = result if isinstance(result, Exception) else None
            native[0].SetWindowPos.return_value = result
            with self.subTest(result_type=type(result).__name__), patch("outlook_maximizer.window_focus._win32", return_value=native):
                self.assertFalse(raise_without_activation(10))

    def test_raise_shows_native_hidden_window_without_activation(self):
        native = self.native()
        native[0].IsWindowVisible.side_effect = [False, True]
        with patch("outlook_maximizer.window_focus._win32", return_value=native):
            self.assertTrue(raise_without_activation(10))
        native[0].ShowWindow.assert_called_once_with(10, 4)
        native[0].SetWindowPos.assert_called_once_with(10, 0, 0, 0, 0, 0, 0x0213)
        native[0].SetForegroundWindow.assert_not_called()

    def test_native_visibility_distinguishes_hidden_and_unavailable(self):
        native = self.native()
        with patch("outlook_maximizer.window_focus._win32", return_value=native):
            self.assertTrue(is_native_window_visible(10))
            native[0].IsWindowVisible.return_value = False
            self.assertFalse(is_native_window_visible(10))
            native[0].IsWindowVisible.side_effect = NativeError("private error")
            self.assertIsNone(is_native_window_visible(10))

    def order(self, windows):
        native = self.native()
        native[0].GetTopWindow.return_value = windows[0] if windows else 0
        below = dict(zip(windows, windows[1:] + [0]))
        native[0].GetWindow.side_effect = lambda hwnd, flag: below[hwnd]
        return native

    def test_visible_windows_are_compared_in_desktop_order(self):
        native = self.order([9, 10, 15, 20, 25])
        with patch("outlook_maximizer.window_focus._win32", return_value=native):
            self.assertTrue(is_window_above(10, 20))
            self.assertFalse(is_window_above(20, 10))
            self.assertTrue(is_window_above(10, 10))
        self.assertTrue(all(call.args[1] == 2 for call in native[0].GetWindow.call_args_list))

    def test_neither_missing_nor_hidden_nor_minimized_windows_are_exposed(self):
        for method in ("IsWindow", "IsWindowVisible", "IsIconic"):
            for missing in (10, 20):
                native = self.order([10, 20])
                getattr(native[0], method).side_effect = lambda hwnd, failed=missing: (hwnd == failed) if method == "IsIconic" else hwnd != failed
                with self.subTest(method=method, missing=missing), patch("outlook_maximizer.window_focus._win32", return_value=native):
                    self.assertFalse(is_window_above(10, 20))

    def test_comparison_requires_both_windows_in_the_same_desktop_order(self):
        native = self.order([9, 10, 30])
        with patch("outlook_maximizer.window_focus._win32", return_value=native):
            self.assertFalse(is_window_above(10, 20))

    def test_z_order_races_and_cycles_are_bounded(self):
        native = self.order([9, 10])
        native[0].GetWindow.side_effect = lambda hwnd, flag: 10 if hwnd == 9 else 9
        with patch("outlook_maximizer.window_focus._win32", return_value=native):
            self.assertFalse(is_window_above(10, 20))
        self.assertEqual(native[0].GetWindow.call_count, 2)
        native[0].GetWindow.side_effect = NativeError("private race detail")
        with patch("outlook_maximizer.window_focus._win32", return_value=native):
            self.assertFalse(is_window_above(10, 20))

    def test_z_order_walk_has_a_hard_limit_even_without_cycles(self):
        native = self.native()
        native[0].GetTopWindow.return_value = 100
        native[0].GetWindow.side_effect = lambda hwnd, flag: hwnd + 1
        with patch("outlook_maximizer.window_focus._win32", return_value=native), \
                patch("outlook_maximizer.window_focus._MAX_WINDOWS", 3):
            self.assertFalse(is_window_above(10, 20))
        self.assertEqual(native[0].GetWindow.call_count, 3)


@unittest.skipUnless(os.name == "nt", "Native desktop window order")
class NativeWindowFocusTests(unittest.TestCase):
    def test_native_raise_preserves_normal_and_topmost_bands_without_activation(self):
        import win32api
        import win32con
        import win32gui

        instance = win32api.GetModuleHandle(None)
        name = f"OutlookMaximizerFocusTest-{os.getpid()}"
        window_class = win32gui.WNDCLASS()
        window_class.hInstance = instance
        window_class.lpszClassName = name
        window_class.lpfnWndProc = win32gui.DefWindowProc
        atom = win32gui.RegisterClass(window_class)
        windows = []
        try:
            for topmost in (False, False, True):
                style = win32con.WS_EX_TOOLWINDOW | (win32con.WS_EX_TOPMOST if topmost else 0)
                hwnd = win32gui.CreateWindowEx(style, atom, "Synthetic focus fixture", win32con.WS_POPUP,
                                               -30000, -30000, 20, 20, 0, 0, instance, None)
                windows.append(hwnd)
                win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
            first, second, pinned = windows
            rectangles = [win32gui.GetWindowRect(hwnd) for hwnd in windows]
            foreground = win32gui.GetForegroundWindow()
            self.assertTrue(raise_without_activation(first))
            self.assertTrue(is_window_above(first, second))
            self.assertTrue(is_window_above(pinned, first))
            self.assertFalse(win32gui.GetWindowLong(first, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOPMOST)
            self.assertTrue(raise_without_activation(pinned))
            self.assertTrue(win32gui.GetWindowLong(pinned, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOPMOST)
            self.assertEqual(rectangles, [win32gui.GetWindowRect(hwnd) for hwnd in windows])
            if foreground not in windows:
                self.assertNotIn(win32gui.GetForegroundWindow(), windows)
        finally:
            for hwnd in reversed(windows):
                win32gui.DestroyWindow(hwnd)
            win32gui.UnregisterClass(atom, instance)


if __name__ == "__main__":
    unittest.main()
