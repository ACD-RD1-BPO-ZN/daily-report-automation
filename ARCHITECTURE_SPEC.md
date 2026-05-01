# 多頻道自動化日報系統架構規格書 (Architecture Specification)

> 最後更新：2026-05-01

## 1. 專案概述 (Project Overview)
本專案 (`daily-report-automation`) 是一個**多頻道、配置驅動**的自動化新聞摘要與發布系統。系統採用共用核心引擎 (`core/`) 搭配頻道專屬設定檔 (`channels/`) 的架構，能以最低邊際成本擴展至不同主題的新聞頻道（如遊戲產業、電影資訊等）。

每個頻道每天定時從多個 RSS 及網頁來源抓取最新資訊，利用大語言模型 (Gemini 2.5 Flash) 進行摘要總結並排版成 Markdown 格式，同時動態爬取配圖 (Playwright / requests)，最後自動推播至 Discord Webhook（普通日報）與 Discord 論壇頻道（論壇版日報）。

此規格書旨在確立目前的系統核心架構、資料流及各模組職責，作為未來新增功能（如：新增新聞來源、新增發布渠道、新增主題頻道）時的核心設計準則，確保系統的穩定性與高擴展性。

---

## 2. 系統架構與模組職責 (System Architecture & Module Responsibilities)

系統採用**管線化 (Pipeline) + 配置驅動 (Config-Driven) 設計**，由 GitHub Actions 負責排程化執行。核心引擎 (`core/`) 提供與主題無關的通用功能，各頻道的特定配置（RSS 來源、板塊結構、Prompt 模板、論壇標籤）儲存在 `channels/<channel_id>/` 目錄中。

### 頻道設定目錄結構：
```
channels/
└── gamedev/
    ├── channel_config.json    # RSS 來源、板塊定義、標籤映射、Discord 設定
    ├── prompt_template.md     # Gemini Prompt 模板（含佔位符）
    ├── section_rules.json     # 各板塊的專屬 Prompt 規則
    └── history.json           # 頻道獨立的 7 天滾動歷史去重紀錄
```

### 核心模組清單：

#### 2.0 共用引擎層: `core/`

- **`core/report_engine.py`**
  - **職責**：通用的報告生成引擎（讀取任意頻道設定 → 爬取 → LLM → 輸出）。
  - **核心函式**：
    - `load_channel_config(channel_dir)` — 讀取 `channel_config.json`
    - `load_history() / save_history()` — 頻道獨立的歷史去重管理（7 天滾動字典結構，向下相容舊版 list-of-lists）
    - `fetch_sources(config, recent_urls)` — 依設定爬取 RSS + 網頁 fallback + 自定義爬蟲
    - `build_prompt(config, channel_dir, scraped_context, today)` — 讀取 Prompt 模板 + 板塊規則，動態組裝完整 Prompt
    - `call_gemini(config, prompt)` — 呼叫 Gemini API
    - `parse_response() / save_outputs()` — JSON 解析 + 防呆驗證 + 輸出 Markdown & daily_targets.json
    - `generate_report(channel_dir)` — 一鍵完整流程入口

- **`core/discord_api.py`**
  - **職責**：Discord API 共用工具（與頻道主題無關的純 API 操作）。
  - **核心函式**：`post_webhook_message`, `post_embed_to_thread`, `create_forum_thread`, `create_divider_thread`, `fetch_og_image`

#### 2.1 內容生成層: `generate_report.py`（薄入口）
- **職責**：命令列入口，將頻道目錄參數傳遞給 `core/report_engine`。
- **用法**：
  - `python generate_report.py` — 預設遊戲頻道 (`channels/gamedev`)
  - `python generate_report.py channels/movie` — 指定其他頻道
- **輸出約定**：
  - `Daily_Full_Report_YYYYMMDD.md`：完整的最終文字報告。
  - `daily_targets.json`：後續爬圖腳本所需的圖片抓取指示清單 (包含 `section_name`, `source_urls`, `image_keywords`)。

#### 2.2 中央網址後設資料擷取: `fetch_url_metadata.py`
- **職責**：一次性掃描日報中所有來源網址的 `og:image`，建立統一快取。
- **核心行為**：結合 `requests` 與 `Playwright` 非同步擷取，存入 `url_metadata_cache.json`。內建 7 天滾動清理機制，自動刪除過期條目。
- **輸出約定**：`url_metadata_cache.json`（供 `fetch_images_v2.py` 與 `discord_forum_sender.py` 共用）。

