from dataclasses import replace
import unittest

from outlook_maximizer.models import Analysis, CalendarEvent, MailSnapshot, ValidationError


def event(**changes):
    return replace(CalendarEvent("討論", "2026-10-22T14:00:00+08:00", "2026-10-22T15:00:00+08:00"), **changes)


class EventValidationTests(unittest.TestCase):
    def test_aware_event_is_insertable(self):
        event().validate(for_insert=True)

    def test_naive_time_is_rejected(self):
        with self.assertRaises(ValidationError):
            event(start="2026-10-22T14:00:00").validate()

    def test_reversed_range_is_rejected(self):
        with self.assertRaises(ValidationError):
            event(end="2026-10-22T13:00:00+08:00").validate()

    def test_ambiguous_event_cannot_be_inserted(self):
        incomplete = event(start="", end="", needs_review=True)
        incomplete.validate()
        with self.assertRaises(ValidationError):
            incomplete.validate(for_insert=True)

    def test_invalid_time_type_is_rejected_even_when_ambiguous(self):
        with self.assertRaises(ValidationError):
            event(start=[], end=[], needs_review=True).validate()

    def test_all_day_end_is_exclusive_and_later(self):
        event(start="2026-10-22", end="2026-10-23", all_day=True).validate(for_insert=True)
        with self.assertRaises(ValidationError):
            event(start="2026-10-22", end="2026-10-22", all_day=True).validate()

    def test_missing_schema_fields_are_rejected(self):
        with self.assertRaises(ValidationError):
            CalendarEvent.from_dict({"title": "討論"})

    def test_unknown_analysis_fields_are_rejected(self):
        with self.assertRaises(ValidationError):
            Analysis.from_dict({"summary": ["內容"], "action_items": [], "reply_draft": "", "events": [], "send": True})

    def test_oversized_summary_is_rejected(self):
        with self.assertRaises(ValidationError):
            Analysis.from_dict({"summary": ["x" * 2001], "action_items": [], "reply_draft": "", "events": []})

    def test_message_identity_survives_outlook_folder_move(self):
        mail = MailSnapshot("entry1", "store", "subject", "sender", "body", "2026-10-21", "<stable-id>")
        self.assertEqual(mail.key, replace(mail, entry_id="entry2").key)

    def test_modified_body_invalidates_analysis_cache(self):
        mail = MailSnapshot("entry1", "store", "subject", "sender", "body", "2026-10-21")
        self.assertNotEqual(mail.analysis_key, replace(mail, body="changed").analysis_key)

    def test_modified_native_meeting_time_invalidates_analysis_cache(self):
        mail = MailSnapshot("entry1", "store", "subject", "sender", "body", "2026-10-21",
                            item_type="meeting_request", meeting_context='{"start":"2026-10-22"}')
        self.assertNotEqual(mail.analysis_key, replace(mail, meeting_context='{"start":"2026-10-23"}').analysis_key)
        self.assertNotEqual(mail.analysis_key, replace(mail, item_type="meeting_cancellation").analysis_key)
