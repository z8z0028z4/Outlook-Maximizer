"""Desktop OAuth and idempotent Google Calendar writes, without a web server."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from pathlib import Path
import secrets
import ssl
import tempfile
import threading
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
import webbrowser

from .models import CalendarEvent, ValidationError


AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://www.googleapis.com/calendar/v3"
SCOPE = "https://www.googleapis.com/auth/calendar.events.owned"
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_CONFIG_BYTES = 64 * 1024


class CalendarError(RuntimeError):
    """A safe, user-facing Calendar error. Never contains raw HTTP responses."""


class CalendarEventDeleted(CalendarError):
    """This exact remote ID is deleted; a persisted new identity may replace it."""

    def __init__(self):
        super().__init__("原行程已在 Google 刪除。請按「再次加入」建立新行程，無需修改日期或時間。")


def format_event_description(event: CalendarEvent, *, ui_language: str = "zh-TW") -> str:
    """The editor preview and Google payload share exactly the same notes."""
    description = event.description.strip()
    if event.evidence.strip():
        evidence_label = "Email context:" if ui_language == "en" else "信件依據："
        description = f"{description}\n\n{evidence_label}\n{event.evidence.strip()}".strip()
    return description


def event_slot_key(calendar_id: str, source_key: str, event: CalendarEvent) -> str:
    """Identify a source/time slot without keeping message text or local offsets."""
    def canonical(value: str) -> str:
        if event.all_day:
            return value
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    identity = json.dumps([calendar_id, source_key, canonical(event.start), canonical(event.end), event.all_day], separators=(",", ":"))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CalendarInsertResult:
    event_id: str
    html_link: str
    created: bool


class _HTTPFailure(Exception):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"HTTP {status}")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        # Never forward bearer tokens or OAuth credentials to a redirect target.
        return None


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=")
    return verifier, challenge.decode("ascii")


def _parse_callback(path: str, expected_state: str) -> dict[str, str]:
    if len(path) > 16384:
        raise CalendarError("Google 登入回應格式不正確。")
    parts = urlsplit(path)
    if parts.scheme or parts.netloc or parts.path != "/":
        raise CalendarError("Google 登入回應路徑不正確。")
    try:
        query = parse_qs(parts.query, keep_blank_values=True, max_num_fields=16)
    except ValueError as exc:
        raise CalendarError("Google 登入回應格式不正確。") from exc
    states = query.get("state", [])
    if len(states) != 1 or not hmac.compare_digest(states[0].encode("utf-8"), expected_state.encode("ascii")):
        raise CalendarError("Google 登入狀態驗證失敗，請重新登入。")
    errors = query.get("error", [])
    if errors:
        if len(errors) != 1:
            raise CalendarError("Google 登入回應格式不正確。")
        return {"error": "access_denied"}
    codes = query.get("code", [])
    if len(codes) != 1 or not codes[0] or len(codes[0]) > 8192:
        raise CalendarError("Google 登入回應沒有有效的授權碼。")
    return {"code": codes[0]}


@dataclass
class _PendingAuth:
    state: str
    result: dict[str, str] | None = None
    ui_language: str = "en"


def _callback_handler(pending: _PendingAuth) -> type[BaseHTTPRequestHandler]:
    def message(english: str, chinese: str) -> str:
        return chinese if pending.ui_language == "zh-TW" else english

    class CallbackHandler(BaseHTTPRequestHandler):
        oauth_pending = pending

        def setup(self) -> None:
            self.request.settimeout(2.0)
            super().setup()

        def log_message(self, format: str, *args: Any) -> None:
            # The request URL contains a short-lived authorization code.
            return

        def do_GET(self) -> None:
            expected_host = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != expected_host:
                self.send_error(400, "Invalid callback host")
                return
            try:
                result = _parse_callback(self.path, pending.state)
            except CalendarError:
                self._respond(400, message("The sign-in response could not be verified. Return to Outlook Maximizer and try again.",
                                           "登入回應驗證失敗。請回到 Outlook 摘要助手重新登入。"))
                return
            if pending.result is not None:
                self._respond(409, message("Sign-in has already completed. Return to Outlook Maximizer.",
                                           "這次登入已完成，請回到 Outlook 摘要助手。"))
                return
            pending.result = result
            response_message = message("Authorization received. Return to Outlook Maximizer. You may close this tab.",
                                       "授權已收到，請回到 Outlook 摘要助手。此分頁可以關閉。")
            if "error" in result:
                response_message = message("Authorization was cancelled. You may close this tab.",
                                           "你已取消授權。此分頁可以關閉。")
            self._respond(200, response_message)

        def _respond(self, status: int, message: str) -> None:
            language = "zh-Hant" if pending.ui_language == "zh-TW" else "en"
            title = "Outlook 摘要助手" if language == "zh-Hant" else "Outlook Maximizer"
            body = f"<!doctype html><html lang='{language}'><meta charset='utf-8'><title>{title}</title><p>{message}</p></html>".encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'")
            self.end_headers()
            self.wfile.write(body)

    return CallbackHandler


def _read_json(path: Path, limit: int = MAX_CONFIG_BYTES) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
        if len(raw) > limit:
            raise CalendarError("Google 設定檔過大。")
        value = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("not an object")
        return value
    except (OSError, ValueError, UnicodeError) as exc:
        raise CalendarError("無法讀取 Google 設定檔，請重新選擇桌面 OAuth JSON。") from exc


def _validate_credentials(data: dict[str, Any]) -> dict[str, str]:
    installed = data.get("installed")
    if not isinstance(installed, dict):
        raise CalendarError("請使用 Google Cloud「桌面應用程式」的 OAuth 用戶端 JSON。")
    client_id = installed.get("client_id")
    secret = installed.get("client_secret")
    if not isinstance(client_id, str) or not client_id.endswith(".apps.googleusercontent.com") or len(client_id) > 1024:
        raise CalendarError("Google OAuth 用戶端 ID 不正確。")
    if not isinstance(secret, str) or not secret or len(secret) > 4096:
        raise CalendarError("Google OAuth 設定缺少用戶端密鑰。")
    if installed.get("auth_uri") not in (AUTH_URL, "https://accounts.google.com/o/oauth2/auth"):
        raise CalendarError("OAuth 授權網址必須是 Google 官方網址。")
    if installed.get("token_uri") != TOKEN_URL:
        raise CalendarError("OAuth 權杖網址必須是 Google 官方網址。")
    return {"client_id": client_id, "client_secret": secret, "auth_uri": AUTH_URL, "token_uri": TOKEN_URL}


class GoogleCalendarClient:
    """Writes only to owned calendars after the user's explicit OAuth consent."""

    def __init__(self, data_dir: Path, *, calendar_id: str = "primary", timeout: float = 20,
                 ui_language: str = "zh-TW"):
        if not isinstance(calendar_id, str) or not calendar_id.strip() or len(calendar_id) > 1024 or any(ord(c) < 32 for c in calendar_id):
            raise CalendarError("Google 行事曆 ID 不正確。")
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise CalendarError("Google 連線逾時設定不正確。")
        self.data_dir = Path(data_dir)
        self.calendar_id = calendar_id.strip()
        self.timeout = float(timeout)
        self.ui_language = "zh-TW" if ui_language == "zh-TW" else "en"
        self.credentials_path = self.data_dir / "google-oauth-client.json"
        self.token_path = self.data_dir / "google-calendar-token.dat"
        self._lock = threading.RLock()
        self._connect_lock = threading.Lock()
        self._auth_cancel = threading.Event()

    def configure_credentials(self, path: Path) -> None:
        credentials = _validate_credentials(_read_json(Path(path)))
        with self._lock:
            previous_id = None
            if self.credentials_path.exists():
                try:
                    previous_id = self._credentials()["client_id"]
                except CalendarError:
                    pass
            self._atomic_write(self.credentials_path, json.dumps({"installed": credentials}).encode("utf-8"))
            if previous_id != credentials["client_id"]:
                self._delete_token()

    @property
    def is_connected(self) -> bool:
        try:
            with self._lock:
                token = self._load_token(self._credentials()["client_id"])
                return bool(token and (token.get("refresh_token") or token.get("access_token")))
        except CalendarError:
            return False

    @property
    def credentials_configured(self) -> bool:
        try:
            with self._lock:
                self._credentials()
            return True
        except CalendarError:
            return False

    def _credentials(self) -> dict[str, str]:
        if not self.credentials_path.exists():
            raise CalendarError("請先在設定中選擇 Google 桌面 OAuth 用戶端 JSON。")
        return _validate_credentials(_read_json(self.credentials_path))

    def connect(
        self,
        *,
        open_browser: Callable[[str], Any] = webbrowser.open,
        timeout: float = 180,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise CalendarError("Google 登入逾時設定不正確。")
        if not self._connect_lock.acquire(blocking=False):
            raise CalendarError("Google 登入已在進行中，請完成瀏覽器中的授權。")
        self._auth_cancel.clear()
        try:
            with self._lock:
                credentials = self._credentials()
            pending = _PendingAuth(secrets.token_urlsafe(32), ui_language=self.ui_language)
            verifier, challenge = _pkce_pair()
            try:
                server = HTTPServer(("127.0.0.1", 0), _callback_handler(pending))
            except OSError as exc:
                raise CalendarError("無法開啟本機 Google 登入接收埠，請檢查防火牆設定。") from exc
            with server:
                server.timeout = min(0.5, timeout)
                redirect_uri = f"http://127.0.0.1:{server.server_port}/"
                query = urlencode({
                    "client_id": credentials["client_id"],
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "scope": SCOPE,
                    "access_type": "offline",
                    "prompt": "consent",
                    "state": pending.state,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                })
                if on_status:
                    on_status("請在瀏覽器登入 Google，並允許存取你擁有的行事曆。")
                try:
                    opened = open_browser(f"{AUTH_URL}?{query}")
                except Exception as exc:
                    raise CalendarError("無法開啟登入瀏覽器，請確認預設瀏覽器設定。") from exc
                if opened is False:
                    raise CalendarError("無法開啟登入瀏覽器，請確認預設瀏覽器設定。")
                deadline = time.monotonic() + timeout
                while pending.result is None:
                    if self._auth_cancel.is_set():
                        raise CalendarError("Google 登入已取消。")
                    if time.monotonic() >= deadline:
                        raise CalendarError("Google 登入等待逾時，請重新按登入。")
                    server.handle_request()
            if "error" in pending.result:
                raise CalendarError("你已取消 Google 授權，尚未連接行事曆。")
            form = {
                "client_id": credentials["client_id"],
                "client_secret": credentials["client_secret"],
                "code": pending.result["code"],
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            }
            try:
                response = self._request_json("POST", TOKEN_URL, form=form)
            except _HTTPFailure as exc:
                raise CalendarError("Google 授權碼交換失敗，請重新登入並確認 OAuth 設定。") from exc
            with self._lock:
                if self._auth_cancel.is_set() or self._credentials()["client_id"] != credentials["client_id"]:
                    raise CalendarError("登入設定已變更，請重新登入 Google。")
                token = self._normalize_token(response, credentials["client_id"])
                if not token.get("refresh_token"):
                    raise CalendarError("Google 未提供離線授權，請重新登入並允許行事曆權限。")
                self._save_token(token)
        finally:
            self._connect_lock.release()

    def cancel_pending_sign_in(self) -> None:
        """Stop a browser sign-in wait without deleting an existing login."""
        self._auth_cancel.set()

    def cancel_auth(self) -> None:
        """Cancel an in-progress OAuth wait; the socket closes within 0.5s."""
        self.cancel_pending_sign_in()

    def disconnect(self) -> None:
        """Remove this app's local token; Google account consent is unchanged."""
        self._auth_cancel.set()
        with self._lock:
            self._delete_token()

    def create_event(self, event: CalendarEvent, source_key: str, *, occurrence_key: str = "") -> CalendarInsertResult:
        try:
            event.validate(for_insert=True)
        except ValidationError as exc:
            raise CalendarError(str(exc)) from exc
        if not isinstance(source_key, str) or not source_key or len(source_key) > 2048:
            raise CalendarError("缺少信件來源識別，無法安全避免重複新增。")
        if not isinstance(occurrence_key, str) or len(occurrence_key) > 128 or any(ord(char) < 32 for char in occurrence_key):
            raise CalendarError("再次加入的操作識別不正確，請重新開啟工具後重試。")
        event_id = self._event_id(event, source_key, occurrence_key=occurrence_key)
        when = (lambda value: {"date": value}) if event.all_day else (
            lambda value: {"dateTime": value, "timeZone": event.timezone}
        )
        payload: dict[str, Any] = {
            "id": event_id,
            "summary": event.title.strip(),
            "start": when(event.start),
            "end": when(event.end),
            "description": format_event_description(event, ui_language=self.ui_language),
            "location": event.location.strip(),
            "extendedProperties": {"private": {"outlookMaximizerSource": hashlib.sha256(source_key.encode("utf-8")).hexdigest()}},
        }
        # Deliberately omit attendees: this is a private reminder, not an invitation.
        url = f"{API_URL}/calendars/{quote(self.calendar_id, safe='')}/events"
        try:
            response = self._authorized_request("POST", url, body=payload)
            created = True
        except _HTTPFailure as exc:
            if exc.status == 409:
                try:
                    response = self._authorized_request("GET", f"{url}/{event_id}")
                except _HTTPFailure as fetch_exc:
                    if fetch_exc.status == 410:
                        raise CalendarEventDeleted() from fetch_exc
                    raise self._api_error(fetch_exc.status) from fetch_exc
                created = False
            else:
                raise self._api_error(exc.status) from exc
        if response.get("id") != event_id:
            raise CalendarError("Google 回傳的行程識別不符，請到行事曆確認後重試。")
        if response.get("status") == "cancelled":
            raise CalendarEventDeleted()
        link = response.get("htmlLink", "")
        if not isinstance(link, str) or (link and (urlsplit(link).scheme != "https" or urlsplit(link).hostname not in {"www.google.com", "calendar.google.com"})):
            link = ""
        return CalendarInsertResult(event_id=event_id, html_link=link, created=created)

    def _event_id(self, event: CalendarEvent, source_key: str, *, occurrence_key: str = "") -> str:
        # Title wording can change between model runs; source and time identify the slot.
        original = event_slot_key(self.calendar_id, source_key, event)
        if not occurrence_key:
            return original
        return hashlib.sha256(f"{original}\0{occurrence_key}".encode("utf-8")).hexdigest()

    def _authorized_request(self, method: str, url: str, *, body: dict[str, Any] | None = None) -> dict[str, Any]:
        access_token = self._access_token()
        try:
            return self._request_json(method, url, body=body, access_token=access_token)
        except _HTTPFailure as exc:
            if exc.status != 401:
                raise
        access_token = self._access_token(force_refresh=True)
        try:
            return self._request_json(method, url, body=body, access_token=access_token)
        except _HTTPFailure as exc:
            if exc.status == 401:
                raise CalendarError("Google 登入已失效，請重新連接行事曆。") from exc
            raise

    def _access_token(self, *, force_refresh: bool = False) -> str:
        with self._lock:
            credentials = self._credentials()
            token = self._load_token(credentials["client_id"])
            if not token:
                raise CalendarError("請先在設定中登入 Google Calendar。")
            if not force_refresh and token["expires_at"] > time.time() + 60:
                return token["access_token"]
            refresh = token.get("refresh_token")
            if not refresh:
                raise CalendarError("Google 登入已過期，請重新連接行事曆。")
            try:
                response = self._request_json("POST", TOKEN_URL, form={
                    "client_id": credentials["client_id"],
                    "client_secret": credentials["client_secret"],
                    "refresh_token": refresh,
                    "grant_type": "refresh_token",
                })
            except _HTTPFailure as exc:
                if exc.status in (400, 401):
                    self._delete_token()
                    raise CalendarError("Google 離線授權已失效，請重新登入 Google Calendar。") from exc
                raise CalendarError("暫時無法更新 Google 登入，請稍後再試。") from exc
            token = self._normalize_token(response, credentials["client_id"], previous_refresh=refresh)
            self._save_token(token)
            return token["access_token"]

    @staticmethod
    def _normalize_token(response: dict[str, Any], client_id: str, *, previous_refresh: str = "") -> dict[str, Any]:
        access = response.get("access_token")
        refresh = response.get("refresh_token", previous_refresh)
        expiry = response.get("expires_in")
        if not isinstance(access, str) or not access or len(access) > 8192:
            raise CalendarError("Google 未回傳有效的登入權杖，請重新登入。")
        if not isinstance(refresh, str) or len(refresh) > 8192:
            raise CalendarError("Google 回傳的離線授權格式不正確。")
        if type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry <= 0 or expiry > 366 * 86400:
            raise CalendarError("Google 回傳的登入有效時間不正確。")
        if str(response.get("token_type", "Bearer")).lower() != "bearer":
            raise CalendarError("Google 回傳的權杖類型不正確。")
        granted_scope = response.get("scope")
        accepted_scopes = {SCOPE, "https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/calendar"}
        if granted_scope is not None and (not isinstance(granted_scope, str) or not accepted_scopes.intersection(granted_scope.split())):
            raise CalendarError("Google 未授予行事曆權限，請重新登入並允許行事曆存取。")
        return {"client_id": client_id, "access_token": access, "refresh_token": refresh, "expires_at": time.time() + expiry}

    def _load_token(self, client_id: str) -> dict[str, Any] | None:
        if not self.token_path.exists():
            return None
        try:
            with self.token_path.open("rb") as handle:
                encrypted = handle.read(MAX_CONFIG_BYTES + 1)
            if len(encrypted) > MAX_CONFIG_BYTES:
                raise ValueError("oversize token")
            token = json.loads(self._unprotect(encrypted).decode("utf-8"))
            if not isinstance(token, dict) or token.get("client_id") != client_id:
                raise ValueError("client mismatch")
            if not isinstance(token.get("access_token"), str) or not token["access_token"] or len(token["access_token"]) > 8192:
                raise ValueError("invalid access token")
            if not isinstance(token.get("refresh_token", ""), str) or len(token.get("refresh_token", "")) > 8192:
                raise ValueError("invalid refresh token")
            expires = token.get("expires_at")
            if type(expires) not in (int, float) or not math.isfinite(expires):
                raise ValueError("invalid expiry")
            return token
        except (OSError, UnicodeError, ValueError, CalendarError) as exc:
            raise CalendarError("無法解密此電腦的 Google 登入資料，請重新登入。") from exc

    def _save_token(self, token: dict[str, Any]) -> None:
        plaintext = json.dumps(token, separators=(",", ":")).encode("utf-8")
        self._atomic_write(self.token_path, self._protect(plaintext))

    @staticmethod
    def _protect(data: bytes) -> bytes:
        try:
            import win32crypt
            return win32crypt.CryptProtectData(data, "Outlook Maximizer Google Calendar", None, None, None, 1)
        except Exception as exc:
            raise CalendarError("Windows 無法加密 Google 登入資料，請確認目前使用者工作階段。") from exc

    @staticmethod
    def _unprotect(data: bytes) -> bytes:
        try:
            import win32crypt
            return win32crypt.CryptUnprotectData(data, None, None, None, 1)[1]
        except Exception as exc:
            raise CalendarError("Windows 無法解密 Google 登入資料，請重新登入。") from exc

    def _delete_token(self) -> None:
        try:
            self.token_path.unlink(missing_ok=True)
        except OSError as exc:
            raise CalendarError("無法移除本機 Google 登入資料，請確認檔案權限。") from exc

    def _atomic_write(self, path: Path, data: bytes) -> None:
        temporary_path: Path | None = None
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.data_dir, prefix=".calendar-", delete=False) as handle:
                temporary_path = Path(handle.name)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        except OSError as exc:
            raise CalendarError("無法儲存本機 Google 設定，請確認資料夾權限。") from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        form: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        if url != TOKEN_URL and not url.startswith(f"{API_URL}/calendars/"):
            raise CalendarError("Google 請求網址不正確。")
        headers = {"Accept": "application/json"}
        payload = None
        if form is not None:
            payload = urlencode(form).encode("ascii")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        if payload is not None and len(payload) > MAX_RESPONSE_BYTES:
            raise CalendarError("行程資料過大，請縮短說明文字。")
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        request = Request(url, data=payload, headers=headers, method=method)
        opener = build_opener(_NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
        try:
            with opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise CalendarError("Google 回應過大，請稍後再試。")
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("not an object")
            return value
        except HTTPError as exc:
            exc.close()
            raise _HTTPFailure(exc.code) from None
        except (URLError, OSError, TimeoutError) as exc:
            raise CalendarError("無法連接 Google，請確認網路或公司代理設定後重試；相同行程不會重複新增。") from exc
        except (ValueError, UnicodeError) as exc:
            raise CalendarError("Google 回應格式不正確，請稍後再試。") from exc

    @staticmethod
    def _api_error(status: int) -> CalendarError:
        if status == 403:
            return CalendarError("Google 拒絕寫入。請確認 Calendar API 已啟用、已授權行事曆權限，且這是你擁有的行事曆。")
        if status == 404:
            return CalendarError("找不到指定的 Google 行事曆，請檢查行事曆 ID。")
        if status == 429 or status >= 500:
            return CalendarError("Google 暫時忙碌，請稍後重試；相同行程不會重複新增。")
        return CalendarError(f"Google 行事曆請求失敗（HTTP {status}），請檢查行程資料後重試。")
