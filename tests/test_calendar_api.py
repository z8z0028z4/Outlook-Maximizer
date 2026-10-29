from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from unittest.mock import MagicMock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from outlook_maximizer.calendar_api import (
    AUTH_URL,
    GoogleCalendarClient,
    CalendarError,
    SCOPE,
    TOKEN_URL,
    _HTTPFailure,
    _parse_callback,
    _pkce_pair,
    format_event_description,
)
from outlook_maximizer.models import CalendarEvent


CLIENT_ID = "test-client.apps.googleusercontent.com"


def credentials(**changes):
    fields = {"client_id": CLIENT_ID, "client_secret": "not-a-real-secret", "auth_uri": AUTH_URL, "token_uri": TOKEN_URL}
    fields.update(changes)
    return {"installed": fields}


def event(**changes):
    fields = dict(title="專案會議", start="2026-10-22T10:00:00+08:00", end="2026-10-22T11:00:00+08:00", evidence="週四 10:00–11:00", description="確認進度")
    fields.update(changes)
    return CalendarEvent(**fields)


class CalendarClientTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path.cwd() / ".local"
        temporary_root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=temporary_root)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.json"
        self.source.write_text(json.dumps(credentials()), encoding="utf-8")
        self.client = GoogleCalendarClient(self.root / "app")
        self.client.configure_credentials(self.source)
        # An intentionally non-production protector makes ciphertext assertions easy.
        self.protect = patch.object(self.client, "_protect", side_effect=lambda value: b"sealed:" + bytes(c ^ 0xAA for c in value))
        self.unprotect = patch.object(self.client, "_unprotect", side_effect=lambda value: bytes(c ^ 0xAA for c in value.removeprefix(b"sealed:")))
        self.protect.start()
        self.unprotect.start()
        self.addCleanup(self.protect.stop)
        self.addCleanup(self.unprotect.stop)
        self.addCleanup(self.temporary.cleanup)

    def save_token(self, *, expired=False):
        token = {"client_id": CLIENT_ID, "access_token": "private-access-token", "refresh_token": "private-refresh-token", "expires_at": time.time() + (-100 if expired else 3600)}
        self.client._save_token(token)
        return token

    def test_rejects_non_desktop_oauth_client(self):
        self.source.write_text(json.dumps({"web": credentials()["installed"]}))
        with self.assertRaisesRegex(CalendarError, "桌面"):
            self.client.configure_credentials(self.source)

    def test_rejects_tampered_google_token_endpoint(self):
        self.source.write_text(json.dumps(credentials(token_uri="https://example.invalid/collect")))
        with self.assertRaisesRegex(CalendarError, "官方"):
            self.client.configure_credentials(self.source)

    def test_rejects_tampered_google_authorization_endpoint(self):
        self.source.write_text(json.dumps(credentials(auth_uri="https://accounts.google.com.example.invalid/oauth")))
        with self.assertRaisesRegex(CalendarError, "官方"):
            self.client.configure_credentials(self.source)

    def test_replacing_oauth_client_removes_old_login(self):
        self.save_token()
        self.source.write_text(json.dumps(credentials(client_id="second.apps.googleusercontent.com")))
        self.client.configure_credentials(self.source)
        self.assertFalse(self.client.token_path.exists())

    def test_reselecting_same_client_preserves_login(self):
        self.save_token()
        self.client.configure_credentials(self.source)
        self.assertTrue(self.client.is_connected)

    def test_tokens_are_protected_before_written_and_decrypted_on_read(self):
        token = self.save_token()
        stored = self.client.token_path.read_bytes()
        self.assertNotIn(token["access_token"].encode(), stored)
        self.assertNotIn(token["refresh_token"].encode(), stored)
        self.assertEqual(self.client._load_token(CLIENT_ID), token)

    def test_failed_token_decryption_is_disconnected_without_disclosing_data(self):
        self.client.token_path.write_bytes(b"corrupted")
        self.assertFalse(self.client.is_connected)
        with self.assertRaisesRegex(CalendarError, "重新登入") as raised:
            self.client._access_token()
        self.assertNotIn("corrupted", str(raised.exception))

    def test_missing_configuration_prompts_for_setup(self):
        client = GoogleCalendarClient(self.root / "unconfigured")
        with self.assertRaisesRegex(CalendarError, "OAuth"):
            client.connect(open_browser=lambda url: self.fail("should not open browser"))

    def test_pkce_challenge_matches_sha256_of_verifier(self):
        import base64
        verifier, challenge = _pkce_pair()
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        self.assertEqual(challenge, expected)
        self.assertGreaterEqual(len(verifier), 43)
        self.assertLessEqual(len(verifier), 128)
        self.assertNotEqual(_pkce_pair()[0], verifier)

    def test_callback_rejects_cross_site_state(self):
        with self.assertRaisesRegex(CalendarError, "狀態"):
            _parse_callback("/?state=wrong&code=secret", "expected")

    def test_callback_rejects_non_ascii_state_without_exposing_code(self):
        with self.assertRaisesRegex(CalendarError, "狀態"):
            _parse_callback("/?state=%E4%B8%AD&code=secret", "expected")

    def test_callback_rejects_duplicate_codes(self):
        with self.assertRaises(CalendarError):
            _parse_callback("/?state=expected&code=one&code=two", "expected")

    def test_callback_rejects_other_paths(self):
        with self.assertRaises(CalendarError):
            _parse_callback("/elsewhere?state=expected&code=secret", "expected")

    def test_callback_rejects_absolute_url_targets(self):
        with self.assertRaises(CalendarError):
            _parse_callback("https://example.invalid/?state=expected&code=secret", "expected")

    def test_callback_handles_denied_consent(self):
        self.assertEqual(_parse_callback("/?state=expected&error=access_denied", "expected"), {"error": "access_denied"})

    def fake_server(self, response):
        class FakeServer:
            server_port = 37645

            def __init__(self, address, handler):
                self.address = address
                self.handler = handler

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def handle_request(self):
                if response is not None:
                    self.handler.oauth_pending.result = response
        return FakeServer

    def test_desktop_login_uses_loopback_pkce_state_and_offline_consent(self):
        opened = []
        response = {"access_token": "access", "refresh_token": "refresh", "expires_in": 3600, "token_type": "Bearer"}
        with patch("outlook_maximizer.calendar_api.HTTPServer", self.fake_server({"code": "temporary-code"})), patch.object(self.client, "_request_json", return_value=response) as request:
            self.client.connect(open_browser=lambda url: opened.append(url), timeout=1)
        query = parse_qs(urlsplit(opened[0]).query)
        self.assertEqual(urlsplit(opened[0]).scheme, "https")
        self.assertEqual(query["redirect_uri"], ["http://127.0.0.1:37645/"])
        self.assertEqual(query["scope"], [SCOPE])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["access_type"], ["offline"])
        self.assertTrue(query["state"][0])
        form = request.call_args.kwargs["form"]
        self.assertEqual(form["grant_type"], "authorization_code")
        self.assertIn("code_verifier", form)
        self.assertTrue(self.client.is_connected)

    def test_denied_login_never_exchanges_code_or_stores_token(self):
        with patch("outlook_maximizer.calendar_api.HTTPServer", self.fake_server({"error": "access_denied"})), patch.object(self.client, "_request_json") as request:
            with self.assertRaisesRegex(CalendarError, "取消"):
                self.client.connect(open_browser=lambda url: True)
        request.assert_not_called()
        self.assertFalse(self.client.token_path.exists())

    def test_login_timeout_never_stores_token(self):
        with patch("outlook_maximizer.calendar_api.HTTPServer", self.fake_server(None)), patch("outlook_maximizer.calendar_api.time.monotonic", side_effect=[0, 2]):
            with self.assertRaisesRegex(CalendarError, "逾時"):
                self.client.connect(open_browser=lambda url: True, timeout=1)
        self.assertFalse(self.client.token_path.exists())

    def test_cancel_pending_sign_in_does_not_delete_existing_login(self):
        self.save_token()
        with patch("outlook_maximizer.calendar_api.HTTPServer", self.fake_server(None)):
            with self.assertRaisesRegex(CalendarError, "取消"):
                self.client.connect(open_browser=lambda url: self.client.cancel_pending_sign_in())
        self.assertTrue(self.client.is_connected)

    def test_expired_access_token_refreshes_without_losing_refresh_token(self):
        self.save_token(expired=True)
        with patch.object(self.client, "_request_json", return_value={"access_token": "updated", "expires_in": 3600}) as request:
            access = self.client._access_token()
        self.assertEqual(access, "updated")
        self.assertEqual(request.call_args.kwargs["form"]["grant_type"], "refresh_token")
        self.assertEqual(self.client._load_token(CLIENT_ID)["refresh_token"], "private-refresh-token")

    def test_revoked_refresh_token_requires_new_sign_in(self):
        self.save_token(expired=True)
        with patch.object(self.client, "_request_json", side_effect=_HTTPFailure(400)):
            with self.assertRaisesRegex(CalendarError, "重新登入"):
                self.client._access_token()
        self.assertFalse(self.client.token_path.exists())

    def test_google_missing_calendar_consent_is_rejected(self):
        with self.assertRaisesRegex(CalendarError, "行事曆權限"):
            self.client._normalize_token({"access_token": "access", "refresh_token": "refresh", "expires_in": 3600, "scope": "openid email"}, CLIENT_ID)

    def test_create_event_has_explicit_timezone_and_no_invitees(self):
        candidate = event()
        identifier = self.client._event_id(candidate, "message-key")
        with patch.object(self.client, "_authorized_request", return_value={"id": identifier, "htmlLink": "https://calendar.google.com/calendar/event?eid=example"}) as request:
            result = self.client.create_event(candidate, "message-key")
        payload = request.call_args.kwargs["body"]
        self.assertEqual(payload["start"], {"dateTime": candidate.start, "timeZone": "Asia/Taipei"})
        self.assertNotIn("attendees", payload)
        self.assertNotIn("attachments", payload)
        self.assertIn(candidate.evidence, payload["description"])
        self.assertTrue(result.created)

    def test_all_day_event_preserves_exclusive_end_date(self):
        candidate = event(start="2026-10-22", end="2026-10-23", all_day=True)
        identifier = self.client._event_id(candidate, "message-key")
        with patch.object(self.client, "_authorized_request", return_value={"id": identifier}) as request:
            self.client.create_event(candidate, "message-key")
        self.assertEqual(request.call_args.kwargs["body"]["end"], {"date": "2026-10-23"})

    def test_description_preview_matches_exact_api_payload(self):
        candidate = event(description="  討論事項\nhttps://example.invalid/範例  ", evidence="  明日 11:00–12:00  ")
        identifier = self.client._event_id(candidate, "message-key")
        expected = "討論事項\nhttps://example.invalid/範例\n\n信件依據：\n明日 11:00–12:00"
        self.assertEqual(format_event_description(candidate), expected)
        with patch.object(self.client, "_authorized_request", return_value={"id": identifier}) as request:
            self.client.create_event(candidate, "message-key")
        self.assertEqual(request.call_args.kwargs["body"]["description"], expected)
        self.assertEqual(format_event_description(event(description="  ", evidence="  ")), "")

    def test_description_preview_and_payload_follow_interface_language(self):
        candidate = event(description="Notes", evidence="Tomorrow at 11")
        identifier = self.client._event_id(candidate, "message-key")
        for language, label in (("en", "Email context:"), ("zh-TW", "信件依據：")):
            with self.subTest(language=language):
                self.client.ui_language = language
                expected = format_event_description(candidate, ui_language=language)
                with patch.object(self.client, "_authorized_request", return_value={"id": identifier}) as request:
                    self.client.create_event(candidate, "message-key")
                self.assertIn(label, expected)
                self.assertEqual(request.call_args.kwargs["body"]["description"], expected)

    def test_explicit_repeat_changes_id_but_retries_keep_repeat_id(self):
        candidate = event()
        original_id = self.client._event_id(candidate, "message-key")
        identity = json.dumps(["primary", "message-key", "2026-10-22T02:00:00+00:00",
                               "2026-10-22T03:00:00+00:00", False], separators=(",", ":"))
        self.assertEqual(original_id, hashlib.sha256(identity.encode("utf-8")).hexdigest())
        repeat_id = self.client._event_id(candidate, "message-key", occurrence_key="repeat-a")
        self.assertNotEqual(original_id, repeat_id)
        self.assertEqual(repeat_id, self.client._event_id(candidate, "message-key", occurrence_key="repeat-a"))
        self.assertNotEqual(repeat_id, self.client._event_id(candidate, "message-key", occurrence_key="repeat-b"))
        with patch.object(self.client, "_authorized_request", side_effect=[_HTTPFailure(409), {"id": repeat_id}]) as request:
            result = self.client.create_event(candidate, "message-key", occurrence_key="repeat-a")
        self.assertEqual(request.call_args_list[0].kwargs["body"]["id"], repeat_id)
        self.assertTrue(request.call_args_list[1].args[1].endswith(repeat_id))
        self.assertFalse(result.created)

    def test_invalid_repeat_key_is_rejected_before_network(self):
        for key in (None, 1, "x" * 129, "bad\nkey"):
            with self.subTest(key=key), patch.object(self.client, "_authorized_request") as request:
                with self.assertRaises(CalendarError):
                    self.client.create_event(event(), "message-key", occurrence_key=key)
                request.assert_not_called()

    def test_ambiguous_event_is_rejected_before_any_network_call(self):
        with patch.object(self.client, "_authorized_request") as request:
            with self.assertRaisesRegex(CalendarError, "確認"):
                self.client.create_event(event(needs_review=True), "message-key")
        request.assert_not_called()

    def test_same_message_time_has_same_id_despite_title_wording_or_offset(self):
        first = event()
        second = replace(first, title="另一種摘要標題", start="2026-10-22T02:00:00Z", end="2026-10-22T03:00:00Z")
        self.assertEqual(self.client._event_id(first, "message-key"), self.client._event_id(second, "message-key"))
        self.assertNotEqual(self.client._event_id(first, "other-message"), self.client._event_id(first, "message-key"))

    def test_duplicate_insert_fetches_original_event(self):
        candidate = event()
        identifier = self.client._event_id(candidate, "message-key")
        with patch.object(self.client, "_authorized_request", side_effect=[_HTTPFailure(409), {"id": identifier}]) as request:
            result = self.client.create_event(candidate, "message-key")
        self.assertFalse(result.created)
        self.assertEqual(request.call_args_list[1].args[0], "GET")
        self.assertTrue(request.call_args_list[1].args[1].endswith(identifier))

    def test_deleted_duplicate_is_not_reported_as_added(self):
        candidate = event()
        identifier = self.client._event_id(candidate, "message-key")
        with patch.object(self.client, "_authorized_request", side_effect=[_HTTPFailure(409), {"id": identifier, "status": "cancelled"}]):
            with self.assertRaisesRegex(CalendarError, "刪除"):
                self.client.create_event(candidate, "message-key")

    def test_deleted_status_with_mismatched_id_is_not_accepted(self):
        with patch.object(self.client, "_authorized_request", side_effect=[_HTTPFailure(409), {"id": "wrong-id", "status": "cancelled"}]):
            with self.assertRaisesRegex(CalendarError, "識別"):
                self.client.create_event(event(), "message-key")

    def test_deleted_status_in_success_response_is_not_reported_as_added(self):
        candidate = event()
        identifier = self.client._event_id(candidate, "message-key")
        with patch.object(self.client, "_authorized_request", return_value={"id": identifier, "status": "cancelled"}):
            with self.assertRaisesRegex(CalendarError, "刪除"):
                self.client.create_event(candidate, "message-key")

    def test_unauthorized_api_response_refreshes_then_retries_once(self):
        with patch.object(self.client, "_access_token", side_effect=["old", "new"]) as access, patch.object(self.client, "_request_json", side_effect=[_HTTPFailure(401), {"id": "example"}]) as request:
            result = self.client._authorized_request("GET", "https://www.googleapis.com/calendar/v3/calendars/primary/events/example")
        self.assertEqual(result, {"id": "example"})
        self.assertEqual(access.call_args.kwargs, {"force_refresh": True})
        self.assertEqual(request.call_args.kwargs["access_token"], "new")

    def test_never_opens_non_google_event_link(self):
        candidate = event()
        identifier = self.client._event_id(candidate, "message-key")
        with patch.object(self.client, "_authorized_request", return_value={"id": identifier, "htmlLink": "https://malicious.invalid/"}):
            result = self.client.create_event(candidate, "message-key")
        self.assertEqual(result.html_link, "")

    def test_disconnect_removes_only_app_token(self):
        self.save_token()
        self.client.disconnect()
        self.assertFalse(self.client.is_connected)
        self.assertTrue(self.client.credentials_path.exists())

    def test_http_transport_rejects_non_google_endpoint(self):
        with self.assertRaisesRegex(CalendarError, "網址"):
            self.client._request_json("GET", "https://example.invalid/")

    def test_http_failure_does_not_disclose_response_contents(self):
        error = HTTPError(TOKEN_URL, 400, "bad", {}, io.BytesIO(b'{"private":"should-not-leak"}'))
        opener = MagicMock()
        opener.open.side_effect = error
        with patch("outlook_maximizer.calendar_api.build_opener", return_value=opener):
            with self.assertRaises(_HTTPFailure) as raised:
                self.client._request_json("POST", TOKEN_URL, form={"refresh_token": "also-private"})
        self.assertEqual(str(raised.exception), "HTTP 400")
        self.assertNotIn("should-not-leak", str(raised.exception))

    def test_http_transport_limits_google_response_size(self):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b"x" * (1024 * 1024 + 1)
        with patch("outlook_maximizer.calendar_api.build_opener", return_value=opener):
            with self.assertRaisesRegex(CalendarError, "回應過大"):
                self.client._request_json("GET", "https://www.googleapis.com/calendar/v3/calendars/primary/events")

    def test_http_transport_sends_bearer_header_only_to_google(self):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b'{"id":"synthetic-event"}'
        with patch("outlook_maximizer.calendar_api.build_opener", return_value=opener):
            result = self.client._request_json("POST", "https://www.googleapis.com/calendar/v3/calendars/primary/events", body={"summary": "test"}, access_token="synthetic-token")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer synthetic-token")
        self.assertEqual(result, {"id": "synthetic-event"})


if __name__ == "__main__":
    unittest.main()
