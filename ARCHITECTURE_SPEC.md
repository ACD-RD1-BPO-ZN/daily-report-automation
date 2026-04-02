# 遊戲產業自動化日報系統分析與架構規格書 (Architecture Specification)

## 1. 專案概述 (Project Overview)
本專案 (`daily-report-automation`) 是一個完全自動化的遊戲產業新聞摘要與發布系統。它每天定時從多個遊戲開發相關的 RSS 及網頁來源抓取最新資訊，利用大語言模型 (Gemini 2.5 Flash) 進行摘要總結並排版成 Markdown 格式，同時動態抓取或生成配圖 (Playwright / Gemini Imagen)，最後自動推播至 Discord 等社群平台。

此規格書旨在確立目前的系統核心架構、資料流及各模組職責，作為未來新增功能（如：新增新聞來源、新增發布渠道）時的核心設計準則，確保系統的穩定性與高擴展性。

---

## 2. 系統架構與模組職責 (System Architecture & Module Responsibilities)

系統採用**管線化 (Pipeline) 設計**，由 GitHub Actions 負責排程化執行，主流程被拆分為多個職責單一的獨立 Python 腳本，透過共用的中間檔案（Markdown、JSON）進行資料傳遞。

### 核心模組清單：

#### 2.1 內容生成層: `generate_report.py`
- **職責**：資料獲取 (Data Ingestion) 與 內容生成 (Content Generation)。
- **核心行為**：
  - 透過 `feedparser` 與 `requests` + `BeautifulSoup` 抓取多個遊戲新聞來源 (80.lv, Unreal, Unity, Godot 等)。
  - 維護 `headline_history.json` 確保 3 天內的頭條不重複。
  - 將爬取的原始內容 (Context) 餵給 Gemini API，並強制要求回傳嚴格的 **JSON 格式**。
- **輸出約定**：
  - `Daily_Full_Report_YYYYMMDD.md`：完整的最終文字報告。
  - `daily_targets.json`：後續爬圖腳本所需的圖片抓取指示清單 (包含 `section_name`, `source_urls`, `image_keywords` 與 AI 算圖用的 `ai_prompt`)。

#### 2.2 媒體獲取層: `fetch_images_v2.py`
- **職責**：圖片抓取 (Image Scraping) 與 AI 圖片生成 (AI Image Generation)。
- **核心行為**：
  - 讀取 `daily_targets.json`。
  - **傳統圖片**：採用多層級的爬蟲策略。
    - *Priority 1*: Request + BeautifulSoup (尋找 `og:image` 或符合關鍵字的大圖)。
    - *Priority 2*: Playwright 無頭瀏覽器截圖 (應對動態網頁、Cloudflare 保護、lazy-loading)。
  - **AI 生成圖片 (Synthesis 區塊)**：呼叫 Gemini `imagen-4.0-generate-001` 模型，利用 `ai_prompt` 產生圖片 (`TEST_MODE=true` 時可跳過以節省成本)。
- **輸出約定**：
  - 將抓取或生成的圖片存入 `assets/` 資料夾，命名規則依賴 JSON 定義 (如 `headline_YYYYMMDD.png`)。

#### 2.3 渠道發布層 (發布器 Generators)
發布層由多個獨立腳本組成，每個腳本負責一個特定的對接渠道，彼此互不干涉。

- **`discord_webhook_sender.py`**
  - **職責**：將最終報告拆解並發送至 Discord Webhook。
  - **核心行為**：讀取 Markdown 與 `daily_targets.json` 映射，依照各個大標題（如 `# 📅` 或 `**📢`）將文章切塊 (Chunking)，並將 `assets/` 下的對應圖片夾帶於該段落的第一個 Chunk 發送，避免超過 Discord 單則訊息長度限制。

- **`discord_forum_sender.py`**
  - **職責**：將每日報告各段落以獨立「討論串 (Thread)」的形式發布至 Discord **論壇頻道 (Forum Channel)**。
  - **核心行為**：
    - 使用 Discord Bot API (`/channels/{forum_channel_id}/threads`) 而非 Webhook，因為論壇頻道必須透過 Bot Token 操作。
    - 段落切割邏輯與 `discord_webhook_sender.py` 相同（共用 `section_pattern`）。
    - 每個段落（跳過 `# 📅` 純標題行）建立一個獨立討論串，串名格式為 `YYYY-MM-DD | {段落標題}`。
    - 透過 `SECTION_TAG_MAP` 字典，自動依段落 Emoji 掛上對應的論壇標籤 ID（如 🔹UE、🔸Unity），讓社群成員能夠篩選只看特定引擎的文章。
    - 圖片以 multipart/form-data 附件形式夾帶於討論串的第一則訊息；超過字元上限的內容自動分段補發至同一串內。
  - **所需環境變數**：`DISCORD_BOT_TOKEN`, `DISCORD_FORUM_CHANNEL_ID`, `FORUM_TAG_HEADLINE_ID`, `FORUM_TAG_UE_ID`, `FORUM_TAG_UNITY_ID`, `FORUM_TAG_AI_ID`, `FORUM_TAG_MARKET_ID`。

- **`facebook_poster.py` (可選/獨立模組)**
  - **職責**：擷取報告中的特定段落（今日頭條）並發送至 Facebook Page。
  - **核心行為**：使用正規表達式精準擷取頭條段落的純文字，並透過 Facebook Graph API 結合 `headline_YYYYMMDD.png` 發布推文。

