import os
import glob
import re
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# 讀取本地端 .env (僅供本地測試用)
load_dotenv()

def post_to_facebook():
    # --- ⚠️ 系統提示：根據您的需求，每日 Facebook 發文功能已「暫時關閉」 ⚠️ ---
    print("🔇 Facebook 發文功能目前設定為【暫時關閉狀態】，如需重新啟用請移除此段攔截程式碼。")
    return

    # 1. 從環境變數取得金鑰 [cite: 1]
    page_access_token = os.getenv("FB_PAGE_ACCESS_TOKEN")
    page_id = os.getenv("FB_PAGE_ID")
    
    if not page_access_token or not page_id:
        print("❌ 錯誤：找不到 FB_PAGE_ACCESS_TOKEN 或 FB_PAGE_ID。")
        return

    # 2. 取得今日日期字串
    tz = timezone(timedelta(hours=8))
    today_str_file = datetime.now(tz).strftime("%Y%m%d")
    
    # 3. 讀取今日的 Markdown 報告
    md_filename = os.path.join("Daily_Report", f"Daily_Full_Report_{today_str_file}.md")
    if not os.path.exists(md_filename):
        print(f"❌ 找不到報告文件: {md_filename}")
        return

    with open(md_filename, "r", encoding="utf-8") as f:
        full_text = f.read()

    # 4. 擷取「今日頭條」段落內容 [cite: 1]
    try:
        headline_section = re.search(r'\*\*📢 【今日頭條】\*\*(.*?)(?=\*\*🎨)', full_text, re.DOTALL)
        if not headline_section:
            print("⚠️ 無法解析今日頭條段落。")
            return
            
        content = headline_section.group(1).strip()
        content_clean = re.sub(r'!\[.*?\]\(.*?\)', '', content).strip()
        content_clean = content_clean.replace('<', '').replace('>', '')
        
        # === 核心修改：重新組裝臉書專用貼文格式 ===
        post_message = (
            f"【遊戲產業資訊日報-今日頭條】\n\n"
            f"{content_clean}\n\n"
            f"💡 趕快加入 Discord 群組，獲得更完整的產業日報資訊吧！\n"
            f"🔗 https://discord.gg/AAErjJ4yUc\n\n"
            f"#AI自動化日報 #遊戲開發 #GameDev"
        )
    except Exception as e:
        print(f"❌ 內容解析失敗: {e}")
        return

    # 5. 圖片路徑定位
    image_path = os.path.join("assets", f"headline_{today_str_file}.png")
    
    # 6. 發送貼文 [cite: 15]
    url = f"https://graph.facebook.com/v19.0/{page_id}/photos"
    payload = {'caption': post_message, 'access_token': page_access_token}
    
    if os.path.exists(image_path):
        with open(image_path, 'rb') as img_file:
            response = requests.post(url, data=payload, files={'source': img_file})
    else:
        response = requests.post(f"https://graph.facebook.com/v19.0/{page_id}/feed", 
                                 data={'message': post_message, 'access_token': page_access_token})

    if response.ok:
        print(f"✅ 臉書貼文成功！")
    else:
        print(f"❌ 臉書貼文失敗: {response.text}")

if __name__ == "__main__":
    post_to_facebook()