#### 2.3 媒體獲取層: `fetch_images_v2.py`
- **職責**：圖片抓取 (Image Scraping)。
- **核心行為**：
  - 讀取 `daily_targets.json`。
  - **多層級爬蟲策略**：
    - *Priority 0*: 查詢 `url_metadata_cache.json` 快取命中（最快）。
    - *Priority 1*: Request + BeautifulSoup (尋找 `og:image` 或符合關鍵字的大圖)。
    - *Priority 2*: Playwright 無頭瀏覽器截圖 (應對動態網頁、Cloudflare 保護、lazy-loading)。
  - **Synthesis 段落**：已停用 Imagen AI 生成，直接跳過該條目（節省費用）。
  - **去重機制**：`used_image_urls` 確保同一張圖不在不同區段重複使用；`used_article_urls` 確保同一篇文章只能貢獻一張圖片。
- **輸出約定**：
  - 將抓取的圖片存入 `assets/` 資料夾，命名規則依賴 JSON 定義 (如 `headline_YYYYMMDD.png`)。
  - **注意**：`assets/` 已加入 `.gitignore`，產出圖片不再推送至 GitHub，僅存在於 GitHub Actions 的臨時執行環境中供發布腳本使用。

#### 2.4 渠道發布層 (發布器 Generators)
發布層由多個獨立腳本組成，每個腳本負責一個特定的對接渠道，彼此互不干涉。

- **`discord_webhook_sender.py`**
  - **職責**：將最終報告拆解並發送至 Discord Webhook（普通日報頻道）。
  - **核心行為**：讀取 Markdown 與 `daily_targets.json` 映射，依照各個大標題（如 `# 📅` 或 `**📢`）將文章切塊 (Chunking)，並將 `assets/` 下的對應圖片夾帶於該段落的第一個 Chunk 發送，避免超過 Discord 單則訊息長度限制。
  - **注意**：`🌌 深度總結` 段落不使用 `synthesis_ai_*.png`，改為重用前一段落已有的圖片。
  - 報告排序依**檔名** (Daily_Full_Report_YYYYMMDD.md 字典序降序) 而非 mtime，確保 CI checkout 後仍能選到最新日期。

- **`discord_forum_sender.py`**
  - **職責**：將每日報告各段落以獨立「討論串 (Thread)」的形式發布至 Discord **論壇頻道 (Forum Channel)**。
  - **核心行為**：
    - 使用 Discord Bot API (`/channels/{forum_channel_id}/threads`) 而非 Webhook。
    - `⚙️ 引擎段落` 依 `ENGINE_SUBSECTION_DEFS` 拆成子串（UE / Unity / 80.lv / 3D），每個子串掛精確的單一標籤。Godot 已移除。
    - `💼 製作人週記` 依引擎關鍵字分流，來源連結透過 `_find_best_bullet_match` 關鍵字匹配（非位置）配對到對應 bullet，繼承該 bullet 的 tag，避免 `[Unity -...]` 顯示名稱誤路由非引擎文章。
    - `_split_into_news_items`：同時支援 `- [xxx](<url>)` 與 `[xxx](<url>)` 兩種來源格式（引擎子段落的連結無前置 `- `）。
    - 每則新聞的**文字 + 來源連結合為單一 Embed**（不再分兩個 Embed）。
    - 透過 `_get_item_title()` 共用函式，將內部 Embed 的標題與最外圍的討論串「預覽字串清單」統整，淘汰依賴粗體字的猜測邏輯，達成內外標題 100% 同步顯示。
    - 討論串封面縮圖一律使用串內第一篇有效 og:image（與串內文章縮圖一致）。
    - `unrealengine.com` 回傳 403 或 Cloudflare 攔截時，直接走 Playwright fallback 抓 og:image。
    - 每日最後發布一則分隔線討論串 `━━━ YYYY-MM-DD ━━━`。
    - `DRY_RUN=true` 時只印預覽，不呼叫 Discord API。
    - 報告排序依**檔名**字典序降序，同 webhook 邏輯。
  - **所需環境變數**：`DISCORD_BOT_TOKEN`, `DISCORD_FORUM_CHANNEL_ID`, `FORUM_TAG_HEADLINE_ID`, `FORUM_TAG_INDIE_ID`, `FORUM_TAG_GLOBAL_ID`, `FORUM_TAG_AI_ID`, `FORUM_TAG_UE_ID`, `FORUM_TAG_UNITY_ID`, `FORUM_TAG_TA_ID`, `FORUM_TAG_3D_ID`。

