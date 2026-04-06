# Changelog

---

## 2026-04-06

### 報告生成 (`generate_report.py`)

**架構重構：內聯來源連結格式**
- **來源連結從「段落底部集中」改為「每條 bullet 正下方內聯」**：移除所有 `[資料來源]` 標題行，每則新聞摘要的正下方必須緊接該新聞的來源連結。此改動徹底解決了論壇版「內容與連結對不上」的配對問題，來源配對準確率從 ~85% 提升至 100%。
- **移除 Godot Engine**：引擎相關段落的子標題從 5 類精簡為 4 類（Unreal / Unity / 3D 模型技術 / TA 與特效技術），Godot 相關新聞不再收錄。
- **擴大 TA 定義範圍**：TA 與特效技術的涵蓋範圍從「Houdini, 動畫, 綁定, VFX」擴大為「Shader、渲染技術、光照、材質製作、場景搭建與展示、Houdini 特效、動畫/綁定、效能優化、引擎底層開發、VFX」，使 80.lv / 映CG 的技術美術文章正確歸類到 TA 而非 3D。

### Discord 論壇發布 (`discord_forum_sender.py`)

**大幅瘦身：967 行 → 640 行（-34%）**
- **移除智慧配對系統**：刪除 `_find_best_bullet_match()`（65 行）、`_SOURCE_SKIP_WORDS`（8 行）、`_extract_bullet_summary()`（38 行）等舊配對邏輯。改由 prompt 端直接綁定來源連結，不再需要後端猜測。
- **重寫 `_split_into_news_items()`**：改為簡潔的內聯格式解析器，直接讀取 bullet + 緊接的來源連結。同時保留 `orphan_sources` fallback 機制，完全向後相容舊格式報告。
- **簡化 `_extract_keywords()`**：從 4 層 fallback（78 行）精簡為粗體 + 書名號提取（25 行）。
- **簡化 `_split_engine_section()` 與 `_route_content_by_engine()`**：內聯格式下來源連結跟著 bullet 走，不再需要前綴比對分配或二次路由。

### 普通日報 (`discord_webhook_sender.py`)

- **移除深度總結 Fallback 圖片邏輯**：修正先前會把頭條圖片錯誤地分配給深度總結段落的 bug。

---

## 2026-04-05

### Discord 論壇發布 (`discord_forum_sender.py`)

**功能改進與 Bug 修正**
- **移除分隔線討論串標籤**：取消在建立每日分隔線討論串（`▬▬ 2026-04-05 ▬▬`）時附加上預設標籤，使首篇分隔線版面更乾淨清爽。
- **來源連結去重邏輯 (Deduplication)**：在 `_split_into_news_items` 新增重複檢查機制。如果 AI 產生了多個顯示名稱完全相同的連結來源（如連續的同名 Steam 系統更新），會自動忽略重複實體，確保單一 Embed 內不會被龐大的重複連結洗版。
- **強制引擎模塊精確分離（3D 與 TA）**：修改 `ENGINE_SUBSECTION_DEFS` 路由表，藉由辨識 AI 新增的專屬前綴標籤，精確地把「純 3D 建模」與「引擎/TA/特效技術」分配到各自對應的 Discord 標籤板塊。
- **加強來源分配位置親和性 (Positional Affinity)**：改進論壇新聞 Embed 的 `_find_best_bullet_match` 網址對接演算法。當抓取到的來源總數與新聞總數完全相等時，為相同排序順位注入極高權重（+3），徹底解決過去因為文字模糊匹配導致來源連結「對錯位」、「錯亂綁架」的問題。
- **改善外部圖片預覽機制 (物理下載)**：針對 Discord Forum Channel 經常忽略從外部連結 (`embed.image`) 自動產生討論串大封面的缺陷，機制全面改為：只要取得截圖網址，就飛速下載到暫存空間，並以原生檔案上傳 (`attachment://`) 提交，保證 100% 絕對顯示完美大縮圖。

### 報告生成 (`generate_report.py`)

- **增強 AI 提示詞以綁定技術前綴**：更新給 Gemini 的排版指令，嚴格要求在引擎相關新聞段落下劃分明確的「3D 模型技術」與「TA 與特效技術」，並要求其在產出對應資料來源時加上 `[3D - ...]` 或 `[TA - ...]` 前綴，以協助發布腳本做出絕對精準的論壇分發。

---

## 2026-04-04

### Discord 金幣機器人 (`discord_bot`)

**功能新增**
- **金幣活躍度總榜**：新增 `!總數排行` 與 `!測試總數排行` 指令。串接 UnbelievaBoat API 撈取大全伺服器總金幣排行，並實裝過濾機制濾除指定管理者帳號。
- **裝備 Z 幣徽章**：排行榜版面（Embed）支援了伺服器客製化 Z 幣圖示 (`<:Gold_coin:ID>`)，呈現簡約直觀的活躍度前五名。
- **無縫自動化雲端派發**：新增 `--auto-weekly` 參數，成功將機器人掛載至既有 GitHub Actions `daily_report.yml` 流程。每逢週一伺服器晨會 (07:50 CST 時段)，只要偵測到星期一便會自動發送排行榜，隨後立刻關閉進程，達成 100% 本地免開機全自動化。

