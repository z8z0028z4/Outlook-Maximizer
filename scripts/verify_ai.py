"""Optional live AI check using a fabricated email, never a real mailbox."""

from pathlib import Path
from datetime import datetime
import argparse
import json
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from outlook_maximizer.ai import create_summarizer
from outlook_maximizer.cli_setup import discover_claude
from outlook_maximizer.demo import demo_data
from outlook_maximizer.settings import Settings, discover_antigravity, discover_codex


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("codex", "antigravity", "claude"), default="codex")
    parser.add_argument("--model", default="")
    parser.add_argument("--effort")
    parser.add_argument("--language", choices=("en", "zh-TW"), default="en")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    if not 15 <= args.timeout <= 300:
        parser.error("--timeout must be between 15 and 300 seconds")
    mail, expected = demo_data(args.language)
    started = time.monotonic()
    settings = Settings(ai_provider=args.provider, ui_language=args.language, codex_path=discover_codex(),
                        antigravity_path=discover_antigravity(), claude_path=discover_claude(),
                        ai_timeout_seconds=args.timeout)
    if args.provider == "codex":
        settings.model = args.model
        if args.effort is not None:
            settings.codex_effort = args.effort
    elif args.provider == "antigravity":
        settings.antigravity_model = args.model
        if args.effort is not None:
            settings.antigravity_effort = args.effort
    else:
        settings.claude_model = args.model
        if args.effort is not None:
            settings.claude_effort = args.effort
    provider = create_summarizer(settings)
    result = provider.analyze(mail, include_reply=False)
    assert result.summary and result.reply_draft == ""
    draft = provider.generate_reply(mail)
    assert draft
    assert len(result.events) == 1
    candidate = result.events[0]
    candidate.validate(for_insert=True)
    assert datetime.fromisoformat(candidate.start.replace("Z", "+00:00")) == datetime.fromisoformat(expected.events[0].start)
    assert datetime.fromisoformat(candidate.end.replace("Z", "+00:00")) == datetime.fromisoformat(expected.events[0].end)
    if args.language == "zh-TW":
        assert any("\u4e00" <= char <= "\u9fff" for line in result.summary for char in line)
    else:
        assert any(char.isascii() and char.isalpha() for line in result.summary for char in line)
    print(json.dumps({"status": "ok", "provider": args.provider, "language": args.language, "synthetic_only": True, "independent_requests": True, "summary_points": len(result.summary), "reply_generated": True, "calendar_times_correct": True, "elapsed_seconds": round(time.monotonic() - started, 1)}))


if __name__ == "__main__":
    main()
