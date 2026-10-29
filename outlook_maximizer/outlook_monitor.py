"""Read-only, per-user classic Outlook monitoring on a dedicated COM STA."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import queue
import threading
import time
from typing import Any, Callable

from .models import MailSnapshot, MonitorNotice


_MAIL_ITEM = 43
_ITEM_TYPES = {
    43: "mail", 26: "appointment", 53: "meeting_request", 54: "meeting_cancellation",
    55: "meeting_response_negative", 56: "meeting_response_positive",
    57: "meeting_response_tentative", 181: "meeting_forward",
}
_EXPLORER = 34
_INSPECTOR = 35
_BODY_LIMIT = 60_000
_CONTENT_REFRESH_SECONDS = 30.0
_MESSAGE_ID_PROPERTIES = (
    "http://schemas.microsoft.com/mapi/proptag/0x1035001F",
    "http://schemas.microsoft.com/mapi/proptag/0x1035001E",
)
_APPOINTMENT_PROPERTIES = "http://schemas.microsoft.com/mapi/id/{00062002-0000-0000-C000-000000000046}/"


class _MailReadError(RuntimeError):
    """Do not include COM exception text: it can contain message information."""


def _win32_modules() -> tuple[Any, Any, Any, Any, Any]:
    import pythoncom
    import win32gui
    import win32process
    from win32com.client import dynamic
    from win32com.server.util import wrap

    return pythoncom, dynamic, win32gui, win32process, wrap


def _attach_existing(pythoncom: Any, dynamic: Any) -> Any:
    # Dispatch("Outlook.Application") would launch Outlook. Attach only.
    unknown = pythoncom.GetActiveObject("Outlook.Application")
    return dynamic.Dispatch(unknown.QueryInterface(pythoncom.IID_IDispatch))


def _source_window(app: Any, previous: Any = None) -> tuple[Any, bool]:
    """Retain Outlook's last source while another app owns the foreground."""
    window = app.ActiveWindow()
    if window is not None:
        return window, False
    try:
        explorers, inspectors = app.Explorers, app.Inspectors
        explorer_count, inspector_count = explorers.Count, inspectors.Count
        if explorer_count + inspector_count == 0:
            return None, True
        if explorer_count + inspector_count == 1:
            collection = explorers if explorer_count else inspectors
            return collection.Item(1), False
        if previous is not None:
            return previous, False
    except Exception:
        # Unknown/busy is not an actual selection clear.
        return previous, False
    return None, False


def _window_item(window: Any) -> Any | None:
    if window is None:
        return None
    window_class = window.Class
    if window_class == _INSPECTOR:
        item = window.CurrentItem
    elif window_class == _EXPLORER:
        selection = window.Selection
        if selection.Count != 1:
            return None
        item = selection.Item(1)
    else:
        return None
    if item is None or item.Class not in _ITEM_TYPES:
        return None
    return item


def _current_item(app: Any) -> Any | None:
    window, _closed = _source_window(app)
    return _window_item(window)


def _item_identity(item: Any) -> tuple[str, str] | None:
    entry_id = str(item.EntryID or "")
    if not entry_id:  # An unsaved compose window is not an opened mail.
        return None
    return str(item.Parent.StoreID or ""), entry_id


def _received_time(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.astimezone()
        return value.isoformat()
    # An unknown timestamp stays unknown; never invent the current date.
    return ""


def _field(obj: Any, name: str) -> Any:
    try:
        return getattr(obj, name)
    except Exception:
        return None


def _property(item: Any, suffix: str) -> Any:
    try:
        return item.PropertyAccessor.GetProperty(_APPOINTMENT_PROPERTIES + suffix)
    except Exception:
        return None


def _meeting_datetime(value: Any, *, utc: bool) -> datetime | None:
    if not isinstance(value, datetime) or value.year >= 4500:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc) if utc else value.astimezone()
    return value


