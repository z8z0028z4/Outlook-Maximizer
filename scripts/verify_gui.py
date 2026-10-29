"""Render synthetic Qt windows; no email AI requests or Calendar writes."""

from pathlib import Path
import argparse
import json
import sys
import tempfile
import time
from dataclasses import replace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

from outlook_maximizer.ui import CompanionApp
from outlook_maximizer.i18n import tr
from outlook_maximizer.settings import Settings
from outlook_maximizer.window_focus import ForegroundWindow


def capture(window, target: Path) -> QImage:
    QApplication.processEvents()
    image = window.grab().toImage()
    if not image.save(str(target)):
        raise RuntimeError(f"Could not save {target.name}")
    return image


def board(target: Path, sections) -> None:
    """Composite only our widget renders on a neutral synthetic desktop surface."""
    image = QImage(1120, 680, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#EDF1F6"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setFont(QFont("Microsoft JhengHei UI", 11))
    for column, title, renders in sections:
        x, y = 28 + column * 368, 30
        painter.setPen(QColor("#62738B"))
        painter.drawText(QRect(x, y, 340, 35), Qt.AlignmentFlag.AlignLeft, title)
        y += 50
        for render in renders:
            scale = 1 / render.devicePixelRatio()
            width, height = round(render.width() * scale), round(render.height() * scale)
            painter.drawImage(QRect(x, y, width, height), render)
            y += height + 8
    painter.end()
    if not image.save(str(target)):
        raise RuntimeError("Could not save visual comparison")


def bottom_action_visible(panel, button, *, require_scroll: bool = True) -> None:
    bar = panel.scroll.verticalScrollBar()
    if require_scroll:
        assert bar.maximum() > 0, "Fixture must exercise scrolling"
    bar.setValue(bar.maximum())
    QApplication.processEvents()
    viewport = panel.scroll.viewport()
    top = button.mapTo(viewport, QPoint(0, 0)).y()
    bottom = button.mapTo(viewport, button.rect().bottomRight()).y()
    assert 0 <= top < bottom < viewport.height(), "Bottom action was clipped"
    left = button.mapTo(viewport, QPoint(0, 0)).x()
    right = button.mapTo(viewport, button.rect().bottomRight()).x()
    assert 0 <= left < right < viewport.width(), "Bottom action was clipped horizontally"


def capture_stack(app, target: Path) -> None:
    summary = app.summary_panel.grab().toImage()
    reply = app.reply_panel.grab().toImage()
    scale = summary.devicePixelRatio()
    image = QImage(max(summary.width(), reply.width()),
                   summary.height() + round(8 * scale) + reply.height(),
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.drawImage(0, 0, summary)
    painter.drawImage(0, summary.height() + round(8 * scale), reply)
    painter.end()
    assert image.save(str(target))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=("en", "zh-TW"), default="en")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    folder = args.output_dir or Path(__file__).resolve().parents[1] / ".local" / "gui-qa"
    folder.mkdir(parents=True, exist_ok=True)
    qt_app = QApplication.instance() or QApplication([])
    qt_app.setFont(QFont("Microsoft JhengHei UI", 10))
    errors = []
    previous_hook = sys.excepthook
    sys.excepthook = lambda kind, value, tb: errors.append(kind.__name__)
    screenshots = []
    # Exercise presentation as if the synthetic item were selected in Outlook,
    # without depending on (or manipulating) the user's actual foreground app.
    with patch("outlook_maximizer.ui.foreground_window", return_value=ForegroundWindow(0, "outlook")), \
            tempfile.TemporaryDirectory(prefix="synthetic-", dir=folder) as runtime:
        Settings(ui_language=args.language).save(Path(runtime))
        app = CompanionApp(qt_app, Path(runtime), demo=True, tray=False, animations=False)
        try:
            def shot(window, name):
                path = folder / name
                screenshots.append(str(path))
                return capture(window, path)

            def check_stack():
                qt_app.processEvents()
                assert app.reply_panel.x() == app.summary_panel.x()
                assert app.reply_panel.y() == app.summary_panel.y() + app.summary_panel.height() + 8
                assert not app.summary_panel.geometry().intersects(app.reply_panel.geometry())

            qt_app.processEvents()
            assert app.summary_panel.state == "ready"
            assert app.reply_panel.state == "collapsed"
            app.summary_panel.move(app.summary_panel.pos() - QPoint(60, 0))
            check_stack()
            app.summary_panel.collapse()
            check_stack()
            analysis_before_collapsed_pin = app.state.analysis
            app.reply_panel.header.pin_button.click()
            qt_app.processEvents()
            for panel in (app.summary_panel, app.reply_panel):
                assert panel.header.pin_button.isVisible()
                assert panel.state == "collapsed"
                assert not panel.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
            assert app.state.analysis is analysis_before_collapsed_pin
            app.summary_panel.header.pin_button.click()
            short_titles = folder / "short-titles.png"
            capture_stack(app, short_titles)
            screenshots.append(str(short_titles))
            assert app.summary_panel.header.text() == tr("摘要", args.language)
            assert app.reply_panel.header.text() == tr("回覆", args.language)
            app.show_panels()
            reason = QSystemTrayIcon.ActivationReason
            for expected_visible in (False, True):
                for event in (reason.Trigger, reason.DoubleClick, reason.Trigger):
                    app.tray.activated.emit(event)
                QTest.qWait(QApplication.doubleClickInterval() + 50)
                assert app.summary_panel.isVisible() == expected_visible
                assert app.reply_panel.isVisible() == expected_visible
                assert app.state.analysis is analysis_before_collapsed_pin
            check_stack()
            app._render_analysis(app.state.analysis)
            summary_top = shot(app.summary_panel, "summary-top.png")
            reply_idle = shot(app.reply_panel, "reply-idle.png")
            analysis_before_pin = app.state.analysis
            app.summary_panel.header.pin_button.click()
            qt_app.processEvents()
            assert not app.settings.always_on_top
            assert not app.reply_panel.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
            shot(app.summary_panel, "summary-unpinned.png")
            app.summary_panel.header.pin_button.click()
            qt_app.processEvents()
            assert app.settings.always_on_top
            assert app.reply_panel.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
            assert app.state.analysis is analysis_before_pin
            shot(app.summary_panel, "summary-pinned.png")
            for panel in (app.summary_panel, app.reply_panel):
                assert panel.windowFlags() & Qt.WindowType.FramelessWindowHint
                assert panel.windowOpacity() == 1.0
                assert panel.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

            app.request_summary(force=True)
            assert app.summary_panel.state == "loading"
            summary_loading = shot(app.summary_panel, "summary-loading.png")
            QTest.qWait(1450)
            app._drain()
            assert app.summary_panel.state == "ready"
            assert app.state.reply_draft == "", "Summary must not generate a hidden reply"

            app.request_reply()
            assert app.reply_panel.state == "loading"
            reply_loading = shot(app.reply_panel, "reply-loading.png")
            QTest.qWait(1450)
            app._drain()
            assert app.reply_panel.state == "ready"
            assert app.state.reply_draft
            check_stack()
            reply_top = shot(app.reply_panel, "reply-top.png")
            bottom_action_visible(app.summary_panel, app.calendar_buttons[-1][0])
            summary_bottom = shot(app.summary_panel, "summary-bottom.png")
            bottom_action_visible(app.reply_panel, app.copy_button)
            reply_bottom = shot(app.reply_panel, "reply-bottom.png")

            app.toggle_panels()
            assert not app.summary_panel.isVisible() and not app.reply_panel.isVisible()
            app._render_reply()
            assert not app.reply_panel.isVisible(), "Late results must respect hidden windows"
            app.toggle_panels()
            assert app.summary_panel.isVisible() and app.reply_panel.isVisible()

            app.open_settings()
            window = app.settings_window
            shot(window, "settings-top.png")
            window.about_me_edit.setPlainText("範例角色：研發工程師\n負責專案技術評估；對外先確認需求，避免直接承諾時程。")
            window.scroll.ensureWidgetVisible(window.about_me_edit)
            shot(window, "settings-about-me.png")
            window.about_me_edit.clear()
            deadline = time.monotonic() + 12
            while not window.reload_models_button.isEnabled() and time.monotonic() < deadline:
                QTest.qWait(50)
            assert window.reload_models_button.isEnabled(), "Model metadata load stalled"
            window.scroll.verticalScrollBar().setValue(window.scroll.verticalScrollBar().maximum())
            shot(window, "settings-ai-codex.png")
            assert window.provider_combo.count() == 3
            window.provider_combo.setCurrentIndex(1)
            shot(window, "settings-ai-antigravity.png")
            window.provider_combo.setCurrentIndex(0)
            assert window.save_button.mapTo(window, window.save_button.rect().bottomRight()).y() < window.height()
            window.opacity_slider.setValue(100)
            window.background_color_edit.setText("#18334A")
            window.scroll.ensureWidgetVisible(window.color_button)
            shot(window, "settings-colors.png")
            window.save_button.click()
            qt_app.processEvents()
            assert app.settings.floating_opacity == 1.0
            assert app.settings.background_color == "#18334A"
            assert app.state.analysis and app.state.reply_draft
            app.summary_panel.scroll.verticalScrollBar().setValue(0)
            app.reply_panel.scroll.verticalScrollBar().setValue(0)
            dark_summary = shot(app.summary_panel, "summary-opaque-dark.png")
            dark_reply = shot(app.reply_panel, "reply-opaque-dark.png")
            pixel = dark_summary.pixelColor(6, 80)
            assert pixel.alpha() == 255 and pixel.name() == "#18334a"
            bottom_action_visible(app.summary_panel, app.calendar_buttons[-1][0])
            shot(app.summary_panel, "summary-opaque-dark-bottom.png")
            app._apply_settings(replace(app.settings, background_color="#F5EBDD"))
            app.summary_panel.scroll.verticalScrollBar().setValue(0)
            warm_summary = shot(app.summary_panel, "summary-opaque-warm.png")
            warm_reply = shot(app.reply_panel, "reply-opaque-warm.png")
            colors = folder / "background-colors.png"
            board(colors, [
                (0, "原有淡背景 · 20%", [summary_top, reply_idle]),
                (1, "自選深色 · 100% 不透明", [dark_summary, dark_reply]),
                (2, "自選淺色 · 100% 不透明", [warm_summary, warm_reply]),
            ])
            screenshots.append(str(colors))
            app._apply_settings(replace(app.settings, background_color="#7C7C7C"))
            shot(app.summary_panel, "summary-opaque-gray.png")
            shot(app.reply_panel, "reply-opaque-gray.png")

            app._apply_settings(replace(app.settings, background_color="#FFF1BA"))
            event = app.state.analysis.events[0]
            source = app.state.current.key
            occurrence = app.calendar_receipts.begin(app.settings.calendar_id, source, event)
            app.calendar_receipts.complete(app.settings.calendar_id, source, event, occurrence)
            app.demo = False
            app.calendar_connected = True
            app._refresh_calendar_buttons()
            button = app.calendar_buttons[0][0]
            assert button.text() == tr("已加入行事曆", args.language) and not button.isEnabled()
            assert app.repeat_buttons[button].isEnabled()
            bottom_action_visible(app.summary_panel, button)
            shot(app.summary_panel, "calendar-added-repeat.png")
            check_stack()
            linked = folder / "linked-panels.png"
            capture_stack(app, linked)
            screenshots.append(str(linked))
            app.summary_panel.collapse()
            check_stack()
            app._render_analysis(app.state.analysis)
            check_stack()
            app.demo = True

            app._apply_settings(replace(app.settings, font_size=16, font_bold=True, font_italic=True))
            app.summary_panel.scroll.verticalScrollBar().setValue(0)
            app.reply_panel.scroll.verticalScrollBar().setValue(0)
            shot(app.summary_panel, "summary-typography.png")
            shot(app.reply_panel, "reply-typography.png")
            check_stack()
            app.open_settings()
            window = app.settings_window
            window.scroll.ensureWidgetVisible(window.font_preview)
            shot(window, "settings-typography.png")
            window.reject()
            for size in (8, 24):
                app._apply_settings(replace(app.settings, font_size=size, font_bold=True, font_italic=False))
                bottom_action_visible(app.summary_panel, app.calendar_buttons[-1][0], require_scroll=False)
                bottom_action_visible(app.reply_panel, app.copy_button, require_scroll=False)
                check_stack()
                shot(app.summary_panel, f"summary-font-{size}.png")
                app.summary_panel.collapse()
                check_stack()
                shot(app.summary_panel, f"summary-font-{size}-collapsed.png")
                app._render_analysis(app.state.analysis)
            app._apply_settings(replace(app.settings, font_size=10, font_bold=False, font_italic=False))

            app.edit_event()
            shot(app.event_editor, "event-editor.png")
            app.event_editor.notes_edit.setPlainText("範例會議備註\nhttps://example.invalid/meeting\n請攜帶進度資料。")
            app.event_editor.scroll.ensureWidgetVisible(app.event_editor.description_preview)
            shot(app.event_editor, "event-notes-preview.png")
            app.event_editor.close()
            app.menu.popup(QPoint(80, 80))
            shot(app.menu, "tray-menu.png")
            app.menu.close()
            labels = [action.text() for action in app.menu.actions()]
            assert {tr(label, args.language) for label in ("顯示浮窗", "隱藏浮窗", "設定", "重新產生摘要", "結束")}.issubset(labels)

            comparison = folder / "floating-panels.png"
            board(comparison, [
                (0, "閱讀信件 · 回覆按需產生", [summary_top, reply_idle]),
                (1, "產生中 · 各自顯示動畫", [summary_loading, reply_loading]),
                (2, "捲動到底 · 操作跟隨內容", [summary_bottom, reply_bottom]),
            ])
            screenshots.append(str(comparison))
            if errors:
                raise RuntimeError(f"Qt callback failures: {errors}")
            print(json.dumps({"status": "ok", "language": args.language, "screenshot_count": len(screenshots), "folder": str(folder),
                              "linked_geometry": True, "font_extremes": [8, 24],
                              "collapsed_pin": True, "tray_double_click": True,
                              "notes_preview": True, "repeat_control": True, "callback_errors": errors}))
        finally:
            app.close()
            qt_app.processEvents()
            sys.excepthook = previous_hook


if __name__ == "__main__":
    main()
