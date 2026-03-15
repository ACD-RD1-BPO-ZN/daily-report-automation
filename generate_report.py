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

def fetch_rss_feeds():
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
                title = entry.get('title', 'No Title')
                link = entry.get('link', '')
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
                    scraped_data += f'- [標題]: {title_text}\n  [網址 URL]: {href}\n  [摘要前言]: (Unity blog article)...\n'
                    count += 1
                    recent_news_count += 1
                    if count >= 5:
                        break
            if count == 0:
                print('  [Warning] Unity Blog direct scrape also returned nothing')
        except Exception as e:
            print(f'  [Error] Unity Blog direct scrape failed: {e}')

    print(f"Scraped {recent_news_count} news items for context.")
    return scraped_data

    async def generate_daily_report():
    print(f"Generating report for: {today_str_display}")

    # ─── 新增：1. 讀取過去的頭條紀錄 ───
    headline_history_file = "headline_history.json"
    headline_history = []
    if os.path.exists(headline_history_file):
        try:
            with open(headline_history_file, "r", encoding="utf-8") as f:
                headline_history = json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load headline history: {e}")
            
    # 將歷史網址組合成字串，準備塞入 Prompt
    history_str = "\n".join([f"- {url}" for url in headline_history]) if headline_history else "無"
    # ────────────────────────────────────

    # 第一階段：爬取真實精確的新聞清單
    scraped_context = fetch_rss_feeds()
    if not scraped_context.strip():
        scraped_context = "無法取得即時 RSS 新聞，請以過去 48 小時內廣為人知的開發新聞進行撰寫，但嚴格標示網址。"
        print("Warning: Scraped context is empty.")

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
    2. 接下來，你必須嚴格產生以下 6 個段落，每個段落的開頭使用「**🔥 標題**」加粗格式（絕對不要在標題正下方加上 `---` 分隔線）：
    
    **📢 【今日頭條】**
    (🚨防重複強制指令🚨：過去幾天已經擔任過頭條的網址如下：
    {history_str}
    請你「絕對不要」再次選擇上述網址作為今天的【今日頭條】！請從 Context 中挑選另一則最重要的新聞。如果你認為上述網址依然很重要，你可以將它們歸類到下方的 TA 或獨立遊戲段落中。)
    (內容)
    [資料來源]
    ---
    **🎨 【Technical Art 相關】**
    (🚨防捏造與強制列舉機制🚨：請從 Context 中篩選 Unreal Engine, Unity, Godot 等引擎相關重點。如果沒有最新消息，請使用近期熱門內容總結。你必須盡可能確保這「三個引擎」的小標題都會出現。請善用條列式 (`-`) 或換行來排版多篇獨立的新聞，不要把不同文章的內容黏在一起。對於每篇文章，請摘要 1~2 句話的核心技術重點，保持版面透氣且易讀。)
    [資料來源]
    ---
    **🎮 【獨立遊戲市場觀察】**
    (🚨防捏造與強制機制🚨：請務必關注並包含「Steam News」的相關情報。如果有重要的 Steam 更新或發布，請在此段落進行深入摘要。若無則總結其他獨立遊戲情報。)
    (內容)
    [資料來源]
    ---
    **🤝 【在地社群】**
    (內容)
    [資料來源]
    ---
    **💼 【製作人週記建議】**
    (內容)
    [資料來源]
    ---
    **🌌 【今日全方位深度總結】**
    (🚨全面總結與技術解析🚨：請依據上述「每一個標題」（頭條、TA、獨立遊戲、在地、製作人）的內容，分別用一句話進行精準總結。其中針對技術與引擎相關內容，請確保帶有 TA 專屬的深度視角 (如 Rendering, Pipeline 等)。請善用換行排版，不要把它寫成一整段擁擠的文字組合。)
    ---

    【文章豐富度與閱讀體驗規範】
    1. 📰 **豐富度指標**：絕對「不要」因為過度追求精簡而隨意拋棄 Context 提供給你的新聞網址。你應該將「每一篇」文章的核心菁華萃取成 1 到 2 句的高濃度摘要，確保所有重要情報都被收錄。
    2. 📝 **排版與易讀性**：請「強烈依賴」Markdown 的條列式 (bullet points `-`) 以及「段落換行」來區隔不同的新聞事件與廠商。嚴禁將多筆毫不相干的事件塞進同一個巨大而擁擠的文字方塊中。請讓內文看起來有「呼吸空間」。
    3. 只有在每個大段落（例如【今日頭條】的整塊內容）的「最結尾」才放置一個 `---` 水平分隔線來區隔下一個大分類標題。
    
    【防捏造警告 (Anti-Hallucination) 與 Discord 超連結優化】
    1. 【嚴格排版規定】：所有的資料來源超連結，必須集中且「統一條列放置於該大標題段落的最底部（[資料來源] 的正下方）」，絕對不可以穿插在每一條新聞摘要的正後方或文字段落中間，以免畫面凌亂影響閱讀體驗。
    2. 由於 Discord 原生超連結會產生冗長的預覽縮圖卡片，請「務必」使用 Markdown 的角括號 `< >` 將 URL 包起來，格式如下：
       `[網站名 - 新聞關鍵字/標題簡稱](<原始HTTPS網址>)`
       範例： `[Unreal - PCG 更新](<https://www...>)`, `[80.lv - 植被渲染](<https://www...>)`
    3. 這些網址「絕對只能」從我上面提供給你的 Context 清單中挑選！嚴禁自行發明、捏造任何不存在的網址。
    4. 如果針對某個標題（例如【在地社群】）在清單中完全找不到相關素材，你可以簡短說明「今日無重大本土社群動態」，但絕對不准無中生有生出假網址。
    5. 最後的【今日全方位深度總結】請不要附上任何資料來源。
    
    【圖文對位規範 (防呆排版)】
    在此 Markdown 輸出中，請確保每一段「**標題**」正下方「必定要」跟一條 Markdown 圖片標籤，然後才開始寫內文。
    範例：
    **📢 【今日頭條】**
    ![頭條圖片](../assets/headline_{today_str_file}.png)
    (內文從這裡開始...)
    
    【特別提醒】
    對於最後一個段落「**🌌 【今日全方位深度總結】**」，請在標題下方固定放置以下圖片標籤：
    **🌌 【今日全方位深度總結】**
    ![深度總結](../assets/synthesis_ai_{today_str_file}.png)
    (內文...)
    ---

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
        }},
        {{
          "section_name": "Synthesis",
          "source_urls": ["GENERATE_AI_IMAGE"],
          "image_filename": "synthesis_ai_{today_str_file}.png",
          "ai_prompt": 身為專業的遊戲概念美術指導，請閱讀「今日全方位深度總結」，挑選最具代表性的一個主題（例如某款大作發售、或某項跨時代光影技術）。將這個主題轉化為「外行人一看就懂」且「充滿獨特藝術風格」的遊戲情境插畫。\n\n例如：\n- 若總結提到『Substrate 材質與光追技術』，請描述：『陽光穿透茂密的奇幻森林，光影極度真實地折射在古代騎士的精緻鎧甲上，展現極致的材質細節』。\n- 若總結提到『牌組建構遊戲爆紅』，請描述：『一張散發著神秘魔法光芒的傳奇卡牌，懸浮在充滿氛圍的幽暗酒館木桌上』。\n- 若總結提到『獨立遊戲開發』，請描述：『一個溫馨且充滿魔法道具的微型工坊，散發著匠人精神的氛圍』。\n\n【強制風格標籤】：'Professional game concept art, highly stylized and expressive, rich vibrant colors, cinematic lighting, engaging storytelling, visually striking, masterpiece, trending on ArtStation'.\n【🚫嚴格禁止】：絕對不要出現任何軟體介面(UI)、節點圖(Node graph)、藍圖、電腦螢幕、程式碼、文字或人類真實臉孔。畫面必須是一張純粹且引人入勝的遊戲世界插畫。"
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

        # ─── 新增：3. 抓出今天的頭條網址並存檔 ───
        current_headline_urls = []
        for target in report_data.get("image_targets", []):
            if target.get("section_name") == "Headline":
                current_headline_urls = target.get("source_urls", [])
                break
        
        if current_headline_urls:
            # 將新的網址加入歷史紀錄中，並移除重複項
            updated_history = headline_history + current_headline_urls
            updated_history = list(dict.fromkeys(updated_history))
            # 只保留最近 10 筆頭條紀錄（約 5~10 天份），避免無限膨脹
            updated_history = updated_history[-10:]
            
            with open(headline_history_file, "w", encoding="utf-8") as f:
                json.dump(updated_history, f, ensure_ascii=False, indent=2)
            print(f"Saved {len(current_headline_urls)} URLs to headline history.")
        # ──────────────────────────────────────────


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
        md_filename = f"Daily_Full_Report_{today_str_file}.md"
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
                
        if not synthesis_found:
            image_targets.append({
                "section_name": "Synthesis",
                "source_urls": ["GENERATE_AI_IMAGE"],
                "image_filename": f"synthesis_ai_{today_str_file}.png",
                "ai_prompt": "Professional game concept art, an epic and highly stylized fantasy landscape with cinematic lighting and rich colors, visually striking storytelling illustration, masterpiece, no UI, no nodes, no text."
            })
        
        with open(targets_filename, "w", encoding="utf-8") as f:
            json.dump(image_targets, f, ensure_ascii=False, indent=2)
        print(f"Generated targets file: {targets_filename} with {len(image_targets)} valid targets.")

    except json.JSONDecodeError as e:
        print(f"Error parsing Gemini response as JSON: {e}")
        print("Raw response saving fallback...")
        
        # Fallback Markdown
        md_filename = f"Daily_Full_Report_{today_str_file}.md"
        with open(md_filename, "w", encoding="utf-8") as f:
            f.write(f"# Ultimate Daily Full Report ({today_str_display})\n\n")
            f.write("Error generating formatted JSON. Raw output from Gemini:\n\n")
            f.write(response.text)
        print(f"Fallback Markdown saved to: {md_filename}")

if __name__ == "__main__":
    asyncio.run(generate_daily_report())
