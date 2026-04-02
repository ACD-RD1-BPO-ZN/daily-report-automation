import requests
import os
import glob
import json
import re
import time
from dotenv import load_dotenv

load_dotenv()

# DRY_RUN=true → 只印預覽，不呼叫 Discord API（用於本地確認內容）
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
FORUM_CHANNEL_ID = os.getenv("DISCORD_FORUM_CHANNEL_ID")

# ============================================================
# 論壇標籤 ID 設定
# ID 已預設為實際值，可透過 GitHub Secrets 或 .env 覆蓋。
# ============================================================
TAG_IDS = {
    "headline": os.getenv("FORUM_TAG_HEADLINE_ID", "1489205702886031500"),  # 頭條
    "indie":    os.getenv("FORUM_TAG_INDIE_ID",    "1489200534941597828"),  # 遊戲 | 獨立遊戲
    "global":   os.getenv("FORUM_TAG_GLOBAL_ID",   "1489200786427875519"),  # 遊戲 | 全球
    "ai":       os.getenv("FORUM_TAG_AI_ID",        "1489198768883630114"),  # AI | 資訊
    "ue":       os.getenv("FORUM_TAG_UE_ID",        "1489178833004265614"),  # 🟦引擎 | UE
    "unity":    os.getenv("FORUM_TAG_UNITY_ID",     "1489189619210911825"),  # 🟩引擎 | Unity
    "ta":       os.getenv("FORUM_TAG_TA_ID",        "1489189750874308780"),  # 🛠 技術 | TA
    "3d":       os.getenv("FORUM_TAG_3D_ID",        "1489198718258380913"),  # 🛠 技術 | 3D
}

# ============================================================
# 段落 Emoji → 論壇標籤鍵值對應表
# 新增報告段落時，在此處補充對應關係即可。
# Discord 論壇頻道每串最多只能套用 5 個標籤。
# ============================================================
SECTION_TAG_MAP = {
    "📢": ["headline"],   # 今日頭條   → 頭條
    "🎮": ["indie"],      # 獨立遊戲   → 遊戲|獨立遊戲
    "🤝": ["global"],    # 在地社群   → 遊戲|全球
    "💼": ["global"],    # 製作人週記 → 遊戲|全球
    "🌌": ["ai"],        # 深度總結   → AI|資訊
}

# ============================================================
# 🎨 引擎段落的子標題拆分設定
# 每個引擎子區塊將被發成獨立的討論串，並掛上精確的單一標籤。
# 格式：(用於比對子標題的 regex, 討論串顯示名稱, tag_keys)
# ============================================================
ENGINE_SUBSECTION_DEFS = [
    # (regex, 串名, tag_keys, 來源連結前綴清單)
    (re.compile(r"\*\*Unreal Engine", re.IGNORECASE), "🟦 Unreal Engine", ["ue"],    ["[Unreal"]),
    (re.compile(r"\*\*Unity",         re.IGNORECASE), "🟩 Unity",         ["unity"], ["[Unity"]),
    (re.compile(r"\*\*Godot",         re.IGNORECASE), "🎮 Godot Engine",  ["ta"],    ["[Godot"]),
    (re.compile(r"\*\*80\.lv",        re.IGNORECASE), "80.lv (TA/Tech)", ["ta"],    ["[80.lv"]),
]

# 段落 Emoji → daily_targets.json 中的 section_name 對應
# 用於不依賴 md 內的日期字串就能正確對到圖片
SECTION_IMG_KEY: dict[str, str] = {
    "📢": "Headline",
    "🎨": "TA",
    "🎮": "Indie",
    "🤝": "Local",
    "💼": "Producer",
}

# Discord 允許的圖片 MIME 類型對應
MIME_TYPES = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif":  "image/gif",
}

# ============================================================
# Embed 左側顏色邊框對應表（十進位整數，Discord API 格式）
# ============================================================
EMBED_COLORS = {
    "headline": 0xE67E22,  # 橙金  — 今日頭條
    "ue":       0x0E4DA4,  # 深藍  — Unreal Engine
    "unity":    0x48A14D,  # 草綠  — Unity
    "ta":       0x9B59B6,  # 紫    — TA / Godot / 80.lv
    "3d":       0x7F8C8D,  # 灰鋼  — 技術 3D
    "indie":    0x2ECC71,  # 翠綠  — 獨立遊戲
    "global":   0x3498DB,  # 天藍  — 全球 / 在地社群
    "ai":       0x7B68EE,  # 靛紫  — AI 資訊
}
DEFAULT_COLOR = 0x36393F  # Discord 預設深灰