- **`facebook_poster.py` (可選/獨立模組)**
  - **職責**：擷取報告中的特定段落（今日頭條）並發送至 Facebook Page。
  - **核心行為**：使用正規表達式精準擷取頭條段落的純文字，並透過 Facebook Graph API 結合 `headline_YYYYMMDD.png` 發布推文。

#### 2.4.1 社群互動層: `discord_bot/main.py` (Z 幣金幣機器人)
- **職責**：社群獎勵發放 (Community Reward Dispatcher)。這是一個**獨立常駐的 Discord Bot**，與日報自動化管線完全解耦，專門負責管理員手動觸發的虛擬金幣（Z 幣）空投任務。
- **依賴第三方 API**：透過 **UnbelievaBoat (UB) API** (`PATCH /guilds/{guild_id}/users/{user_id}`) 向特定成員的錢包存入金幣。
- **核心指令清單**（前綴 `!`，**僅限管理員**）：

  | 指令 | 功能描述 |
  |---|---|
  | `!空投 <金額> @成員 <原因>` | 對單一對象正式發放金幣，公告至正式頻道 |
  | `!測試空投 <金額> @成員 <原因>` | 對單一對象測試發放，公告至測試頻道（面板呈現灰色） |
  | `!多重空投 <金額> @成員A @成員B... <原因>` | 對多位指定成員批次發放，合併為單一公告面板 |
  | `!測試多重空投 <金額> @成員A @成員B... <原因>` | 多重空投的測試版，公告至測試頻道 |
  | `!互動發放 <訊息ID> <金額> <原因>` | 掃描指定訊息的所有 Emoji 反應，自動發放給所有互動者（**自動去重複**），含 Money Log 審計 |
  | `!測試互動發放 <訊息ID> <金額> <原因>` | 互動發放的測試版，公告至測試頻道，含 Money Log 審計 |
  | `!全體空投 <金額> <原因>` | 對全伺服器所有非機器人成員進行群發（需 `members` Intent），含二次確認與 Money Log 審計 |
  | `!測試全體空投 <金額> <原因>` | 全體空投的測試版，公告至測試頻道，含 Money Log 審計 |
  | `!總數排行` | 撈取全伺服器金幣排行榜，發布至正式頻道 |
  | `!測試總數排行` | 排行榜測試版，發布至測試頻道 |
  | `!發公告 <文字>` | 讓機器人代發自定義文字至正式頻道 |
  | `!測試發公告 <文字>` | 公告測試版，發布至測試頻道 |

- **核心保護機制**：
  - **PID 實例鎖檔 (`.bot.pid`)**：啟動時偵測是否有存活的其他 Bot 進程，若有則強制阻斷，杜絕雙重實例導致的指令重複執行。
  - **API 429 智能重試 (`_patch_user_balance`)**：捕獲 `429 Too Many Requests` 並解析 `retry_after`，確保每個請求被限速也會排隊等到成功。
  - **速率限制保護**：所有批次 API 請求均在迴圈中加入 `await asyncio.sleep(0.2)`（每秒最多處理 5 人）。**禁止移除此保護**。
  - **Money Log 財報審計系統**：`!全體空投`、`!互動發放` 及其測試版指令均會在發幣前擷取全伺服器餘額快照，發放後寫入 `money_logs/` JSON 紀錄檔。內建滾動清理機制，固定保留最近 10 次紀錄。
  - **全體空投二次確認**：`!全體空投` 會彈出 ✅/❌ 確認面板與狀態鎖 (`_global_airdrop_running`)，防止重複觸發。
  - **Embed 字元截斷**：成功名單若超過 Discord Embed 欄位 1024 字元上限，自動截斷並加上 `...`。
  - **廣播隔離**：管理員在隱密後台頻道輸入指令，公告面板只會發送至對外的 `TARGET_CHANNEL_ID`，指令本身不外洩。