def _meeting_context(item: Any, item_type: str) -> str:
    if item_type == "mail":
        return ""
    source = item if item_type == "appointment" else None
    if source is None:
        try:
            # False obtains metadata without adding the invitation to Outlook's calendar.
            source = item.GetAssociatedAppointment(False)
        except Exception:
            source = None
    all_day = _field(source, "AllDayEvent")
    if type(all_day) is not bool:
        all_day = _property(item, "8215000B")
    all_day = all_day if type(all_day) is bool else False
    details: dict[str, Any] = {"all_day": all_day}
    for key, local_name, utc_name, property_id in (
        ("start", "Start", "StartUTC", "820D0040"),
        ("end", "End", "EndUTC", "820E0040"),
    ):
        value = _meeting_datetime(_field(source, local_name), utc=False) if all_day else None
        if value is None:
            value = _meeting_datetime(_field(source, utc_name), utc=True)
        if value is None:
            value = _meeting_datetime(_field(source, local_name), utc=False)
        if value is None:
            value = _meeting_datetime(_property(item, property_id), utc=True)
            if value is not None and all_day:
                value = value.astimezone()
        if value is not None:
            details[key] = value.date().isoformat() if all_day else value.astimezone(timezone.utc).isoformat()
    if not all_day and ("start" in details or "end" in details):
        details["timezone"] = "UTC"
    location = _field(source, "Location")
    if not isinstance(location, str):
        location = _property(item, "8208001F")
    if not isinstance(location, str):
        location = _property(item, "8208001E")
    organizer = _field(source, "Organizer") or _field(item, "SenderName")
    for key, value, limit in (("location", location, 2000), ("organizer", organizer, 500)):
        if isinstance(value, str) and value:
            details[key] = value[:limit]
    response_status = _field(source, "ResponseStatus")
    if type(response_status) is int and 0 <= response_status <= 5:
        details["response_status"] = response_status
    recurring = _field(source, "IsRecurring")
    if type(recurring) is bool:
        details["is_recurring"] = recurring
    return json.dumps(details, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _snapshot(item: Any, identity: tuple[str, str]) -> MailSnapshot:
    item_type = _ITEM_TYPES.get(item.Class, "mail")
    try:
        body = str(item.Body or "")
    except Exception as exc:
        raise _MailReadError("目前信件的本文無法讀取。") from exc
    if len(body) > _BODY_LIMIT:
        marker = "\n[本文過長，後續內容已截斷]"
        body = body[: _BODY_LIMIT - len(marker)] + marker
    try:
        sender = str(item.SenderName or "")[:500]
    except Exception:
        organizer = _field(item, "Organizer") if item_type == "appointment" else None
        sender = organizer[:500] if isinstance(organizer, str) else "（寄件者資訊不可讀）"
    try:
        received_at = _received_time(item.ReceivedTime)
    except Exception:
        received_at = ""
    internet_message_id = ""
    try:
        accessor = item.PropertyAccessor
        for property_name in _MESSAGE_ID_PROPERTIES:
            try:
                internet_message_id = str(accessor.GetProperty(property_name) or "")[:2000]
                if internet_message_id:
                    break
            except Exception:
                continue
    except Exception:
        pass
    return MailSnapshot(
        entry_id=identity[1],
        store_id=identity[0],
        subject=str(item.Subject or "（無主旨）")[:1000],
        sender=sender,
        body=body,
        received_at=received_at,
        internet_message_id=internet_message_id,
        item_type=item_type,
        meeting_context=_meeting_context(item, item_type),
    )


def _foreground_allowed(win32gui: Any, win32process: Any) -> bool:
    """Allow Outlook and this Python GUI, but not background mail navigation."""
    try:
        window = win32gui.GetForegroundWindow()
        if not window:
            return False
        _, process_id = win32process.GetWindowThreadProcessId(window)
        if process_id == os.getpid():
            return True
        return win32gui.GetClassName(window).casefold() == "rctrl_renwnd32"
    except Exception:
        return False


class _EventSink:
    """Minimal IDispatch sink whose DISPIDs come from Outlook's own typelib."""

    def __init__(self, iid: Any, methods: dict[int, str], callback: Callable[..., None]):
        self._com_interfaces_ = [iid]
        self._public_methods_ = list(methods.values())
        self._dispid_to_func_ = methods
        self._callback = callback

    def __getattr__(self, name: str) -> Any:
        if name in self._public_methods_:
            def handle(*args: Any) -> None:
                self._callback(name, *args)
            return handle
        raise AttributeError(name)


class _EventConnection:
    """Direct connection points avoid generating or importing makepy files."""

    def __init__(
        self,
        kind: str,
        obj: Any,
        wanted: set[str],
        callback: Callable[..., None],
        pythoncom: Any,
        wrap: Any,
    ):
        self.kind = kind
        self.identity = obj._oleobj_.QueryInterface(pythoncom.IID_IUnknown)
        self.point = None
        self.cookie = None
        self.sink = None
        self.gateway = None
        library = obj._oleobj_.GetTypeInfo().GetContainingTypeLib()[0]
        container = obj._oleobj_.QueryInterface(pythoncom.IID_IConnectionPointContainer)
        points = container.EnumConnectionPoints()
        while True:
            batch = points.Next(1)
            if not batch:
                break
            point = batch[0]
            iid = point.GetConnectionInterface()
            info = library.GetTypeInfoOfGuid(iid)
            methods = {
                info.GetFuncDesc(index)[0]: info.GetNames(info.GetFuncDesc(index)[0])[0]
                for index in range(info.GetTypeAttr()[6])
            }
            if not wanted.issubset(methods.values()):
                continue
            # Do not handle unrelated events, particularly send/cancel events.
            selected = {dispid: name for dispid, name in methods.items() if name in wanted}
            self.sink = _EventSink(iid, selected, callback)
            self.gateway = wrap(self.sink)
            self.cookie = point.Advise(self.gateway)
            self.point = point
            return
        raise RuntimeError("Outlook 未提供指定的事件介面。")

    def close(self) -> None:
        if self.point is not None and self.cookie is not None:
            try:
                self.point.Unadvise(self.cookie)
            except Exception:
                pass
        self.point = self.cookie = self.sink = self.gateway = None
        self.identity = None


def _sync_connections(
    app: Any,
    connections: list[_EventConnection],
    callback: Callable[..., None],
    pythoncom: Any,
    wrap: Any,
) -> int:
    specs = [
        ("application", app, {"NewMailEx", "Quit"}),
        ("explorers", app.Explorers, {"NewExplorer"}),
        ("inspectors", app.Inspectors, {"NewInspector"}),
    ]
    for collection, kind, wanted in (
        (app.Explorers, "explorer", {"SelectionChange", "Activate", "Close"}),
        (app.Inspectors, "inspector", {"Activate", "Close"}),
    ):
        for index in range(1, min(collection.Count, 40) + 1):
            specs.append((kind, collection.Item(index), wanted))
    retained: list[_EventConnection] = []
    for kind, obj, wanted in specs:
        try:
            identity = obj._oleobj_.QueryInterface(pythoncom.IID_IUnknown)
            existing = next(
                (connection for connection in connections if connection.kind == kind and connection.identity == identity),
                None,
            )
            retained.append(existing or _EventConnection(kind, obj, wanted, callback, pythoncom, wrap))
        except Exception:
            # Some managed installations forbid connection points. Poll only
            # the active item's IDs in that case; never scan mailbox folders.
            continue
    for connection in connections:
        if connection not in retained:
            connection.close()
    connections[:] = retained
    return len(connections)


class OutlookMonitor:
    def __init__(self, on_event: Callable[[MonitorNotice], None], *, poll_interval: float = 0.75):
        self._on_event = on_event
        self._poll_interval = max(0.1, float(poll_interval))
        self._stop = threading.Event()
        self._refresh = threading.Event()
        self._dirty = threading.Event()
        self._quit = threading.Event()
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()
        self._last_identity: tuple[str, str] | None = None
        self._last_analysis_key: str | None = None
        self._failed_identity: tuple[str, str] | None = None
        self._last_snapshot_at = 0.0
        self._last_status = ""
        self._last_window: Any = None

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._refresh.set()
            self._quit.clear()
            self._thread = threading.Thread(target=self._run, name="outlook-monitor", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 3) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, timeout))

    def request_refresh(self) -> None:
        self._refresh.set()

    def _notify(self, notice: MonitorNotice) -> None:
        try:
            self._on_event(notice)
        except Exception:
            # UI callback failures must not tear down Outlook's COM event sink.
            pass

    def _status(self, message: str) -> None:
        if message != self._last_status:
            self._last_status = message
            self._notify(MonitorNotice("status", message))

    def _clear(self, message: str = "請在 Outlook 選取一封郵件。") -> None:
        had_selection = self._last_identity is not None or self._last_analysis_key is not None
        self._last_identity = self._last_analysis_key = self._failed_identity = None
        if had_selection:
            self._notify(MonitorNotice("selection_cleared", message))

    def _on_com_event(self, name: str, *_args: Any) -> None:
        if name == "NewMailEx":
            self._notify(MonitorNotice("new_mail", "收到新郵件；閱讀時才會產生摘要。"))
        elif name == "Quit":
            self._quit.set()
        self._dirty.set()

    def _poll(self, app: Any, *, allowed: bool = True, force: bool = False) -> None:
        # `allowed` remains compatible with callers; foreground no longer controls reading.
        try:
            window, closed = _source_window(app, self._last_window)
            if window is None and not closed:
                return
            self._last_window = window
            item = _window_item(window)
            identity = _item_identity(item) if item is not None else None
        except Exception:
            self._status("Outlook 暫時忙碌，保留目前摘要並等待讀取…")
            return
        if identity is None:
            self._clear()
            return
        changed = identity != self._last_identity
        if changed:
            self._clear("正在讀取所選郵件…")
            self._last_identity = identity
        if self._failed_identity == identity and not force:
            return
        now = time.monotonic()
        if not (changed or force or now - self._last_snapshot_at >= _CONTENT_REFRESH_SECONDS):
            return
        try:
            mail = _snapshot(item, identity)
        except _MailReadError:
            self._failed_identity = identity
            self._notify(MonitorNotice(
                "error",
                "目前信件本文無法讀取。若 Outlook 顯示存取提示，請依公司規範處理，再按重新摘要。",
            ))
            return
        self._failed_identity = None
        self._last_snapshot_at = now
        if mail.analysis_key != self._last_analysis_key or force:
            self._last_analysis_key = mail.analysis_key
            self._notify(MonitorNotice("mail", mail=mail))

    def _run(self) -> None:
        try:
            pythoncom, dynamic, win32gui, win32process, wrap = _win32_modules()
        except ImportError:
            self._notify(MonitorNotice("error", "找不到 pywin32，請使用已安裝 Outlook 與 pywin32 的 Windows Python。"))
            return
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        app = None
        connections: list[_EventConnection] = []
        next_poll = next_sync = next_attach = 0.0
        try:
            while not self._stop.is_set():
                pythoncom.PumpWaitingMessages()
                now = time.monotonic()
                if self._quit.is_set():
                    for connection in connections:
                        connection.close()
                    connections.clear()
                    app = None
                    self._last_window = None
                    self._quit.clear()
                    self._clear()
                    next_attach = now + 1
                if app is None:
                    if now < next_attach:
                        self._stop.wait(0.05)
                        continue
                    try:
                        app = _attach_existing(pythoncom, dynamic)
                        self._refresh.set()
                        next_poll = next_sync = 0.0
                    except Exception as exc:
                        if getattr(exc, "hresult", None) in {-2147221021, 0x800401E3}:
                            self._last_window = None
                            self._clear()
                        self._status("等待開啟傳統版 Outlook…")
                        next_attach = now + 2
                        self._stop.wait(0.05)
                        continue
                try:
                    if now >= next_sync:
                        count = _sync_connections(app, connections, self._on_com_event, pythoncom, wrap)
                        mode = "事件監聽＋選取輪詢" if count else "選取輪詢"
                        self._status(f"已連線 Outlook（{mode}）")
                        next_sync = now + 3
                    force = self._refresh.is_set()
                    if now >= next_poll or force or self._dirty.is_set():
                        self._refresh.clear()
                        self._dirty.clear()
                        self._poll(app, force=force)
                        next_poll = now + self._poll_interval
                except Exception:
                    for connection in connections:
                        connection.close()
                    connections.clear()
                    app = None
                    self._last_window = None
                    self._status("Outlook 暫時無法回應，正在重新連線…")
                    next_attach = now + 2
                self._stop.wait(0.05)
        finally:
            for connection in connections:
                connection.close()
            connections.clear()
            app = None
            pythoncom.CoUninitialize()