def _resolve_tag_ids(section_text: str) -> list[str]:
    """根據段落開頭的 Emoji，回傳對應的有效 Tag ID 清單。"""
    heading = section_text[:80]
    for emoji, keys in SECTION_TAG_MAP.items():
        if emoji in heading:
            return [TAG_IDS[k] for k in keys if TAG_IDS.get(k)]
    return []


def _resolve_color(tag_keys: list[str]) -> int:
    """依照 tag_keys 回傳第一個命中的 Embed 顏色，無命中則回傳預設色。"""
    for k in tag_keys:
        if k in EMBED_COLORS:
            return EMBED_COLORS[k]
    return DEFAULT_COLOR


def _split_engine_section(section_text: str) -> list[tuple[str, list[str], str]]:
    """
    將 🎨 引擎段落依照引擎子標題（Unreal/Unity/Godot/80.lv）切割為多份，
    並將 [資料來源] 區塊中的連結依引擎名稱前綴分配至對應子串。
    回傳 list of (content, tag_keys, sub_title)。
    """
    # 1. 分離 [資料來源] 區塊與主體內容
    sources_lines: list[str] = []
    body = section_text
    sources_match = re.search(r"\[資料來源\]", section_text)
    if sources_match:
        body = section_text[:sources_match.start()]
        sources_lines = section_text[sources_match.start():].split("\n")

    # 2. 在主體內找各引擎子標題起始位置
    split_points: list[tuple[int, str, list[str], list[str]]] = []
    for pattern, display_name, tag_keys, src_prefixes in ENGINE_SUBSECTION_DEFS:
        for m in pattern.finditer(body):
            line_start = body.rfind("\n", 0, m.start()) + 1
            split_points.append((line_start, display_name, tag_keys, src_prefixes))

    if not split_points:
        return []

    split_points.sort(key=lambda x: x[0])
    results = []
    for i, (start, display_name, tag_keys, src_prefixes) in enumerate(split_points):
        end = split_points[i + 1][0] if i + 1 < len(split_points) else len(body)
        content = body[start:end].strip()
        # 3. 篩選屬於此引擎的來源連結並附加
        if sources_lines and src_prefixes:
            matched = [ln for ln in sources_lines
                       if any(ln.strip().startswith(p) for p in src_prefixes)]
            if matched:
                content += "\n[資料來源]\n" + "\n".join(matched)
        if content:
            results.append((content, tag_keys, display_name))
    return results


def _extract_preview(content: str) -> tuple[str, str]:
    """
    從段落內文提取極簡預覽（最多 2 行亮點 + 第一個來源連結）。
    格式：blockquote 亮點 + 來源連結，作為論壇卡片預覽的 Embed 內容。
    回傳 (preview_text, first_source_url)。
    """
    lines = content.split("\n")
    highlights: list[str] = []
    first_url = ""
    in_sources = False

    for line in lines:
        s = line.strip()
        if not s or s == "---":
            continue
        if s == "[資料來源]":
            in_sources = True
            continue
        if in_sources:
            if not first_url:
                m = re.search(r"\(<(https?://[^>]+)>\)", s)
                if m:
                    first_url = m.group(1)
            continue
        # 跳過圖片標籤、大標題、分隔線
        if s.startswith("![") or (s.startswith("**") and s.endswith("**")):
            continue
        # 跳過引擎子標題行，例如「- **Unreal Engine**：」
        if re.match(r"^-\s+\*\*[^*]+\*\*[\uff1a:]?\s*$", s):
            continue
        if len(highlights) >= 2:
            continue
        # 條列式
        if s.startswith("- "):
            clean = re.sub(r"\*\*(.+?)\*\*", r"\1", s[2:]).strip()
        elif not s.startswith("-") and not s.startswith("[") and not s.startswith("*"):
            # 純段落文字（如頭條的長段落）
            clean = re.sub(r"\*\*(.+?)\*\*", r"\1", s).strip()
        else:
            continue
        # 移除 Markdown 連結標記
        clean = re.sub(r"\[(.+?)\]\(<.+?>\)", r"\1", clean)
        if len(clean) > 10:
            highlights.append(clean[:130])

    preview = "\n".join(f"> {h}" for h in highlights)
    if first_url:
        preview += f"\n\n[📰 來源]({first_url})"
    return preview or content[:300], first_url


