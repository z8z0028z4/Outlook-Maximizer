from dataclasses import replace
import unittest

from outlook_maximizer.event_editor import event_from_fields
from outlook_maximizer.models import CalendarEvent, ValidationError


class EditorTests(unittest.TestCase):
    def make(self, **overrides):
        fields = dict(title="會議", start_date="2026-10-22", start_time="14:00", end_date="2026-10-22", end_time="15:00", timezone_name="Asia/Taipei", all_day=False, location="A")
        fields.update(overrides)
        return event_from_fields(CalendarEvent("原名", "", "", needs_review=True), **fields)

    def test_confirmed_fields_become_aware_taipei_times(self):
        event = self.make()
        self.assertEqual(event.start, "2026-10-22T14:00:00+08:00")
        self.assertFalse(event.needs_review)

    def test_notes_are_editable_without_changing_evidence(self):
        original = CalendarEvent("原名", "", "", description="原備註", evidence="原信依據", needs_review=True)
        fields = dict(title="會議", start_date="2026-10-22", start_time="14:00",
                      end_date="2026-10-22", end_time="15:00", timezone_name="Asia/Taipei",
                      all_day=False, location="A", description="  確認進度\n附上報告連結  ")
        updated = event_from_fields(original, **fields)
        self.assertEqual(updated.description, "確認進度\n附上報告連結")
        self.assertEqual(updated.evidence, "原信依據")

    def test_omitting_notes_preserves_existing_description(self):
        original = CalendarEvent("原名", "", "", description="原備註", needs_review=True)
        updated = event_from_fields(original, title="會議", start_date="2026-10-22", start_time="14:00",
                                    end_date="2026-10-22", end_time="15:00", timezone_name="Asia/Taipei",
                                    all_day=False, location="A")
        self.assertEqual(updated.description, "原備註")

    def test_notes_cannot_exceed_event_limit(self):
        with self.assertRaises(ValidationError):
            self.make(description="x" * 8001)

    def test_all_day_user_end_date_becomes_exclusive(self):
        event = self.make(all_day=True, end_date="2026-10-24")
        self.assertEqual(event.end, "2026-10-25")

    def test_empty_required_date_does_not_silently_use_today(self):
        with self.assertRaises(ValidationError):
            self.make(start_date="")

    def test_reversed_meeting_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.make(end_time="13:00")

    def test_dst_missing_hour_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.make(start_date="2026-03-08", end_date="2026-03-08", start_time="02:30", end_time="04:30", timezone_name="America/New_York")

    def test_dst_repeated_hour_requires_explicit_utc_time(self):
        with self.assertRaises(ValidationError):
            self.make(start_date="2026-11-01", end_date="2026-11-01", start_time="01:30", end_time="04:30", timezone_name="America/New_York")
