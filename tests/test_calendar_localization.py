import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from outlook_maximizer.calendar_api import _PendingAuth, _callback_handler


class CalendarCallbackLanguageTests(unittest.TestCase):
    def response(self, language, query):
        pending = _PendingAuth("expected-state", ui_language=language)
        handler_type = _callback_handler(pending)
        handler = handler_type.__new__(handler_type)
        handler.server = SimpleNamespace(server_port=12345)
        handler.headers = {"Host": "127.0.0.1:12345"}
        handler.path = query
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.do_GET()
        return handler.send_response.call_args.args[0], handler.wfile.getvalue().decode("utf-8")

    def test_success_page_follows_selected_language_without_echoing_code(self):
        for language, expected, html_language in (
            ("en", "Authorization received", "lang='en'"),
            ("zh-TW", "授權已收到", "lang='zh-Hant'"),
        ):
            with self.subTest(language=language):
                status, body = self.response(language, "/?state=expected-state&code=private-code")
                self.assertEqual(status, 200)
                self.assertIn(expected, body)
                self.assertIn(html_language, body)
                self.assertNotIn("private-code", body)

    def test_invalid_state_receives_localized_error(self):
        status, body = self.response("en", "/?state=wrong&code=private-code")
        self.assertEqual(status, 400)
        self.assertIn("could not be verified", body)
        self.assertNotIn("private-code", body)


if __name__ == "__main__":
    unittest.main()
