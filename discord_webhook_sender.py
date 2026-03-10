import requests
import os
import glob
import json
import re
import time
from dotenv import load_dotenv

load_dotenv()

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

def send_to_discord():
    if not WEBHOOK_URL:
        print("Error: DISCORD_WEBHOOK_URL is not set. Skipping Discord push.")
        return

    # 1. 取得最新報告
    report_files = glob.glob("Daily_Full_Report_*.md")
    if not report_files: return
    report_files.sort(key=os.path.getmtime, reverse=True)
    latest_report = report_files[0]
    print(f"Sending report: {latest_report}")

    with open(latest_report, "r", encoding="utf-8") as f:
        full_text = f.read()

    # 2. 依照標題切割各段落（保留標題本身）
    # 格式：# 📅... 或 **📢/🎨/🎮/🇹🇼/💼/🌌 【...】**
    section_pattern = re.compile(
        r'(?=# 📅|\*\*[📢🎨🎮🇨🇳🇹🇼📍💼🌌])',
        re.UNICODE
    )
    raw_sections = section_pattern.split(full_text)
    sections = [s.strip() for s in raw_sections if s.strip()]

    if not sections:
        # 舊格式 fallback：按 ## 標題分割
        raw_sections = full_text.split("## ")
        sections = []
        for i, s in enumerate(raw_sections):
            if s.strip():
                prefix = "" if i == 0 else "## "
                sections.append(prefix + s.strip())

    # 3. 讀取 targets 映射 (section_name -> image path)
    target_mappings = {}
    if os.path.exists("daily_targets.json"):
        with open("daily_targets.json", "r", encoding="utf-8") as f:
            targets_data = json.load(f)
            for item in targets_data:
                target_mappings[item["image_filename"]] = os.path.join("assets", item["image_filename"])

    for i, section in enumerate(sections):
        # 清除 Markdown 圖片標籤（圖片由附件上傳）
        message_content = re.sub(r'!\[.*?\]\(.*?\)', '', section).strip()

        # 尋找此段落對應的圖片（根據 Markdown 中的 filename 來匹配）
        img_path = None
        for filename, abs_path in target_mappings.items():
            if filename in section and os.path.exists(abs_path):
                img_path = abs_path
                break

        # 安全切分字串，避免截斷 Markdown 或網址
        max_chunk = 1950
        chunks = []
        while message_content:
            if len(message_content) <= max_chunk:
                chunks.append(message_content)
                break
            # 找最後一個換行符
            split_idx = message_content.rfind('\n', 0, max_chunk)
            if split_idx == -1:
                split_idx = max_chunk
            chunks.append(message_content[:split_idx])
            message_content = message_content[split_idx:].strip()
            
        if not chunks and img_path:
            chunks.append("")
            
        for chunk_idx, chunk_text in enumerate(chunks):
            payload = {"content": chunk_text}
            files = {}
            # 只在該段落的第一個 Chunk 附加圖片
            if chunk_idx == 0 and img_path:
                fname = os.path.basename(img_path)
                files = {"file": (fname, open(img_path, "rb"))}

            response = requests.post(WEBHOOK_URL, data=payload, files=files)
            if not response.ok:
                print(f"❌ 第 {i} 段 chunk {chunk_idx} 發送失敗: {response.text[:200]}")
            else:
                print(f"✅ 第 {i} 段 chunk {chunk_idx} 已發送 (含圖片: {bool(files)})")

            time.sleep(1)

    print("✅ 報告與全部圖片已成功送達 Discord！")


if __name__ == "__main__":
    send_to_discord()
