from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from outlook_maximizer.calendar_api import CalendarError, GoogleCalendarClient
from outlook_maximizer.calendar_receipts import CalendarReceipts
from outlook_maximizer.models import CalendarEvent


class CalendarReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path.cwd() / ".local"
        temporary_root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=temporary_root)
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.event = CalendarEvent("不應寫入標題", "2026-10-22T10:00:00+08:00",
                                   "2026-10-22T11:00:00+08:00", description="私密備註", evidence="原信內容")
        self.receipts = CalendarReceipts(self.root)

    def test_first_insert_and_receipt_survive_restart_and_equivalent_offsets(self):
        self.assertFalse(self.receipts.is_added("primary", "source", self.event))
        occurrence = self.receipts.begin("primary", "source", self.event)
        self.assertEqual(occurrence, "")
        self.assertFalse(self.receipts.is_added("primary", "source", self.event))
        self.receipts.complete("primary", "source", self.event, occurrence)
        reloaded = CalendarReceipts(self.root)
        same_slot = replace(self.event, title="新摘要名稱", start="2026-10-22T02:00:00Z", end="2026-10-22T03:00:00Z")
        self.assertTrue(reloaded.is_added("primary", "source", same_slot))
        self.assertFalse(reloaded.is_added("other", "source", same_slot))
        self.assertFalse(reloaded.is_added("primary", "other", same_slot))

    def test_repeat_uses_one_persisted_operation_until_success_then_next_click_is_new(self):
        first = self.receipts.begin("primary", "source", self.event)
        self.receipts.complete("primary", "source", self.event, first)
        repeated = self.receipts.begin("primary", "source", self.event, repeat=True)
        self.assertTrue(repeated)
        reloaded = CalendarReceipts(self.root)
        self.assertEqual(reloaded.begin("primary", "source", self.event, repeat=True), repeated)
        self.assertTrue(reloaded.is_added("primary", "source", self.event))
        client = GoogleCalendarClient(self.root)
        self.assertNotEqual(client._event_id(self.event, "source"),
                            client._event_id(self.event, "source", occurrence_key=repeated))
        reloaded.complete("primary", "source", self.event, repeated)
        self.assertNotEqual(reloaded.begin("primary", "source", self.event, repeat=True), repeated)

    def test_disk_stores_only_hashes_and_operation_ids(self):
        occurrence = self.receipts.begin("private-calendar@example.invalid", "private-source", self.event)
        self.receipts.complete("private-calendar@example.invalid", "private-source", self.event, occurrence)
        text = self.receipts.path.read_text(encoding="utf-8")
        for private in ("private-calendar", "private-source", self.event.title, self.event.description,
                        self.event.evidence, self.event.start, self.event.end):
            self.assertNotIn(private, text)
        self.assertEqual(json.loads(text)["version"], 1)

    def test_failed_atomic_write_does_not_change_memory_or_old_file(self):
        occurrence = self.receipts.begin("primary", "source", self.event)
        previous = self.receipts.path.read_bytes()
        with patch("outlook_maximizer.calendar_receipts.os.replace", side_effect=OSError("blocked")):
            with self.assertRaises(CalendarError):
                self.receipts.complete("primary", "source", self.event, occurrence)
        self.assertFalse(self.receipts.is_added("primary", "source", self.event))
        self.assertEqual(self.receipts.path.read_bytes(), previous)
        self.assertEqual(list(self.root.glob(".calendar-receipts-*")), [])
        self.receipts.complete("primary", "source", self.event, occurrence)
        self.assertTrue(self.receipts.is_added("primary", "source", self.event))

    def test_corrupt_receipts_do_not_silently_forget_prior_additions(self):
        self.receipts.path.write_text('{"version":1,"entries":{"bad":"bad"}}', encoding="utf-8")
        receipts = CalendarReceipts(self.root)
        self.assertTrue(receipts.load_error)
        self.assertFalse(receipts.is_added("primary", "source", self.event))
        with self.assertRaises(CalendarError):
            receipts.begin("primary", "source", self.event)
        self.assertIn('"bad"', self.receipts.path.read_text(encoding="utf-8"))

    def test_malformed_json_keeps_app_startup_available_and_writes_blocked(self):
        self.receipts.path.write_text("invalid json", encoding="utf-8")
        receipts = CalendarReceipts(self.root)
        self.assertTrue(receipts.load_error)
        with self.assertRaises(CalendarError):
            receipts.begin("primary", "source", self.event)
        self.assertEqual(self.receipts.path.read_text(encoding="utf-8"), "invalid json")

    def test_uncertain_times_are_not_recorded_or_submitted(self):
        incomplete = replace(self.event, start="", end="", needs_review=True)
        self.assertFalse(self.receipts.is_added("primary", "source", incomplete))
        with self.assertRaises(CalendarError):
            self.receipts.begin("primary", "source", incomplete)
        self.assertFalse(self.receipts.path.exists())

    def test_repeat_requires_prior_success(self):
        with self.assertRaises(CalendarError):
            self.receipts.begin("primary", "source", self.event, repeat=True)

    def test_normal_request_retains_original_identity_after_completed_repeat(self):
        original = self.receipts.begin("primary", "source", self.event)
        self.receipts.complete("primary", "source", self.event, original)
        repeated = self.receipts.begin("primary", "source", self.event, repeat=True)
        self.receipts.complete("primary", "source", self.event, repeated)
        self.assertEqual(self.receipts.begin("primary", "source", self.event), "")

    def test_stale_completion_cannot_clear_new_pending_repeat(self):
        first = self.receipts.begin("primary", "source", self.event)
        self.receipts.complete("primary", "source", self.event, first)
        repeated = self.receipts.begin("primary", "source", self.event, repeat=True)
        with self.assertRaises(CalendarError):
            self.receipts.complete("primary", "source", self.event, first)
        self.assertEqual(self.receipts.begin("primary", "source", self.event, repeat=True), repeated)
