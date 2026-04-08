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
# ============================================================
SECTION_TAG_MAP = {
    "📢": ["headline"],   # 今日頭條     → 頭條
    "✨": ["ta"],         # TA 相關      → 技術|TA
    "🎮": ["indie"],      # 獨立遊戲     → 遊戲|獨立遊戲
    "🤝": ["indie"],      # 在地社群     → 遊戲|獨立遊戲
    "💼": ["global"],     # 製作人週記   → 遊戲|全球 (引擎相關自動分流)
    "🌌": ["ai"],         # 深度總結     → AI|資訊
}

# ============================================================
# 標籤顯示名稱（用於討論串標題）
# ============================================================
TAG_DISPLAY_NAMES = {
    "headline": "🔥 頭條",
    "indie":    "💗 遊戲｜獨立遊戲",
    "global":   "🌍 遊戲｜全球",
    "ue":       "📘 引擎｜UE",
    "unity":    "📙 引擎｜Unity",
    "ta":       "🛠️ 技術｜TA",
    "3d":       "🛠️ 技術｜3D",
    "ai":       "🤖 AI｜資訊",
}

# 引擎關鍵字路由（用於將製作人週記中的條目分流到引擎標籤）
ENGINE_ROUTE_KEYWORDS: dict[str, list[str]] = {
    "unity": ["unity"],
    "ue":    ["unreal", "ue5", "ue4"],
}

# ============================================================
# 🎨 引擎段落的子標題拆分設定
# 每個引擎子區塊將被發成獨立的討論串，並掛上精確的單一標籤。
# 格式：(用於比對子標題的 regex, 討論串顯示名稱, tag_keys)
# ============================================================
ENGINE_SUBSECTION_DEFS = [
    (re.compile(r"\*\*Unreal", re.IGNORECASE), "🟦 Unreal Engine", ["ue"]),
    (re.compile(r"\*\*Unity",         re.IGNORECASE), "🟩 Unity",         ["unity"]),
    (re.compile(r"\*\*Godot",         re.IGNORECASE), "🎮 Godot Engine",  ["ta"]),
    # 新版精確分類
    (re.compile(r"\*\*3D\s*模",        re.IGNORECASE), "🛠️ 3D 模型技術",   ["3d"]),
    (re.compile(r"\*\*TA\s*(與|&)?\s*特效", re.IGNORECASE), "🛠️ TA 與其他技術", ["ta"]),
    (re.compile(r"\*\*TA\s*相關",       re.IGNORECASE), "🛠️ TA 相關",        ["ta"]),
    # 以下為相容舊報告的 Fallback
    (re.compile(r"\*\*80\.lv",        re.IGNORECASE), "80.lv (TA/Tech)", ["ta"]),
    (re.compile(r"\*\*(映CG|InCG|3D/CG|Blender|Maya|3ds\s*Max|ZBrush|Houdini|Boris\s*FX|Substance)", re.IGNORECASE),
     "映CG (3D/CG/VFX)", ["3d"]),
]

