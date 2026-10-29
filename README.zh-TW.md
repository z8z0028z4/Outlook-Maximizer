# Outlook Maximizer

[English](README.md) · [首次使用指南](GETTING_STARTED.zh-TW.md) · [Getting started](GETTING_STARTED.md)

**你的 Outlook，搭配你選擇的 AI。**

Outlook Maximizer 是搭配 **Windows 傳統版 Outlook** 使用的桌面助手。在 Outlook 選取一封信或會議項目後，可以在浮窗查看 AI 摘要、產生並複製回覆草稿，或先確認建議行程，再加入自己的 Google Calendar。

此工具配合電腦上既有的 Outlook 與支援的 AI 命令列工具（CLI）運作，不會替你寄出回覆。請自行檢查草稿、複製並貼回 Outlook，再決定是否寄送。

## 使用前需要

- Windows 與已安裝、已登入的 **Windows 傳統版 Outlook**。使用期間請保持 Outlook 開啟；不支援新版 Outlook for Windows。
- 如需使用摘要與回覆草稿，需準備網路連線，以及至少一個支援的 AI 平台帳號：**ChatGPT / Codex**、**Google Antigravity** 或 **Claude Code**。需安裝對應 CLI 並完成登入；本工具可協助搜尋及安裝。
- 所屬組織允許你執行本工具及 CLI、讀取 Outlook 項目，並將所選內容傳送給所選 AI 服務。公司資安、代理伺服器或管理員設定可能影響使用。
- 如需新增 Google 行程，還需有可使用 Google Calendar 的 Google 帳號，以及自己建立的 Google Cloud 桌面 OAuth 用戶端。

平台訂閱、API 金鑰、使用額度或第三方服務設定都可能影響可用性與費用。已有 ChatGPT、Google 或 Claude 訂閱，不代表所有模型或 API 請求都包含在訂閱內；請確認供應商現行條款及帳號設定。

## 使用 Windows 發行版

發行版公開後，可從 [GitHub Releases](https://github.com/z8z0028z4/Outlook-Maximizer/releases) 下載封裝好的 Windows 程式。若收到的是壓縮檔，先解壓縮，再雙擊 **OutlookMaximizer.exe**。封裝版不需要另外安裝 Python、PyQt6 或 pywin32；請將發行包內的檔案放在一起。

介面預設為英文；如要切換為繁體中文，請在語言設定中選擇、儲存設定，然後重新啟動程式。第一次啟動會開啟設定與可取消的 AI 設定精靈，Outlook 監控預設暫停。只需勾選你想使用的 AI 平台：精靈會搜尋其 CLI，也可使用供應商的安裝程式安裝缺少的工具。在供應商自己的視窗或瀏覽器完成必要登入，再於設定中選擇 AI 來源並儲存；之後從系統匣選擇 **開始監控**。AI 設定與 Google 授權都是自選項目，但摘要和草稿需要可用的 AI 來源。若尚未儲存設定就結束程式，下次啟動會再次顯示首次引導。

詳細步驟請見[首次使用指南](GETTING_STARTED.zh-TW.md)。

## 日常使用

1. 開啟傳統版 Outlook 並啟動 Outlook Maximizer，在 Outlook 選取一封郵件或會議項目。
2. 在浮窗閱讀摘要；可在設定中控制自動摘要。工具可能參考 Outlook 會議欄位產生建議行程；讀取項目不會接受會議邀請。
3. 展開回覆區產生草稿，檢查並複製後自行貼回 Outlook。預設不自動產生回覆，工具也不會寄送郵件。
4. 若出現建議行程，請確認名稱、日期、時間、時區、地點與備註；資訊不清楚時先編輯確認，再選擇加入 Google Calendar。這是個人提醒，工具不會把信件收件人加為與會者。

行程未指定時區時，工具預設使用 `Asia/Taipei`。請在行程編輯畫面確認時區，尤其是在其他地區使用時。

程式會要求 AI 使用介面語言撰寫摘要與待辦，並使用原信語言撰寫回覆草稿；使用前請檢查實際結果。

新增行程是單次動作，並非持續同步。Outlook 中的後續變更或取消不會自動更新或刪除 Google 行程。工具保留本機新增紀錄，協助避免意外重複；確實需要另一筆時，可使用明確的再次加入功能。

程式使用 Windows 通知區域（系統匣）。可從選單顯示或隱藏浮窗、暫停或開始監控、開啟設定，或結束程式。隱藏浮窗會停止自動摘要與回覆產生，直到再次顯示；從系統匣選 **結束** 才會完全關閉程式。

## 隱私與本機資料

產生摘要或回覆時，目前選取項目的主旨、寄件者、內文、接收時間及可取得的會議資訊，連同你自行填寫的 **About me／關於我**，會經由所選本機 CLI 傳給該 AI 服務。摘要與回覆分別發出請求；這不是離線操作，資料保存方式依供應商、帳號及組織政策而定。傳送敏感資料前請先確認。

Google Calendar 為選用功能。你明確同意授權後，工具會要求 `calendar.events.owned` 範圍，該權限涵蓋你擁有的行事曆事件。工具會建立你選擇的事件，並可能在處理重試時查詢同一事件。加入時，行程資料及畫面顯示的信件依據可能傳送給 Google；工具不會持續掃描或同步行事曆。

設定、複製後的 OAuth 用戶端設定與行程新增紀錄儲存於 `%LOCALAPPDATA%\OutlookMaximizer`。Google access／refresh token 使用 Windows DPAPI 加密，綁定目前 Windows 使用者。清除本機 Google 授權會移除本機 token；如需撤銷 Google 帳號端的授權，請另至 Google 帳號設定操作。工具自己的紀錄不保存郵件本文、摘要或回覆文字；AI CLI 與供應商可能有各自的保存政策。請勿將 OAuth JSON 或資料目錄提交至版本庫。

## 從原始碼執行

原始碼版與封裝版不同，需要 Windows、傳統版 Outlook、Python、[PyQt6](https://pypi.org/project/PyQt6/) 與 [pywin32](https://pypi.org/project/pywin32/)。以下指令使用 Python 3.12。選用的 `tzdata` 套件可在 Windows 提供內建備援時區以外的時區資料。請在專案資料夾開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install PyQt6 pywin32 tzdata
.\.venv\Scripts\python app.py
```

若只想用範例資料預覽，不讀取 Outlook，也不呼叫 AI 或 Google Calendar：

```powershell
.\.venv\Scripts\python app.py --demo
```

現有的 `Start Outlook Maximizer.vbs` 與 `start.cmd` 會使用電腦上可用的 Python 啟動原始碼；若依上方步驟將套件安裝在 `.venv`，請使用上方的虛擬環境指令。執行測試：

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
```

若要從原始碼製作單一 Windows 執行檔，請在同一個環境安裝 PyInstaller，並執行封裝腳本：

```powershell
.\.venv\Scripts\python -m pip install PyInstaller
.\.venv\Scripts\python scripts/build_windows.py
```

完成後可雙擊 `dist/OutlookMaximizer.exe` 啟動。請使用與目標電腦相同架構的 Windows 環境進行封裝。

## 其他說明

- [首次設定與故障排除](GETTING_STARTED.zh-TW.md)
- [Google Calendar API 授權範圍](https://developers.google.com/workspace/calendar/api/auth)
- [Google OAuth 同意畫面設定](https://developers.google.com/workspace/guides/configure-oauth-consent)
- [Codex CLI 文件](https://developers.openai.com/codex/cli/)
- [Antigravity CLI 安裝](https://antigravity.google/docs/cli/install/)
- [Claude Code 安裝](https://code.claude.com/docs/en/setup)
