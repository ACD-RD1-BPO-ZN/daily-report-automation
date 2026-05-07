import requests
import os
import glob
import json
import re
import time
import sys
from dotenv import load_dotenv

load_dotenv()

def send_to_discord():
    channel_dir = sys.argv[1] if len(sys.argv) > 1 else "channels/gamedev"
    config_path = os.path.join(channel_dir, "channel_config.json")
    channel_id = "gamedev"
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
            channel_id = config.get("channel_id", "gamedev")
            discord_cfg = config.get("discord", {})
            env_key = discord_cfg.get("webhook_env", "")
            if env_key:
                webhook_url = os.getenv(env_key)

    if not webhook_url:
        print("Error: Webhook URL is not set. Skipping Discord push.")
        return

    # 1. 取得最新報告
    report_files = glob.glob(os.path.join("Daily_Report", f"Daily_Full_Report_{channel_id}_*.md"))
    if not report_files:
        report_files = glob.glob(os.path.join("Daily_Report", "Daily_Full_Report_*.md"))
    if not report_files: return
    report_files.sort(reverse=True)
    latest_report = report_files[0]
    print(f"Sending report: {latest_report}")

    with open(latest_report, "r", encoding="utf-8") as f:
        full_text = f.read()

    # 2. 依照標題切割各段落（保留標題本身）
    # 格式：# 📅... 或 **📢/🎨/⚙️/🧱/✨/🎮/🇹🇼/💼/🌌 【...】**
    section_pattern = re.compile(
        r'(?=# 📅|\*\*[📢🎨⚙️🧱✨🎮🇨🇳🇹🇼📍💼🤝🌌🎬🎟️📺🎌])',
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
    targets_file = f"daily_targets_{channel_id}.json"
    if not os.path.exists(targets_file):
        targets_file = "daily_targets.json"
        
    if os.path.exists(targets_file):
        with open(targets_file, "r", encoding="utf-8") as f:
            targets_data = json.load(f)
            for item in targets_data:
                target_mappings[item["image_filename"]] = os.path.join("assets", item["image_filename"])

    for i, section in enumerate(sections):
        # 略過 TA 模塊 (普通日報不發送 TA 內容，保留在論壇版)
        if "✨" in section[:80] or "TA 相關" in section[:80]:
            print("Skipping TA section in webhook newspaper.")
            continue

        # 清除 Markdown 圖片標籤（圖片由附件上傳）
        message_content = re.sub(r'!\[.*?\]\(.*?\)', '', section).strip()

        # 尋找此段落對應的圖片（根據 Markdown 中的 filename 來匹配）
        img_path = None
        for filename, abs_path in target_mappings.items():
            if filename in section and os.path.exists(abs_path):
                # 深度總結的 synthesis_ai 佔位圖略過，改用下方 fallback
                if "synthesis_ai" in filename:
                    continue
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

            response = requests.post(webhook_url, data=payload, files=files)
            if not response.ok:
                print(f"❌ 第 {i} 段 chunk {chunk_idx} 發送失敗: {response.text[:200]}")
            else:
                print(f"✅ 第 {i} 段 chunk {chunk_idx} 已發送 (含圖片: {bool(files)})")

            time.sleep(1)

    print("✅ 報告與全部圖片已成功送達 Discord！")


if __name__ == "__main__":
    send_to_discord()
