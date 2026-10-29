from dataclasses import replace
import os
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from outlook_maximizer.calendar_api import format_event_description
from outlook_maximizer.event_dialog import EventEditor
from outlook_maximizer.models import CalendarEvent


class EventDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.event = CalendarEvent("會議", "2026-10-22T10:00:00+08:00", "2026-10-22T11:00:00+08:00",
                                   description="原備註", evidence="明日 10:00–11:00")
        self.saved = Mock()
        self.dialog = EventEditor(self.event, self.saved)
        self.addCleanup(self.dialog.close)

    def test_preview_includes_exact_evidence_and_updates_as_notes_change(self):
        self.assertTrue(self.dialog.description_preview.isReadOnly())
        self.assertEqual(self.dialog.description_preview.toPlainText(), format_event_description(self.event, ui_language="en"))
        self.dialog.notes_edit.setPlainText("新的備註\nhttps://example.invalid/meeting")
        self.assertEqual(self.dialog.description_preview.toPlainText(),
                         format_event_description(replace(self.event, description=self.dialog.notes_edit.toPlainText()), ui_language="en"))

    def test_save_keeps_notes_and_evidence(self):
        self.dialog.notes_edit.setPlainText("修改後備註")
        self.dialog.save_button.click()
        self.saved.assert_called_once()
        updated = self.saved.call_args.args[0]
        self.assertEqual(updated.description, "修改後備註")
        self.assertEqual(updated.evidence, self.event.evidence)
        self.assertEqual(self.dialog.result(), self.dialog.DialogCode.Accepted)

    def test_cancel_does_not_call_save_or_modify_event(self):
        self.dialog.notes_edit.setPlainText("不儲存的內容")
        self.dialog.cancel_button.click()
        self.saved.assert_not_called()
        self.assertEqual(self.event.description, "原備註")

    def test_oversized_notes_remain_in_editor_with_error(self):
        self.dialog.notes_edit.setPlainText("x" * 8001)
        self.dialog.save_button.click()
        self.saved.assert_not_called()
        self.assertTrue(self.dialog.error.text())
