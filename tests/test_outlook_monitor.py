from datetime import datetime, timedelta, timezone
import json
import os
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from outlook_maximizer.outlook_monitor import (
    OutlookMonitor,
    _BODY_LIMIT,
    _EventSink,
    _attach_existing,
    _current_item,
    _foreground_allowed,
    _item_identity,
    _received_time,
    _snapshot,
    diagnose_outlook,
)


def mail_item(entry_id="entry-1", body="Hello"):
    return SimpleNamespace(
        Class=43,
        EntryID=entry_id,
        Parent=SimpleNamespace(StoreID="store-1"),
        Subject="Test mail",
        SenderName="Sender",
        Body=body,
        ReceivedTime=datetime(2026, 10, 21, 9, 0, tzinfo=timezone(timedelta(hours=8))),
        PropertyAccessor=SimpleNamespace(GetProperty=lambda _name: "<test@example.invalid>"),
    )


def explorer_app(item=None, count=1):
    selection = SimpleNamespace(Count=count, Item=lambda _index: item or mail_item())
    window = SimpleNamespace(Class=34, Selection=selection)
    return SimpleNamespace(ActiveWindow=lambda: window,
                           Explorers=SimpleNamespace(Count=1, Item=lambda _index: window),
                           Inspectors=SimpleNamespace(Count=0))


def meeting_item(item_class=53):
    item = mail_item()
    item.Class = item_class
    appointment = SimpleNamespace(
        Class=26, StartUTC=datetime(2026, 10, 22, 3, 0, tzinfo=timezone.utc),
        EndUTC=datetime(2026, 10, 22, 4, 0, tzinfo=timezone.utc), AllDayEvent=False,
        Location="Microsoft Teams", Organizer="Organizer", ResponseStatus=5, IsRecurring=False,
    )
    item.GetAssociatedAppointment = Mock(return_value=appointment)
    return item


