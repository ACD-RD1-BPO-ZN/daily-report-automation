import os
import glob
import re
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# 讀取本地端 .env (僅供本地測試用)
load_dotenv()

def post_to_facebook():
    # 1. 從環境變數取得我們之前設好的金鑰
    page_access_token = os.getenv("FB_PAGE_ACCESS_TOKEN")
    page_id = os.getenv("FB_PAGE_ID")
    
    if not page_access_token or not page_id:
        print("❌ 錯誤：找不到 FB_PAGE_ACCESS_TOKEN 或 FB_PAGE_ID。跳過臉書發文。")
        return

    # 2. 取得今日日期字串 (配合你 generate_report.py 的命名邏輯)
    tz = timezone(timedelta(hours=8))
    today_str_file = datetime.now(tz).strftime("%Y%m%d")
    
    # 3. 讀取今日的 Markdown 報告
    md_filename = f"Daily_Full_Report_{today_str_file}.md"
    if not os.path.exists(md_filename):
        print(f"❌ 找不到今日報告文件: {md_filename}")
        return

    with open(md_filename, "r", encoding="utf-8") as f:
        full_text = f.read()

    # 4. 精準擷取「今日頭條」段落的文字 (排除標籤與圖片語法)
    try:
        # 尋找今日頭條與下一個標題 (TA相關) 之間的內容
        headline_section = re.search(r'\*\*📢 【今日頭條】\*\*(.*?)(?=\*\*🎨)', full_text, re.DOTALL)
        if not headline_section:
            print("⚠️ 無法解析今日頭條段落。")
            return
            
        content = headline_section.group(1).strip()
        # 移除 Markdown 圖片標籤 ![...](...)
        content_clean = re.sub(r'!\[.*?\]\(.*?\)', '', content).strip()
        # 移除 Discord 專用的角括號連結格式 < >
        content_clean = content_clean.replace('<', '').replace('>', '')
        
        # 加上粉專專屬的標語或 HashTag
        post_message = f"【今日頭條】\n\n{content_clean}\n\n#AI自動化日報 #遊戲開發 #GameDev"
    except Exception as e:
        print(f"❌ 內容解析失敗: {e}")
        return

    # 5. 定位今日頭條的圖片路徑
    image_path = os.path.join("assets", f"headline_{today_str_file}.png")
    if not os.path.exists(image_path):
        print(f"⚠️ 找不到頭條圖片: {image_path}，嘗試以純文字發文。")
        image_path = None

    # 6. 呼叫 Meta Graph API 發文 (圖片 + 文字)
    if image_path:
        url = f"https://graph.facebook.com/v19.0/{page_id}/photos"
        payload = {
            'caption': post_message,
            'access_token': page_access_token
        }
        with open(image_path, 'rb') as img_file:
            files = {'source': img_file}
            response = requests.post(url, data=payload, files=files)
    else:
        # 純文字發文備案
        url = f"https://graph.facebook.com/v19.0/{page_id}/feed"
        payload = {
            'message': post_message,
            'access_token': page_access_token
        }
        response = requests.post(url, data=payload)

    # 7. 結果反饋
    if response.ok:
        print(f"✅ 臉書貼文成功！Response: {response.json()}")
    else:
        print(f"❌ 臉書貼文失敗: {response.text}")

if __name__ == "__main__":
    post_to_facebook()