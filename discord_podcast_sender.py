# -*- coding: utf-8 -*-
import os
import glob
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

PODCAST_WEBHOOK_URL = os.getenv("PODCAST_WEBHOOK_URL")

def send_podcast_to_discord():
    if not PODCAST_WEBHOOK_URL:
        print("Error: PODCAST_WEBHOOK_URL is not set. Skipping Podcast push.")
        return

    # Find the latest generated podcast mp3 in Podcast/ directory
    mp3_files = glob.glob(os.path.join("Podcast", "daily_podcast_*.mp3"))
    if not mp3_files:
        print("No podcast mp3 found to send.")
        return
        
    mp3_files.sort(key=os.path.getmtime, reverse=True)
    latest_mp3 = mp3_files[0]
    
    tz = timezone(timedelta(hours=8))
    today_str = datetime.now(tz).strftime("%Y年%m月%d日")
    
    print(f"Sending podcast: {latest_mp3} to Discord...")

    payload = {
        "content": f"🎙️ **【{today_str}】每日遊戲資訊日報 Podcast** 已經準備好囉！\n由 AI 主播姍姍與 zn 帶來的 5 分鐘深度解析，點擊下方語音立刻收聽！",
        "username": "日報播報員姍姍",
    }

    try:
        with open(latest_mp3, "rb") as f:
            files = {
                "file": (os.path.basename(latest_mp3), f, "audio/mpeg")
            }
            response = requests.post(PODCAST_WEBHOOK_URL, data=payload, files=files)
            if response.status_code in [200, 204]:
                print("✅ Successfully sent podcast to Discord!")
            else:
                print(f"❌ Failed to send podcast. Status code: {response.status_code}")
                print(response.text)
    except Exception as e:
        print(f"❌ Exception sending podcast: {e}")

if __name__ == "__main__":
    send_podcast_to_discord()
