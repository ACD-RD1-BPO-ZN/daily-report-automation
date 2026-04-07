# -*- coding: utf-8 -*-
import os
import json
import asyncio
from datetime import datetime, timezone, timedelta
import requests
from bs4 import BeautifulSoup
import google.genai as genai
from google.genai import types as genai_types
from dotenv import load_dotenv
import feedparser
import re

# 讀取本地端 .env
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("Error: GEMINI_API_KEY is not set.")
    exit(1)

client = genai.Client(api_key=GEMINI_API_KEY)

# 台灣時間 (UTC+8)
tz = timezone(timedelta(hours=8))
today_date = datetime.now(tz)
today_str_display = today_date.strftime("%Y-%m-%d")
today_str_file = today_date.strftime("%Y%m%d")

def fetch_rss_feeds(recent_urls=None):
    if recent_urls is None:
        recent_urls = set()
    print("Fetching active RSS feeds for strict context...")
    feeds = {
        "80.lv (TA/Tech)": "https://80.lv/rss",
        "Unreal Engine News": "https://www.unrealengine.com/en-US/rss",
        "Unity Blog": "https://blog.unity.com/feed",
        "Godot Engine Blog": "https://godotengine.org/rss.xml",
        "Game Developer (Indie/Business)": "https://www.gamedeveloper.com/rss.xml",
        "Bahamut GNN (Local Taiwan News)": "https://gnn.gamer.com.tw/rss.xml",
        "Steam News": "https://store.steampowered.com/feeds/news.xml"
    }
    
    scraped_data = ""
    # Hack to bypass some basic user-agent blocks in feedparser
    feedparser.USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    
    recent_news_count = 0
    for source_name, url in feeds.items():
        try:
            parsed = feedparser.parse(url)
            if not parsed.entries:
                print(f"  [Warning] No entries found for {source_name}")
                continue
                
            scraped_data += f"\n### 來源資訊: {source_name}\n"
            for entry in parsed.entries[:6]:  # 取前 6 篇最新文章
                link = entry.get('link', '')
                if link in recent_urls:
                    print(f"  [Skip] Recently reported: {link}")
                    continue
                title = entry.get('title', 'No Title')
                # 清理 HTML 標籤
                summary_raw = entry.get('summary', '') or entry.get('description', '')
                summary_clean = re.sub(r'<[^>]+>', '', summary_raw)[:250].strip()
                scraped_data += f"- 【標題】: {title}\n  【網址 URL】: {link}\n  【摘要前言】: {summary_clean}...\n"
                recent_news_count += 1
        except Exception as e:
            print(f"  [Error] Failed to fetch {source_name}: {e}")
            
    # Fallback: directly scrape sources that block RSS
    # ── 完整瀏覽器偽裝 Header，對抗 Akamai / Cloudflare ──
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36'
        ),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Ch-Ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
    }
    
    # 80.lv fallback (if RSS returned nothing)
    if '80.lv (TA/Tech)' not in scraped_data:
        try:
            r = requests.get('https://80.lv/articles/', headers=headers, timeout=10)
            soup = BeautifulSoup(r.text, 'html.parser')
            scraped_data += '\n### 來源資訊: 80.lv (TA/Tech)\n'
            count = 0
            for a in soup.select('a[href*="/articles/"]'):
                href = a['href']
                title_text = a.get_text(strip=True)
                if title_text and len(title_text) > 15 and not href.endswith('/articles/'):
                    if not href.startswith('http'):
                        href = 'https://80.lv' + href
                    if href in recent_urls: continue
                    scraped_data += f'- [標題]: {title_text}\n  [網址 URL]: {href}\n  [摘要前言]: (Latest TA article)...\n'
                    count += 1
                    recent_news_count += 1
                    if count >= 5:
                        break
            if count == 0:
                print('  [Warning] 80.lv direct scrape also returned nothing')
        except Exception as e:
            print(f'  [Error] 80.lv direct scrape failed: {e}')

    # Unity Blog fallback (if RSS returned nothing)
    if 'Unity Blog' not in scraped_data:
        try:
            r = requests.get('https://unity.com/blog', headers=headers, timeout=10)
            soup = BeautifulSoup(r.text, 'html.parser')
            scraped_data += '\n### 來源資訊: Unity Blog\n'
            count = 0
            for a in soup.select('a[href*="/blog/"]'):
                href = a['href']
                if not href.startswith('http'):
                    href = 'https://unity.com' + href
                title_text = a.get_text(strip=True)
                if title_text and len(title_text) > 15 and not href.endswith('/blog/') and not href.endswith('/blog') and href not in scraped_data:
                    if href in recent_urls: continue
                    scraped_data += f'- [標題]: {title_text}\n  [網址 URL]: {href}\n  [摘要前言]: (Unity blog article)...\n'
                    count += 1
                    recent_news_count += 1
                    if count >= 5:
                        break
            if count == 0:
                print('  [Warning] Unity Blog direct scrape also returned nothing')
        except Exception as e:
            print(f'  [Error] Unity Blog direct scrape failed: {e}')

    # 映CG (incgmedia) — 無 RSS，爬分類頁面最新文章 (3D/CG/VFX)
    try:
        scraped_data += '\n### 來源資訊: 映CG InCG Media (3D/CG/VFX)\n'
        count = 0
        seen_incg = set()
        incg_categories = [
            ('https://www.incgmedia.com/new-release', '/new-release/'),
            ('https://www.incgmedia.com/behind-the-scene', '/behind-the-scene/'),
        ]
        for page_url, slug_prefix in incg_categories:
            r = requests.get(page_url, headers=headers, timeout=10)
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.select('a[href]'):
                href = a.get('href', '')
                # 只取分類下的文章連結（含 slug 前綴且有子路徑）
                if not href.startswith(slug_prefix) or href.rstrip('/') == slug_prefix.rstrip('/'):
                    continue
                full_url = 'https://www.incgmedia.com' + href
                title_text = a.get_text(strip=True)
                if (title_text and len(title_text) > 15
                        and full_url not in seen_incg):
                    if full_url in recent_urls: continue
                    seen_incg.add(full_url)
                    scraped_data += f'- [標題]: {title_text}\n  [網址 URL]: {full_url}\n  [摘要前言]: (映CG 3D/CG/VFX article)...\n'
                    count += 1
                    recent_news_count += 1
                    if count >= 6:
                        break
            if count >= 6:
                break
        if count == 0:
            print('  [Warning] 映CG scrape returned nothing')
        else:
            print(f'  映CG: scraped {count} articles')
    except Exception as e:
        print(f'  [Error] 映CG scrape failed: {e}')

    print(f"Scraped {recent_news_count} news items for context.")
    return scraped_data

