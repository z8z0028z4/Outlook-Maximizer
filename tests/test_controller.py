import unittest

from outlook_maximizer.controller import SelectionState
from outlook_maximizer.models import Analysis, MailSnapshot


def mail(identity):
    return MailSnapshot(identity, "store", "subject", "sender", "body", "2026-10-21")


RESULT = Analysis(("摘要",), (), "回覆", ())


class SelectionTests(unittest.TestCase):
    def test_switch_cancels_previous_generation(self):
        state = SelectionState()
        state.select(mail("a"))
        old_cancel = state.cancel
        state.select(mail("b"))
        self.assertTrue(old_cancel.is_set())
        self.assertFalse(state.cancel.is_set())

    def test_late_result_cannot_overwrite_another_message(self):
        state = SelectionState()
        first = state.select(mail("a"))
        state.select(mail("b"))
        self.assertFalse(state.accept(first, RESULT))
        self.assertIsNone(state.analysis)

    def test_cached_result_restored_on_reopen(self):
        state = SelectionState()
        generation = state.select(mail("a"))
        self.assertTrue(state.accept(generation, RESULT))
        state.select(mail("b"))
        state.select(mail("a"))
        self.assertEqual(state.analysis, RESULT)

    def test_cache_evicts_old_mail(self):
        state = SelectionState(cache_size=1)
        state.accept(state.select(mail("a")), RESULT)
        state.accept(state.select(mail("b")), RESULT)
        state.select(mail("a"))
        self.assertIsNone(state.analysis)

    def test_clearing_selection_rejects_pending_result(self):
        state = SelectionState()
        generation = state.select(mail("a"))
        state.select(None)
        self.assertFalse(state.accept(generation, RESULT))

    def test_switch_also_cancels_reply_request(self):
        state = SelectionState()
        state.select(mail("a"))
        previous = state.reply_cancel
        state.select(mail("b"))
        self.assertTrue(previous.is_set())
        self.assertFalse(state.reply_cancel.is_set())

    def test_late_reply_cannot_cross_mail_selection(self):
        state = SelectionState()
        old = state.select(mail("a"))
        state.select(mail("b"))
        self.assertFalse(state.accept_reply(old, "old draft"))
        self.assertEqual(state.reply_draft, "")

    def test_cancel_pending_preserves_selection_results_and_edit_generation(self):
        state = SelectionState()
        current = mail("a")
        generation = state.select(current)
        state.accept(generation, RESULT)
        old_cancel, old_reply_cancel = state.cancel, state.reply_cancel
        state.cancel_pending()
        self.assertTrue(old_cancel.is_set() and old_reply_cancel.is_set())
        self.assertFalse(state.cancel.is_set() or state.reply_cancel.is_set())
        self.assertEqual(state.generation, generation)
        self.assertIs(state.current, current)
        self.assertIs(state.analysis, RESULT)
        self.assertTrue(state.accept(generation, RESULT))

    def test_empty_reply_stays_completed_after_cancelling_pending_work(self):
        state = SelectionState()
        generation = state.select(mail("a"))
        self.assertFalse(state.reply_received)
        state.accept_reply(generation, "")
        state.cancel_pending()
        self.assertTrue(state.reply_received)
        state.select(mail("b"))
        self.assertFalse(state.reply_received)
        state.select(mail("a"))
        self.assertTrue(state.reply_received)

    def test_reply_cache_restored_independently_of_summary(self):
        state = SelectionState()
        generation = state.select(mail("a"))
        self.assertTrue(state.accept_reply(generation, "draft"))
        state.select(mail("b"))
        state.select(mail("a"))
        self.assertIsNone(state.analysis)
        self.assertEqual(state.reply_draft, "draft")

    def test_summary_restart_preserves_reply_and_mail_generation(self):
        state = SelectionState()
        generation = state.select(mail("a"))
        state.accept(generation, RESULT)
        previous = state.cancel
        reply_cancel = state.reply_cancel
        current = state.restart_summary()
        self.assertTrue(previous.is_set())
        self.assertFalse(current.is_set())
        self.assertIs(state.cancel, current)
        self.assertIs(state.reply_cancel, reply_cancel)
        self.assertEqual(state.generation, generation)
        self.assertEqual(state.reply_draft, RESULT.reply_draft)
        self.assertIsNone(state.analysis)

    def test_reply_restart_preserves_summary(self):
        state = SelectionState()
        generation = state.select(mail("a"))
        state.accept(generation, RESULT)
        previous = state.reply_cancel
        summary_cancel = state.cancel
        current = state.restart_reply()
        self.assertTrue(previous.is_set())
        self.assertFalse(current.is_set())
        self.assertIs(state.reply_cancel, current)
        self.assertIs(state.cancel, summary_cancel)
        self.assertEqual(state.analysis, RESULT)
        self.assertEqual(state.reply_draft, "")

    def test_legacy_summary_draft_does_not_overwrite_explicit_reply(self):
        state = SelectionState()
        generation = state.select(mail("a"))
        state.accept_reply(generation, "newer explicit draft")
        state.accept(generation, RESULT)
        self.assertEqual(state.reply_draft, "newer explicit draft")

    def test_reply_rejected_after_cancellation(self):
        state = SelectionState()
        generation = state.select(mail("a"))
        state.reply_cancel.set()
        self.assertFalse(state.accept_reply(generation, "draft"))

    def test_explicit_empty_reply_is_not_replaced_by_legacy_summary_draft(self):
        state = SelectionState()
        generation = state.select(mail("a"))
        self.assertTrue(state.accept_reply(generation, ""))
        state.accept(generation, RESULT)
        self.assertEqual(state.reply_draft, "")

    def test_reply_cache_is_bounded(self):
        state = SelectionState(cache_size=1)
        state.accept_reply(state.select(mail("a")), "draft a")
        state.accept_reply(state.select(mail("b")), "draft b")
        state.select(mail("a"))
        self.assertEqual(state.reply_draft, "")

    def test_changed_body_does_not_reuse_prior_reply(self):
        from dataclasses import replace
        state = SelectionState()
        original = mail("a")
        state.accept_reply(state.select(original), "draft for old body")
        state.select(replace(original, body="new body"))
        self.assertEqual(state.reply_draft, "")

    def test_provider_reset_cancels_jobs_and_discards_all_cached_results(self):
        state = SelectionState()
        state.accept(state.select(mail("a")), RESULT)
        generation = state.select(mail("b"))
        state.accept(generation, RESULT)
        summary_cancel, reply_cancel = state.cancel, state.reply_cancel
        state.clear_results()
        self.assertTrue(summary_cancel.is_set())
        self.assertTrue(reply_cancel.is_set())
        self.assertIsNone(state.analysis)
        self.assertEqual(state.reply_draft, "")
        self.assertEqual(state.current.key, mail("b").key)
        self.assertFalse(state.accept(generation, RESULT))
        self.assertFalse(state.accept_reply(generation, "old provider draft"))
        state.select(mail("a"))
        self.assertIsNone(state.analysis)
        self.assertEqual(state.reply_draft, "")