- **顯示偏好**：
  - `!互動發放` 與 `!測試互動發放` 的發放名單以純文字 `display_name`（逗號分隔）顯示，**不 @標註 (Mention) 使用者**。公告面板外圍也不附加標註字串。
- **所需環境變數**（讀取自根目錄 `.env` 或 `discord_bot/.env`）：
  - `DISCORD_BOT_TOKEN`：Bot 登入 Token。
  - `UB_TOKEN`：UnbelievaBoat API 授權 Token。
  - `GUILD_ID`：目標伺服器 ID。
  - `TARGET_CHANNEL_ID`：正式空投公告頻道 ID。
  - `TEST_CHANNEL_ID`：測試版公告頻道 ID。
  - `OFFICIAL_MENTION`：正式公告的 @mention 對象（`everyone` 或 Role ID）。
  - `TEST_MENTION`：測試公告的 @mention 對象。
- **注意事項**：
  - `!互動發放` 的訊息搜尋範圍**僅限**：①指令發動的當下頻道、②`TARGET_CHANNEL_ID` 頻道。若目標訊息在其他頻道，需在該頻道直接輸入指令。
  - 本 Bot 為**長駐進程**（`bot.run()`），需在專屬伺服器/本地環境獨立啟動，**不由 GitHub Actions 管線觸發**（僅 `--auto-weekly` 排行榜為例外，由 Actions 一次性呼叫後自動關閉）。
  - `money_logs/` 與 `.bot.pid` 已加入 `.gitignore`，不上傳至 GitHub。

#### 2.5 自動化排程層: `.github/workflows/daily_report.yml`
- **職責**：CI/CD 與環境變數注入。
- **核心行為**：
  - 透過 Cron Job 每天定時觸發 (`50 23 * * *` UTC = 07:50 CST)，同時支援 `workflow_dispatch` 手動觸發。
  - 依序執行：`generate_report.py` → `fetch_url_metadata.py` → `fetch_images_v2.py` → `discord_webhook_sender.py` → `discord_forum_sender.py` → `discord_bot/main.py --auto-weekly`（僅週一發布排行榜）。
  - 將變更後的 `global_history.json`、`daily_targets.json`、`url_metadata_cache.json`、`Daily_Report/` 自動 commit 並 push 回 Repository（commit message 附 `[skip ci]` 避免無限循環）。
  - **注意**：`assets/` 與 `Podcast/` 已從 commit 清單中移除，產出的圖片與音檔僅存在於 Actions 臨時環境中，不再推送至 GitHub，以避免二進制檔案持續膨脹儲存庫容量。

---

## 3. 核心設計模式與資料流向 (Design Patterns & Data Flow)

### 3.1 資料流管線 (Data Pipeline)
整個系統嚴格遵守單向資料流，上游模組的輸出即為下游模組的輸入。
```text
[網頁/RSS來源]
      ↓ (fetch)
[generate_report.py] --(給 Prompt)--> [Gemini API]
      ↓
(產生文字報告 Markdown) + (產生圖片需求 daily_targets.json)
      ↓
[fetch_url_metadata.py] --(擷取 og:image)--> [存入 url_metadata_cache.json]
      ↓
[fetch_images_v2.py] --(依據 JSON + Cache)--> [爬取圖片 -> 存入 assets/]
      ↓
[discord_webhook_sender.py] --(讀取 Markdown + assets/)--> [Discord Webhook 普通日報]
[discord_forum_sender.py]   --(讀取 Markdown + Cache)--> [Discord Forum 論壇版日報]
[discord_bot/main.py --auto-weekly] --(僅週一)--> [Discord 排行榜公告]
      ↓
[GitHub Actions] --(自動 commit)--> [僅推送 JSON + Markdown 回 Repo，不含圖片/音檔]
```

### 3.2 解耦設計 (Decoupling)
- **文字與圖片解耦**：`generate_report.py` 只管文字與宣告圖片需求，不負責耗時且易錯的圖片處理；`fetch_images_v2.py` 只管產生實體圖片。這保證了文字生成的穩定性。
- **發布渠道解耦**：Discord 發布與 Facebook 發布完全無關。這讓各種平台的格式適應（如 Discord 的 Chunking, FB 的純提取）能夠各自封裝。
- **頻道與引擎解耦**：頻道設定 (`channels/`) 與核心引擎 (`core/`) 完全分離。新增頻道不需修改任何 Python 程式碼，只需建立新的 JSON 設定檔。

