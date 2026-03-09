# -*- coding: utf-8 -*-
import os
import json
import asyncio
from datetime import datetime, timezone, timedelta
import google.generativeai as genai
from dotenv import load_dotenv

# 讀取本地端 .env
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("Error: GEMINI_API_KEY is not set.")
    exit(1)

genai.configure(api_key=GEMINI_API_KEY)

# 台灣時間 (UTC+8)
tz = timezone(timedelta(hours=8))
today_date = datetime.now(tz)
today_str_display = today_date.strftime("%Y-%m-%d")
today_str_file = today_date.strftime("%Y%m%d")

async def generate_daily_report():
    print(f"Generating report for: {today_str_display}")

    # 使用 Gemini 模型
    model = genai.GenerativeModel('gemini-2.5-flash')

    prompt = f"""
    你是專業的遊戲開發與業界分析師、技術美術分析師。
    請搜尋過去 24 到 48 小時內（特別是 {today_str_display} 附近），關於全球遊戲業界、遊戲引擎更新 (Unreal Engine 5, Unity, Godot)、TA (Technical Art) 技術新聞、以及 Steam 暢銷或獨立遊戲消息。

    請幫我撰寫一份 Markdown 格式的「Ultimate Daily Full Report」，內容請至少包含以下段落：
    1. 今日頭條 (Headline)
    2. 引擎動態 (Unreal Engine, Unity, Godot 等)
    3. 3A 大作與遊戲業界動態 (AAA & Industry)
    4. Steam 市場與獨立遊戲觀察 (Steam & Indie)
    5. TA 技術分析 (Shader, Rendering, PCG 等)
    6. 當地社群新聞 (台灣在地新聞或開發者社群動態)

    每個段落請詳細總結資訊，使用適當的排版與條列式描述。
    
    【關鍵輸出要求】
    為了讓視覺化抓圖腳本能夠與這份報告搭配，除了 Markdown 內容之外，你必須規劃對應的圖片內容。
    請嚴格使用以下 JSON 格式回傳（注意不要加上多餘的程式碼區塊標記，如 ```json，直接回傳 JSON 物件字串即可）：

    {{
      "markdown_content": "這是一篇完整的 Markdown 報告字串內容，段落標題請用 ## 。請在每個段落適當的地方加入圖片標籤，例如： ![頭條圖片](../assets/headline_{today_str_file}.png) 。所有的圖片檔名必須對應下方的 image_filename。",
      "image_targets": [
        {{
          "section_name": "Headline",
          "source_url": "該段落對應的真實新聞或技術網站圖片網址，若無明確網址可以用該網站的來源網址",
          "image_filename": "headline_{today_str_file}.png"
        }},
        {{
          "section_name": "Engine",
          "source_url": "引擎新聞對應網址",
          "image_filename": "engine_{today_str_file}.png"
        }},
        {{
          "section_name": "AAA",
          "source_url": "3A大作對應網址",
          "image_filename": "aaa_{today_str_file}.png"
        }},
        {{
          "section_name": "Steam",
          "source_url": "Steam遊戲網址",
          "image_filename": "steam_{today_str_file}.png"
        }},
        {{
          "section_name": "TA",
          "source_url": "TA技術網站網址",
          "image_filename": "ta_{today_str_file}.png"
        }},
        {{
          "section_name": "Local",
          "source_url": "台灣社群網站網址",
          "image_filename": "local_{today_str_file}.png"
        }}
      ]
    }}
    """

    print("Requesting content from Gemini...")
    response = model.generate_content(prompt)
    res_text = response.text.strip()
    
    # 移除可能的 Markdown 標記
    if res_text.startswith("```json"):
        res_text = res_text[7:]
    elif res_text.startswith("```"):
        res_text = res_text[3:]
    if res_text.endswith("```"):
        res_text = res_text[:-3]
    res_text = res_text.strip()

    try:
        report_data = json.loads(res_text)

        # 1. 輸出 Markdown
        md_filename = f"Daily_Full_Report_{today_str_file}.md"
        with open(md_filename, "w", encoding="utf-8") as f:
            f.write(report_data["markdown_content"])
        print(f"Generated Markdown report: {md_filename}")

        # 2. 輸出 daily_targets.json
        targets_filename = "daily_targets.json"
        
        # 追加 AI 生成圖的 Target
        image_targets = report_data.get("image_targets", [])
        image_targets.append({
            "section_name": "Synthesis",
            "source_url": "GENERATE_AI_IMAGE",
            "image_filename": f"synthesis_ai_{today_str_file}.png",
            "ai_prompt": "16:9 cinematic tech-art cyberpunk concept art representing the synthesis of today's game dev news. Modern engine rendering style, hyper-detailed, global illumination, unreal engine 5 style."
        })
        
        with open(targets_filename, "w", encoding="utf-8") as f:
            json.dump(image_targets, f, ensure_ascii=False, indent=2)
        print(f"Generated targets file: {targets_filename}")

    except json.JSONDecodeError as e:
        print(f"Error parsing Gemini response as JSON: {e}")
        print("Raw response:")
        print(response.text)
        
        # Fallback Markdown
        md_filename = f"Daily_Full_Report_{today_str_file}.md"
        with open(md_filename, "w", encoding="utf-8") as f:
            f.write(f"# Ultimate Daily Full Report ({today_str_display})\n\n")
            f.write("Error generating formatted JSON. Raw output from Gemini:\n\n")
            f.write(response.text)
        print(f"Fallback Markdown saved to: {md_filename}")

if __name__ == "__main__":
    asyncio.run(generate_daily_report())
