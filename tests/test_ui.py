import queue
from dataclasses import replace
import unittest
from unittest.mock import MagicMock, patch

from outlook_maximizer.calendar_api import CalendarError
from outlook_maximizer.ai import AIError
from outlook_maximizer.controller import SelectionState
from outlook_maximizer.demo import demo_data
from outlook_maximizer.models import Analysis, MailSnapshot, MonitorNotice
from outlook_maximizer.settings import Settings
from outlook_maximizer.ui import CompanionApp
from outlook_maximizer.i18n import tr

MAIL = MailSnapshot("current", "store", "subject", "sender", "body", "")


class UILifecycleTests(unittest.TestCase):
    def bare_app(self):
        app = object.__new__(CompanionApp)
        app.language = "zh-TW"
        app._t = lambda text: tr(text, app.language)
        app._first_run = False
        app.qt_app = MagicMock()
        app.settings_window = None
        app.running = True
        app.closed = False
        app.monitor_session = 3
        app.monitor = MagicMock()
        app.connection_status = ""
        app.summary_panel = MagicMock()
        app.reply_panel = MagicMock()
        app.state = SelectionState()
        app.demo = False
        app.panels_visible = True
        app.io_busy = False
        app.calendar_connected = False
        app.messages = queue.Queue()
        app.settings = Settings()
        app._job_serial = {"summary": 0, "reply": 0}
        app._present = MagicMock()
        app._panels_in_front = MagicMock(return_value=True)
        app._update_tray = MagicMock()
        app._load_models = MagicMock()
        app.data_dir = MagicMock()
        app.debounce_timer = MagicMock()
        app._tray_click_timer = MagicMock()
        app.edited_events = {}
        app.calendar_buttons = []
        app.repeat_buttons = {}
        app.calendar_receipts = MagicMock()
        app.calendar_receipts.is_added.return_value = False
        app.copy_button = None
        return app

    def test_late_notice_from_previous_monitor_is_discarded_after_resume(self):
        app = self.bare_app()
        app._receive_mail = MagicMock()
        app._handle_monitor_notice(2, MonitorNotice("mail", mail=MAIL))
        app._receive_mail.assert_not_called()

    def test_notice_from_current_monitor_is_accepted(self):
        app = self.bare_app()
        app._receive_mail = MagicMock()
        app._handle_monitor_notice(3, MonitorNotice("mail", mail=MAIL))
        app._receive_mail.assert_called_once_with(MAIL)

    def test_initial_read_error_can_be_retried_without_a_snapshot(self):
        app = self.bare_app()
        app._handle_monitor_notice(3, MonitorNotice("error", "無法讀取"))
        app.summary_panel.show_error.assert_called_once_with("無法讀取")
        app.retry_analysis()
        app.monitor.request_refresh.assert_called_once()

    def test_disabled_auto_reply_never_submits_reply_job(self):
        app = self.bare_app()
        app.state.select(MAIL)
        app.settings.auto_summarize = True
        app.settings.auto_reply = False
        app.request_summary = MagicMock()
        app.request_reply = MagicMock()
        app._run_auto()
        app.request_summary.assert_called_once()
        app.request_reply.assert_not_called()

    def test_enabled_auto_reply_is_independent_of_auto_summary(self):
        app = self.bare_app()
        app.state.select(MAIL)
        app.settings.auto_summarize = False
        app.settings.auto_reply = True
        app.request_summary = MagicMock()
        app.request_reply = MagicMock()
        app._run_auto()
        app.request_summary.assert_not_called()
        app.request_reply.assert_called_once()

    def test_manual_reply_submits_its_own_job(self):
        app = self.bare_app()
        app.state.select(MAIL)
        app._submit_job = MagicMock()
        app.request_reply()
        app._submit_job.assert_called_once_with("reply")

    def test_cached_reply_reopens_without_new_ai_call(self):
        app = self.bare_app()
        generation = app.state.select(MAIL)
        app.state.accept_reply(generation, "cached draft")
        app._submit_job = MagicMock()
        app._render_reply = MagicMock()
        app.request_reply()
        app._render_reply.assert_called_once()
        app._submit_job.assert_not_called()

    def test_late_reply_cannot_render_on_another_mail(self):
        app = self.bare_app()
        old = app.state.select(MAIL)
        app.state.select(MailSnapshot("next", "store", "", "", "", ""))
        app._render_reply = MagicMock()
        app.messages.put(("reply", old, 0, "old draft", app.state.reply_cancel))
        app._drain()
        app._render_reply.assert_not_called()

    def test_superseded_same_mail_summary_cannot_overwrite_retry(self):
        app = self.bare_app()
        generation = app.state.select(MAIL)
        app._job_serial["summary"] = 2
        app._render_analysis = MagicMock()
        app.messages.put(("analysis", generation, 1, Analysis(("stale",), (), "", ()), app.state.cancel))
        app._drain()
        app._render_analysis.assert_not_called()

    def test_copy_uses_independent_current_reply(self):
        app = self.bare_app()
        generation = app.state.select(MAIL)
        app.state.accept_reply(generation, "current draft")
        app._flash_reply = MagicMock()
        app.copy_reply()
        app.qt_app.clipboard().setText.assert_called_once_with("current draft")

    def test_invalid_calendar_settings_are_not_persisted(self):
        app = self.bare_app()
        app.data_dir = MagicMock()
        with patch.object(Settings, "save") as save:
            with self.assertRaises(CalendarError):
                app._apply_settings(Settings(calendar_id="bad\nvalue"))
            save.assert_not_called()

    def test_pause_invalidates_both_pending_jobs(self):
        app = self.bare_app()
        app.state.select(MAIL)
        summary_cancel, reply_cancel = app.state.cancel, app.state.reply_cancel
        app.debounce_timer = MagicMock()
        app.toggle_monitor()
        self.assertTrue(summary_cancel.is_set())
        self.assertTrue(reply_cancel.is_set())
        self.assertFalse(app.running)

    def test_debounce_cannot_duplicate_an_in_progress_request(self):
        app = self.bare_app()
        app.state.select(MAIL)
        app.settings.auto_reply = True
        app.summary_panel.state = app.reply_panel.state = "loading"
        app._submit_job = MagicMock()
        app._run_auto()
        app._submit_job.assert_not_called()

    def test_forced_retry_can_replace_an_in_progress_summary(self):
        app = self.bare_app()
        app.state.select(MAIL)
        app.summary_panel.state = "loading"
        app.edited_events = {}
        app._submit_job = MagicMock()
        cancelled = app.state.cancel
        app.request_summary(force=True)
        self.assertTrue(cancelled.is_set())
        app._submit_job.assert_called_once_with("summary")

    def test_old_editor_cannot_replace_an_event_after_same_mail_retry(self):
        app = self.bare_app()
        mail, analysis = demo_data()
        generation = app.state.select(mail)
        app.state.accept(generation, analysis)
        app.edited_events = {}
        app._render_analysis = MagicMock()
        with patch("outlook_maximizer.ui.EventEditor") as editor:
            app.edit_event()
            save = editor.call_args.args[1]
        app._job_serial["summary"] += 1
        app.state.restart_summary()
        app.state.accept(generation, analysis)
        save(analysis.events[0])
        self.assertEqual(app.edited_events, {})
        app._render_analysis.assert_not_called()

    def test_current_editor_still_updates_current_event(self):
        app = self.bare_app()
        mail, analysis = demo_data()
        generation = app.state.select(mail)
        app.state.accept(generation, analysis)
        app.edited_events = {}
        app._render_analysis = MagicMock()
        with patch("outlook_maximizer.ui.EventEditor") as editor:
            app.edit_event()
            save = editor.call_args.args[1]
        save(analysis.events[0])
        self.assertEqual(app.edited_events[0], analysis.events[0])
        app._render_analysis.assert_called_once_with(analysis)

    def test_calendar_completion_reenables_open_settings(self):
        app = self.bare_app()
        app.io_busy = True
        app._update_settings_status = MagicMock()
        app._refresh_calendar_buttons = MagicMock()
        app.messages.put(("calendar_done", ("primary", "mail", "start", "end"), {}))
        app._drain()
        self.assertFalse(app.io_busy)
        app._update_settings_status.assert_called_once()

    def test_open_settings_retains_google_status_and_busy_controls(self):
        app = self.bare_app()
        app.calendar = MagicMock()
        app.credentials_configured = False
        app.io_busy = True
        app._update_settings_status = MagicMock()
        with patch("outlook_maximizer.ui.SettingsDialog"):
            app.open_settings()
        self.assertTrue(app._update_settings_status.call_args.args[0])

    def test_provider_switch_discards_old_results_and_respects_manual_toggles(self):
        app = self.bare_app()
        app.settings.auto_summarize = False
        generation = app.state.select(MAIL)
        app.state.accept(generation, Analysis(("old",), (), "old draft", ()))
        summary_cancel, reply_cancel = app.state.cancel, app.state.reply_cancel
        app.request_summary = MagicMock()
        app.request_reply = MagicMock()
        with patch("outlook_maximizer.ui.GoogleCalendarClient"), patch.object(Settings, "save"):
            app._apply_settings(replace(app.settings, ai_provider="antigravity"))
        self.assertTrue(summary_cancel.is_set() and reply_cancel.is_set())
        self.assertIsNone(app.state.analysis)
        self.assertEqual(app.state.reply_draft, "")
        app.request_summary.assert_not_called()
        app.request_reply.assert_not_called()
        self.assertFalse(app.state.accept_reply(generation, "late codex result"))

    def test_language_change_discards_cached_results(self):
        app = self.bare_app()
        app.settings.auto_summarize = False
        generation = app.state.select(MAIL)
        app.state.accept(generation, Analysis(("old summary",), (), "", ()))
        with patch("outlook_maximizer.ui.GoogleCalendarClient"), patch.object(Settings, "save"):
            app._apply_settings(replace(app.settings, ui_language="zh-TW"))
        self.assertIsNone(app.state.analysis)
        self.assertFalse(app.state.accept(generation, Analysis(("late",), (), "", ())))

    def test_active_effort_change_triggers_only_enabled_generation(self):
        app = self.bare_app()
        app.state.select(MAIL)
        app.request_summary = MagicMock()
        app.request_reply = MagicMock()
        with patch("outlook_maximizer.ui.GoogleCalendarClient"), patch.object(Settings, "save"):
            app._apply_settings(replace(app.settings, codex_effort="high"))
        app.request_summary.assert_called_once()
        app.request_reply.assert_not_called()

    def test_appearance_and_inactive_provider_changes_preserve_current_results(self):
        app = self.bare_app()
        generation = app.state.select(MAIL)
        analysis = Analysis(("current",), (), "current draft", ())
        app.state.accept(generation, analysis)
        app.request_summary = MagicMock()
        with patch("outlook_maximizer.ui.GoogleCalendarClient"), patch.object(Settings, "save"):
            app._apply_settings(replace(app.settings, floating_opacity=1.0,
                                        background_color="#112233", antigravity_model="unused"))
        self.assertIs(app.state.analysis, analysis)
        self.assertEqual(app.state.reply_draft, "current draft")
        app.request_summary.assert_not_called()
        self.assertEqual(app.summary_panel.configure_style.call_args.kwargs["background_color"], "#112233")

    def test_claude_changes_invalidate_only_the_active_provider_results(self):
        app = self.bare_app()
        app.settings = replace(app.settings, ai_provider="claude", claude_model="sonnet",
                               auto_summarize=False, auto_reply=False)
        generation = app.state.select(MAIL)
        analysis = Analysis(("current",), (), "draft", ())
        app.state.accept(generation, analysis)
        with patch("outlook_maximizer.ui.GoogleCalendarClient"), patch.object(Settings, "save"):
            app._apply_settings(replace(app.settings, model="other-codex-model"))
            self.assertIs(app.state.analysis, analysis)
            app._apply_settings(replace(app.settings, claude_effort="xhigh"))
        self.assertIsNone(app.state.analysis)
        self.assertEqual(app.state.reply_draft, "")

    def test_paused_monitor_does_not_generate_when_model_or_effort_changes(self):
        app = self.bare_app()
        app.running = False
        app.state.select(MAIL)
        app.settings.auto_reply = True
        app.request_summary = MagicMock()
        app.request_reply = MagicMock()
        with patch("outlook_maximizer.ui.GoogleCalendarClient"), patch.object(Settings, "save"):
            app._apply_settings(replace(app.settings, codex_effort="high"))
        app.request_summary.assert_not_called()
        app.request_reply.assert_not_called()

    def test_selected_source_model_and_effort_reach_worker_with_own_settings_snapshot(self):
        app = self.bare_app()
        app.state.select(MAIL)
        app.settings = replace(app.settings, ai_provider="antigravity",
                               antigravity_model="agy-model", antigravity_effort="high")
        app.ai_pool = MagicMock()
        app.ai_pool.submit.side_effect = lambda run: run()
        app._render_analysis = MagicMock()
        analysis = Analysis(("fresh",), (), "", ())
        with patch("outlook_maximizer.ai.create_summarizer") as factory:
            factory.return_value.analyze.return_value = analysis
            app._submit_job("summary")
            configured = factory.call_args.args[0]
            self.assertIsNot(configured, app.settings)
            self.assertEqual((configured.ai_provider, configured.antigravity_model, configured.antigravity_effort),
                             ("antigravity", "agy-model", "high"))
            factory.return_value.analyze.assert_called_once_with(MAIL, app.state.cancel, include_reply=False)
        app._drain()
        app._render_analysis.assert_called_once_with(analysis)

    def test_provider_setup_error_is_rendered_in_panel_without_qt_exception(self):
        app = self.bare_app()
        app.state.select(MAIL)
        app.ai_pool = MagicMock()
        app.ai_pool.submit.side_effect = lambda run: run()
        with patch("outlook_maximizer.ai.create_summarizer", side_effect=AIError("configuration failure")):
            app._submit_job("reply")
        app._drain()
        app.reply_panel.show_error.assert_called_once_with("configuration failure")

    def test_about_me_change_invalidates_old_summary_and_draft(self):
        app = self.bare_app()
        app.settings.auto_summarize = False
        generation = app.state.select(MAIL)
        app.state.accept(generation, Analysis(("old context",), (), "old draft", ()))
        with patch("outlook_maximizer.ui.GoogleCalendarClient"), patch.object(Settings, "save"):
            app._apply_settings(replace(app.settings, about_me="New role"))
        self.assertIsNone(app.state.analysis)
        self.assertEqual(app.state.reply_draft, "")
        self.assertFalse(app.state.accept_reply(generation, "old context draft"))

    def test_pin_updates_shared_topmost_preference_only(self):
        app = self.bare_app()
        app._apply_settings = MagicMock()
        app._pin_windows(False)
        updated = app._apply_settings.call_args.args[0]
        self.assertFalse(updated.always_on_top)
        self.assertEqual(updated.ai_provider, app.settings.ai_provider)

    def test_receiving_unchanged_snapshot_does_not_clear_or_regenerate(self):
        app = self.bare_app()
        generation = app.state.select(MAIL)
        analysis = Analysis(("cached",), (), "draft", ())
        app.state.accept(generation, analysis)
        app._receive_mail(MAIL)
        self.assertIs(app.state.analysis, analysis)
        app.summary_panel.show_idle.assert_not_called()
        app.debounce_timer.start.assert_not_called()

    def test_hidden_panels_do_not_run_auto_summary_or_reply(self):
        app = self.bare_app()
        app.panels_visible = False
        app.settings.auto_reply = True
        app.state.select(MAIL)
        app.request_summary = MagicMock()
        app.request_reply = MagicMock()
        app._run_auto()
        app.request_summary.assert_not_called()
        app.request_reply.assert_not_called()

    def test_hidden_mail_changes_do_not_schedule_auto_work(self):
        app = self.bare_app()
        app.panels_visible = False
        app._receive_mail(MAIL)
        self.assertIs(app.state.current, MAIL)
        app.debounce_timer.start.assert_not_called()

    def test_hidden_settings_changes_never_generate(self):
        app = self.bare_app()
        app.panels_visible = False
        app.state.select(MAIL)
        app.request_summary = MagicMock()
        app.request_reply = MagicMock()
        with patch("outlook_maximizer.ui.GoogleCalendarClient"), patch.object(Settings, "save"):
            app._apply_settings(replace(app.settings, codex_effort="high", auto_reply=True))
        app.request_summary.assert_not_called()
        app.request_reply.assert_not_called()

    def test_hiding_cancels_loading_jobs_and_makes_panels_usable_again(self):
        app = self.bare_app()
        app.state.select(MAIL)
        old_summary_cancel, old_reply_cancel = app.state.cancel, app.state.reply_cancel
        app.summary_panel.state = app.reply_panel.state = "loading"
        app.toggle_panels()
        self.assertFalse(app.panels_visible)
        self.assertTrue(old_summary_cancel.is_set() and old_reply_cancel.is_set())
        app.debounce_timer.stop.assert_called()
        app.summary_panel.show_idle.assert_called_once()
        app.reply_panel.show_idle.assert_called_once()

    def test_showing_hidden_panels_schedules_enabled_auto_work(self):
        app = self.bare_app()
        app.panels_visible = False
        app.state.select(MAIL)
        app.toggle_panels()
        self.assertTrue(app.panels_visible)
        app.debounce_timer.start.assert_called_once_with(app.settings.debounce_ms)

    def test_hidden_manual_and_direct_job_entrypoints_are_blocked(self):
        app = self.bare_app()
        app.panels_visible = False
        app.state.select(MAIL)
        app.ai_pool = MagicMock()
        app.request_summary(force=True)
        app.request_reply(force=True)
        app._submit_job("summary")
        app._submit_job("reply")
        app.ai_pool.submit.assert_not_called()

    def test_worker_queued_before_hide_never_starts_provider(self):
        app = self.bare_app()
        app.state.select(MAIL)
        app.ai_pool = MagicMock()
        app._submit_job("summary")
        queued = app.ai_pool.submit.call_args.args[0]
        app.summary_panel.state = "loading"
        app.toggle_panels()
        with patch("outlook_maximizer.ai.create_summarizer") as factory:
            queued()
        factory.assert_not_called()

    def test_valid_empty_reply_is_not_generated_again(self):
        app = self.bare_app()
        app.settings.auto_summarize = False
        app.settings.auto_reply = True
        generation = app.state.select(MAIL)
        app.state.accept_reply(generation, "")
        app.request_reply = MagicMock()
        app._run_auto()
        app.request_reply.assert_not_called()

    def test_result_from_hidden_cancelled_job_is_rejected_after_show(self):
        app = self.bare_app()
        app.settings.auto_summarize = False
        generation = app.state.select(MAIL)
        app._job_serial["summary"] = 1
        cancelled = app.state.cancel
        app.summary_panel.state = "loading"
        app._render_analysis = MagicMock()
        app.toggle_panels()
        app.toggle_panels()
        app.messages.put(("analysis", generation, 1, Analysis(("old cancelled result",), (), "", ()), cancelled))
        app._drain()
        app._render_analysis.assert_not_called()

    def test_monitor_error_does_not_allow_cancelled_completion_after_show(self):
        for kind in ("analysis", "ai_error"):
            with self.subTest(kind=kind):
                app = self.bare_app()
                app.settings.auto_summarize = False
                generation = app.state.select(MAIL)
                app._job_serial["summary"] = 1
                cancelled = app.state.cancel
                app.summary_panel.state = "error"
                app._render_analysis = MagicMock()
                app.hide_panels()
                app.show_panels()
                if kind == "analysis":
                    app.messages.put((kind, generation, 1, Analysis(("cancelled",), (), "", ()), cancelled))
                else:
                    app.messages.put((kind, "summary", generation, 1, "cancelled error", cancelled))
                app._drain()
                app._render_analysis.assert_not_called()
                app.summary_panel.show_error.assert_not_called()

    def test_calendar_editor_save_remains_valid_after_hide_and_show(self):
        app = self.bare_app()
        mail, analysis = demo_data()
        generation = app.state.select(mail)
        app.state.accept(generation, analysis)
        app._render_analysis = MagicMock()
        with patch("outlook_maximizer.ui.EventEditor") as editor:
            app.edit_event()
            save = editor.call_args.args[1]
        app.hide_panels()
        app.show_panels()
        updated = replace(analysis.events[0], description="User's pending edit")
        save(updated)
        self.assertEqual(app.state.analysis.events[0], updated)
        app._render_analysis.assert_called_once()