class SourceTests(unittest.TestCase):
    def test_active_reading_pane_wins_over_open_but_inactive_inspector(self):
        selected = mail_item()
        app = explorer_app(selected)
        app.ActiveInspector = lambda: SimpleNamespace(CurrentItem=mail_item("other"))
        self.assertIs(_current_item(app), selected)

    def test_active_inspector_uses_its_current_item(self):
        selected = mail_item()
        app = SimpleNamespace(ActiveWindow=lambda: SimpleNamespace(Class=35, CurrentItem=selected))
        self.assertIs(_current_item(app), selected)

    def test_multiple_selection_does_not_pick_an_arbitrary_mail(self):
        self.assertIsNone(_current_item(explorer_app(count=2)))

    def test_non_mail_items_are_filtered(self):
        self.assertIsNone(_current_item(explorer_app(SimpleNamespace(Class=48))))

    def test_meeting_request_and_responses_are_supported(self):
        for item_class in (26, 53, 54, 55, 56, 57, 181):
            item = meeting_item(item_class)
            self.assertIs(_current_item(explorer_app(item)), item)

    def test_meeting_snapshot_reads_associated_appointment_without_adding_it(self):
        item = meeting_item()
        snapshot = _snapshot(item, _item_identity(item))
        item.GetAssociatedAppointment.assert_called_once_with(False)
        details = json.loads(snapshot.meeting_context)
        self.assertEqual(snapshot.item_type, "meeting_request")
        self.assertEqual(details["start"], "2026-10-22T03:00:00+00:00")
        self.assertEqual(details["end"], "2026-10-22T04:00:00+00:00")
        self.assertEqual(details["location"], "Microsoft Teams")

    def test_meeting_native_metadata_is_available_when_body_has_no_times(self):
        item = meeting_item()
        item.Body = "Please see the invitation above."
        snapshot = _snapshot(item, _item_identity(item))
        self.assertIn("2026-10-22", snapshot.meeting_context)
        self.assertEqual(snapshot.body, item.Body)

    def test_meeting_uses_read_only_mapi_fallback_when_no_associated_appointment(self):
        item = meeting_item()
        item.GetAssociatedAppointment.return_value = None
        properties = {
            "820D0040": datetime(2026, 10, 22, 3, 0),
            "820E0040": datetime(2026, 10, 22, 4, 0),
            "8215000B": False,
            "8208001F": "Room A",
        }
        def get_property(name):
            if name.rsplit("/", 1)[-1] in properties:
                return properties[name.rsplit("/", 1)[-1]]
            raise RuntimeError("property absent")
        item.PropertyAccessor = SimpleNamespace(GetProperty=Mock(side_effect=get_property))
        snapshot = _snapshot(item, _item_identity(item))
        details = json.loads(snapshot.meeting_context)
        self.assertEqual(details["start"], "2026-10-22T03:00:00+00:00")
        self.assertEqual(details["end"], "2026-10-22T04:00:00+00:00")
        self.assertEqual(details["location"], "Room A")
        item.GetAssociatedAppointment.assert_called_once_with(False)

    def test_all_day_appointment_preserves_exclusive_native_dates(self):
        item = meeting_item(26)
        item.AllDayEvent = True
        item.Start = datetime(2026, 10, 22, 0, 0)
        item.End = datetime(2026, 10, 23, 0, 0)
        item.Organizer = "Organizer"
        item.Location = "Room A"
        snapshot = _snapshot(item, _item_identity(item))
        details = json.loads(snapshot.meeting_context)
        self.assertEqual(snapshot.item_type, "appointment")
        self.assertEqual((details["start"], details["end"], details["all_day"]),
                         ("2026-10-22", "2026-10-23", True))
        item.GetAssociatedAppointment.assert_not_called()

    def test_unsaved_compose_window_has_no_identity(self):
        self.assertIsNone(_item_identity(mail_item("")))

    def test_snapshot_preserves_timezone_and_caps_body(self):
        selected = mail_item(body="x" * (_BODY_LIMIT + 100))
        result = _snapshot(selected, _item_identity(selected))
        self.assertEqual(result.received_at, "2026-10-21T09:00:00+08:00")
        self.assertEqual(len(result.body), _BODY_LIMIT)
        self.assertIn("已截斷", result.body)

    def test_unknown_received_time_stays_unknown(self):
        self.assertEqual(_received_time(None), "")

    def test_sender_access_failure_does_not_discard_readable_body(self):
        class ProtectedSender:
            def __getattr__(self, name):
                if name == "SenderName":
                    raise RuntimeError("protected private information")
                return getattr(mail_item(), name)
        result = _snapshot(ProtectedSender(), ("store-1", "entry-1"))
        self.assertEqual(result.body, "Hello")
        self.assertEqual(result.sender, "（寄件者資訊不可讀）")

    def test_attach_never_creates_an_outlook_instance(self):
        pythoncom = Mock()
        dynamic = Mock()
        result = _attach_existing(pythoncom, dynamic)
        pythoncom.GetActiveObject.assert_called_once_with("Outlook.Application")
        dynamic.Dispatch.assert_called_once_with(pythoncom.GetActiveObject.return_value.QueryInterface.return_value)
        self.assertIs(result, dynamic.Dispatch.return_value)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.notices = []
        self.monitor = OutlookMonitor(self.notices.append)

    def mail_notices(self):
        return [notice for notice in self.notices if notice.kind == "mail"]

    def test_unchanged_selection_is_not_read_or_published_again(self):
        app = explorer_app()
        self.monitor._poll(app, allowed=True)
        with patch("outlook_maximizer.outlook_monitor._snapshot") as snapshot:
            self.monitor._poll(app, allowed=True)
        snapshot.assert_not_called()
        self.assertEqual(len(self.mail_notices()), 1)

    def test_content_recheck_publishes_only_when_content_changed(self):
        item = mail_item()
        app = explorer_app(item)
        self.monitor._poll(app, allowed=True)
        self.monitor._last_snapshot_at = 0
        self.monitor._poll(app, allowed=True)
        self.assertEqual(len(self.mail_notices()), 1)
        item.Body = "Updated content"
        self.monitor._last_snapshot_at = 0
        self.monitor._poll(app, allowed=True)
        self.assertEqual(len(self.mail_notices()), 2)

    def test_explicit_refresh_republishes_the_same_mail(self):
        app = explorer_app()
        self.monitor._poll(app, allowed=True)
        self.monitor._poll(app, allowed=True, force=True)
        self.assertEqual(len(self.mail_notices()), 2)

    def test_background_selection_changes_are_read(self):
        app = explorer_app(mail_item("background"))
        self.monitor._poll(app, allowed=False)
        self.assertEqual(len(self.mail_notices()), 1)

    def test_leaving_and_returning_to_outlook_retains_current_result(self):
        app = explorer_app()
        self.monitor._poll(app, allowed=True)
        self.monitor._poll(app, allowed=False)
        self.monitor._poll(app, allowed=True)
        self.assertEqual(len(self.mail_notices()), 1)
        self.assertFalse(any(notice.kind == "selection_cleared" for notice in self.notices))

    def test_transient_active_window_none_does_not_clear_last_selection(self):
        app = explorer_app()
        self.monitor._poll(app, allowed=True)
        app.ActiveWindow = lambda: None
        self.monitor._poll(app, allowed=False)
        self.assertEqual(len(self.mail_notices()), 1)
        self.assertIsNotNone(self.monitor._last_identity)
        self.assertFalse(any(notice.kind == "selection_cleared" for notice in self.notices))

    def test_retained_inspector_closure_can_fall_back_to_only_remaining_explorer(self):
        app = explorer_app(mail_item("explorer"))
        inspector = SimpleNamespace(Class=35, CurrentItem=mail_item("inspector"))
        app.ActiveWindow = lambda: inspector
        self.monitor._poll(app)
        app.ActiveWindow = lambda: None
        self.monitor._poll(app)
        self.assertEqual(self.mail_notices()[-1].mail.entry_id, "explorer")

    def test_selection_can_change_in_retained_background_explorer(self):
        item = mail_item()
        app = explorer_app(item)
        self.monitor._poll(app, allowed=True)
        app.ActiveWindow = lambda: None
        item.EntryID = "another-mail"
        item.Body = "Another body"
        self.monitor._poll(app, allowed=False)
        self.assertEqual(len(self.mail_notices()), 2)
        self.assertEqual(self.mail_notices()[-1].mail.entry_id, "another-mail")

    def test_actual_outlook_window_closure_clears_current_selection(self):
        app = explorer_app()
        self.monitor._poll(app, allowed=True)
        app.ActiveWindow = lambda: None
        app.Explorers.Count = 0
        self.monitor._poll(app, allowed=False)
        self.assertIsNone(self.monitor._last_identity)
        self.assertEqual(self.notices[-1].kind, "selection_cleared")

    def test_temporary_active_window_read_error_preserves_result_then_recovers(self):
        app = explorer_app()
        original = app.ActiveWindow
        self.monitor._poll(app, allowed=True)
        app.ActiveWindow = Mock(side_effect=RuntimeError("Outlook busy"))
        self.monitor._poll(app, allowed=False)
        app.ActiveWindow = original
        self.monitor._poll(app, allowed=True)
        self.assertEqual(len(self.mail_notices()), 1)
        self.assertIsNotNone(self.monitor._last_identity)

    def test_non_mail_selection_clears_previous_mail(self):
        self.monitor._poll(explorer_app(), allowed=True)
        self.monitor._poll(explorer_app(SimpleNamespace(Class=48)), allowed=True)
        self.assertIsNone(self.monitor._last_identity)
        self.assertEqual(self.notices[-1].kind, "selection_cleared")

    def test_failed_body_is_not_repeatedly_retried_until_explicit_refresh(self):
        class ProtectedBody:
            def __getattr__(self, name):
                if name == "Body":
                    raise RuntimeError("Do not display this private COM exception")
                return getattr(mail_item(), name)
        app = explorer_app(ProtectedBody())
        self.monitor._poll(app, allowed=True)
        self.monitor._poll(app, allowed=True)
        errors = [notice for notice in self.notices if notice.kind == "error"]
        self.assertEqual(len(errors), 1)
        self.assertNotIn("private", errors[0].message)
        self.monitor._poll(app, allowed=True, force=True)
        self.assertEqual(len([notice for notice in self.notices if notice.kind == "error"]), 2)

    def test_new_mail_event_emits_no_snapshot_or_identifier(self):
        self.monitor._on_com_event("NewMailEx", "private-entry-id")
        notice = self.notices[-1]
        self.assertEqual(notice.kind, "new_mail")
        self.assertIsNone(notice.mail)
        self.assertNotIn("private-entry-id", notice.message)

    def test_application_quit_requests_reconnect(self):
        self.monitor._on_com_event("Quit")
        self.assertTrue(self.monitor._quit.is_set())

    def test_callback_failure_does_not_escape_com_event(self):
        monitor = OutlookMonitor(Mock(side_effect=RuntimeError("UI shutting down")))
        monitor._on_com_event("NewMailEx", "anything")

    def test_status_is_not_spammed(self):
        self.monitor._status("Waiting")
        self.monitor._status("Waiting")
        self.assertEqual(len(self.notices), 1)

    def test_disconnected_outlook_is_reconnected_on_the_same_sta_thread(self):
        pythoncom = Mock()
        first, second = object(), object()
        finished = threading.Event()

        def after_reconnect(*_args, **_kwargs):
            if not getattr(after_reconnect, "failed_once", False):
                after_reconnect.failed_once = True
                raise RuntimeError("disconnected")
            finished.set()
            self.monitor.stop()

        ticks = iter(range(100))
        with (
            patch("outlook_maximizer.outlook_monitor._win32_modules", return_value=(pythoncom, Mock(), Mock(), Mock(), Mock())),
            patch("outlook_maximizer.outlook_monitor._attach_existing", side_effect=[first, second]) as attach,
            patch("outlook_maximizer.outlook_monitor._sync_connections", return_value=0),
            patch("outlook_maximizer.outlook_monitor._foreground_allowed", return_value=True),
            patch("outlook_maximizer.outlook_monitor.time.monotonic", side_effect=lambda: next(ticks)),
            patch.object(self.monitor, "_poll", side_effect=after_reconnect),
        ):
            self.monitor.start()
            self.assertTrue(finished.wait(2))
            self.monitor.stop()
        self.assertEqual(attach.call_count, 2)
        pythoncom.CoInitializeEx.assert_called_once()
        pythoncom.CoUninitialize.assert_called_once()

    def test_transient_reconnect_preserves_existing_selection_until_outlook_returns(self):
        self.monitor._poll(explorer_app())
        pythoncom = Mock()
        def finish(*_args, **_kwargs):
            self.monitor.stop()
        ticks = iter(range(100))
        with (
            patch("outlook_maximizer.outlook_monitor._win32_modules", return_value=(pythoncom, Mock(), Mock(), Mock(), Mock())),
            patch("outlook_maximizer.outlook_monitor._attach_existing", side_effect=[object(), object()]),
            patch("outlook_maximizer.outlook_monitor._sync_connections", side_effect=[RuntimeError("busy"), 0]),
            patch("outlook_maximizer.outlook_monitor.time.monotonic", side_effect=lambda: next(ticks)),
            patch.object(self.monitor, "_poll", side_effect=finish),
        ):
            self.monitor._run()
        self.assertIsNotNone(self.monitor._last_identity)
        self.assertFalse(any(notice.kind == "selection_cleared" for notice in self.notices))

    def test_confirmed_outlook_unavailable_clears_prior_selection(self):
        class NotRunning(RuntimeError):
            hresult = -2147221021  # MK_E_UNAVAILABLE from GetActiveObject.
        self.monitor._poll(explorer_app())
        def unavailable(*_args):
            self.monitor.stop()
            raise NotRunning("private COM text")
        with (
            patch("outlook_maximizer.outlook_monitor._win32_modules", return_value=(Mock(), Mock(), Mock(), Mock(), Mock())),
            patch("outlook_maximizer.outlook_monitor._attach_existing", side_effect=unavailable),
        ):
            self.monitor._run()
        self.assertIsNone(self.monitor._last_identity)
        self.assertEqual([notice.kind for notice in self.notices].count("selection_cleared"), 1)