def _extract_thread_name(section_text: str, date_str: str) -> str:
    """
    從段落第一行提取出乾淨的討論串標題。
    格式：{date_str} | {section_title}，最長 100 字元（Discord 上限）。
    """
    first_line = section_text.split("\n")[0].strip()
    first_line = first_line.replace("**", "").strip()
    return f"{date_str} | {first_line}"[:100]


def _chunk_text(text: str, max_len: int = 1950) -> list[str]:
    """將長文字切分成不超過 max_len 字元的 Chunk 清單，優先在換行符處切割。"""
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_idx = text.rfind("\n", 0, max_len)
        if split_idx == -1:
            split_idx = max_len
        chunks.append(text[:split_idx])
        text = text[split_idx:].strip()
    return chunks


def _create_forum_thread(thread_name: str, content: str, tag_ids: list[str], img_path: str | None, color: int = DEFAULT_COLOR) -> str | None:
    """
    在論壇頻道建立新討論串（含 Embed 訊息、顏色邊框與可選圖片附件）。
    回傳建立成功的討論串 Channel ID，失敗則回傳 None。
    DRY_RUN 模式下只印預覽。
    """
    if DRY_RUN:
        tag_names = [k for k, v in TAG_IDS.items() if v in tag_ids]
        print(f"[DRY-RUN] 串名 : {thread_name}")
        print(f"[DRY-RUN] 標籤 : {tag_names}  顏色: #{color:06X}")
        print(f"[DRY-RUN] 圖片 : {img_path}")
        print(f"[DRY-RUN] 內容預覽 (前200字):\n{content[:200]}")
        print("-" * 60)
        return "DRY_RUN_THREAD_ID"

    url = f"https://discord.com/api/v10/channels/{FORUM_CHANNEL_ID}/threads"
    headers = {"Authorization": f"Bot {BOT_TOKEN}"}

    embed: dict = {"description": content, "color": color}
    payload: dict = {
        "name": thread_name,
        "message": {"embeds": [embed]},
    }
    if tag_ids:
        payload["applied_tags"] = tag_ids

    if img_path and os.path.exists(img_path):
        ext = os.path.splitext(img_path)[1].lower()
        mime = MIME_TYPES.get(ext, "application/octet-stream")
        fname = os.path.basename(img_path)
        embed["image"] = {"url": f"attachment://{fname}"}
        payload["message"]["attachments"] = [{"id": 0, "filename": fname}]
        with open(img_path, "rb") as img_file:
            resp = requests.post(
                url,
                headers=headers,
                files={
                    "payload_json": (None, json.dumps(payload), "application/json"),
                    "files[0]":     (fname, img_file, mime),
                },
            )
    else:
        headers["Content-Type"] = "application/json"
        resp = requests.post(url, headers=headers, json=payload)

    if not resp.ok:
        print(f"❌ 建立討論串失敗 [{resp.status_code}]: {resp.text[:300]}")
        return None

    thread_id = resp.json().get("id")
    print(f"✅ 討論串已建立: 「{thread_name}」 (id={thread_id})")
    return thread_id