# 段落 Emoji → daily_targets.json 中的 section_name 對應
SECTION_IMG_KEY: dict[str, str] = {
    "📢": "Headline",
    "⚙️": "Engine",
    "✨": "TA",
    "🎨": "TA",    # 向後相容舊引擎格式
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


def _resolve_color(tag_keys: list[str]) -> int:
    """依照 tag_keys 回傳第一個命中的 Embed 顏色，無命中則回傳預設色。"""
    for k in tag_keys:
        if k in EMBED_COLORS:
            return EMBED_COLORS[k]
    return DEFAULT_COLOR


# ============================================================
# 內聯格式解析器 — 取代舊版智慧配對系統
# ============================================================

def _is_source_link(line: str) -> bool:
    """判斷一行文字是否為來源連結（Markdown 超連結格式）。"""
    s = line.strip().lstrip("- ")
    return bool(re.match(r'\[', s) and re.search(r'\(<https?://|\(https?://', s))


def _split_into_news_items(content: str) -> list[dict]:
    """
    解析內聯格式：每條 bullet + 緊接的來源連結（子項目）= 一條新聞。
    使用縮排層級來正確區分「新聞 bullet」與「來源連結 sub-bullet」。
    回傳 [{"text": "...", "source": "..."}, ...]
    """
    items: list[dict] = []
    current_text = ""
    current_source = ""
    orphan_sources: list[str] = []

    # 先計算整個 content 的 bullet 縮排基準
    # 找出最淺的 bullet 縮排，作為「新聞級」的基準
    bullet_indent_levels: list[int] = []
    for line in content.split("\n"):
        if re.match(r'^(\s*)-\s', line):
            indent = len(line) - len(line.lstrip(' '))
            bullet_indent_levels.append(indent)

    # 排序後取最小值與次小值，用來判斷「內容bullet」vs「來源sub-bullet」
    unique_indents = sorted(set(bullet_indent_levels))
    content_indent = unique_indents[0] if unique_indents else 0
    # 若只有一種縮排，來源辨識完全靠 _is_source_link
    source_indent = unique_indents[1] if len(unique_indents) > 1 else 9999

    for line in content.split("\n"):
        s = line.strip()
        if not s or s == "---" or s == "[資料來源]":
            continue

        # 跳過子標題行（**Unreal Engine**：等）
        if re.match(r"^\*\*[^*]+\*\*[\uff1a:]?\s*$", s):
            if current_text:
                items.append({"text": current_text, "source": current_source})
                current_text = ""
                current_source = ""
            continue

        # 計算本行縮排
        raw_indent = len(line) - len(line.lstrip(' '))
        is_bullet = bool(re.match(r'^\s*-\s', line))

        # 來源連結行：必須同時是 _is_source_link，且縮排比內容 bullet 深
        if _is_source_link(s) and (raw_indent > content_indent or not is_bullet):
            clean_src = s.lstrip("- ").strip()
            if current_text:
                if current_source:
                    # 已有來源 → 此為額外連結（合併來源同一新聞罕見場合）
                    current_source += "\n" + clean_src
                else:
                    current_source = clean_src
            else:
                orphan_sources.append(clean_src)
            continue

        # 新聞 bullet：縮排在內容層級或更淺
        if is_bullet and raw_indent <= content_indent:
            stripped = re.sub(r"^\s*-\s*", "", s)
            # 跳過引擎子標題 bullet（- **Unity**：）
            if re.match(r"^\*\*[^*]+\*\*[\uff1a:]?\s*$", stripped):
                if current_text:
                    items.append({"text": current_text, "source": current_source})
                    current_text = ""
                    current_source = ""
                continue
            # flush 前一條
            if current_text:
                items.append({"text": current_text, "source": current_source})
            current_text = s
            current_source = ""
            continue

        # 比內容層稍深的 bullet 但不是來源連結 → 次級 bullet，附加到當前新聞
        if is_bullet and raw_indent > content_indent and not _is_source_link(s):
            if current_text:
                current_text += "\n" + s
            else:
                current_text = s
            continue

        # 其他文字：追加到當前 bullet
        if current_text:
            current_text += "\n" + s
        else:
            current_text = s

    # flush 最後一條
    if current_text:
        items.append({"text": current_text, "source": current_source})

    # 舊格式相容：將孤立來源按順序分配給沒有來源的 bullet
    if orphan_sources:
        empty_items = [it for it in items if not it["source"]]
        for i, src in enumerate(orphan_sources):
            if i < len(empty_items):
                empty_items[i]["source"] = src
            elif items:
                items[-1]["source"] += ("\n" + src) if items[-1]["source"] else src

    return items



def _split_engine_section(section_text: str) -> list[tuple[str, list[str], str]]:
    """
    將 🎨 引擎段落依照引擎子標題（Unreal/Unity/Godot/3D/TA）切割為多份。
    內聯格式下，來源連結已跟在各 bullet 旁，不需要額外的前綴比對分配。
    回傳 list of (content, tag_keys, sub_title)。
    """
    body = section_text

    # 在主體內找各引擎子標題起始位置
    split_points: list[tuple[int, str, list[str]]] = []
    for pattern, display_name, tag_keys in ENGINE_SUBSECTION_DEFS:
        for m in pattern.finditer(body):
            line_start = body.rfind("\n", 0, m.start()) + 1
            split_points.append((line_start, display_name, tag_keys))

    if not split_points:
        return []

    split_points.sort(key=lambda x: x[0])
    results = []
    for i, (start, display_name, tag_keys) in enumerate(split_points):
        end = split_points[i + 1][0] if i + 1 < len(split_points) else len(body)
        content = body[start:end].strip()
        if content:
            results.append((content, tag_keys, display_name))
    return results


def _route_content_by_engine(content: str, default_tag: str = "global") -> dict[str, str]:
    """
    將段落的 bullet 依引擎關鍵字分流。
    回傳 {tag_key: content_str}，非引擎相關的放在 default_tag。
    內聯格式下，來源連結跟著 bullet 一起路由。
    """
    # 移除開頭粗體標題行
    content = re.sub(r"^\*\*[^\n]+\*\*\s*\n*", "", content, count=1).strip()

    # 路由 bullet（連同後面的來源連結行一起歸類）
    routed: dict[str, list[str]] = {}
    current_tag = default_tag

    for line in content.split("\n"):
        s = line.strip()
        if not s or s == "---":
            continue

        # 新的 bullet → 決定路由
        if s.startswith("- ") and not _is_source_link(s):
            current_tag = default_tag
            lower = s.lower()
            for tag_key, keywords in ENGINE_ROUTE_KEYWORDS.items():
                if any(kw in lower for kw in keywords):
                    current_tag = tag_key
                    break

        # 所有行（包括來源連結）都跟著 current_tag
        routed.setdefault(current_tag, []).append(line)

    return {k: "\n".join(v).strip() for k, v in routed.items()}


def _has_cjk(text: str) -> bool:
    """檢查字串是否包含中日韓文字。"""
    return bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf]', text))