#### 2.4 自動化排程層: `.github/workflows/daily_report.yml`
- **職責**：CI/CD 與環境變數注入。
- **核心行為**：
  - 透過 Cron Job 每天定時觸發 (`50 23 * * *` UTC)。
  - 依序執行：`generate_report.py` -> `fetch_images_v2.py` -> `discord_webhook_sender.py`。
  - 將變更後的 `headline_history.json` 自動 commit 並 push 回 Repository，確保狀態持久化。

---

## 3. 核心設計模式與資料流向 (Design Patterns & Data Flow)

### 3.1 資料流管線 (Data Pipeline)
整個系統嚴格遵守單向資料流，上游模組的輸出即為下游模組的輸入。
```text
[網頁/RSS來源]
      ↓ (fetch)
[generate_report.py] --(Prompt)--> [Gemini API]
      ↓
(產生文字報告 Markdown) + (產生圖片需求 daily_targets.json)
      ↓
[fetch_images_v2.py] --(依據 JSON)--> [抓圖片/生成圖片 -> 存入 assets/]
      ↓
[發布腳本 (discord, facebook 等)] --(讀取 Markdown 與 assets/)--> [目標社群 API]
```

### 3.2 解耦設計 (Decoupling)
- **文字與圖片解耦**：`generate_report.py` 只管文字與宣告圖片需求，不負責耗時且易錯的圖片處理；`fetch_images_v2.py` 只管產生實體圖片。這保證了文字生成的穩定性。
- **發布渠道解耦**：Discord 發布與 Facebook 發布完全無關。這讓各種平台的格式適應（如 Discord 的 Chunking, FB 的純提取）能夠各自封裝。

---

## 4. 未來擴展與修改準則 (Guidelines for Future Extension)

確保未來維護與擴充時，不會破壞現有的穩定架構。請嚴格遵守以下準則：

### 4.1 新增或修改新聞來源 (News Sources)
- **修改位置**：`generate_report.py` 裡的 `fetch_rss_feeds()`。
- **準則**：
  1. 確保回傳的 `scraped_data` 格式一致（`### 來源資訊: ... \n - [標題]: ... `）。
  2. 若遇到反爬蟲 (403 等)，應在該函式內實作 Try-Catch Fallback (例如改用 `requests` + 偽裝 Header)，**絕對不要**讓單一來源的失敗導致整個腳本崩潰。
  3. 不要在此處過濾資料，將原始摘要交給 Gemini 來做 AI 篩選。

### 4.2 調整報告結構或新增區塊 (New Report Sections)
- **修改位置**：`generate_report.py` (Prompt 定義區段)。
- **準則**：
  1. 必須同步調整 Prompt 中的 Markdown 模板要求（如新增 `**🔮 【未來趨勢】**` 及對應的圖片標籤）。
  2. **致命關鍵**：必須在 JSON 結構輸出範例中的 `image_targets` 陣列新增該區塊的定義，包含 `section_name` 與 `image_keywords`，否則 `fetch_images_v2.py` 將無法為其抓取圖片。
  3. 修改 `discord_webhook_sender.py` 裡的 `section_pattern` 正規表達式，確保新的大標題能被正確切塊（Chunking），例如新增 `🔮` 到 `(?=# 📅|\*\*[📢🎨🎮🇨🇳🇹🇼📍💼🤝🌌🔮])`。

### 4.3 新增新的發布渠道 (e.g., Line Bot, Telegram)
- **修改位置**：建立全新的獨立 Python 腳本 (如 `telegram_sender.py`)，並修改 `.github/workflows/daily_report.yml`。
- **準則**：
  1. **禁止修改現有 Sender**：不要把 Telegram 的邏輯寫進 `discord_webhook_sender.py`，應保持單一職責模式。
  2. 新模組純粹負責讀取已生成的 `Daily_Full_Report_*.md` 及 `assets/` 目錄，然後進行自身平台需要的文字轉換或切塊。
  3. 在 `daily_report.yml` 的 `Run Report Automation` step 中追加 `python telegram_sender.py` 即可。

### 4.3.1 調整論壇頻道標籤對應 (Forum Channel Tag Mapping)
- **修改位置**：`discord_forum_sender.py` 裡的 `SECTION_TAG_MAP` 字典與 `TAG_IDS` 字典。
- **準則**：
  1. **新增段落 Emoji → 標籤**：在 `SECTION_TAG_MAP` 新增一筆，鍵為段落 Emoji（如 `"🔮"`），值為 `TAG_IDS` 中的鍵名清單（如 `["ai"]`）。
  2. **新增標籤種類**：在 Discord 論壇頻道建立新標籤後，將標籤 ID 加入 GitHub Secrets，再於 `TAG_IDS` 新增對應的 `os.getenv(...)` 讀取。
  3. 同步更新 `daily_report.yml` 中 `env:` 區塊，宣告新增的 Secret 為環境變數。

### 4.4 圖片抓取邏輯優化 (Image Fetching Enhancements)
- **修改位置**：`fetch_images_v2.py`。
- **準則**：
  1. 必須維持現有的靜態嘗試 (Priority 1) -> 動態 Playwright (Priority 2) -> AI 生成 / 預設圖 (Fallback) 的恩典降級 (Graceful Degradation) 策略。
  2. 確保 `used_image_urls` 機制持續運作，避免同一張圖在不同區段被重複使用。

### 4.5 測試環境 (Test Mode)
- **準則**：開發新功能時，請善用 `TEST_MODE=true` 環境變數。`fetch_images_v2.py` 已實作此判斷來略過昂貴的 Gemini 圖片生成。未來加入耗費成本或具破壞性的呼叫時，應將其包覆在 `if not test_mode:` 的檢查中。