---

## 4. 未來擴展與修改準則 (Guidelines for Future Extension)

確保未來維護與擴充時，不會破壞現有的穩定架構。請嚴格遵守以下準則：

### 4.0 新增主題頻道 (New Topic Channel)
- **修改位置**：建立 `channels/<new_id>/` 目錄。
- **準則**：
  1. 複製 `channels/gamedev/` 目錄並重新命名（如 `channels/movie/`）。
  2. 修改 `channel_config.json`：更換 `rss_feeds`、`sections`、`forum_tags`、`discord` 等設定。
  3. 修改 `prompt_template.md`：調整 AI 角色定位與內容指引。
  4. 修改 `section_rules.json`：定義各板塊的專屬 Prompt 規則。
  5. 在 `.github/workflows/` 新增或修改 workflow，加入 `python generate_report.py channels/movie`。
  6. **全程不需修改任何 Python 程式碼**。

### 4.1 新增或修改新聞來源 (News Sources)
- **修改位置**：`channels/<channel_id>/channel_config.json` 中的 `rss_feeds`、`scrape_fallbacks`、`scrape_custom`。
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
  1. 必須維持現有的快取命中 (Priority 0) → 靜態嘗試 (Priority 1) → 動態 Playwright (Priority 2) → 預設圖 (Fallback) 的恩典降級 (Graceful Degradation) 策略。
  2. `Synthesis` 類型已停用，若未來要恢復請在 `if sec_type == "Synthesis"` 處重新實作，並評估 Imagen API 費用。
  3. 確保 `used_image_urls` 與 `used_article_urls` 機制持續運作，避免同一張圖或同一篇文章在不同區段被重複使用。

### 4.5 論壇發布邏輯維護 (Forum Sender Maintenance)
- **修改位置**：`discord_forum_sender.py`。
- **關鍵資料結構**：
  - `ENGINE_SUBSECTION_DEFS`：新增引擎/工具子標題時，在此新增 `(regex, display_name, tag_keys, src_prefixes)` 元組。`src_prefixes` 須包含該來源所有可能的連結前綴（如 `[80.lv`）。
  - `SECTION_TAG_MAP`：新增段落 Emoji → tag 對應。
  - `TAG_IDS`：新增 tag key → Discord 標籤 ID。
- **來源連結配對規則**：
  - `_split_into_news_items` 同時辨識 `[xxx](<url>)` 與 `- [xxx](<url>)` 格式。
  - `_find_best_bullet_match` 採用英文關鍵字 + 版本號 + 中文滑動視窗多維度匹配。
  - `_route_content_by_engine` 的 source 路由依 `_find_best_bullet_match` 繼承 bullet 的 tag（非獨立靠顯示名稱關鍵字）。

### 4.6 儲存空間與二進制檔案管理 (Storage & Binary File Policy)
- **準則**：
  1. **禁止將每日產出的圖片、音檔推送至 GitHub**：`assets/`、`Podcast/`、`*.mp3` 已在 `.gitignore` 中排除。工作流程 `.github/workflows/daily_report.yml` 中也已移除 `git add assets/`。
  2. **僅允許純文字紀錄回存 GitHub**：`global_history.json`、`daily_targets.json`、`url_metadata_cache.json`、`Daily_Report/*.md`。
  3. **`money_logs/` 與 `.bot.pid` 只存在於本機**：已加入 `.gitignore`，不上傳至 GitHub。Money Log 內建滾動清理，固定保留最近 10 筆。
  4. 若未來新增產出物（如新的圖片或媒體檔案），必須同步將其加入 `.gitignore`，避免儲存庫容量無限膏脹。

### 4.7 測試環境 (Test Mode)
- **準則**：開發論壇發布新功能時善用 `DRY_RUN=true` 環境變數，只印預覽不呼叫 Discord API。開發圖片抓取時善用 `USE_LOCAL_TEST_IMAGE=True`（在 `fetch_images_v2.py` 中設定）。