def _get_item_title(item: dict) -> str:
    """提取單則新聞的標題（支援從粗體、書名號或網址名稱提取）。"""
    if not item.get("text") and not item.get("source"):
        return ""
    base = item.get("text") or item.get("source")
    raw = re.sub(r"^\-\s*", "", base.split("\n")[0]).strip()
    
    # 1. 優先從粗體或書名號取標題
    title_match = re.search(r"\*\*(.+?)\*\*", raw) or re.search(r"《(.+?)》", raw)
    title = title_match.group(1).strip().rstrip("：:") if title_match else ""
    
    # 2. 若無 bold/書名 → 從來源連結顯示名稱提取
    if not title and item.get("source"):
        src_m = re.search(r'\[([^\]]+)\]', item.get("source", ""))
        if src_m:
            display = src_m.group(1)
            title = display.split(" - ", 1)[1].strip() if " - " in display else display
            
    # 3. 再不行就拔純文字前 50 字作為保底
    if not title:
        clean = re.sub(r'\[.*?\]\(.*?\)', '', raw)
        clean = re.sub(r'https?://\S+', '', clean)
        clean = re.sub(r'[\*\#\-🔗<>\n_]', ' ', clean).strip()
        title = clean[:50]
        
    return title[:80].strip()


# ============================================================
# og:image 爬蟲
# ============================================================

def _extract_url_from_source(source: str) -> str:
    """從來源連結文字中提取第一個 URL。"""
    m = re.search(r"<(https?://[^>]+)>", source) or re.search(r"\((https?://[^)]+)\)", source)
    return m.group(1) if m else ""


def _fetch_og_image(url: str) -> str:
    """抓取網頁的 og:image，回傳圖片 URL；失敗則回傳空字串。
    使用完整的瀏覽器 Headers 來避開 Cloudflare / Akamai 等反爬蟲機制。
    對 JS 渲染網站（如 unrealengine.com）自動使用 Playwright fallback。
    """
    if not url:
        return ""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
    }
    try:
        resp = requests.get(url, timeout=8, headers=headers)
        if not resp.ok:
            if any(domain in url for domain in _JS_RENDERED_DOMAINS):
                return _fetch_og_image_playwright(url)
            return ""
        # 偵測 Cloudflare 攔截頁
        if any(t in resp.text[:500].lower() for t in ["just a moment", "cloudflare", "checking your browser"]):
            if any(domain in url for domain in _JS_RENDERED_DOMAINS):
                return _fetch_og_image_playwright(url)
            return ""
        og = _parse_og_image(resp.text)
        if og:
            return og
        if any(domain in url for domain in _JS_RENDERED_DOMAINS):
            return _fetch_og_image_playwright(url)
        return ""
    except Exception:
        return ""


# 需要 Playwright 支援的 JS 渲染網站
_JS_RENDERED_DOMAINS = ["unrealengine.com"]


