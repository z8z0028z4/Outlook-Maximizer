"""Synthetic data only; no Outlook or Google calls."""

from datetime import datetime, timedelta, timezone

from .models import Analysis, CalendarEvent, MailSnapshot


def demo_data(language: str = "zh-TW") -> tuple[MailSnapshot, Analysis]:
    now = datetime.now(timezone(timedelta(hours=8)))
    start = (now + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=1)
    day = start.strftime("%Y-%m-%d")
    if language == "en":
        mail = MailSnapshot(
            "demo", "demo", "Project progress meeting — please confirm", "Project team · Sample data",
            f"We plan to meet on {day} from 14:00 to 15:00 (Taipei time) in Meeting Room A. "
            "Please prepare the current progress and any items to coordinate, and confirm receipt.",
            now.isoformat(),
        )
        analysis = Analysis(
            ("A project progress meeting is scheduled for tomorrow, 14:00–15:00, in Meeting Room A.",
             "Prepare the current progress and items that need coordination.",
             "The sender asks you to confirm receipt."),
            ("Prepare the project status and coordination items.", "Confirm receipt of the meeting details."),
            "Hello,\n\nThank you for the meeting details. I will prepare the project status and items for discussion.\n\nThank you.",
            (CalendarEvent("Project progress meeting", start.isoformat(), end.isoformat(),
                           location="Meeting Room A", description="Discuss project status and coordination items.",
                           evidence=f"{day} 14:00–15:00 (Taipei time), Meeting Room A"),),
        )
        return mail, analysis
    mail = MailSnapshot(
        "demo", "demo", "專案進度討論｜請確認明天下午的會議", "專案團隊 · 展示用資料",
        f"我們預計 {day} 14:00–15:00（台灣時間）在 A 會議室討論專案進度。請準備目前進度與待協調事項，並回覆是否收到。",
        now.isoformat(),
    )
    analysis = Analysis(
        ("明天下午 2 點至 3 點進行專案進度討論，地點為 A 會議室。", "會前需整理目前進度與待協調事項。", "寄件者希望你回覆確認收到會議資訊。"),
        ("整理專案進度與需要協調的問題。", "回覆確認收到會議通知。"),
        "您好，\n\n已收到會議資訊，謝謝通知。我會整理目前的專案進度與待協調事項，供會議討論。\n\n謝謝！",
        (CalendarEvent("專案進度討論", start.isoformat(), end.isoformat(), location="A 會議室", description="討論專案進度與待協調事項。", evidence=f"{day} 14:00–15:00（台灣時間）在 A 會議室"),),
    )
    return mail, analysis
