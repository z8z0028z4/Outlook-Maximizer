# Outlook Maximizer

[繁體中文](README.zh-TW.md) · [Getting started](GETTING_STARTED.md) · [首次使用指南](GETTING_STARTED.zh-TW.md)

**Your Outlook, your choice of AI.**

Outlook Maximizer is a Windows companion for **classic Outlook**. Select a message or meeting item in Outlook to see an AI summary in a floating panel, prepare a reply to copy, or review a proposed event before adding it to your own Google Calendar.

The app works alongside your existing Outlook installation and a supported AI command-line tool (CLI). It does not send replies for you. Review the draft, copy it, and decide whether to paste and send it in Outlook.

## What you need

- Windows and **classic Outlook for Windows**, installed and signed in. Keep Outlook open while using the companion. New Outlook for Windows is not supported.
- For summaries and reply drafts, internet access and an account for at least one supported AI provider: **ChatGPT / Codex**, **Google Antigravity**, or **Claude Code**. The corresponding CLI must be installed and signed in; the app can search for it and help install it.
- Permission under your organization's policies to run the app and CLI, read Outlook items, and send the selected information to the chosen AI service. Corporate security, proxy, or administrator settings may affect availability.
- Optionally, a Google account with Google Calendar and your own Google Cloud desktop OAuth client if you want to add events.

Provider subscriptions, API keys, usage limits, or third-party settings can affect access and cost. An existing ChatGPT, Google, or Claude subscription does not by itself guarantee that every model or API request is included. Check the provider's current terms and your account configuration.

## Start with a Windows release

When you have a packaged Windows release, extract it first if it is supplied as an archive, then double-click **OutlookMaximizer.exe**. The packaged app does not require a separate Python, PyQt6, or pywin32 installation. Keep the supplied package files together.

The interface opens in English by default. To switch to Traditional Chinese, select it in the language setting, save, and restart the app. The first launch opens settings and a cancellable AI setup assistant, with Outlook monitoring paused. Select only the AI platforms you want to use: setup searches for their CLIs and can install missing ones with the vendors' installers. Complete any required sign-in in the provider's own window or browser, choose your AI source, and save settings. Then choose **Start monitoring** from the tray menu. AI setup and Google authorization are optional, although an AI source is needed for summaries and drafts. If you exit without saving settings, the first-run guidance appears again next time.

No download link is listed here. Obtain a release package from the project's distribution source when one is available. See [Getting started](GETTING_STARTED.md) for detailed first-run, Google Calendar, and troubleshooting steps.

## Everyday use

1. Open classic Outlook and start Outlook Maximizer. Select one email or meeting item in Outlook.
2. Read the summary in the floating panel. Automatic summaries can be controlled in settings. Outlook meeting details may be used to identify a proposed event; reading the item does not accept a meeting invitation.
3. Open the reply section to generate a draft. Review it, copy it, and paste it into Outlook yourself. Automatic reply generation is off by default; the app does not send mail.
4. If a proposed event appears, check its title, dates, time, time zone, location, and notes. Edit or confirm unclear details and choose to add it to Google Calendar. The event is a personal reminder; the app does not add mail recipients as attendees.

The app's default time zone is `Asia/Taipei` when an event does not specify one. Check the time zone in the event editor, especially if you are elsewhere.

The app asks the AI to write summaries and action items in the interface language, and reply drafts in the original email's language. Check the result before using it.

Adding an event is a one-time action, not ongoing synchronization. Later changes or cancellations in Outlook do not automatically update or remove the Google event. The app retains a local record to help avoid unintended duplicates; use the explicit add-again action if you intentionally need another event.

The app uses the Windows notification area (system tray). Use its menu to show or hide the floating panels, pause or resume monitoring, open settings, or exit. Hiding the panels stops automatic summary and reply generation until they are shown again; choose **Exit** from the tray menu to close the app completely.

## Privacy and local data

When you request a summary or reply, the selected item's subject, sender, body, received time, and available meeting details, together with any optional **About me** text you supplied, are sent through the selected local CLI to its AI service. Summary and reply are separate requests. They are not an offline operation; provider retention, account, and organization rules apply. Check the text before sharing sensitive material.

Google Calendar is optional. After your explicit Google consent, the app requests the `calendar.events.owned` scope, which permits access to events on calendars you own. The app creates the event you choose and may look up that same event when resolving a retry. Event details, including any displayed source evidence, are sent to Google when you add it. The app does not continuously scan or synchronize your calendar.

Settings, the copied OAuth client configuration, and calendar addition records are stored under `%LOCALAPPDATA%\OutlookMaximizer`. Google access and refresh tokens are encrypted with Windows DPAPI for the current Windows user. Clearing local Google authorization removes the local token; revoke the app's access in your Google account separately if desired. The app's own records do not store email bodies, summaries, or reply text; the AI CLI and provider may have their own storage policies. Do not commit your OAuth JSON or the data directory to a repository.

## Run from source

The source version is separate from the packaged release and requires Windows, classic Outlook, Python, [PyQt6](https://pypi.org/project/PyQt6/), and [pywin32](https://pypi.org/project/pywin32/). The commands below use Python 3.12. The optional `tzdata` package supplies time-zone data on Windows for event times outside the built-in fallback zones. In a PowerShell window opened at this project folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install PyQt6 pywin32 tzdata
.\.venv\Scripts\python app.py
```

For a preview using sample data without reading Outlook or calling AI or Google Calendar:

```powershell
.\.venv\Scripts\python app.py --demo
```

The existing `Start Outlook Maximizer.vbs` and `start.cmd` launch the source app using an available Python installation; use the virtual-environment command above when dependencies are installed in `.venv`. To run the test suite:

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
```

To build a single-file Windows executable from source, install PyInstaller in the same environment and run the build script:

```powershell
.\.venv\Scripts\python -m pip install PyInstaller
.\.venv\Scripts\python scripts/build_windows.py
```

The resulting `dist/OutlookMaximizer.exe` can be started by double-clicking it. Build on the Windows architecture you intend to support.

## More help

- [First-time setup and troubleshooting](GETTING_STARTED.md)
- [Google Calendar API authorization scopes](https://developers.google.com/workspace/calendar/api/auth)
- [Google OAuth consent configuration](https://developers.google.com/workspace/guides/configure-oauth-consent)
- [Codex CLI documentation](https://developers.openai.com/codex/cli/)
- [Antigravity CLI installation](https://antigravity.google/docs/cli/install/)
- [Claude Code setup](https://code.claude.com/docs/en/setup)
