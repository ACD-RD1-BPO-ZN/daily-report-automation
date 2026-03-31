# -*- coding: utf-8 -*-
import os
import glob
import json
import asyncio
import io
import re
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import google.genai as genai
from google.genai import types as genai_types
from pydub import AudioSegment

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("Error: GEMINI_API_KEY is not set.")
    exit(1)

client = genai.Client(api_key=GEMINI_API_KEY)

# 台灣時間，用於檔名對齊
tz = timezone(timedelta(hours=8))
now_tz = datetime.now(tz)
today_str_file = now_tz.strftime("%Y%m%d")
today_zh_format = f"{now_tz.year}年{now_tz.month}月{now_tz.day}日"

# 確保 Podcast 資料夾存在
PODCAST_DIR = "Podcast"
os.makedirs(PODCAST_DIR, exist_ok=True)

# --- 角色與聲音配置 ---
# 姍姍：活潑好奇的年輕女性主持人 → Zephyr (Bright) 明亮的聲音
# zn：冷靜專業的 TA 技術專家     → Charon (Informative) 知性的聲音
VOICE_MAPPING = {
    "姍姍": "Zephyr",    # 女聲：明亮
    "zn": "Charon"       # 男聲：知性
}

SYSTEM_PROMPT = f"""
你是一位專業的技術科技 Podcast 製作人與腳本編劇。
你的任務是將每日的「遊戲開發與技術美術日報」轉化為一段生動、自然且具有「呼吸感」的雙人對話腳本。
這是一檔時長約 5 分鐘的 Podcast，請確保雙人對話內容具備足夠的深度與長度（總對話回數大約在 25 到 35 句之間）。

【角色設定】
- 姍姍 (女)：聲音明亮、充滿活力與好奇心的年輕主播。負責開場、引導話題、提出開發者常遇到的痛點或疑問。說話風格親切、活潑，偶爾帶有俏皮的語氣。
- zn (男)：冷靜、客觀且經驗豐富的技術專家。負責深度解析新聞。回答要具體、專業，並常以「獨立遊戲製作人」的實務視角來評估影響與推演未來情況。

【腳本撰寫原則】
1. 內容焦點：
   - 節目的前半重點必須著重在「今日頭條」，詳細講解事件的來龍去脈與對產業的直接影響。
   - 節目的後半重點必須從日報最後的「今日全方位深度總結」中，挑選出最值得延伸討論的重點，並以此為基礎，由 zn 進行市場或技術發展趨勢的推演與預測。
2. 資訊忠實與延伸：嚴格根據提供的 Markdown 日報內容進行改寫。可以基於新聞與「總結」的內容，以 zn 的專家視角進行合理的延伸剖析，但不捏造不存在的新聞事件。
3. 對話感：使用繁體中文自然口語，加入語氣詞（例如：「耶」、「嗯」、「對啊」、「喔」），讓兩人自然互動，避免單人長篇大論。
4. 固定開場與收尾：
   - 開場白：姍姍必須使用這句話開場：「歡迎收聽{today_zh_format}的『每日遊戲資訊日報』！我是你們的主播姍姍！」
   - 結尾白：節目最後的道別，必須由雙方或其中一人說出這句完整的話：「感謝大家的收聽，希望今天的資訊對你們有幫助，我們明天同時間準時收聽！」

【輸出格式限制】
只輸出合法的 JSON 陣列 (JSON Array)，包含 "speaker" 與 "text" 兩個欄位。
speaker 只能是 "姍姍" 或 "zn"。
所有 text 內容必須使用繁體中文。
絕對不要加上 ```json 標記。
"""

async def generate_script_from_md(md_content: str) -> list:
    """節點一：讀取 Markdown 日報，呼叫文字模型轉化為對話腳本 JSON"""
    print("🧠 正在將日報轉換為 Podcast 雙人劇本...")
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=md_content,
        config=genai_types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            temperature=0.7, # 稍微提高溫度讓對話更自然
        )
    )
    res_text = response.text.strip()
    
    # 移除可能的 Markdown 標記
    json_match = re.search(r'```(?:json)?(.*?)```', res_text, re.DOTALL)
    if json_match:
        res_text = json_match.group(1).strip()
        
    try:
        script_data = json.loads(res_text, strict=False)
        return script_data
    except json.JSONDecodeError as e:
        print(f"❌ JSON 解析失敗: {e}\n模型原始輸出:\n{res_text}")
        return []