def _post_message_to_thread(thread_id: str, content: str, color: int = DEFAULT_COLOR) -> None:
    """在已存在的討論串中補發後續 Embed 訊息（用於超過字元上限時的分段）。"""
    if DRY_RUN:
        print(f"[DRY-RUN] 補發至串 {thread_id} (前100字): {content[:100]}")
        return
    url = f"https://discord.com/api/v10/channels/{thread_id}/messages"
    headers = {
        "Authorization": f"Bot {BOT_TOKEN}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, headers=headers, json={"embeds": [{"description": content, "color": color}]})
    if not resp.ok:
        print(f"❌ 後續訊息發送失敗 [{resp.status_code}]: {resp.text[:200]}")
    else:
        print(f"  ↳ 後續訊息已補發至討論串 {thread_id}")


def send_to_discord_forum() -> None:
    if DRY_RUN:
        print("🔍 DRY-RUN 模式：只預覽，不發送至 Discord\n" + "=" * 60)
    elif not BOT_TOKEN or not FORUM_CHANNEL_ID:
        print("Error: DISCORD_BOT_TOKEN 或 DISCORD_FORUM_CHANNEL_ID 尚未設定，跳過論壇發布。")
        return

    # 1. 取得最新報告檔案
    report_files = glob.glob(os.path.join("Daily_Report", "Daily_Full_Report_*.md"))
    if not report_files:
        print("找不到報告檔案，中止執行。")
        return
    report_files.sort(key=os.path.getmtime, reverse=True)
    latest_report = report_files[0]
    print(f"发布報告: {latest_report}")

    # 2. 從檔名解析日期字串 (YYYYMMDD → YYYY-MM-DD)
    date_match = re.search(r"(\d{8})", os.path.basename(latest_report))
    raw_date = date_match.group(1) if date_match else "00000000"
    date_str = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"

    with open(latest_report, "r", encoding="utf-8") as f:
        full_text = f.read()

    # 3. 依照大標題切割段落（與 discord_webhook_sender.py 邏輯一致）
    section_pattern = re.compile(
        r"(?=# 📅|\*\*[📢🎨🎮🇨🇳🇹🇼📍💼🤝🌌])",
        re.UNICODE,
    )
    raw_sections = section_pattern.split(full_text)
    sections = [s.strip() for s in raw_sections if s.strip()]

    if not sections:
        # 舊格式 fallback
        raw_sections = full_text.split("## ")
        sections = [("## " + s.strip() if i > 0 else s.strip())
                    for i, s in enumerate(raw_sections) if s.strip()]

    # 4. 讀取圖片路徑映射（依 section_name 匹配，與日期無關）
    section_name_to_img: dict[str, str] = {}
    if os.path.exists("daily_targets.json"):
        with open("daily_targets.json", "r", encoding="utf-8") as f:
            targets_data = json.load(f)
        for item in targets_data:
            sname = item.get("section_name", "").strip()  # e.g. "Headline", "TA"
            fpath = os.path.join("assets", item["image_filename"])
            if os.path.exists(fpath):
                section_name_to_img[sname] = fpath
                print(f"🖼  圖片對應: {sname} → {fpath}")
            else:
                print(f"⚠️  圖片不存在: {fpath}")

    # 5. 逐段建立論壇討論串
    for section in sections:
        # 跳過純日期標題行與深度總結
        if section.startswith("# 📅") or "🌌" in section[:80]:
            print(f"⏭  跳過: {section[:30].strip()}")
            continue

        # 清除 Markdown 圖片標籤（圖片改以附件形式附加）
        message_content = re.sub(r"!\[.*?\]\(.*?\)", "", section).strip()
        if not message_content:
            continue

        # 依 section emoji 查找對應圖片
        img_path: str | None = None
        emoji_found = next((e for e in SECTION_IMG_KEY if e in section[:80]), None)
        if emoji_found:
            img_path = section_name_to_img.get(SECTION_IMG_KEY[emoji_found])

        # 🎨 引擎段落：按子標題拆成多個獨立討論串
        if "🎨" in section[:80]:
            engine_parts = _split_engine_section(message_content)
            if engine_parts:
                for idx, (eng_content, eng_tag_keys, eng_title) in enumerate(engine_parts):
                    eng_thread_name = f"{date_str} | {eng_title}"[:100]
                    eng_tag_ids = [TAG_IDS[k] for k in eng_tag_keys if TAG_IDS.get(k)]
                    eng_color = _resolve_color(eng_tag_keys)
                    use_img = img_path if idx == 0 else None
                    # 建立討論串（極簡預覽 Embed）
                    preview, _ = _extract_preview(eng_content)
                    tid = _create_forum_thread(eng_thread_name, preview, eng_tag_ids, use_img, eng_color)
                    time.sleep(1.5)
                    # 完整內容補發至串內
                    if tid:
                        for chunk in _chunk_text(eng_content, max_len=4000):
                            _post_message_to_thread(tid, chunk, eng_color)
                            time.sleep(1)
                continue  # 跳過下方通用發布邏輯

        thread_name = _extract_thread_name(section, date_str)
        emoji_key = next((e for e in SECTION_TAG_MAP if e in section[:80]), None)
        sec_tag_keys = SECTION_TAG_MAP.get(emoji_key, []) if emoji_key else []
        tag_ids = [TAG_IDS[k] for k in sec_tag_keys if TAG_IDS.get(k)]
        sec_color = _resolve_color(sec_tag_keys)
        # 建立討論串（極簡預覽 Embed + 圖片）
        preview, _ = _extract_preview(message_content)
        thread_id = _create_forum_thread(thread_name, preview, tag_ids, img_path, sec_color)
        time.sleep(1.5)
        # 完整內容補發至串內
        if thread_id:
            for chunk in _chunk_text(message_content, max_len=4000):
                _post_message_to_thread(thread_id, chunk, sec_color)
                time.sleep(1)

    print("✅ 所有段落已成功發布至 Discord 論壇頻道！")


if __name__ == "__main__":
    send_to_discord_forum()