async def generate_daily_report():
    print(f"Generating report for: {today_str_display}")

    # --- 新增：讀取過去 3 天的全域新聞歷史 ---
    history_file = "global_history.json"
    global_history = []
    if os.path.exists(history_file):
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                global_history = json.load(f)
            print(f"Loaded global history ({len(global_history)} days)")
        except json.JSONDecodeError:
            print("History file exists but is invalid JSON. Starting fresh.")
            
    recent_urls = set()
    for daily_urls in global_history:
        recent_urls.update(daily_urls)
    # ------------------------------------
    
    # 第一階段：爬取真實精確的新聞清單 (並將最近 3 天網址過濾掉)
    scraped_context = fetch_rss_feeds(recent_urls)
    if not scraped_context.strip():
        scraped_context = "無法取得即時 RSS 新聞，請以過去 48 小時內廣為人知的開發新聞進行撰寫，但嚴格標示網址。"
        print("Warning: Scraped context is empty.")
    # --- ✅ 修正：在這裡定義圖片標籤變數 (位置必須在 prompt 之前) ---
    headline_img_tag = f"![頭條圖片](../assets/headline_{today_str_file}.png)"
    engine_img_tag = f"![引擎相關](../assets/engine_{today_str_file}.png)"
    ta_img_tag = f"![TA技術](../assets/ta_{today_str_file}.png)"
    indie_img_tag = f"![獨立遊戲](../assets/indie_{today_str_file}.png)"
    local_img_tag = f"![在地社群](../assets/local_{today_str_file}.png)"
    producer_img_tag = f"![製作人週記](../assets/producer_{today_str_file}.png)"
    # --------------------------------------------------------
    # 第二階段：限縮 AI 發揮空間 (Strict Prompting)
    prompt = f"""
    你是專業的遊戲開發與業界分析師、技術美術分析師。
    【絕對強制指令：爬蟲優先 (Scrape-First)】
    我已經為你爬取了最新的真實遊戲業界新聞清單，請你「僅基於以下提供的新聞清單」進行篩選與總結。
    
    以下是為你準備的真實新聞清單 (Context)：
    ======================================
    {scraped_context}
    ======================================

    請幫我撰寫一份 Discord 格式的「 Ultimate Daily Full Report」。所有的總結與說明內容請務必使用「繁體中文」撰寫。
    【重要：結構強制規範】
    1. 報告的第一行必須是最大字體的 H1 標題（使用 # 號），格式如下，且標題下方「不需要」有 `---` 水平線：
       # 📅 Ultimate Daily Full Report — {today_str_display}
    2. 接下來，你必須嚴格產生以下 7 個段落，每個段落的開頭使用「**🔥 標題**」加粗格式（絕對不要在標題正下方加上 `---` 分隔線）：
    
    **📢 【今日頭條】**
    {headline_img_tag}
    (🚨頭條規則🚨：頭條只能是「一篇」最具影響力的新聞，用 2~3 句話深入摘要其核心重點。摘要結束後的「下一行」，必須另起一個子項目放置來源連結，例如：`- 🔗 來源: [標題](<網址>)`。)
    ---
    **⚙️ 【引擎相關】**
    {engine_img_tag}
    (🚨防捏造與強制列舉機制🚨：請從 Context 篩選引擎與 3D 模型相關新聞，並將它們強制分類到以下三個小標題中（若某分類無新聞，則跳過該小標題）：
    - **Unreal Engine**
    - **Unity**
    - **3D 模型技術**：僅限純 3D 建模、雕刻、拓撲相關（Maya, ZBrush, 3ds Max, Blender 的建模類文章）。
    ⚠️ 不需要收錄 Godot Engine 的相關新聞，請直接忽略。
    各引擎維持原本的標題前綴即可。系統若需辨識來源，若屬「3D 模型技術」請在其來源連結加上 `[3D - ]` 前綴。)
    ---
    **✨ 【TA 相關】**
    {ta_img_tag}
    (🚨防捏造與強制列舉機制🚨：請從 Context 篩選 TA 領域新聞，並「統一以 bullet point 列出」。包含：Shader、渲染技術、光照、材質製作、場景搭建與展示、Houdini 特效、動畫/綁定、效能優化、引擎底層開發、VFX 視覺特效，以及 80.lv / 映CG 上的技術美術類文章。
    為了方便系統辨識，來源連結命名請務必加上 `[TA - ]` 前綴，例如 `[TA - 映CG - 標題]`。)
    ---
    **🎮 【獨立遊戲市場觀察】**
    {indie_img_tag}
    (🚨防捏造與強制機制🚨：請務必關注並包含「Steam News」的相關情報。如果有重要的 Steam 更新或發布，請在此段落進行深入摘要。若無則總結其他獨立遊戲情報。)
    ---
    **🤝 【在地社群】**
    {local_img_tag}
    (內容)
    ---
    **💼 【製作人週記建議】**
    {producer_img_tag}
    (內容)
    ---
    **🌌 【今日全方位深度總結】**
    (🚨全面總結與技術解析🚨：請依據上述「每一個標題」（頭條、TA、獨立遊戲、在地、製作人）的內容，分別用一句話進行精準總結。其中針對技術與引擎相關內容，請確保帶有 TA 專屬的深度視角 (如 Rendering, Pipeline 等)。請善用換行排版，不要把它寫成一整段擁擠的文字組合。)
    ---

    【文章豐富度與閱讀體驗規範】
    1. 📰 **豐富度指標**：請萃取所有重要情報。**【⚠️ 合併去重規則】**：針對同一款遊戲或軟體的連續「例行更新、微調修復」（例如同系列多篇 Steam 新聞），請您務必將它們「合併總結為單一條新聞」，並**「只需挑選其中一條網址作為來源示意即可，絕對不要堆疊多個重複或高度相似的網址」**。
    2. 📝 **排版與易讀性**：請「強烈依賴」Markdown 的條列式 (bullet points `-`) 以及「段落換行」來區隔不同的新聞事件與廠商。嚴禁將多筆毫不相干的事件塞進同一個巨大而擁擠的文字方塊中。請讓內文看起來有「呼吸空間」。
    3. 只有在每個大段落（例如【今日頭條】的整塊內容）的「最結尾」才放置一個 `---` 水平分隔線來區隔下一個大分類標題。
    
    🚨🚨【強制內聯來源格式 — 最關鍵規範】🚨🚨
    每一則獨立的新聞「必須」是一個獨立的 bullet point（以 `- ` 開頭），1~2 句摘要。
    為了確保在 Discord 能正確換行顯示，每條新聞摘要的「正下方」，必須使用一個「子項目 (sub-bullet)」來放置來源連結，格式嚴格如下：
    ```
    - **Unreal Engine**：
      - 第一篇文章的摘要內容。
        - 🔗 來源: [Unreal - 第一篇標題簡稱](<https://原始網址>)
      - 第二篇文章的摘要內容。
        - 🔗 來源: [Unreal - 第二篇標題簡稱](<https://原始網址>)
    - **Unity**：
      - 第一篇文章的摘要內容。
        - 🔗 來源: [Unity - 標題簡稱](<https://原始網址>)
    ```
    🚨 絕對不要把來源連結集中放到段落底部！每條新聞摘要下方必須「立刻」另起一行子項目放它的來源連結。
    🚨 不需要寫整體 `[資料來源]` 標題行，每則新聞皆獨立附上自己的子項目連結即可。
    🚨 每條獨立的 bullet 必須有一條對應的來源連結；若為多篇合併的新聞，保留最具代表性的一條連結即可。
    
    【防捏造警告 (Anti-Hallucination) 與 Discord 超連結優化】
    1. 由於 Discord 原生超連結會產生冗長的預覽縮圖卡片，請「務必」使用 Markdown 的角括號 `< >` 將 URL 包起來，格式如下：
       `[網站名 - 新聞關鍵字/標題簡稱](<原始HTTPS網址>)`
       範例： `[Unreal - PCG 更新](<https://www...>)`, `[3D - 80.lv - 植被渲染](<https://www...>)`
    2. 這些網址「絕對只能」從我上面提供給你的 Context 清單中挑選！嚴禁自行發明、捏造任何不存在的網址。
    3. 如果針對某個標題（例如【在地社群】）在清單中完全找不到相關素材，你可以簡短說明「今日無重大本土社群動態」，但絕對不准無中生有生出假網址。
    4. 最後的【今日全方位深度總結】請不要附上任何資料來源。
    
    【圖文對位規範 (防呆排版)】
    在此 Markdown 輸出中，請確保每一段「**標題**」正下方「必定要」跟一條 Markdown 圖片標籤，然後才開始寫內文。
    範例：
    **📢 【今日頭條】**
    ![頭條圖片](../assets/headline_{today_str_file}.png)
    (內文從這裡開始...)
    

    【關鍵輸出要求】
    請嚴格使用以下 JSON 格式回傳（直接回傳 JSON 物件字串，絕對不要加 ```json 區塊標記，確保能被 Python json.loads 直接解析）：

    {{
      "markdown_content": "這是一篇完整的 Markdown 報告字串內容，遵照上述大標題日期、6 個標題結構、圖文對位，與真實 [網站名 - 標題簡稱](<URL>) 網址。",
      "image_targets": [
        {{
          "section_name": "Headline",
          "source_urls": ["從 Context 挑選出的頭條相關網址1", "從 Context 挑選出的頭條相關網址2"],
          "image_filename": "headline_{today_str_file}.png",
          "image_keywords": ["official", "hero", "cover", "headline", "promotion"]
        }},
        {{
          "section_name": "Engine",
          "source_urls": ["(請優先挑選有包含編輯器截圖的引擎新聞網址)", "備用網址2"],
          "image_filename": "engine_{today_str_file}.png",
          "image_keywords": ["engine", "editor", "unreal", "unity", "viewport"]
        }},
        {{
          "section_name": "TA",
          "source_urls": ["(請優先挑選有豐富遊戲或開發截圖的網址排在第一位)", "備用網址2", "備用網址3"],
          "image_filename": "ta_{today_str_file}.png",
          "image_keywords": ["screenshot", "preview", "featured", "spotlight", "render", "engine", "technology", "shader", "scene"]
        }},
        {{
          "section_name": "Indie",
          "source_urls": ["從 Context 挑選出的獨立遊戲網址1", "從 Context 挑選出的獨立遊戲網址2"],
          "image_filename": "indie_{today_str_file}.png",
          "image_keywords": ["gameplay", "chart", "capsule", "steam", "indie"]
        }},
        {{
          "section_name": "Local",
          "source_urls": ["從 Context 挑選出的在地新聞網址1", "從 Context 挑選出的在地新聞網址2"],
          "image_filename": "local_{today_str_file}.png",
          "image_keywords": ["event", "meetup", "poster", "community"]
        }},
        {{
          "section_name": "Producer",
          "source_urls": ["(請優先挑選有遊戲宣傳圖的網址，避免純公司 Logo 新聞)", "備用網址"],
          "image_filename": "producer_{today_str_file}.png",
          "image_keywords": ["marketing", "development", "producer", "idea"]
        }}
      ]
    }}
    【致命關鍵：JSON 結構完整性警告】
    ⚠️⚠️⚠️ 警告：這是一個自動化處理程序的 API 呼叫。你回傳的 JSON **絕對不可以**只輸出 "markdown_content" 就結束！
    你必須完整輸出下方的 "image_targets" 陣列配置，因為這是後續 Python 腳本去爬取圖片的唯一依據！如果你省略了 "image_targets"，整個報告發送就會因為缺少圖片而徹底失敗！請確保你的 JSON 結尾包含完整的 "image_targets"。
    """

    print("Requesting strictly constrained content from Gemini...")
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        )
    )
    res_text = response.text.strip()
    
    print("--- RAW GEMINI RESPONSE PREVIEW ---")
    print(res_text[:500] + "\n...\n" + res_text[-500:])
    print("-----------------------------------")
    
    # 移除可能的 Markdown 標記
    # 將可能包在 ```json 和 ``` 裡面的內容萃取出來
    json_match = re.search(r'```(?:json)?(.*?)```', res_text, re.DOTALL)
    if json_match:
        res_text = json_match.group(1).strip()
    else:
        res_text = res_text.strip()

    # 第三階段：輸出正確的格式檔 (Markdown & JSON Generation)
    try:
        report_data = json.loads(res_text, strict=False)

        # 防呆機制：檢查 JSON 裡的 source_urls 是否合法
        valid_targets = []
        for target in report_data.get("image_targets", []):
            urls = target.get("source_urls", [])
            # Fallback for old schema
            if "source_url" in target and not urls:
                urls = [target["source_url"]]
                
            formatted_urls = []
            for url in urls:
                if url and (url.startswith("http") or url == "GENERATE_AI_IMAGE"):
                    formatted_urls.append(url)
                    
            if formatted_urls:
                target["source_urls"] = formatted_urls
                valid_targets.append(target)
            else:
                print(f"Warning: Discarding invalid URLs for section {target.get('section_name')}")

        report_data["image_targets"] = valid_targets

        # 1. 輸出 Markdown
        os.makedirs("Daily_Report", exist_ok=True)
        md_filename = os.path.join("Daily_Report", f"Daily_Full_Report_{today_str_file}.md")
        with open(md_filename, "w", encoding="utf-8") as f:
            f.write(report_data.get("markdown_content", "No markdown content returned."))
        print(f"Generated Markdown report: {md_filename}")

        # 2. 輸出 daily_targets.json
        targets_filename = "daily_targets.json"
        
        # 追加 AI 生成圖的 Target
        image_targets = report_data.get("image_targets", [])
        
        # 尋找 AI Prompt 是否在 JSON 根目錄被拋出 (給 Synthesis)，如果 Gemini 把他放在外層，或者我們從 markdown 萃取
        # 為了確保 prompt 結構正確，這裡直接假定 Gemini 會照著把 Synthesis 放到 JSON 內，
        # 如果沒有，我們就還是給一個動態生成的提示詞 (從回應裡抓，但為了穩定性，請確認 prompt 格式有要求出 Synthesis)
        # 上方的 JSON 範例已更新，要求 Gemini 吐出 Synthesis 資料
        
        synthesis_found = False
        for tgt in image_targets:
            if tgt.get("section_name") == "Synthesis":
                synthesis_found = True
                break
                
        with open(targets_filename, "w", encoding="utf-8") as f:
            json.dump(image_targets, f, ensure_ascii=False, indent=2)
        print(f"Generated targets file: {targets_filename} with {len(image_targets)} valid targets.")

        # --- 新增：更新 3 天滾動全域新聞紀錄 ---
        today_urls = []
        # 從 Markdown 內容取出所有連結以確保是最完整的清單
        for m in re.finditer(r"\((https?://[^)]+)\)|<(https?://[^>]+)>", report_data.get("markdown_content", "")):
            url = m.group(1) or m.group(2)
            if url and url != "GENERATE_AI_IMAGE" and url not in today_urls:
                today_urls.append(url)
                
        if today_urls:
            global_history.append(today_urls)
            # 強制切片，只保留陣列最後 3 筆資料（最近 3 天）
            global_history = global_history[-3:]
            with open(history_file, "w", encoding="utf-8") as f:
                json.dump(global_history, f, ensure_ascii=False, indent=2)
            print(f"Saved {len(today_urls)} today's urls to history. Current history days: {len(global_history)}")
        # ------------------------------------
    except json.JSONDecodeError as e:
        print(f"Error parsing Gemini response as JSON: {e}")
        print("Raw response saving fallback...")
        
        # Fallback Markdown
        os.makedirs("Daily_Report", exist_ok=True)
        md_filename = os.path.join("Daily_Report", f"Daily_Full_Report_{today_str_file}.md")
        with open(md_filename, "w", encoding="utf-8") as f:
            f.write(f"# Ultimate Daily Full Report ({today_str_display})\n\n")
            f.write("Error generating formatted JSON. Raw output from Gemini:\n\n")
            f.write(response.text)
        print(f"Fallback Markdown saved to: {md_filename}")

if __name__ == "__main__":
    asyncio.run(generate_daily_report())