async def generate_speech(text: str, voice_name: str) -> tuple[bytes, str]:
    """節點二：單句文字轉語音，回傳 (audio_bytes, mime_type)"""
    print(f"  🎙️ 生成音軌 [{voice_name}]: {text[:30]}...")
    response = client.models.generate_content(
        model='gemini-2.5-flash-preview-tts',
        contents=text,
        config=genai_types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=genai_types.SpeechConfig(
                voice_config=genai_types.VoiceConfig(
                    prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                        voice_name=voice_name
                    )
                )
            )
        )
    )
    part = response.candidates[0].content.parts[0]
    return part.inline_data.data, part.inline_data.mime_type


def parse_audio_mime(mime_type: str) -> int:
    """從 mime_type (如 'audio/L16;codec=pcm;rate=24000') 解析取樣率"""
    rate_match = re.search(r'rate=(\d+)', mime_type)
    return int(rate_match.group(1)) if rate_match else 24000

async def main():
    # --- 判斷是否為「僅文案」模式 ---
    script_only = os.getenv("SCRIPT_ONLY", "false").lower() == "true"
    
    # 1. 尋找最新生成的 Markdown 日報
    report_files = glob.glob(os.path.join("Daily_Report", "Daily_Full_Report_*.md"))
    if not report_files:
        print("❌ 找不到 Markdown 日報，請先執行 generate_report.py")
        return
        
    report_files.sort(key=os.path.getmtime, reverse=True)
    latest_report = report_files[0]
    print(f"📄 讀取來源日報: {latest_report}")
    
    with open(latest_report, "r", encoding="utf-8") as f:
        md_content = f.read()

    # 2. 產出劇本
    script_data = await generate_script_from_md(md_content)
    if not script_data:
        return
        
    # 將生成的劇本存一份 JSON，方便除錯與確認對話品質
    script_filename = os.path.join(PODCAST_DIR, f"daily_podcast_script_{today_str_file}.json")
    with open(script_filename, "w", encoding="utf-8") as f:
        json.dump(script_data, f, ensure_ascii=False, indent=2)
    print(f"✅ 劇本已儲存至: {script_filename}")
    
    # 輸出劇本預覽
    print("\n" + "="*60)
    print("📋 劇本預覽：")
    print("="*60)
    for i, line in enumerate(script_data):
        speaker = line.get("speaker", "???")
        text = line.get("text", "")
        print(f"  [{i+1:02d}] {speaker}：{text}")
    print("="*60 + "\n")
    
    # --- 如果是僅文案模式，到此結束 ---
    if script_only:
        print("📝 僅文案模式 (SCRIPT_ONLY=true)，跳過語音合成。")
        return

    # 3. 逐句合成音檔
    print("🎧 開始進行多角色語音合成與拼接...")
    combined_audio = AudioSegment.empty()
    
    for index, line in enumerate(script_data):
        speaker = line.get("speaker", "姍姍")
        text = line.get("text", "")
        voice_name = VOICE_MAPPING.get(speaker, "Leda")
        
        try:
            audio_bytes, mime_type = await generate_speech(text, voice_name)
            sample_rate = parse_audio_mime(mime_type)
            segment = AudioSegment.from_raw(
                io.BytesIO(audio_bytes),
                sample_width=2,      # 16-bit (L16)
                frame_rate=sample_rate,
                channels=1           # mono
            )
            combined_audio += segment
            
            # 加入 0.6 秒的靜音停頓，營造呼吸感
            combined_audio += AudioSegment.silent(duration=600)
        except Exception as e:
            print(f"❌ 句子 {index} 生成失敗: {e}")

    # 4. 匯出實體 MP3 音檔
    output_filename = os.path.join(PODCAST_DIR, f"daily_podcast_{today_str_file}.mp3")
    print(f"💾 正在匯出最終音檔至 {output_filename}...")
    combined_audio.export(output_filename, format="mp3", bitrate="128k")
    print(f"🚀 Podcast 處理完畢！可直接播放 {output_filename} 進行品質確認。")

if __name__ == "__main__":
    asyncio.run(main())