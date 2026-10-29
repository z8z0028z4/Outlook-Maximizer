from dataclasses import replace
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from outlook_maximizer.calendar_api import CalendarError, CalendarInsertResult, _HTTPFailure
from outlook_maximizer.calendar_receipts import CalendarReceipts
from outlook_maximizer.models import MailSnapshot
from outlook_maximizer.ui import CompanionApp


class CalendarActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        Path(".local").mkdir(exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=".local")
        self.app = CompanionApp(self.qt_app, Path(self.folder.name), demo=True,
                                tray=False, animations=False)
        self.app.demo = False
        self.app.running = True
        self.app.calendar_connected = True
        self.app.settings.auto_summarize = False
        self.mail, self.analysis = self.app.state.current, self.app.state.analysis
        self.event = self.analysis.events[0]
        self.app._refresh_calendar_buttons()

    def tearDown(self):
        self.app.close()
        self.qt_app.processEvents()
        self.folder.cleanup()

    def record_added(self):
        occurrence = self.app.calendar_receipts.begin("primary", self.mail.key, self.event)
        self.app.calendar_receipts.complete("primary", self.mail.key, self.event, occurrence)
        self.app._refresh_calendar_buttons()

    def test_added_state_survives_mail_navigation_and_receipt_reload(self):
        self.record_added()
        self.app._receive_mail(MailSnapshot("other", "store", "other", "sender", "body", ""))
        self.app.calendar_receipts = CalendarReceipts(self.app.data_dir)
        self.app._receive_mail(self.mail)
        self.app.request_summary()
        button, _ = self.app.calendar_buttons[0]
        self.assertEqual(button.text(), "Added to calendar")
        self.assertFalse(button.isEnabled())
        repeat = self.app.repeat_buttons[button]
        self.assertFalse(repeat.isHidden())
        self.assertTrue(repeat.isEnabled())

    def test_main_button_and_direct_calls_cannot_reinsert_added_event(self):
        self.record_added()
        with patch.object(self.app.io_pool, "submit") as submit:
            self.app.calendar_buttons[0][0].click()
            self.app.insert_event(0)
        submit.assert_not_called()

    def test_corrected_time_and_notes_survive_navigation_without_reenabling_main(self):
        self.app.edit_event(0)
        editor = self.app.event_editor
        editor.values["start_time"].setText("16:00")
        editor.values["end_time"].setText("17:00")
        editor.notes_edit.setPlainText("已確認的新時間與會議連結")
        editor.save_button.click()
        self.event = self.app._event(0)
        self.assertIn("T16:00", self.event.start)
        self.record_added()
        self.app._receive_mail(MailSnapshot("other", "store", "other", "sender", "body", ""))
        self.app._receive_mail(self.mail)
        self.app.request_summary()
        self.assertEqual(self.app._event(0), self.event)
        self.assertEqual(self.app._event(0).description, "已確認的新時間與會議連結")
        self.assertFalse(self.app.calendar_buttons[0][0].isEnabled())

    def test_repeat_control_creates_a_distinct_id_and_keeps_main_disabled(self):
        self.record_added()
        base_id = self.app.calendar._event_id(self.event, self.mail.key)
        def created(event, source, *, occurrence_key):
            return CalendarInsertResult(self.app.calendar._event_id(event, source, occurrence_key=occurrence_key), "", True)
        with patch.object(self.app.io_pool, "submit", side_effect=lambda run: run()), \
                patch.object(self.app.calendar, "create_event", side_effect=created) as create:
            button, _ = self.app.calendar_buttons[0]
            self.app.repeat_buttons[button].click()
            self.assertTrue(self.app.io_busy)
            self.assertFalse(self.app.repeat_buttons[button].isEnabled())
            self.app._drain()
            occurrence = create.call_args.kwargs["occurrence_key"]
        self.assertTrue(occurrence)
        self.assertNotEqual(self.app.calendar._event_id(self.event, self.mail.key, occurrence_key=occurrence), base_id)
        self.assertFalse(button.isEnabled())
        self.assertTrue(self.app.repeat_buttons[button].isEnabled())

    def test_repeat_retry_reuses_pending_identity_after_network_error(self):
        self.record_added()
        with patch.object(self.app.io_pool, "submit", side_effect=lambda run: run()), \
                patch.object(self.app.calendar, "create_event", side_effect=[CalendarError("network"), CalendarInsertResult("id", "", False)]) as create, \
                patch("outlook_maximizer.ui.QMessageBox.warning"):
            self.app.insert_event(0, repeat=True)
            self.app._drain()
            self.app.calendar_connected = True
            self.app.insert_event(0, repeat=True)
            self.app._drain()
        self.assertEqual(create.call_count, 2)
        self.assertTrue(create.call_args_list[0].kwargs["occurrence_key"])
        self.assertEqual(create.call_args_list[0].kwargs["occurrence_key"],
                         create.call_args_list[1].kwargs["occurrence_key"])

    def test_equivalent_timezone_and_title_changes_preserve_added_status(self):
        self.record_added()
        changed = replace(self.event, title="新的摘要標題",
                          start=datetime.fromisoformat(self.event.start).astimezone(timezone.utc).isoformat(),
                          end=datetime.fromisoformat(self.event.end).astimezone(timezone.utc).isoformat())
        self.app.edited_events[0] = changed
        self.app._render_analysis(self.analysis)
        self.assertEqual(self.app.calendar_buttons[0][0].text(), "Added to calendar")
        self.assertFalse(self.app.calendar_buttons[0][0].isEnabled())

    def test_deleted_old_event_is_recreated_with_same_times_and_a_new_persisted_id(self):
        original = self.app.calendar._event_id(self.event, self.mail.key)
        posted = []
        def request(method, url, *, body=None):
            if method == "GET":
                self.assertTrue(url.endswith(original))
                return {"id": original, "status": "cancelled"}
            posted.append(body)
            if body["id"] == original:
                raise _HTTPFailure(409)
            return {"id": body["id"]}
        with patch.object(self.app.io_pool, "submit", side_effect=lambda run: run()), \
                patch.object(self.app.calendar, "_authorized_request", side_effect=request), \
                patch("outlook_maximizer.ui.QMessageBox.warning") as warning:
            self.app.insert_event(0)
            self.app._drain()
        self.assertEqual(len(posted), 2)
        self.assertNotEqual(posted[0]["id"], posted[1]["id"])
        self.assertEqual(posted[0]["start"], posted[1]["start"])
        self.assertEqual(posted[0]["end"], posted[1]["end"])
        self.assertTrue(CalendarReceipts(self.app.data_dir).is_added("primary", self.mail.key, self.event))
        self.assertFalse(self.app.calendar_buttons[0][0].isEnabled())
        warning.assert_not_called()

    def test_deleted_pending_repeat_gets_a_new_id(self):
        self.record_added()
        pending = self.app.calendar_receipts.begin("primary", self.mail.key, self.event, repeat=True)
        deleted_id = self.app.calendar._event_id(self.event, self.mail.key, occurrence_key=pending)
        self.app.calendar_receipts = CalendarReceipts(self.app.data_dir)
        posted = []
        def request(method, url, *, body=None):
            if method == "GET":
                return {"id": deleted_id, "status": "cancelled"}
            posted.append(body["id"])
            if body["id"] == deleted_id:
                raise _HTTPFailure(409)
            return {"id": body["id"]}
        with patch.object(self.app.io_pool, "submit", side_effect=lambda run: run()), \
                patch.object(self.app.calendar, "_authorized_request", side_effect=request), \
                patch("outlook_maximizer.ui.QMessageBox.warning") as warning:
            self.app.insert_event(0, repeat=True)
            self.app._drain()
        self.assertEqual(len(posted), 2)
        self.assertNotEqual(posted[0], posted[1])
        warning.assert_not_called()

    def test_replacement_id_survives_uncertain_creation_and_receipt_reload(self):
        original = self.app.calendar._event_id(self.event, self.mail.key)
        posted = []
        phase = ["first"]
        def request(method, url, *, body=None):
            if method == "GET":
                identifier = url.rsplit("/", 1)[-1]
                return {"id": identifier, "status": "cancelled" if identifier == original else "confirmed"}
            posted.append(body["id"])
            if body["id"] == original or phase[0] == "retry":
                raise _HTTPFailure(409)
            raise CalendarError("network outcome unknown")
        with patch.object(self.app.io_pool, "submit", side_effect=lambda run: run()), \
                patch.object(self.app.calendar, "_authorized_request", side_effect=request), \
                patch("outlook_maximizer.ui.QMessageBox.warning"):
            self.app.insert_event(0)
            self.app._drain()
            self.assertEqual(len(posted), 2)
            self.app.calendar_receipts = CalendarReceipts(self.app.data_dir)
            self.app.calendar_connected = True
            phase[0] = "retry"
            self.app.insert_event(0, repeat=True)
            self.app._drain()
        self.assertEqual(posted[1], posted[2])
        self.assertNotEqual(posted[0], posted[1])

    def test_unknown_conflict_lookup_does_not_rotate_the_id(self):
        posted = []
        def request(method, url, *, body=None):
            if method == "GET":
                raise _HTTPFailure(404)
            posted.append(body["id"])
            raise _HTTPFailure(409)
        with patch.object(self.app.io_pool, "submit", side_effect=lambda run: run()), \
                patch.object(self.app.calendar, "_authorized_request", side_effect=request), \
                patch("outlook_maximizer.ui.QMessageBox.warning"):
            self.app.insert_event(0)
            self.app._drain()
        self.assertEqual(posted, [self.app.calendar._event_id(self.event, self.mail.key)])
        self.assertFalse(self.app.calendar_receipts.is_added("primary", self.mail.key, self.event))

    def test_gone_response_for_the_exact_conflicting_event_allows_replacement(self):
        original = self.app.calendar._event_id(self.event, self.mail.key)
        posted = []
        def request(method, url, *, body=None):
            if method == "GET":
                raise _HTTPFailure(410)
            posted.append(body["id"])
            if body["id"] == original:
                raise _HTTPFailure(409)
            return {"id": body["id"]}
        with patch.object(self.app.io_pool, "submit", side_effect=lambda run: run()), \
                patch.object(self.app.calendar, "_authorized_request", side_effect=request), \
                patch("outlook_maximizer.ui.QMessageBox.warning") as warning:
            self.app.insert_event(0)
            self.app._drain()
        self.assertEqual(len(posted), 2)
        self.assertNotEqual(posted[0], posted[1])
        warning.assert_not_called()

    def test_recovery_is_limited_to_one_replacement_per_click(self):
        posted = []
        def request(method, url, *, body=None):
            if method == "GET":
                return {"id": url.rsplit("/", 1)[-1], "status": "cancelled"}
            posted.append(body["id"])
            raise _HTTPFailure(409)
        with patch.object(self.app.io_pool, "submit", side_effect=lambda run: run()), \
                patch.object(self.app.calendar, "_authorized_request", side_effect=request), \
                patch("outlook_maximizer.ui.QMessageBox.warning") as warning:
            self.app.insert_event(0)
            self.app._drain()
        self.assertEqual(len(posted), 2)
        warning.assert_called_once()

    def test_receipt_failure_after_remote_replacement_keeps_retry_identity(self):
        original = self.app.calendar._event_id(self.event, self.mail.key)
        posted = []
        def request(method, url, *, body=None):
            if method == "GET":
                identifier = url.rsplit("/", 1)[-1]
                return {"id": identifier, "status": "cancelled" if identifier == original else "confirmed"}
            posted.append(body["id"])
            if body["id"] == original or len(posted) == 3:
                raise _HTTPFailure(409)
            return {"id": body["id"]}
        save = self.app.calendar_receipts._save_entry
        saves = [0]
        def fail_final_receipt(key, entry):
            saves[0] += 1
            if saves[0] == 4:
                raise CalendarError("receipt write failed")
            save(key, entry)
        with patch.object(self.app.io_pool, "submit", side_effect=lambda run: run()), \
                patch.object(self.app.calendar, "_authorized_request", side_effect=request), \
                patch("outlook_maximizer.ui.QMessageBox.warning"):
            with patch.object(self.app.calendar_receipts, "_save_entry", side_effect=fail_final_receipt):
                self.app.insert_event(0)
                self.app._drain()
            self.app.calendar_receipts = CalendarReceipts(self.app.data_dir)
            self.app.calendar_connected = True
            self.app.insert_event(0, repeat=True)
            self.app._drain()
        self.assertEqual(len(posted), 3)
        self.assertNotEqual(posted[0], posted[1])
        self.assertEqual(posted[1], posted[2])