def _parse_og_image(html: str) -> str:
    """從 HTML 中提取 og:image URL，過濾 logo/placeholder。"""
    pat1 = r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\'<>]+)["\']'
    pat2 = r'<meta[^>]+content=["\']([^"\'<>]+)["\'][^>]+property=["\']og:image["\']'
    m = re.search(pat1, html) or re.search(pat2, html)
    if not m:
        return ""
    img_url = m.group(1)
    if any(skip in img_url.lower() for skip in ["logo", "avatar", "icon", "default", "placeholder"]):
        return ""
    return img_url


def _fetch_og_image_playwright(url: str) -> str:
    """用 Playwright（反偵測模式）載入 JS 渲染頁面後提取 og:image。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                    "--no-zygote",
                    "--disable-gpu",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="zh-TW",
            )
            page = context.new_page()
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
                window.chrome = { runtime: {} };
            """)
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            try:
                page.wait_for_selector('meta[property="og:image"]', timeout=5000)
            except Exception:
                pass
            html = page.content()
            browser.close()
        return _parse_og_image(html)
    except Exception:
        return ""


# ============================================================
# Discord API 互動
# ============================================================

def _post_embed_to_thread(thread_id: str, title: str, description: str,
                          color: int = DEFAULT_COLOR, source: str = "",
                          thumbnail_url: str = "") -> None:
    """在討論串中發送一則獨立的 Embed 訊息（用於單條新聞展示）。"""
    if source:
        description += f"\n\n{source}"

    if DRY_RUN:
        thumb_tag = f" 🖼️" if thumbnail_url else ""
        print(f"  [DRY-RUN] 📰 {title[:60]}{thumb_tag}")
        print(f"  [DRY-RUN]    {description[:150]}")
        return

    url = f"https://discord.com/api/v10/channels/{thread_id}/messages"
    headers = {
        "Authorization": f"Bot {BOT_TOKEN}",
        "Content-Type": "application/json",
    }
    embed: dict = {"description": description[:4096], "color": color}
    if title:
        embed["title"] = title[:256]
    if thumbnail_url:
        embed["thumbnail"] = {"url": thumbnail_url}
    resp = requests.post(url, headers=headers, json={"embeds": [embed]})
    if not resp.ok:
        print(f"  ❌ Embed 發送失敗 [{resp.status_code}]: {resp.text[:200]}")
    else:
        print(f"  📰 已發送: {title[:50]}")