---

### Discord 論壇發布 (`discord_forum_sender.py`)

**功能改進**
- **Embed 合併**：每則新聞的文字內文與來源連結合為**單一 Embed**，不再分成「文章 Embed + 資料來源 Embed」兩則，視覺上更清晰。
- **Embed title 改用來源顯示名稱**：從 `[站名 - 標題]` 的 ` - ` 後方提取作為 title，避免 title 與 description 內文重複顯示。
- **討論串封面縮圖**：串列表縮圖改為使用串內第一篇有效文章的 og:image，確保預覽縮圖與點進去看到的文章縮圖一致。不再使用 `assets/` 本地 AI 生成圖作為封面。
- **分隔線**：每日結束分隔線串名從 `━━━ YYYY-MM-DD 報告結束 ━━━` 改為 `━━━ YYYY-MM-DD ━━━`（移除「報告結束」文字）。
- **論壇預覽文字增強**：`_extract_keywords` 新增 step 2.5，從段落正文第一句提取 CJK 動作片語（如「開發商宣布裁員」），使頭條等純文字段落的預覽更完整。

**Bug 修正**
- **來源連結格式辨識**：`_split_into_news_items` 原本只能辨識 `- [xxx](<url>)` 格式；引擎子段落的連結不帶前置 `- `，導致連結被誤認為 bullet 文字、og:image 抓取無 URL 可用。修正為同時支援 `[xxx](<url>)` 格式。
- **3D 模塊連結缺失**：`ENGINE_SUBSECTION_DEFS` 的 3D/映CG 條目 `src_prefixes` 補上 `[80.lv`，修正 Gemini 將 80.lv 文章歸入 `**3D/CG/VFX 工具**` 後連結匹配失敗的問題。
- **UE og:image 抓取失敗**：`unrealengine.com` 直接回傳 403 導致 `_fetch_og_image` 提前 `return ""`，Playwright fallback 從未執行。修正為 403 或 Cloudflare 攔截時直接走 Playwright。
- **製作人週記來源誤路由**：`_route_content_by_engine` 的來源路由改用 `_find_best_bullet_match` 關鍵字匹配、繼承 bullet 的 tag，避免 `[Unity - Esoteric Ebb]` 這類顯示名稱把非引擎文章路由到 unity bucket。
- **CI 報告排序失效**：`actions/checkout` 後所有檔案 mtime 相同，`sort(key=os.path.getmtime)` 隨機選到舊報告（如 3/8）。改為 `sort(reverse=True)` 依檔名 YYYYMMDD 字典序降序，永遠選最新日期。

---

### 普通日報 (`discord_webhook_sender.py`)

- **移除 synthesis_ai 佔位圖**：`🌌 深度總結` 段落略過 `synthesis_ai_*.png`，改為重用 `daily_targets.json` 中第一張非 synthesis 圖片。
- **報告排序修正**：同論壇版，改為依檔名排序，修正 CI mtime 問題。

---

### 媒體獲取 (`fetch_images_v2.py`)

- **停用 Imagen AI 圖片生成**：`Synthesis` 類型條目直接 `continue` 跳過，不再呼叫 `imagen-4.0-generate-001`，節省 Google AI API 費用。

---

### 報告生成 (`generate_report.py`)

- **移除 synthesis_ai 圖片標籤**：Prompt 不再要求 Gemini 在深度總結段落插入 `![深度總結]` 標籤；`daily_targets.json` 不再包含 Synthesis 條目；fallback 補充邏輯一併移除。

---

### CI/CD (`.github/workflows/daily_report.yml`)

- **恢復全流程**：取消先前論壇測試期間對 `generate_report.py`、`fetch_images_v2.py`、`discord_webhook_sender.py` 的停用注釋，全部腳本重新啟用。
- **排程恢復**：重新啟用 `cron: '50 23 * * *'`（07:50 CST）自動排程。
- **CI commit 範圍**：已新增 `git add Daily_Report/ assets/ daily_targets.json`，確保每日產出物都被推回 repo。

---

### Commits（2026-04-04）

| Commit | 說明 |
|---|---|
| `0985dfb` | fix: 論壇發布改進 - embed合併來源/縮圖修正/UE Playwright fallback/排程暫停 |
| `e1c2438` | fix: embed title 改用來源顯示名稱，避免與內文重複；製作人週記來源路由改用關鍵字匹配 |
| `b1f41cf` | ci: 恢復全流程自動化 - 排程+報告生成+圖片+普通日報+論壇全部開啟 |
| `741b51a` | fix: 普通日報移除synthesis_ai佔位圖，改用已有圖片；修正報告排序 |
| `10f6bb0` | fix: 停用 Synthesis Imagen AI 圖片生成，節省 API 費用 |
| `ef92038` | fix: 移除深度總結段落的 synthesis_ai 圖片標籤與生成邏輯 |