class EventAndDiagnosticTests(unittest.TestCase):
    def test_event_sink_maps_only_selected_event_names(self):
        callback = Mock()
        sink = _EventSink("iid", {64181: "NewMailEx"}, callback)
        sink.NewMailEx("entry")
        callback.assert_called_once_with("NewMailEx", "entry")
        with self.assertRaises(AttributeError):
            sink.ItemSend(None, False)

    @unittest.skipUnless(os.name == "nt", "Windows COM gateway")
    def test_native_dispatch_gateway_delivers_new_mail_event(self):
        import pythoncom
        import pywintypes
        from win32com.server.util import wrap

        callback = Mock()
        pythoncom.CoInitialize()
        try:
            sink = _EventSink(pywintypes.IID("{0006302C-0000-0000-C000-000000000046}"), {64181: "NewMailEx"}, callback)
            gateway = wrap(sink)
            gateway.Invoke(64181, 0, pythoncom.DISPATCH_METHOD, True, "synthetic-entry")
            callback.assert_called_once_with("NewMailEx", "synthetic-entry")
        finally:
            pythoncom.CoUninitialize()

    def test_foreground_companion_is_allowed(self):
        gui = SimpleNamespace(GetForegroundWindow=lambda: 1)
        process = SimpleNamespace(GetWindowThreadProcessId=lambda _window: (1, os.getpid()))
        self.assertTrue(_foreground_allowed(gui, process))

    def test_foreground_other_app_is_not_allowed(self):
        gui = SimpleNamespace(GetForegroundWindow=lambda: 1, GetClassName=lambda _window: "Chrome_WidgetWin_1")
        process = SimpleNamespace(GetWindowThreadProcessId=lambda _window: (1, os.getpid() + 100))
        self.assertFalse(_foreground_allowed(gui, process))

    def test_diagnostic_error_reports_type_without_private_exception_text(self):
        with patch("outlook_maximizer.outlook_monitor._win32_modules", side_effect=RuntimeError("private-token")):
            result = diagnose_outlook(timeout=1)
        self.assertEqual(result, {"status": "unavailable", "error_type": "RuntimeError"})


if __name__ == "__main__":
    unittest.main()
