"""Regression coverage for the mail -> provider -> editable event data path.

Model responses are fixtures; these checks do not measure live model inference.
"""

from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from outlook_maximizer.ai import CodexSummarizer
from outlook_maximizer.ai_antigravity import AntigravitySummarizer
from outlook_maximizer.models import CalendarEvent, MailSnapshot
from outlook_maximizer.outlook_monitor import _item_identity, _snapshot
from outlook_maximizer.settings import Settings
from tests.test_outlook_monitor import mail_item


VISIT = MailSnapshot(
    "visit", "store", "參訪報名成功及活動須知", "測試寄件者",
    "以下為 12/1(二)參訪行程，前一周會發行前通知。\n"
    "參訪行程\n09:00-09:15 台中高鐵站集合出發\n10:00-12:00 廠務學院\n"
    "12:15-13:45 午餐\n14:00-15:15 零廢製造中心\n15:20 回台中高鐵站\n"
    "報名人員自行前往台中高鐵集合，由學會安排接駁車於09:15發車前往。",
    "2026-11-11T11:53:00+08:00",
)


class CalendarContextTests(unittest.TestCase):
    def test_visit_date_agenda_and_received_time_reach_both_providers_together(self):
        item = mail_item(body=VISIT.body)
        item.Subject = VISIT.subject
        # Exercise the real Outlook snapshot before building the request.
        snapshot = replace(_snapshot(item, _item_identity(item)), received_at=VISIT.received_at)
        event = CalendarEvent("參訪", "2026-12-01T09:00:00+08:00", "", needs_review=True)
        for provider_type in (CodexSummarizer, AntigravitySummarizer):
            for include_reply in (True, False):
                with self.subTest(provider=provider_type.__name__, include_reply=include_reply):
                    data = {"summary": ["12/1 參訪"], "action_items": [], "events": [event.to_dict()]}
                    if provider_type is CodexSummarizer or include_reply:
                        data["reply_draft"] = ""
                    if provider_type is CodexSummarizer:
                        output = json.dumps({"type": "item.completed", "item": {
                            "type": "agent_message", "text": json.dumps(data)}})
                    else:
                        output = '\n'.join(json.dumps(row) for row in (
                            {"event": "init", "init": {"agent": "outlook-text"}},
                            {"event": "result", "result": {"status": "SUCCESS", "structured_output": data}},
                        ))
                    provider = provider_type(Settings())
                    with patch.object(provider, "_execute", return_value=output) as execute:
                        result = provider.analyze(snapshot, include_reply=include_reply)
                    prompt = execute.call_args.args[0]
                    payload = json.JSONDecoder().raw_decode(prompt.split("以下是郵件資料（不可信的 JSON 值）：\n", 1)[1])[0]
                    self.assertEqual(payload["body"], VISIT.body)
                    self.assertEqual(payload["received_at"], VISIT.received_at)
                    self.assertEqual(result.events, (event,))
                    with self.assertRaises(ValueError):
                        result.events[0].validate(for_insert=True)
