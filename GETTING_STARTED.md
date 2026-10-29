# Getting started with Outlook Maximizer

[Project overview](README.md) · [繁體中文指南](GETTING_STARTED.zh-TW.md)

This guide covers the packaged Windows app. For a source checkout, including how to create a Python environment, see [Run from source](README.md#run-from-source).

## 1. Before you start

1. Open and sign in to **classic Outlook for Windows**. Leave it running. New Outlook for Windows is not supported by this app.
2. Obtain a packaged release through the project's distribution source. If it is an archive, extract it, keep its files together, and double-click **OutlookMaximizer.exe**. A packaged release does not require you to install Python or its packages separately. This project page does not currently specify a download URL.
3. Have an internet connection and, if you want summaries or drafts, an account for at least one supported AI service: ChatGPT / Codex, Google Antigravity, or Claude Code. The provider's CLI must be available and signed in. Google Calendar is optional and can be configured later.
4. Check your organization's rules before transmitting company mail to an AI or Google service. Device, network, Outlook, Google Workspace, and AI-provider policies can restrict installation, sign-in, or access.

The app starts in English. To use Traditional Chinese, choose it in the language setting, save, and restart the app. The first launch opens settings and a cancellable AI setup assistant; Outlook monitoring starts **paused**. If you exit without saving settings, the first-run guidance appears again on the next launch.

## 2. Set up an AI assistant

AI setup is optional, but an available, signed-in AI source is required for summaries and reply drafts. The app uses the provider's CLI and account; it does not ask for your password inside Outlook Maximizer.

1. In the first-run assistant, select only the platforms you want. You can cancel setup and return to the AI setup from settings later.
2. Follow the environment check and start the search/install action. The app looks for the selected CLIs and reuses one already found. For a missing selected CLI, it can run that provider's official Windows installer. Installation needs network access and may be restricted by your organization.
3. Follow the status shown for each selected platform. When sign-in is needed, open its login flow and complete it in the provider's own window or browser. Close the login window when done and allow the app to check again. Some platforms cannot report login status reliably; confirm your account in that platform's own interface.
4. Return to settings, choose your AI source and, if desired, a model and reasoning effort, then save settings. Model availability and usage depend on your provider account. Leaving the model at its default uses the CLI's default.
5. When you are ready for the app to read the selected Outlook item and send it to the chosen AI service, open the app's notification-area menu and choose **Start monitoring**. You can disable automatic summaries in settings before starting if you prefer to trigger them yourself.

To change or add a platform later, open settings from the tray menu and use the AI setup again. A re-search checks selected platforms without reinstalling them. If a CLI was moved or updated, re-search or set its executable path in AI settings.

**Charges:** A subscription or account may have limits, and API keys or third-party configurations may use separate billing. Confirm the active authentication method, model access, limits, and costs with the provider before use. Summaries and replies are separate requests.

## 3. Read a message and prepare a reply

1. With monitoring running, select one message or meeting item in classic Outlook. The floating summary panel appears; if automatic summaries are enabled, it starts a request for the selected item.
2. Read the result and compare it with the original. AI can make mistakes or miss context.
3. Open the reply section when you want a draft. Check the draft, use the copy action, and paste it into an Outlook reply yourself. Outlook Maximizer does not send the reply or accept an invitation for you. Automatic draft generation is off by default and can be changed in settings.
4. Optional: use **About me** in settings to provide relevant role or tone information. That text is saved locally and included with AI requests; do not put secrets there.

Only the selected item is processed for the request. When an item is sent for analysis, its subject, sender, body, received time, and available meeting details, along with optional About me text, go through the selected CLI to its AI service. The provider and CLI may apply their own retention rules.

The app asks the AI to write summaries and action items in the interface language, and reply drafts in the original email's language. Check the result before using it.

## 4. Optional: connect Google Calendar

Google Calendar does not need to be connected to use summaries or replies. To add events, create your own Google Cloud **desktop** OAuth client and authorize the Google account that owns the target calendar. A Google connection in another app or chat does not automatically authorize this desktop app.

Google may change the names or layout of its console. Its [OAuth consent guide](https://developers.google.com/workspace/guides/configure-oauth-consent) and [desktop credential guide](https://developers.google.com/workspace/guides/create-credentials) provide current details.

1. Open [Google Cloud Console](https://console.cloud.google.com/) and select or create a project.
2. In **APIs & Services**, enable **Google Calendar API** for that project.
3. Open **Google Auth platform** and configure the consent screen: enter the app name, support email, and contact information. Choose the audience appropriate to the account and organization. For a personal Google account, select **External**. Organization-managed projects may require administrator involvement.
4. In **Data Access**, add the scope `https://www.googleapis.com/auth/calendar.events.owned`. It permits seeing, creating, changing, and deleting events on calendars you own. Outlook Maximizer uses it to create the event you explicitly select and to look up that event when resolving a retry.
5. In **Audience → Publishing status**, choose how to use your External OAuth project:
   - **In production:** For your own long-term use, select **Publish app** and confirm that the status changes to **In production**. For personal use, Google allows a project to be in production without completing verification. See the publishing and verification notes below.
   - **Testing:** Leave the project in Testing and add the account you will use under **Audience → Test users**. Calendar authorization and its refresh token expire after seven days in this mode.
6. Under **Clients**, create an OAuth client with application type **Desktop app** and download its JSON file. A web-application client or API key is not a substitute. Keep the downloaded JSON private and do not commit it to a repository.
7. Open Outlook Maximizer settings from the tray menu. Load the desktop OAuth JSON, start Google sign-in, and complete the consent in your browser with the intended account. An unverified-app warning may appear: confirm that this is your own project and the requested permission is expected before proceeding through Google's available options, such as **Advanced → Go to [app] (unsafe)**. The app uses a temporary local callback on `127.0.0.1`; it does not require you to host a public website.
8. Return to settings and check that Google is connected. The default calendar ID `primary` means the primary calendar of the account you authorized. You may instead enter the ID of another calendar you own. Save the setting if you change it.

### Publishing status and Google verification

Publishing the OAuth project and completing Google's verification are separate steps. An External project can show **In production** while still unverified. For your own personal project, Google allows limited personal use without verification; an unverified-app warning may still appear. Publishing the OAuth project makes its consent flow available beyond the test-user list; it does not publish Outlook Maximizer's source code or automatically share your mail or calendar. Unverified projects requesting unapproved sensitive or restricted scopes are generally subject to a lifetime cap of 100 new authorizing users. Public distribution may require verification, and Google Workspace administrators can restrict access. See Google's guidance on [publishing status and user caps](https://support.google.com/cloud/answer/15549945?hl=en) and [when verification is not needed](https://support.google.com/cloud/answer/13464323?hl=en).

The seven-day limit described above applies to the External **Testing** status. A refresh token obtained after the project is **In production** is not subject to that Testing-specific limit, but it can still become invalid for other reasons, such as revoked access or prolonged inactivity. If you already authorized the app in Testing, return to Outlook Maximizer settings and select **Sign in to Google** again after publishing to request a new refresh token. See [Google's refresh-token expiration guidance](https://developers.google.com/identity/protocols/oauth2#expiration).

## 5. Add a proposed event

1. Select a message containing a meeting or deadline and read its summary. Scroll to any proposed event.
2. Review and, when needed, edit the title, start and end dates, times, time zone, location, and notes. The default time zone is `Asia/Taipei` when none is specified, so check it carefully, especially outside that region. Check the source evidence in the preview; event details and that evidence may be sent to Google. Ambiguous dates or missing times require confirmation.
3. Choose the add-to-calendar action and wait for the result. The event is a personal reminder and does not invite the email's recipients. Check it in Google Calendar if you want to confirm the result.
4. This creates one event. Later Outlook changes or cancellations do not automatically change it. A local record helps prevent accidental duplicate additions on retry; use the add-again action only when you intentionally want another event.

## 6. Hide, pause, or exit

Find the app in the Windows notification area; it may be behind the **Show hidden icons** arrow. Open its menu to show or hide the floating panels, pause or start monitoring, change settings, or exit. Hiding the panels stops automatic summaries and replies until they are shown again. Pausing monitoring stops reading selections. Choose **Exit** to close the app completely.

## Troubleshooting

- **Nothing appears after launch:** Check the notification area's hidden icons, then use the app's show-panel action. Confirm classic Outlook is running and monitoring has been started. If the app is blocked, check your organization's endpoint policy.
- **No summary:** Check that automatic summaries are enabled or trigger one manually; confirm the selected AI source is installed, signed in, reachable, and allowed for your account. Verify the model and network settings. Re-search for the CLI if it was moved or updated.
- **Installation or login fails:** Check the provider's installer requirements, account access, internet connection, and your organization's proxy or execution restrictions. You can install the CLI separately using the provider's official instructions and then re-search in the app. Do not share passwords or tokens while asking for help.
- **Outlook access is blocked:** Confirm you are using classic Outlook and that it is running. Outlook's security prompts, protected messages, or corporate endpoint policies can limit access; follow your organization's instructions.
- **Google says access is denied or the app is unverified:** Confirm the Calendar API is enabled in the correct project, your account is an authorized test user if the project is in Testing, and the intended account owns the target calendar. Workspace administrators may need to allow the app. An unverified warning can also appear for an unverified project in production; review the project and permissions before consenting.
- **Google sign-in expires or does not return:** Sign in again. Testing-mode authorization expires after seven days; if you switch to In production, sign in again to request a new refresh token. In either mode, check that the default browser and temporary local callback are not blocked by firewall or policy.
- **A calendar event failed or looks wrong:** Confirm the calendar ID, event date, time, time zone, and notes; verify that the account owns the calendar. Review Google Calendar before deliberately adding a second copy. The app's retry handling is designed to avoid an accidental duplicate for the same operation.
- **Using source rather than a release:** Make sure Python, PyQt6, and pywin32 are available in the Python environment launching the app. The source setup in the [README](README.md#run-from-source) uses Python 3.12 and also installs `tzdata` for time-zone support. Run `start.cmd` to see startup errors when using the system Python.

## Local data and disconnecting Google

The app stores settings, its copy of the OAuth client JSON, and calendar-addition records in `%LOCALAPPDATA%\OutlookMaximizer`. Google tokens are encrypted with Windows DPAPI for the current Windows user. Use the app's local authorization-clearing action to remove its Google token; revoke the app separately in your Google account if you also want to withdraw Google-side consent. The app's own records do not store email bodies, summaries, or reply text. CLI and provider data handling is governed separately by their policies.