def diagnose_outlook(timeout: float = 8) -> dict[str, Any]:
    """Bounded read-only probe. Return counts, never message text or IDs."""
    result_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

    def probe() -> None:
        connections: list[_EventConnection] = []
        app = None
        pythoncom = None
        initialized = False
        try:
            pythoncom, dynamic, _gui, _process, wrap = _win32_modules()
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
            initialized = True
            app = _attach_existing(pythoncom, dynamic)
            result: dict[str, Any] = {
                "status": "attached_existing_instance",
                "outlook_version": str(app.Version),
                "explorer_windows": app.Explorers.Count,
                "inspector_windows": app.Inspectors.Count,
            }
            item = _current_item(app)
            result["mail_selected"] = item is not None
            if item is not None:
                result["body_char_count"] = len(item.Body or "")
                item_type = _ITEM_TYPES.get(item.Class, "mail")
                result["item_type"] = item_type
                if item_type != "mail":
                    result["meeting_fields"] = sorted(json.loads(_meeting_context(item, item_type)))
            result["event_connections"] = _sync_connections(app, connections, lambda *_args: None, pythoncom, wrap)
            result_queue.put(result)
        except Exception as exc:
            result_queue.put({"status": "unavailable", "error_type": type(exc).__name__})
        finally:
            for connection in connections:
                connection.close()
            connections.clear()
            app = None
            if initialized and pythoncom is not None:
                pythoncom.CoUninitialize()

    thread = threading.Thread(target=probe, name="outlook-diagnostic", daemon=True)
    thread.start()
    try:
        return result_queue.get(timeout=max(0.01, timeout))
    except queue.Empty:
        return {"status": "timeout", "message": "Outlook 未在期限內回應；可能正在等待存取提示。"}