def _create_forum_thread(thread_name: str, display_title: str, embed_description: str,
                         tag_ids: list[str], img_path: str | None, color: int = DEFAULT_COLOR,
                         keywords: str = "", img_url: str | None = None) -> str | None:
    """
    在論壇頻道建立新討論串。
    回傳建立成功的討論串 Channel ID，失敗則回傳 None。
    """
    if DRY_RUN:
        tag_names = [k for k, v in TAG_IDS.items() if v in tag_ids]
        print(f"[DRY-RUN] 串名 : {thread_name}")
        print(f"[DRY-RUN] 標籤 : {tag_names}  顏色: #{color:06X}")
        print(f"[DRY-RUN] 圖片 : {img_path or img_url or '(無)'}")
        print(f"[DRY-RUN] 關鍵詞 : {keywords}")
        print(f"[DRY-RUN] Embed 標題 : {display_title}")
        print(f"[DRY-RUN] Embed 內容 (前400字):\n{embed_description[:400]}")
        print("-" * 60)
        return "DRY_RUN_THREAD_ID"

    url = f"https://discord.com/api/v10/channels/{FORUM_CHANNEL_ID}/threads"
    headers = {"Authorization": f"Bot {BOT_TOKEN}"}

    embed: dict = {"title": display_title, "description": embed_description[:4096], "color": color}
    payload: dict = {
        "name": thread_name,
        "message": {
            "content": keywords[:2000] if keywords else "",
            "embeds": [embed],
        },
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
    elif img_url:
        embed["image"] = {"url": img_url}
        headers["Content-Type"] = "application/json"
        resp = requests.post(url, headers=headers, json=payload)
    else:
        headers["Content-Type"] = "application/json"
        resp = requests.post(url, headers=headers, json=payload)

    if not resp.ok:
        print(f"❌ 建立討論串失敗 [{resp.status_code}]: {resp.text[:300]}")
        return None

    thread_id = resp.json().get("id")
    print(f"✅ 討論串已建立: 「{thread_name}」 (id={thread_id})")
    return thread_id


# ============================================================
# 主流程
# ============================================================

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
    report_files.sort(reverse=True)
    latest_report = report_files[0]
    print(f"发布報告: {latest_report}")

    # 2. 從檔名解析日期字串 (YYYYMMDD → YYYY-MM-DD)
    date_match = re.search(r"(\d{8})", os.path.basename(latest_report))
    raw_date = date_match.group(1) if date_match else "00000000"
    date_str = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"

    with open(latest_report, "r", encoding="utf-8") as f:
        full_text = f.read()

    section_pattern = re.compile(
        r"(?=# 📅|\*\*[📢🎨⚙️✨🎮🇨🇳🇹🇼📍💼🤝🌌])",
        re.UNICODE,
    )
    raw_sections = section_pattern.split(full_text)
    sections = [s.strip() for s in raw_sections if s.strip()]

    if not sections:
        raw_sections = full_text.split("## ")
        sections = [("## " + s.strip() if i > 0 else s.strip())
                    for i, s in enumerate(raw_sections) if s.strip()]

    # 4. 讀取圖片路徑映射（依 section_name 匹配）
    section_name_to_img: dict[str, str] = {}
    if os.path.exists("daily_targets.json"):
        with open("daily_targets.json", "r", encoding="utf-8") as f:
            targets_data = json.load(f)
        for item in targets_data:
            sname = item.get("section_name", "").strip()
            fpath = os.path.join("assets", item["image_filename"])
            if os.path.exists(fpath):
                section_name_to_img[sname] = fpath
                print(f"🖼  圖片對應: {sname} → {fpath}")
            else:
                print(f"⚠️  圖片不存在: {fpath}")

    # 5. 依標籤分類聚合內容（每個標籤建立一個討論串）
    buckets: dict[str, list[str]] = {}          # tag_key → content blocks
    bucket_imgs: dict[str, str | None] = {}     # tag_key → first image

    for section in sections:
        # 跳過純日期標題行與深度總結
        if section.startswith("# 📅") or "🌌" in section[:80]:
            print(f"⏭  跳過: {section[:30].strip()}")
            continue

        # 清除 Markdown 圖片標籤
        message_content = re.sub(r"!\[.*?\]\(.*?\)", "", section).strip()
        if not message_content:
            continue

        # 依 section emoji 查找對應圖片
        img_path: str | None = None
        emoji_found = next((e for e in SECTION_IMG_KEY if e in section[:80]), None)
        if emoji_found:
            img_path = section_name_to_img.get(SECTION_IMG_KEY[emoji_found])

        # 🎨/⚙️ 引擎段落：拆分後路由到各引擎標籤桶
        if "🎨" in section[:80] or "⚙️" in section[:80]:
            engine_parts = _split_engine_section(message_content)
            for eng_content, eng_tag_keys, _eng_title in engine_parts:
                tag_key = eng_tag_keys[0]
                buckets.setdefault(tag_key, []).append(eng_content)
            if img_path and "ta" in buckets:
                bucket_imgs.setdefault("ta", img_path)
            continue

        # 💼 製作人週記：依引擎關鍵字分流
        if "💼" in section[:80]:
            routed = _route_content_by_engine(message_content, default_tag="global")
            for tag_key, routed_content in routed.items():
                buckets.setdefault(tag_key, []).append(routed_content)
            if "global" not in bucket_imgs and img_path:
                bucket_imgs["global"] = img_path
            continue

        # 一般段落：路由到對應標籤，並將引擎相關條目分流
        emoji_key = next((e for e in SECTION_TAG_MAP if e in section[:80]), None)
        if not emoji_key:
            continue
        tag_key = SECTION_TAG_MAP[emoji_key][0]
        content = re.sub(r"^\*\*[^\n]+\*\*\s*\n*", "", message_content, count=1).strip()
        routed = _route_content_by_engine(content, default_tag=tag_key)
        for rk, rc in routed.items():
            buckets.setdefault(rk, []).append(rc)
        if tag_key not in bucket_imgs and img_path:
            bucket_imgs[tag_key] = img_path

    # 6. 依標籤建立討論串，每條新聞獨立 Embed
    TAG_ORDER = ["ai", "3d", "unity", "ue", "ta", "global", "indie", "headline"]
    for tag_key in TAG_ORDER:
        if tag_key not in buckets:
            continue
        merged = "\n\n".join(buckets[tag_key])
        tag_ids = [TAG_IDS[tag_key]] if TAG_IDS.get(tag_key) else []
        color = _resolve_color([tag_key])
        display_name = TAG_DISPLAY_NAMES.get(tag_key, tag_key)
        thread_name = f"{date_str} | {display_name}"[:100]
        img = bucket_imgs.get(tag_key)

        # 預先拆分新聞
        news_items = _split_into_news_items(merged)
        
        # 透過各別新聞的標題擷取作為預覽字串
        item_titles = []
        for it in news_items:
            t = _get_item_title(it)
            if t and t not in item_titles:
                item_titles.append(t)
        kw = " ｜ ".join(item_titles[:6])

        # 討論串封面一律使用串內第一篇文章的 og:image
        thread_cover_url: str | None = None
        for item in news_items:
            src_url = _extract_url_from_source(item.get("source", ""))
            if src_url:
                thread_cover_url = _fetch_og_image(src_url)
                if thread_cover_url:
                    print(f"🖼️ {tag_key}: 串封面縮圖: {thread_cover_url[:80]}")
                    break
        if not thread_cover_url:
            print(f"⚠️ {tag_key}: 所有來源均無 og:image，此串不使用縮圖")

        # 下載封面圖實體，確保 Discord 外層預覽 100% 顯示
        final_img_path = img
        downloaded_temp = None

        if thread_cover_url:
            try:
                import tempfile
                resp = requests.get(thread_cover_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                if resp.status_code == 200:
                    ext = ".png" if "png" in resp.headers.get("Content-Type", "").lower() else ".jpg"
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
                    tmp.write(resp.content)
                    tmp.close()
                    final_img_path = tmp.name
                    downloaded_temp = tmp.name
            except Exception as e:
                print(f"⚠️ 下載縮圖實體失敗: {e}")

        # 建立討論串
        thread_id = _create_forum_thread(
            thread_name, display_name,
            f"今日 {display_name} 共 {len(news_items)} 則新聞，請往下瀏覽各則詳情。",
            tag_ids, final_img_path, color, keywords=kw, img_url=None if final_img_path else thread_cover_url,
        )

        if downloaded_temp and os.path.exists(downloaded_temp):
            try:
                os.remove(downloaded_temp)
            except:
                pass
        if not thread_id:
            continue

        # 逐條發送：每則新聞文字 + 來源連結合為單一 Embed
        for idx, item in enumerate(news_items, 1):
            if not item["text"] and not item["source"]:
                continue
            embed_title = _get_item_title(item)
            if not embed_title:
                embed_title = "最新資訊"
            # 抓取文章縮圖（og:image）
            thumb = ""
            if item["source"]:
                src_url = _extract_url_from_source(item["source"])
                if src_url:
                    thumb = _fetch_og_image(src_url)
            _post_embed_to_thread(thread_id, embed_title, item["text"], color,
                                  source=item["source"], thumbnail_url=thumb)
            time.sleep(0.5)

        time.sleep(1.5)

    # 最後建立一個分割線討論串
    divider_name = f"━━━ {date_str} ━━━"
    if DRY_RUN:
        print(f"[DRY-RUN] 分割線 : {divider_name}  (無標籤)")
    else:
        url = f"https://discord.com/api/v10/channels/{FORUM_CHANNEL_ID}/threads"
        headers = {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}
        divider_embed = {
            "description": f"━━━━━━━━━━━━━━━━━━━━\n📅 **{date_str}**\n━━━━━━━━━━━━━━━━━━━━",
            "color": 0x2C2F33,
        }
        payload = {
            "name": divider_name[:100],
            "message": {"content": "", "embeds": [divider_embed]},
        }
        resp = requests.post(url, headers=headers, json=payload)
        if resp.ok:
            print(f"📏 分割線已建立: {divider_name}")
        else:
            print(f"⚠️ 分割線建立失敗: {resp.status_code}")

    print("✅ 所有段落已成功發布至 Discord 論壇頻道！")


if __name__ == "__main__":
    send_to_discord_forum()
